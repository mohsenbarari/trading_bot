#!/usr/bin/env python3
"""Coordinate exact production-shadow Nginx generations across both FI hosts.

The default invocation is plan-only and executes no command. Apply mode uses
the operation's release-owned host worker locally for Bot-FI and through one
pinned SSH endpoint for WebApp-FI. Controller evidence contains only bounded
command metadata and hashes, never command output bodies.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from core.three_site_topology import (  # noqa: E402
    BOT_FI_HOST,
    WEBAPP_FI_HOST,
)
from scripts import production_shadow_nginx_generation as GENERATION  # noqa: E402


COORDINATOR_SCHEMA = "production-shadow-nginx-coordinator-journal-v1"
EVIDENCE_SCHEMA = "production-shadow-nginx-coordinator-command-evidence-v1"
RESULT_SCHEMA = "production-shadow-nginx-coordinator-result-v1"
STATE_RECEIPT_SCHEMA = (
    "production-shadow-nginx-coordinator-state-receipt-v1"
)
PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA = (
    "production-shadow-nginx-coordinator-fresh-state-receipt-v1"
)
READBACK_CHALLENGE_SET_SCHEMA = (
    "production-shadow-nginx-readback-challenge-set-v1"
)
LIVE_LEASE_CLAIM_SCHEMA = (
    "production-shadow-nginx-coordinator-live-lease-claim-v1"
)
LIVE_LEASE_CONSUMPTION_SCHEMA = (
    "production-shadow-nginx-coordinator-live-lease-consumption-v1"
)
LIVE_LEASE_READINESS_SCHEMA = (
    "production-shadow-nginx-coordinator-live-lease-readiness-v1"
)
LEGACY_WRITER_READINESS_SET_SCHEMA = (
    "production-shadow-legacy-writer-readiness-set-v2"
)
LEGACY_WRITER_RESULT_SCHEMA = (
    "production-shadow-legacy-writer-freeze-result-v3"
)
LEGACY_WRITER_LIVE_CHALLENGE_SCHEMA = (
    "production-shadow-legacy-writer-live-checkpoint-challenge-v1"
)
LEGACY_WRITER_LIVE_RESPONSE_SCHEMA = (
    "production-shadow-legacy-writer-live-checkpoint-response-v1"
)
PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
CONTROLLER_SECRET_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
KNOWN_HOSTS = Path("/root/.ssh/known_hosts")
DEFAULT_SSH_IDENTITY = Path("/root/.ssh/id_ed25519")
HOST_CONTROL_PARENT = Path("/etc/trading-bot-production-shadow")
HOST_OPERATION_BASE = GENERATION.DEFAULT_OPERATION_BASE
PYTHON = "/usr/bin/python3"
ENV = "/usr/bin/env"
SSH = "/usr/bin/ssh"
SCP = "/usr/bin/scp"
CURL = "/usr/bin/curl"
WEBAPP_FI_SSH_USER = "root"
WEBAPP_FI_SSH_PORT = 37067
WORKER_RELATIVE_PATH = Path(
    "scripts/production_shadow_nginx_generation.py"
)
ROLE_ORDER = ("bot_fi", "webapp_fi")
LEGACY_WRITER_COUNTS = {"bot_fi": 3, "webapp_fi": 2}
LEGACY_WRITER_KINDS = {
    "bot_fi": ("application", "bot", "sync_worker"),
    "webapp_fi": ("application", "sync_worker"),
}
LEGACY_WRITER_CHECKPOINT_RE = re.compile(
    r"^(?:before-stop|after-stop|before-start|after-start):"
    r"(?:application|bot|sync_worker)$"
    r"|^readiness-(?:http|stability):[1-9][0-9]{0,2}$"
    r"|^before-result$"
)
ACTIONS = (
    "install",
    "test",
    "activate",
    "rollback-freeze",
    "readback",
    "restore",
)
STATE_RECEIPT_ACTIONS = ACTIONS
SUCCESS_STATUSES = frozenset(
    {
        "planned",
        "installed",
        "already-installed",
        "tested",
        "activated",
        "already-active",
        "read-back",
        "restored",
        "already-restored",
    }
)
NEXT_STATE = {
    "legacy-normal": "legacy-frozen",
    "legacy-frozen": "shadow-readonly",
    "shadow-readonly": "shadow-writable",
}
BLOCKED_STATES = frozenset({"legacy-frozen", "shadow-readonly"})
PROBE_PATH = "/.production-shadow-impossible-write-probe-v1"
VHOST_TARGETS = (
    ("coin.362514.ir", BOT_FI_HOST),
    ("mini-app.362514.ir", BOT_FI_HOST),
    ("coin.gold-trade.ir", WEBAPP_FI_HOST),
)
VHOST_RECEIPT_LAYOUT = {
    "coin.362514.ir": {
        "role": "bot_fi",
        "destination": "/etc/nginx/sites-available/coin.362514.ir",
    },
    "mini-app.362514.ir": {
        "role": "bot_fi",
        "destination": "/etc/nginx/sites-available/trading-bot",
    },
    "coin.gold-trade.ir": {
        "role": "webapp_fi",
        "destination": "/etc/nginx/sites-available/trading-bot",
    },
}
MAX_COMMAND_STDOUT_BYTES = 2 * 1024 * 1024
MAX_COMMAND_STDERR_BYTES = 2 * 1024 * 1024
MAX_COMMAND_TIMEOUT_SECONDS = 300
COMMAND_TERM_GRACE_SECONDS = 2.0
PROCESS_POLL_SECONDS = 0.05
PROCESS_TREE_QUIESCENCE_SECONDS = 0.1
PR_SET_CHILD_SUBREAPER = 36
READBACK_MAX_CROSS_HOST_SKEW_SECONDS = 15
MAX_KEY_BYTES = 1024 * 1024
FILE_MODE = 0o600
DIRECTORY_MODE = 0o700
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=%+-]+$")
EVIDENCE_KIND_RE = re.compile(r"^[a-z0-9-]+$")
LIVE_LEASE_NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
LIVE_LEASE_OWNER_OUTCOMES = {
    "capture-frozen-final-snapshots": frozenset(
        {"handoff-shadow-readonly"}
    ),
    "verify-current-frozen-writers": frozenset(
        {"current-frozen-verified"}
    ),
    "restore-legacy-writers": frozenset({"legacy-restored"}),
    "restore-shadow-frozen-final": frozenset(
        {"frozen-final-shadow-restored"}
    ),
}
LOCAL_EXECUTABLES = frozenset({ENV, PYTHON, SSH, SCP, CURL})
REMOTE_EXECUTABLES = frozenset(
    {
        PYTHON,
        "/usr/bin/stat",
        "/usr/bin/mkdir",
        "/usr/bin/sha256sum",
        "/usr/bin/mv",
        "/usr/bin/unlink",
    }
)


class NginxCoordinatorError(RuntimeError):
    """Raised when two-host coordination cannot be proven safe."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


RunFn = Callable[..., CommandResult]


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


@dataclass(frozen=True)
class RoleMaterial:
    role: str
    expected_host: str
    manifest_path: Path
    archive_path: Path
    manifest_sha256: str
    manifest_payload: bytes
    manifest: Mapping[str, Any]
    members: Mapping[str, bytes]


@dataclass(frozen=True)
class CoordinatorInputs:
    aggregate_path: Path
    aggregate_payload: bytes
    aggregate_sha256: str
    aggregate: Mapping[str, Any]
    roles: Mapping[str, RoleMaterial]
    operation_id: str
    release_sha: str
    release_tree_sha: str
    release_root: Path
    worker_path: Path
    worker_sha256: str
    worker_bytes: int
    coordinator_root: Path
    evidence_root: Path
    receipts_root: Path
    journal_path: Path
    remote_manifest_path: Path
    remote_archive_path: Path
    known_hosts: Path
    ssh_identity: Path
    ssh_identity_sha256: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NginxCoordinatorError("JSON contains a duplicate key")
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise NginxCoordinatorError(
            f"{label} must be a nonzero SHA-256"
        )
    return value


def _canonical_uuid4(value: Any) -> str:
    if not isinstance(value, str):
        raise NginxCoordinatorError(
            "operation ID must be a canonical UUIDv4"
        )
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as exc:
        raise NginxCoordinatorError(
            "operation ID must be a canonical UUIDv4"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise NginxCoordinatorError(
            "operation ID must be a canonical UUIDv4"
        )
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or GENERATION.SHA40_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise NginxCoordinatorError(
            f"{label} must be a nonzero lowercase 40-hex identity"
        )
    return value


def _canonical_path(path: Path, *, label: str) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path != Path(os.path.abspath(os.fspath(path)))
        or path.name in {"", ".", ".."}
        or "\0" in os.fspath(path)
    ):
        raise NginxCoordinatorError(
            f"{label} must be an absolute canonical path"
        )
    return path


def _read_private_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    exact_mode: int = FILE_MODE,
) -> bytes:
    path = _canonical_path(path, label=label)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != exact_mode
            or not 1 <= before.st_size <= maximum
        ):
            raise NginxCoordinatorError(
                f"{label} is not an exact root-only file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = path.stat(follow_symlinks=False)
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            len(payload) != before.st_size
            or len(payload) > maximum
            or any(
                getattr(before, field) != getattr(after, field)
                or getattr(before, field) != getattr(visible, field)
                for field in fields
            )
        ):
            raise NginxCoordinatorError(
                f"{label} changed during its stable read"
            )
        return payload
    except NginxCoordinatorError:
        raise
    except OSError as exc:
        raise NginxCoordinatorError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_canonical_json(
    path: Path,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    payload = _read_private_file(
        path,
        label=label,
        maximum=GENERATION.MAX_JSON_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except NginxCoordinatorError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise NginxCoordinatorError(f"{label} is invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or payload != canonical_json_bytes(document)
    ):
        raise NginxCoordinatorError(f"{label} is not canonical JSON")
    return document, payload


def load_state_receipt(
    path: Path,
    expected_state: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    aggregate_sha256: str,
    *,
    allow_historical: bool = False,
    observed_at_epoch: int | None = None,
    _require_current_journal: bool = True,
) -> tuple[dict[str, Any], str]:
    """Load and fully verify a canonical two-host state receipt."""
    if type(allow_historical) is not bool:
        raise NginxCoordinatorError(
            "historical receipt policy is invalid"
        )
    if type(_require_current_journal) is not bool:
        raise NginxCoordinatorError(
            "current journal receipt policy is invalid"
        )
    if allow_historical and not _require_current_journal:
        raise NginxCoordinatorError(
            "historical and transferred-fresh policies cannot be combined"
        )
    if (
        observed_at_epoch is not None
        and (
            type(observed_at_epoch) is not int
            or observed_at_epoch < 1
        )
    ):
        raise NginxCoordinatorError(
            "receipt observation time is invalid"
        )
    if expected_state not in GENERATION.GENERATION_STATES:
        raise NginxCoordinatorError(
            "expected receipt state is not allowlisted"
        )
    operation_id = _canonical_uuid4(operation_id)
    release_sha = _release_sha(release_sha, label="receipt release SHA")
    release_tree_sha = _release_sha(
        release_tree_sha,
        label="receipt release tree SHA",
    )
    aggregate_sha256 = _nonzero_sha256(
        aggregate_sha256,
        label="receipt aggregate SHA-256",
    )
    document, payload = _load_canonical_json(
        path,
        label="production Nginx coordinator state receipt",
    )
    base_fields = {
        "schema",
        "verification_status",
        "source_action",
        "requested_target_state",
        "coordinator_status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "role_bindings",
        "state",
        "vhost_generation_sha256",
        "global_generation_sha256",
        "readbacks",
        "external_readback",
        "journal_sha256",
        "evidence_count",
        "evidence_tail_sha256",
        "production_contacted",
        "active_configuration_mutated",
        "current_mutated",
        "container_mutated",
        "volume_mutated",
        "data_mutated",
    }
    freshness_fields = {
        "readback_challenge_sha256",
        "issued_at_epoch",
        "expires_at_epoch",
        "captured_at_epoch",
    }
    is_fresh = (
        document.get("schema")
        == PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
    )
    is_historical = document.get("schema") == STATE_RECEIPT_SCHEMA
    expected_fields = (
        base_fields | freshness_fields if is_fresh else base_fields
    )
    identity = {
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "aggregate_sha256": aggregate_sha256,
    }
    if (
        set(document) != expected_fields
        or not (is_fresh or is_historical)
        or (is_historical and not allow_historical)
        or document["verification_status"] != "verified"
        or document["source_action"] not in STATE_RECEIPT_ACTIONS
        or not isinstance(document["coordinator_status"], str)
        or not document["coordinator_status"]
        or any(document.get(key) != value for key, value in identity.items())
        or document["state"] != expected_state
        or document["production_contacted"] is not True
        or document["active_configuration_mutated"] is not False
        or any(
            document[field] is not False
            for field in (
                "current_mutated",
                "container_mutated",
                "volume_mutated",
                "data_mutated",
            )
        )
        or type(document["evidence_count"]) is not int
        or document["evidence_count"] < 1
    ):
        raise NginxCoordinatorError(
            "production Nginx coordinator state receipt differs"
        )
    requested = document["requested_target_state"]
    if (
        document["source_action"] in {
            "test",
            "activate",
            "rollback-freeze",
        }
        and requested not in GENERATION.GENERATION_STATES
    ) or (
        document["source_action"]
        not in {"test", "activate", "rollback-freeze"}
        and requested is not None
    ) or (
        document["source_action"] == "rollback-freeze"
        and requested != "legacy-frozen"
    ):
        raise NginxCoordinatorError(
            "state receipt requested target differs"
        )
    _nonzero_sha256(
        document["journal_sha256"],
        label="state receipt journal SHA-256",
    )
    _nonzero_sha256(
        document["evidence_tail_sha256"],
        label="state receipt evidence tail SHA-256",
    )
    role_bindings = document["role_bindings"]
    if (
        not isinstance(role_bindings, dict)
        or set(role_bindings) != set(ROLE_ORDER)
    ):
        raise NginxCoordinatorError(
            "state receipt role closure differs"
        )
    for role in ROLE_ORDER:
        binding = role_bindings[role]
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "expected_host",
                "manifest_sha256",
                "archive_sha256",
            }
            or binding["expected_host"] != GENERATION.ROLE_HOSTS[role]
        ):
            raise NginxCoordinatorError(
                "state receipt role binding differs"
            )
        _nonzero_sha256(
            binding["manifest_sha256"],
            label="state receipt manifest SHA-256",
        )
        _nonzero_sha256(
            binding["archive_sha256"],
            label="state receipt archive SHA-256",
        )
    vhost_rows = document["vhost_generation_sha256"]
    if (
        not isinstance(vhost_rows, dict)
        or set(vhost_rows) != set(VHOST_RECEIPT_LAYOUT)
    ):
        raise NginxCoordinatorError(
            "state receipt vhost digest closure differs"
        )
    role_digest_rows: dict[str, dict[str, str]] = {
        role: {} for role in ROLE_ORDER
    }
    global_rows: dict[str, str] = {}
    for vhost, expected_layout in VHOST_RECEIPT_LAYOUT.items():
        row = vhost_rows[vhost]
        if (
            not isinstance(row, dict)
            or set(row) != {
                "role",
                "destination",
                "generation_sha256",
            }
            or row["role"] != expected_layout["role"]
            or row["destination"] != expected_layout["destination"]
        ):
            raise NginxCoordinatorError(
                "state receipt vhost digest binding differs"
            )
        digest = _nonzero_sha256(
            row["generation_sha256"],
            label="state receipt vhost generation SHA-256",
        )
        role_digest_rows[row["role"]][row["destination"]] = digest
        global_rows[f"{row['role']}:{row['destination']}"] = digest
    readbacks = document["readbacks"]
    if (
        not isinstance(readbacks, dict)
        or set(readbacks) != set(ROLE_ORDER)
    ):
        raise NginxCoordinatorError(
            "state receipt host readback closure differs"
        )
    historical_readback_fields = {
        "schema",
        "status",
        "operation_id",
        "role",
        "expected_host",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "archive_sha256",
        "state",
        "generation_sha256",
        "enabled_inventory_sha256",
        "enabled_inventory_count",
        "active_configuration_mutated",
        "service_reloaded",
        "journal_sha256",
    }
    fresh_readback_fields = historical_readback_fields | {
        "readback_challenge_nonce",
        "readback_challenge_sha256",
        "issued_at_epoch",
        "expires_at_epoch",
        "captured_at_epoch",
    }
    readback_fields = (
        fresh_readback_fields if is_fresh else historical_readback_fields
    )
    for role in ROLE_ORDER:
        readback = readbacks[role]
        binding = role_bindings[role]
        expected_role_digest = GENERATION._generation_digest(  # noqa: SLF001
            role_digest_rows[role]
        )
        if (
            not isinstance(readback, dict)
            or set(readback) != readback_fields
            or readback["schema"]
            != (
                GENERATION.HOST_FRESH_READBACK_SCHEMA
                if is_fresh
                else "production-shadow-nginx-host-readback-v1"
            )
            or readback["status"] != "read-back"
            or readback["operation_id"] != operation_id
            or readback["role"] != role
            or readback["expected_host"] != binding["expected_host"]
            or readback["release_sha"] != release_sha
            or readback["release_tree_sha"] != release_tree_sha
            or readback["manifest_sha256"]
            != binding["manifest_sha256"]
            or readback["archive_sha256"] != binding["archive_sha256"]
            or readback["state"] != expected_state
            or readback["generation_sha256"] != expected_role_digest
            or type(readback["enabled_inventory_count"]) is not int
            or readback["enabled_inventory_count"] < 1
            or readback["active_configuration_mutated"] is not False
            or readback["service_reloaded"] is not False
        ):
            raise NginxCoordinatorError(
                "state receipt host readback differs"
            )
        _nonzero_sha256(
            readback["enabled_inventory_sha256"],
            label="state receipt inventory SHA-256",
        )
        _nonzero_sha256(
            readback["journal_sha256"],
            label="state receipt host journal SHA-256",
        )
    global_digest = GENERATION._generation_digest(global_rows)  # noqa: SLF001
    if document["global_generation_sha256"] != global_digest:
        raise NginxCoordinatorError(
            "state receipt global generation digest differs"
        )
    external = document["external_readback"]
    historical_external_fields = {
        "states",
        "states_by_role",
        "blocked_probes_performed",
        "write_method_probe_performed",
        "vhosts",
    }
    external_fields = historical_external_fields | (
        freshness_fields if is_fresh else set()
    )
    blocked = expected_state in BLOCKED_STATES
    if (
        not isinstance(external, dict)
        or set(external) != external_fields
        or external["states"] != [expected_state]
        or external["states_by_role"]
        != {role: expected_state for role in ROLE_ORDER}
        or external["blocked_probes_performed"] is not blocked
        or external["write_method_probe_performed"] is not blocked
        or not isinstance(external["vhosts"], dict)
        or set(external["vhosts"]) != {
            vhost for vhost, _ in VHOST_TARGETS
        }
    ):
        raise NginxCoordinatorError(
            "state receipt external readback closure differs"
        )
    expected_probes = (
        {"get", "post", "websocket"} if blocked else {"get"}
    )
    for vhost, _ in VHOST_TARGETS:
        probes = external["vhosts"][vhost]
        if (
            not isinstance(probes, dict)
            or set(probes) != expected_probes
            or type(probes["get"]) is not int
            or not 200 <= probes["get"] <= 399
            or (
                blocked
                and (
                    probes["post"] != 503
                    or probes["websocket"] != 503
                )
            )
        ):
            raise NginxCoordinatorError(
                "state receipt external probe result differs"
            )
    if is_fresh:
        freshness = _readback_freshness(
            operation_id=operation_id,
            release_sha=release_sha,
            release_tree_sha=release_tree_sha,
            aggregate_sha256=aggregate_sha256,
            readbacks=readbacks,
        )
        if (
            any(
                document[field] != freshness[field]
                for field in (
                    "readback_challenge_sha256",
                    "issued_at_epoch",
                    "expires_at_epoch",
                )
            )
            or any(
                external[field] != document[field]
                for field in freshness_fields
            )
            or type(document["captured_at_epoch"]) is not int
            or document["captured_at_epoch"]
            < freshness["captured_at_epoch"]
            - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
            or document["captured_at_epoch"]
            > document["expires_at_epoch"]
        ):
            raise NginxCoordinatorError(
                "state receipt fresh readback binding differs"
            )
        if not allow_historical:
            now_epoch = (
                int(time.time())
                if observed_at_epoch is None
                else observed_at_epoch
            )
            if (
                now_epoch
                < document["issued_at_epoch"]
                - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
                or now_epoch > document["expires_at_epoch"]
                or document["captured_at_epoch"]
                > now_epoch + GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
            ):
                raise NginxCoordinatorError(
                    "state receipt fresh readback window is not current"
                )
            if _require_current_journal:
                digest = _sha256(payload)
                expected_path = _canonical_receipt_path(
                    operation_id=operation_id,
                    state=expected_state,
                    digest=digest,
                )
                if path != expected_path:
                    raise NginxCoordinatorError(
                        "fresh state receipt path is not controller-canonical"
                    )
                current_journal, _ = _load_canonical_json(
                    path.parent.parent / "journal.json",
                    label="current production Nginx coordinator journal",
                )
                if (
                    current_journal.get("schema") != COORDINATOR_SCHEMA
                    or current_journal.get("operation_id") != operation_id
                    or current_journal.get("release_sha") != release_sha
                    or current_journal.get("release_tree_sha")
                    != release_tree_sha
                    or current_journal.get("aggregate_sha256")
                    != aggregate_sha256
                    or current_journal.get("stable_state") != expected_state
                    or current_journal.get("pending") is not None
                    or current_journal.get("state_sha256")
                    != _journal_hash(current_journal)
                    or current_journal.get("state_sha256")
                    != document["journal_sha256"]
                    or current_journal.get("evidence_count")
                    != document["evidence_count"]
                    or current_journal.get("evidence_tail_sha256")
                    != document["evidence_tail_sha256"]
                ):
                    raise NginxCoordinatorError(
                        "fresh state receipt is not bound to the current journal"
                    )
    return document, _sha256(payload)


def _expected_global_generation(
    roles: Mapping[str, RoleMaterial],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for state in GENERATION.GENERATION_STATES:
        rows = {
            f"{role}:{row['destination']}": row["generation_sha256"][state]
            for role in ROLE_ORDER
            for row in roles[role].manifest["vhosts"]
        }
        result[state] = GENERATION._generation_digest(rows)  # noqa: SLF001
    return result


def _validate_aggregate(
    document: Mapping[str, Any],
    *,
    roles: Mapping[str, RoleMaterial],
) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "shadow_release_root",
        "roles",
        "generation_sha256",
        "legacy_upstream_closure_sha256",
        "nginx_legacy_normal_generation_sha256",
        "nginx_rollback_generation_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_shadow_readonly_generation_sha256",
        "nginx_shadow_writable_generation_sha256",
        "contains_tls_key_or_certificate_body",
        "production_contacted",
        "active_configuration_mutated",
    }
    if not isinstance(document, Mapping) or set(document) != fields:
        raise NginxCoordinatorError("aggregate fields are not exact")
    operation_id = _canonical_uuid4(document["operation_id"])
    release_sha = _release_sha(document["release_sha"], label="release SHA")
    release_tree_sha = _release_sha(
        document["release_tree_sha"],
        label="release tree SHA",
    )
    release_root = (
        PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
    )
    if (
        document["schema"] != GENERATION.PRODUCER_SCHEMA
        or release_sha == release_tree_sha
        or document["shadow_release_root"] != os.fspath(release_root)
        or document["contains_tls_key_or_certificate_body"] is not False
        or document["production_contacted"] is not False
        or document["active_configuration_mutated"] is not False
    ):
        raise NginxCoordinatorError("aggregate identity or safety flags differ")
    aggregate_roles = document["roles"]
    role_fields = {
        "expected_host",
        "manifest_sha256",
        "manifest_bytes",
        "archive_sha256",
        "archive_bytes",
        "legacy_upstream_closure_sha256",
        "generation_sha256",
    }
    if (
        not isinstance(aggregate_roles, dict)
        or set(aggregate_roles) != set(ROLE_ORDER)
    ):
        raise NginxCoordinatorError("aggregate role closure differs")
    for role in ROLE_ORDER:
        row = aggregate_roles[role]
        material = roles[role]
        manifest = material.manifest
        if (
            not isinstance(row, dict)
            or set(row) != role_fields
            or row["expected_host"] != GENERATION.ROLE_HOSTS[role]
            or row["manifest_sha256"] != material.manifest_sha256
            or row["manifest_bytes"] != len(material.manifest_payload)
            or row["archive_sha256"] != manifest["archive"]["sha256"]
            or row["archive_bytes"] != manifest["archive"]["bytes"]
            or row["legacy_upstream_closure_sha256"]
            != manifest["legacy_upstream_closure_sha256"]
            or row["generation_sha256"] != manifest["generation_sha256"]
        ):
            raise NginxCoordinatorError(
                f"aggregate {role} material binding differs"
            )
    expected_global = _expected_global_generation(roles)
    if (
        document["generation_sha256"] != expected_global
        or len(set(expected_global.values()))
        != len(GENERATION.GENERATION_STATES)
    ):
        raise NginxCoordinatorError(
            "aggregate global generation digests differ"
        )
    closure = _sha256(
        canonical_json_bytes(
            {
                role: roles[role].manifest[
                    "legacy_upstream_closure_sha256"
                ]
                for role in ROLE_ORDER
            }
        )
    )
    aliases = {
        "nginx_legacy_normal_generation_sha256": "legacy-normal",
        "nginx_rollback_generation_sha256": "legacy-normal",
        "nginx_freeze_generation_sha256": "legacy-frozen",
        "nginx_shadow_readonly_generation_sha256": "shadow-readonly",
        "nginx_shadow_writable_generation_sha256": "shadow-writable",
    }
    if (
        document["legacy_upstream_closure_sha256"] != closure
        or any(
            document[field] != expected_global[state]
            for field, state in aliases.items()
        )
    ):
        raise NginxCoordinatorError(
            "aggregate cutover digest aliases differ"
        )
    return json.loads(canonical_json_bytes(document).decode("utf-8"))


def _release_worker_path(operation_id: str, release_sha: str) -> Path:
    return (
        PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
        / WORKER_RELATIVE_PATH
    )


def _validate_release_worker(path: Path) -> bytes:
    return _read_private_file(
        path,
        label="release-owned Nginx host worker",
        maximum=GENERATION.MAX_JSON_BYTES,
        exact_mode=0o755,
    )


def _incoming_paths(
    operation_id: str,
    material: RoleMaterial,
) -> tuple[Path, Path]:
    root = PROJECT_ROOT_PREFIX / operation_id / "incoming"
    role_path = material.role.replace("_", "-")
    manifest = root / (
        f"nginx-generation-manifest-{role_path}-"
        f"{material.manifest_sha256}.json"
    )
    archive = root / (
        f"nginx-generations-{role_path}-"
        f"{material.manifest['archive']['sha256']}.tar"
    )
    return manifest, archive


def load_inputs(
    *,
    aggregate_path: Path,
    bot_fi_manifest: Path,
    bot_fi_archive: Path,
    webapp_fi_manifest: Path,
    webapp_fi_archive: Path,
    known_hosts: Path = KNOWN_HOSTS,
    ssh_identity: Path = DEFAULT_SSH_IDENTITY,
) -> CoordinatorInputs:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise NginxCoordinatorError("Nginx coordinator requires root:root")
    paths = (
        aggregate_path,
        bot_fi_manifest,
        bot_fi_archive,
        webapp_fi_manifest,
        webapp_fi_archive,
        known_hosts,
        ssh_identity,
    )
    canonical_paths = tuple(
        _canonical_path(path, label="coordinator input") for path in paths
    )
    if len(set(canonical_paths)) != len(canonical_paths):
        raise NginxCoordinatorError(
            "coordinator input paths must be distinct"
        )
    aggregate_document, aggregate_payload = _load_canonical_json(
        canonical_paths[0],
        label="Nginx generation aggregate",
    )
    operation_id = _canonical_uuid4(
        aggregate_document.get("operation_id")
    )
    release_sha = _release_sha(
        aggregate_document.get("release_sha"),
        label="release SHA",
    )
    release_tree_sha = _release_sha(
        aggregate_document.get("release_tree_sha"),
        label="release tree SHA",
    )
    raw_roles = aggregate_document.get("roles")
    if not isinstance(raw_roles, dict) or set(raw_roles) != set(ROLE_ORDER):
        raise NginxCoordinatorError("aggregate role closure differs")
    path_by_role = {
        "bot_fi": (canonical_paths[1], canonical_paths[2]),
        "webapp_fi": (canonical_paths[3], canonical_paths[4]),
    }
    roles: dict[str, RoleMaterial] = {}
    for role in ROLE_ORDER:
        row = raw_roles[role]
        if not isinstance(row, dict):
            raise NginxCoordinatorError(
                f"aggregate {role} binding is invalid"
            )
        manifest_path, archive_path = path_by_role[role]
        try:
            manifest, manifest_payload, members = (
                GENERATION.load_role_material(
                    manifest_path=manifest_path,
                    expected_manifest_sha256=row.get("manifest_sha256"),
                    archive_path=archive_path,
                    expected_role=role,
                    expected_host=GENERATION.ROLE_HOSTS[role],
                    operation_id=operation_id,
                    release_sha=release_sha,
                    release_tree_sha=release_tree_sha,
                    owner_uid=0,
                )
            )
        except GENERATION.NginxGenerationError as exc:
            raise NginxCoordinatorError(
                f"{role} generation material is invalid"
            ) from exc
        roles[role] = RoleMaterial(
            role=role,
            expected_host=GENERATION.ROLE_HOSTS[role],
            manifest_path=manifest_path,
            archive_path=archive_path,
            manifest_sha256=row["manifest_sha256"],
            manifest_payload=manifest_payload,
            manifest=manifest,
            members=members,
        )
    aggregate = _validate_aggregate(aggregate_document, roles=roles)
    release_root = (
        PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
    )
    worker = _release_worker_path(operation_id, release_sha)
    worker_payload = _validate_release_worker(worker)
    _read_private_file(
        canonical_paths[5],
        label="pinned SSH known-hosts file",
        maximum=MAX_KEY_BYTES,
    )
    ssh_identity_payload = _read_private_file(
        canonical_paths[6],
        label="pinned SSH private identity",
        maximum=MAX_KEY_BYTES,
    )
    coordinator_root = (
        CONTROLLER_SECRET_PREFIX / operation_id / "nginx-coordinator"
    )
    remote_manifest, remote_archive = _incoming_paths(
        operation_id,
        roles["webapp_fi"],
    )
    return CoordinatorInputs(
        aggregate_path=canonical_paths[0],
        aggregate_payload=aggregate_payload,
        aggregate_sha256=_sha256(aggregate_payload),
        aggregate=aggregate,
        roles=roles,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        release_root=release_root,
        worker_path=worker,
        worker_sha256=_sha256(worker_payload),
        worker_bytes=len(worker_payload),
        coordinator_root=coordinator_root,
        evidence_root=coordinator_root / "evidence",
        receipts_root=coordinator_root / "receipts",
        journal_path=coordinator_root / "journal.json",
        remote_manifest_path=remote_manifest,
        remote_archive_path=remote_archive,
        known_hosts=canonical_paths[5],
        ssh_identity=canonical_paths[6],
        ssh_identity_sha256=_sha256(ssh_identity_payload),
    )


def _role_bindings(inputs: CoordinatorInputs) -> dict[str, Any]:
    return {
        role: {
            "expected_host": inputs.roles[role].expected_host,
            "manifest_sha256": inputs.roles[role].manifest_sha256,
            "archive_sha256": inputs.roles[role].manifest["archive"]["sha256"],
        }
        for role in ROLE_ORDER
    }


def _journal_hash(journal: Mapping[str, Any]) -> str:
    unsigned = dict(journal)
    unsigned["state_sha256"] = ""
    return _sha256(canonical_json_bytes(unsigned))


def _new_journal(inputs: CoordinatorInputs) -> dict[str, Any]:
    journal: dict[str, Any] = {
        "schema": COORDINATOR_SCHEMA,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "role_bindings": _role_bindings(inputs),
        "installed_roles": [],
        "tested_states": {},
        "stable_state": None,
        "pending": None,
        "events": [],
        "evidence_count": 0,
        "evidence_tail_sha256": "0" * 64,
        "state_sha256": "",
    }
    journal["state_sha256"] = _journal_hash(journal)
    return journal


def _append_event(
    journal: dict[str, Any],
    kind: str,
    *,
    data: Mapping[str, Any],
    evidence_sha256: str | None = None,
) -> None:
    event = {
        "index": len(journal["events"]) + 1,
        "kind": kind,
        "data": dict(data),
        "evidence_sha256": evidence_sha256,
        "previous_event_sha256": (
            journal["events"][-1]["event_sha256"]
            if journal["events"]
            else "0" * 64
        ),
    }
    event["event_sha256"] = _sha256(canonical_json_bytes(event))
    journal["events"].append(event)


def _validate_journal(
    journal: Mapping[str, Any],
    *,
    inputs: CoordinatorInputs,
) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "role_bindings",
        "installed_roles",
        "tested_states",
        "stable_state",
        "pending",
        "events",
        "evidence_count",
        "evidence_tail_sha256",
        "state_sha256",
    }
    expected = {
        "schema": COORDINATOR_SCHEMA,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "role_bindings": _role_bindings(inputs),
    }
    if (
        not isinstance(journal, Mapping)
        or set(journal) != fields
        or any(journal.get(key) != value for key, value in expected.items())
        or journal.get("state_sha256") != _journal_hash(journal)
    ):
        raise NginxCoordinatorError(
            "coordinator journal is invalid or bound elsewhere"
        )
    installed = journal["installed_roles"]
    if (
        not isinstance(installed, list)
        or installed != list(ROLE_ORDER[: len(installed)])
        or len(installed) != len(set(installed))
    ):
        raise NginxCoordinatorError(
            "coordinator installed-role journal is invalid"
        )
    tested = journal["tested_states"]
    if (
        not isinstance(tested, dict)
        or any(state not in GENERATION.GENERATION_STATES for state in tested)
        or any(
            roles != list(ROLE_ORDER)
            for roles in tested.values()
        )
    ):
        raise NginxCoordinatorError(
            "coordinator tested-state journal is invalid"
        )
    stable = journal["stable_state"]
    if stable is not None and stable not in GENERATION.GENERATION_STATES:
        raise NginxCoordinatorError(
            "coordinator stable state is invalid"
        )
    pending = journal["pending"]
    if pending is not None:
        pending_fields = {
            "action",
            "target_state",
            "from_state",
            "policy",
            "source_receipt_sha256",
            "lease_claim_sha256",
            "status",
            "completed_roles",
            "attempt",
        }
        if (
            not isinstance(pending, dict)
            or set(pending) != pending_fields
            or pending["action"]
            not in {
                "install",
                "activate",
                "rollback-freeze",
                "restore",
            }
            or pending["target_state"] not in {
                None,
                *GENERATION.GENERATION_STATES,
            }
            or pending["from_state"] not in {
                None,
                *GENERATION.GENERATION_STATES,
            }
            or pending["policy"]
            not in {
                "complete-both",
                "compensate-legacy-normal",
                "keep-write-blocked",
                "rollback-to-frozen-write-blocked",
                "forward-only-same-target",
            }
            or (
                pending["source_receipt_sha256"] is not None
                and (
                    not isinstance(
                        pending["source_receipt_sha256"],
                        str,
                    )
                    or SHA256_RE.fullmatch(
                        pending["source_receipt_sha256"]
                    )
                    is None
                    or pending["source_receipt_sha256"] == "0" * 64
                )
            )
            or (
                pending["action"] == "rollback-freeze"
                and pending["source_receipt_sha256"] is None
            )
            or (
                pending["action"] != "rollback-freeze"
                and pending["source_receipt_sha256"] is not None
            )
            or (
                pending["lease_claim_sha256"] is not None
                and (
                    not isinstance(pending["lease_claim_sha256"], str)
                    or SHA256_RE.fullmatch(
                        pending["lease_claim_sha256"]
                    )
                    is None
                    or pending["lease_claim_sha256"] == "0" * 64
                )
            )
            or (
                pending["action"] == "restore"
                and pending["lease_claim_sha256"] is None
            )
            or (
                pending["action"] != "restore"
                and pending["lease_claim_sha256"] is not None
            )
            or pending["status"]
            not in {
                "running",
                "partial-resumable",
                "forward-only-retry",
                "compensated-failed",
                "failed",
            }
            or not isinstance(pending["completed_roles"], list)
            or pending["completed_roles"]
            != [
                role
                for role in ROLE_ORDER
                if role in pending["completed_roles"]
            ]
            or type(pending["attempt"]) is not int
            or not 1 <= pending["attempt"] <= 1_000_000
        ):
            raise NginxCoordinatorError(
                "coordinator pending transition is invalid"
            )
    events = journal["events"]
    if not isinstance(events, list):
        raise NginxCoordinatorError("coordinator event journal is invalid")
    previous = "0" * 64
    command_count = 0
    evidence_tail = "0" * 64
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event)
            != {
                "index",
                "kind",
                "data",
                "evidence_sha256",
                "previous_event_sha256",
                "event_sha256",
            }
            or event["index"] != index
            or not isinstance(event["kind"], str)
            or not event["kind"]
            or not isinstance(event["data"], dict)
            or event["previous_event_sha256"] != previous
        ):
            raise NginxCoordinatorError(
                "coordinator event chain is invalid"
            )
        unsigned = dict(event)
        observed = unsigned.pop("event_sha256")
        if observed != _sha256(canonical_json_bytes(unsigned)):
            raise NginxCoordinatorError(
                "coordinator event hash chain differs"
            )
        evidence = event["evidence_sha256"]
        if evidence is not None:
            _nonzero_sha256(evidence, label="event evidence")
            command_count += 1
            evidence_tail = evidence
        previous = observed
    if (
        journal["evidence_count"] != command_count
        or journal["evidence_tail_sha256"] != evidence_tail
    ):
        raise NginxCoordinatorError(
            "coordinator evidence chain differs"
        )
    return json.loads(canonical_json_bytes(journal).decode("utf-8"))


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    create: bool,
) -> None:
    journal["state_sha256"] = _journal_hash(journal)
    payload = canonical_json_bytes(journal)
    try:
        if create:
            write_secure_new_bytes(
                path,
                payload,
                label="Nginx coordinator journal",
                mode=FILE_MODE,
                max_size=GENERATION.MAX_JSON_BYTES,
            )
        else:
            write_secure_atomic_bytes(
                path,
                payload,
                label="Nginx coordinator journal",
                mode=FILE_MODE,
                max_size=GENERATION.MAX_JSON_BYTES,
            )
    except SecureFileError as exc:
        raise NginxCoordinatorError(
            "coordinator journal could not be persisted"
        ) from exc


def _load_journal(inputs: CoordinatorInputs) -> dict[str, Any]:
    document, _ = _load_canonical_json(
        inputs.journal_path,
        label="Nginx coordinator journal",
    )
    journal = _validate_journal(document, inputs=inputs)
    previous = "0" * 64
    evidence_index = 0
    fields = {
        "schema",
        "index",
        "kind",
        "scope",
        "operation_id",
        "action",
        "target_state",
        "argv_sha256",
        "returncode",
        "stdout_sha256",
        "stdout_bytes",
        "stderr_sha256",
        "stderr_bytes",
        "previous_evidence_sha256",
        "evidence_sha256",
    }
    for event in journal["events"]:
        evidence_sha256 = event["evidence_sha256"]
        if evidence_sha256 is None:
            continue
        evidence_index += 1
        kind = event["data"].get("kind")
        if (
            not isinstance(kind, str)
            or EVIDENCE_KIND_RE.fullmatch(kind) is None
        ):
            raise NginxCoordinatorError(
                "coordinator evidence kind is invalid"
            )
        path = inputs.evidence_root / (
            f"{evidence_index:06d}-{kind}-{evidence_sha256}.json"
        )
        evidence, _ = _load_canonical_json(
            path,
            label="coordinator command evidence",
        )
        unsigned = dict(evidence)
        observed_hash = unsigned.pop("evidence_sha256", None)
        if (
            set(evidence) != fields
            or evidence["schema"] != EVIDENCE_SCHEMA
            or evidence["index"] != evidence_index
            or evidence["kind"] != kind
            or evidence["scope"] != event["data"].get("scope")
            or evidence["operation_id"] != inputs.operation_id
            or evidence["action"] != event["data"].get("action")
            or evidence["target_state"]
            != event["data"].get("target_state")
            or evidence["returncode"] != event["data"].get("returncode")
            or evidence["previous_evidence_sha256"] != previous
            or observed_hash != evidence_sha256
            or observed_hash != _sha256(canonical_json_bytes(unsigned))
        ):
            raise NginxCoordinatorError(
                "coordinator command evidence differs"
            )
        for digest_field in (
            "argv_sha256",
            "stdout_sha256",
            "stderr_sha256",
        ):
            _nonzero_sha256(
                evidence[digest_field],
                label="coordinator evidence digest",
            )
        for size_field in ("stdout_bytes", "stderr_bytes"):
            if (
                type(evidence[size_field]) is not int
                or not 0
                <= evidence[size_field]
                <= MAX_COMMAND_STDOUT_BYTES
            ):
                raise NginxCoordinatorError(
                    "coordinator evidence size differs"
                )
        previous = evidence_sha256
    return journal


def _ensure_private_directory(
    path: Path,
    *,
    create: bool,
) -> None:
    path = _canonical_path(path, label="coordinator directory")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise NginxCoordinatorError(
                "required coordinator directory is absent"
            )
        parent = path.parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or parent.st_gid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise NginxCoordinatorError(
                "coordinator directory parent is unsafe"
            )
        try:
            path.mkdir(mode=DIRECTORY_MODE)
        except OSError as exc:
            raise NginxCoordinatorError(
                "coordinator directory could not be created"
            ) from exc
        _fsync_directory(path.parent)
        metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != DIRECTORY_MODE
    ):
        raise NginxCoordinatorError(
            "coordinator directory is not root-only"
        )


def _prepare_controller_state(inputs: CoordinatorInputs) -> dict[str, Any]:
    operation_root = CONTROLLER_SECRET_PREFIX / inputs.operation_id
    _ensure_private_directory(CONTROLLER_SECRET_PREFIX, create=False)
    _ensure_private_directory(operation_root, create=False)
    _ensure_private_directory(inputs.coordinator_root, create=True)
    _ensure_private_directory(inputs.evidence_root, create=True)
    _ensure_private_directory(inputs.receipts_root, create=True)
    if inputs.journal_path.exists() or inputs.journal_path.is_symlink():
        return _load_journal(inputs)
    journal = _new_journal(inputs)
    _write_journal(inputs.journal_path, journal, create=True)
    return journal


class _CoordinatorLock:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.descriptor = -1

    def __enter__(self) -> "_CoordinatorLock":
        if self.descriptor >= 0:
            raise NginxCoordinatorError(
                "coordinator lock is already held by this handle"
            )
        path = self.root / "coordinator.lock"
        try:
            self.descriptor = os.open(
                path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                FILE_MODE,
            )
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != FILE_MODE
            ):
                raise NginxCoordinatorError(
                    "coordinator lock is unsafe"
                )
            try:
                fcntl.flock(
                    self.descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as exc:
                raise NginxCoordinatorError(
                    "coordinator lock is busy"
                ) from exc
            return self
        except NginxCoordinatorError:
            if self.descriptor >= 0:
                os.close(self.descriptor)
                self.descriptor = -1
            raise
        except OSError as exc:
            if self.descriptor >= 0:
                os.close(self.descriptor)
                self.descriptor = -1
            raise NginxCoordinatorError(
                "coordinator lock is unavailable"
            ) from exc

    @property
    def held(self) -> bool:
        return self.descriptor >= 0

    def __exit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        return False


def _process_identity(pid: int) -> ProcessIdentity | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            return None
        return ProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1], 10),
            process_group=int(fields[2], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _process_snapshot() -> dict[int, ProcessIdentity]:
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise NginxCoordinatorError(
            "subprocess ownership inventory is unavailable"
        ) from exc
    return {
        identity.pid: identity
        for entry in entries
        if entry.name.isdecimal()
        for identity in (_process_identity(int(entry.name, 10)),)
        if identity is not None
    }


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise NginxCoordinatorError(
            f"child subreaper setup failed with errno {error}"
        )


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_pid == owner
    )


def _owned_processes(
    root: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> set[ProcessIdentity]:
    snapshot = _process_snapshot()
    observed_root = snapshot.get(root.pid)
    owned_ids: set[int] = set()
    if (
        observed_root is not None
        and observed_root.start_time == root.start_time
    ):
        owned_ids.add(root.pid)
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.pid not in owned_ids
                and identity.parent_pid in owned_ids
            ):
                owned_ids.add(identity.pid)
                changed = True
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.pid)
    return {
        identity
        for pid, identity in snapshot.items()
        if pid in owned_ids
    }


def _identity_is_live(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state != "Z"
    )


def _identity_is_current(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
    )


def _reap_adopted_zombies(
    tracked: set[ProcessIdentity],
    *,
    root_pid: int,
) -> None:
    for identity in tuple(tracked):
        if identity.pid == root_pid:
            continue
        current = _process_identity(identity.pid)
        if (
            current is None
            or current.start_time != identity.start_time
            or current.parent_pid != os.getpid()
            or current.state != "Z"
        ):
            continue
        try:
            reaped, _status = os.waitpid(identity.pid, os.WNOHANG)
        except ChildProcessError:
            continue
        except OSError as exc:
            raise NginxCoordinatorError(
                "identity-bound adopted subprocess could not be reaped"
            ) from exc
        if reaped not in {0, identity.pid}:
            raise NginxCoordinatorError(
                "identity-bound adopted subprocess reap differed"
            )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    current = _process_identity(identity.pid)
    if current is None or current.start_time != identity.start_time:
        return
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise NginxCoordinatorError(
            "identity-bound subprocess handle cannot be opened"
        ) from exc
    try:
        refreshed = _process_identity(identity.pid)
        if refreshed is None or refreshed.start_time != identity.start_time:
            return
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise NginxCoordinatorError(
            "identity-bound subprocess signal failed"
        ) from exc
    finally:
        os.close(descriptor)


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    root: ProcessIdentity,
    tracked: set[ProcessIdentity],
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    def refresh() -> None:
        tracked.update(
            _owned_processes(
                root,
                baseline_children=baseline_children,
            )
        )
        _reap_adopted_zombies(tracked, root_pid=root.pid)

    def signal_live(*, force: bool) -> None:
        refresh()
        for identity in tuple(tracked):
            if _identity_is_live(identity):
                _signal_process_identity(
                    identity,
                    (
                        signal.SIGKILL
                        if force
                        or identity.process_group != root.process_group
                        else signal.SIGTERM
                    ),
                )

    signal_live(force=False)
    deadline = time.monotonic() + COMMAND_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        signal_live(force=False)
        if process.poll() is not None and not any(
            _identity_is_live(identity) for identity in tracked
        ):
            break
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    signal_live(force=True)
    try:
        process.wait(timeout=COMMAND_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=COMMAND_TERM_GRACE_SECONDS)
    absence_deadline = (
        time.monotonic()
        + COMMAND_TERM_GRACE_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        refresh()
        live = {
            identity for identity in tracked if _identity_is_live(identity)
        }
        if live:
            stable_since = None
            for identity in live:
                _signal_process_identity(identity, signal.SIGKILL)
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= PROCESS_TREE_QUIESCENCE_SECONDS
        ):
            return
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, absence_deadline - time.monotonic()),
            )
        )
    refresh()
    if any(
        identity.pid != root.pid and _identity_is_current(identity)
        for identity in tracked
    ):
        raise NginxCoordinatorError(
            "subprocess process tree survived forced cleanup"
        )


def _subprocess_runner(
    argv: Sequence[str],
    timeout: int,
    *,
    stdin: int = subprocess.DEVNULL,
    pass_fds: tuple[int, ...] = (),
) -> CommandResult:
    if (
        type(timeout) is not int
        or not 1 <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS
    ):
        raise NginxCoordinatorError(
            "coordinator command timeout is outside the bounded contract"
        )
    if (
        stdin != subprocess.DEVNULL
        and (type(stdin) is not int or stdin < 0)
    ):
        raise NginxCoordinatorError(
            "coordinator command stdin is outside the bounded contract"
        )
    if (
        not isinstance(pass_fds, tuple)
        or pass_fds
    ):
        raise NginxCoordinatorError(
            "coordinator inherited descriptors are outside the bounded contract"
        )
    if stdin != subprocess.DEVNULL:
        try:
            metadata = os.fstat(stdin)
            flags = fcntl.fcntl(stdin, fcntl.F_GETFL)
            target = os.readlink(f"/proc/self/fd/{stdin}")
        except OSError as exc:
            raise NginxCoordinatorError(
                "coordinator command liveness pipe is unavailable"
            ) from exc
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or target != f"pipe:[{metadata.st_ino}]"
        ):
            raise NginxCoordinatorError(
                "coordinator command stdin is not an anonymous read pipe"
            )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    tracked: set[ProcessIdentity] = set()
    root: ProcessIdentity | None = None
    cleaned = False
    deadline = time.monotonic() + timeout
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    try:
        process = subprocess.Popen(  # noqa: S603
            list(argv),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        root = _process_identity(process.pid)
        if root is None:
            process.poll()
            root = ProcessIdentity(
                pid=process.pid,
                parent_pid=os.getpid(),
                process_group=process.pid,
                start_time=-1,
                state="?",
            )
        if process.stdout is None or process.stderr is None:
            raise NginxCoordinatorError(
                "bounded coordinator pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            tracked.update(
                _owned_processes(
                    root,
                    baseline_children=baseline_children,
                )
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NginxCoordinatorError(
                    "bounded coordinator command timed out"
                )
            events = selector.select(
                min(PROCESS_POLL_SECONDS, remaining)
            )
            if not events:
                if process.poll() is not None and not cleaned:
                    _terminate_process_tree(
                        process,
                        root,
                        tracked,
                        baseline_children=baseline_children,
                    )
                    cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                buffer = buffers[label]
                limit = (
                    MAX_COMMAND_STDOUT_BYTES
                    if label == "stdout"
                    else MAX_COMMAND_STDERR_BYTES
                )
                if len(buffer) + len(chunk) > limit:
                    raise NginxCoordinatorError(
                        f"bounded coordinator {label} is oversized"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not cleaned:
                _terminate_process_tree(
                    process,
                    root,
                    tracked,
                    baseline_children=baseline_children,
                )
                cleaned = True
        returncode = process.poll()
        if returncode is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NginxCoordinatorError(
                    "bounded coordinator command timed out"
                )
            returncode = process.wait(timeout=remaining)
    except NginxCoordinatorError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise NginxCoordinatorError(
            "bounded coordinator command could not execute"
        ) from exc
    finally:
        selector.close()
        if process is not None and root is not None:
            try:
                if not cleaned:
                    _terminate_process_tree(
                        process,
                        root,
                        tracked,
                        baseline_children=baseline_children,
                    )
            finally:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
    return CommandResult(
        returncode=returncode,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
    )


def _write_evidence(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    kind: str,
    scope: str,
    action: str,
    target_state: str | None,
    argv: Sequence[str],
    result: CommandResult,
) -> str:
    index = journal["evidence_count"] + 1
    evidence: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "index": index,
        "kind": kind,
        "scope": scope,
        "operation_id": inputs.operation_id,
        "action": action,
        "target_state": target_state,
        "argv_sha256": _sha256(canonical_json_bytes(list(argv))),
        "returncode": result.returncode,
        "stdout_sha256": _sha256(result.stdout),
        "stdout_bytes": len(result.stdout),
        "stderr_sha256": _sha256(result.stderr),
        "stderr_bytes": len(result.stderr),
        "previous_evidence_sha256": journal["evidence_tail_sha256"],
    }
    evidence["evidence_sha256"] = _sha256(canonical_json_bytes(evidence))
    payload = canonical_json_bytes(evidence)
    path = inputs.evidence_root / (
        f"{index:06d}-{kind}-{evidence['evidence_sha256']}.json"
    )
    if path.exists() or path.is_symlink():
        observed = _read_private_file(
            path,
            label="existing coordinator evidence",
            maximum=GENERATION.MAX_JSON_BYTES,
        )
        if observed != payload:
            raise NginxCoordinatorError(
                "existing coordinator evidence differs"
            )
    else:
        try:
            write_secure_new_bytes(
                path,
                payload,
                label="coordinator command evidence",
                mode=FILE_MODE,
                max_size=GENERATION.MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise NginxCoordinatorError(
                "coordinator command evidence could not be persisted"
            ) from exc
    journal["evidence_count"] = index
    journal["evidence_tail_sha256"] = evidence["evidence_sha256"]
    _append_event(
        journal,
        "command",
        data={
            "kind": kind,
            "scope": scope,
            "action": action,
            "target_state": target_state,
            "returncode": result.returncode,
        },
        evidence_sha256=evidence["evidence_sha256"],
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return evidence["evidence_sha256"]


def _run_audited(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    argv: Sequence[str],
    *,
    runner: RunFn,
    kind: str,
    scope: str,
    action: str,
    target_state: str | None,
    timeout: int,
    accepted_returncodes: frozenset[int] = frozenset({0}),
    stdin: int = subprocess.DEVNULL,
    pass_fds: tuple[int, ...] = (),
) -> CommandResult:
    if (
        not isinstance(argv, (tuple, list))
        or not argv
        or any(not isinstance(token, str) or not token for token in argv)
        or argv[0] not in LOCAL_EXECUTABLES
    ):
        raise NginxCoordinatorError("command argv is invalid")
    result = runner(
        tuple(argv),
        timeout,
        stdin=stdin,
        pass_fds=pass_fds,
    )
    if (
        not isinstance(result, CommandResult)
        and not isinstance(result, GENERATION.CommandResult)
    ):
        raise NginxCoordinatorError("coordinator runner result is invalid")
    normalized = CommandResult(
        result.returncode,
        bytes(result.stdout),
        bytes(result.stderr),
    )
    if (
        isinstance(normalized.returncode, bool)
        or not isinstance(normalized.returncode, int)
        or len(normalized.stdout) > MAX_COMMAND_STDOUT_BYTES
        or len(normalized.stderr) > MAX_COMMAND_STDERR_BYTES
    ):
        raise NginxCoordinatorError(
            "coordinator runner output is invalid or oversized"
        )
    _write_evidence(
        inputs,
        journal,
        kind=kind,
        scope=scope,
        action=action,
        target_state=target_state,
        argv=argv,
        result=normalized,
    )
    if normalized.returncode not in accepted_returncodes:
        raise NginxCoordinatorError(f"{kind} command failed")
    return normalized


def _ssh_prefix(
    known_hosts: Path,
    ssh_identity: Path,
) -> tuple[str, ...]:
    return (
        SSH,
        "-F",
        "/dev/null",
        "-T",
        "-i",
        os.fspath(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
        "-p",
        str(WEBAPP_FI_SSH_PORT),
        "--",
        f"{WEBAPP_FI_SSH_USER}@{WEBAPP_FI_HOST}",
    )


def _scp_prefix(
    known_hosts: Path,
    ssh_identity: Path,
) -> tuple[str, ...]:
    return (
        SCP,
        "-F",
        "/dev/null",
        "-q",
        "-B",
        "-p",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
        "-P",
        str(WEBAPP_FI_SSH_PORT),
        "-i",
        os.fspath(ssh_identity),
        "--",
    )


def _safe_remote_command(tokens: Sequence[str]) -> tuple[str, ...]:
    if any(
        SAFE_REMOTE_TOKEN_RE.fullmatch(token) is None
        for token in tokens
    ):
        raise NginxCoordinatorError(
            "remote command contains an unsafe token"
        )
    return tuple(tokens)


def _worker_arguments(
    inputs: CoordinatorInputs,
    *,
    role: str,
    action: str,
    generation: str | None,
    remote: bool,
    readback_challenge: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    material = inputs.roles[role]
    if remote:
        manifest_path = inputs.remote_manifest_path
        archive_path = inputs.remote_archive_path
    else:
        manifest_path = material.manifest_path
        archive_path = material.archive_path
    arguments = [
        ENV,
        "-i",
        "PATH=/usr/bin:/bin",
        "HOME=/root",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "GIT_NO_REPLACE_OBJECTS=1",
        PYTHON,
        "-I",
        "-B",
        os.fspath(inputs.worker_path),
        "host",
        "--manifest",
        os.fspath(manifest_path),
        "--manifest-sha256",
        material.manifest_sha256,
        "--archive",
        os.fspath(archive_path),
        "--role",
        role,
        "--expected-host",
        material.expected_host,
        "--operation-id",
        inputs.operation_id,
        "--release-sha",
        inputs.release_sha,
        "--release-tree-sha",
        inputs.release_tree_sha,
        "--action",
        action,
    ]
    if generation is not None:
        arguments.extend(("--generation", generation))
    arguments.append("--apply")
    if action == "readback":
        if (
            not isinstance(readback_challenge, Mapping)
            or set(readback_challenge)
            != {
                "readback_challenge_nonce",
                "readback_challenge_sha256",
                "issued_at_epoch",
                "expires_at_epoch",
            }
        ):
            raise NginxCoordinatorError(
                "fresh host readback challenge is required"
            )
        arguments.extend(
            (
                "--readback-challenge-nonce",
                str(readback_challenge["readback_challenge_nonce"]),
                "--readback-challenge-sha256",
                str(readback_challenge["readback_challenge_sha256"]),
                "--issued-at-epoch",
                str(readback_challenge["issued_at_epoch"]),
                "--expires-at-epoch",
                str(readback_challenge["expires_at_epoch"]),
                "--control-fd",
                "0",
            )
        )
    elif action in {
        "install",
        "test",
        "activate",
        "rollback-freeze",
        "restore",
    }:
        arguments.extend(("--control-fd", "0"))
        effective = "legacy-normal" if action == "restore" else generation
        arguments.extend(
            (
                "--confirm",
                GENERATION.confirmation_phrase(
                    action=action,
                    operation_id=inputs.operation_id,
                    role=role,
                    generation=effective,
                ),
            )
        )
    if remote:
        return (
            *_ssh_prefix(inputs.known_hosts, inputs.ssh_identity),
            *_safe_remote_command(arguments),
        )
    return tuple(arguments)


def _new_host_readback_challenge(
    inputs: CoordinatorInputs,
    *,
    role: str,
) -> dict[str, Any]:
    material = inputs.roles[role]
    issued_at_epoch = int(time.time())
    expires_at_epoch = (
        issued_at_epoch + GENERATION.READBACK_CHALLENGE_TTL_SECONDS
    )
    challenge = {
        "schema": GENERATION.HOST_READBACK_CHALLENGE_SCHEMA,
        "operation_id": inputs.operation_id,
        "role": role,
        "expected_host": material.expected_host,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "manifest_sha256": material.manifest_sha256,
        "archive_sha256": material.manifest["archive"]["sha256"],
        "readback_challenge_nonce": secrets.token_hex(32),
        "issued_at_epoch": issued_at_epoch,
        "expires_at_epoch": expires_at_epoch,
    }
    challenge_sha256 = _sha256(canonical_json_bytes(challenge))
    return {
        "readback_challenge_nonce": challenge[
            "readback_challenge_nonce"
        ],
        "readback_challenge_sha256": challenge_sha256,
        "issued_at_epoch": issued_at_epoch,
        "expires_at_epoch": expires_at_epoch,
    }


def _parse_exact_json_stdout(
    payload: bytes,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except NginxCoordinatorError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise NginxCoordinatorError(f"{label} output is invalid JSON") from exc
    if (
        not isinstance(document, dict)
        or payload
        != json.dumps(document, sort_keys=True).encode("utf-8") + b"\n"
    ):
        raise NginxCoordinatorError(
            f"{label} output is not exact host-worker JSON"
        )
    return document


def _validate_command_evidence(value: Any) -> None:
    fields = {
        "argv_sha256",
        "returncode",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_bytes",
        "stderr_bytes",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["returncode"] != 0
        or any(
            not isinstance(value[field], str)
            or SHA256_RE.fullmatch(value[field]) is None
            for field in (
                "argv_sha256",
                "stdout_sha256",
                "stderr_sha256",
            )
        )
        or any(
            type(value[field]) is not int
            or not 0 <= value[field] <= MAX_COMMAND_STDOUT_BYTES
            for field in ("stdout_bytes", "stderr_bytes")
        )
    ):
        raise NginxCoordinatorError(
            "host worker command evidence is invalid"
        )


def _validate_stability_evidence(
    value: Any,
    *,
    expected_state: str,
) -> None:
    if (
        not isinstance(value, list)
        or not 2 <= len(value) <= 10
    ):
        raise NginxCoordinatorError(
            "activation stability evidence is invalid"
        )
    for index, observation in enumerate(value, 1):
        if (
            not isinstance(observation, dict)
            or set(observation)
            != {"index", "service", "nginx_test", "state"}
            or type(observation["index"]) is not int
            or observation["index"] != index
            or observation["state"] != expected_state
        ):
            raise NginxCoordinatorError(
                "activation stability observation differs"
            )
        _validate_command_evidence(observation["service"])
        _validate_command_evidence(observation["nginx_test"])


def _validate_host_result(
    document: Mapping[str, Any],
    *,
    inputs: CoordinatorInputs,
    role: str,
    action: str,
    generation: str | None,
    readback_challenge: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    material = inputs.roles[role]
    identity = {
        "operation_id": inputs.operation_id,
        "role": role,
        "expected_host": material.expected_host,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "manifest_sha256": material.manifest_sha256,
        "archive_sha256": material.manifest["archive"]["sha256"],
    }
    if action == "readback":
        fields = {
            "schema",
            "status",
            *identity,
            "state",
            "generation_sha256",
            "enabled_inventory_sha256",
            "enabled_inventory_count",
            "active_configuration_mutated",
            "service_reloaded",
            "journal_sha256",
            "readback_challenge_nonce",
            "readback_challenge_sha256",
            "issued_at_epoch",
            "expires_at_epoch",
            "captured_at_epoch",
        }
        now_epoch = int(time.time())
        if (
            not isinstance(document, Mapping)
            or set(document) != fields
            or document["schema"]
            != GENERATION.HOST_FRESH_READBACK_SCHEMA
            or document["status"] != "read-back"
            or any(document.get(key) != value for key, value in identity.items())
            or not isinstance(readback_challenge, Mapping)
            or any(
                document.get(field) != readback_challenge.get(field)
                for field in (
                    "readback_challenge_nonce",
                    "readback_challenge_sha256",
                    "issued_at_epoch",
                    "expires_at_epoch",
                )
            )
            or type(document["captured_at_epoch"]) is not int
            or document["captured_at_epoch"] < 1
            or document["captured_at_epoch"]
            < document["issued_at_epoch"]
            - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
            or document["captured_at_epoch"] > document["expires_at_epoch"]
            or document["captured_at_epoch"]
            > now_epoch + GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
            or now_epoch > document["expires_at_epoch"]
            or document["state"] not in GENERATION.GENERATION_STATES
            or document["generation_sha256"]
            != material.manifest["generation_sha256"][document["state"]]
            or type(document["enabled_inventory_count"]) is not int
            or document["enabled_inventory_count"] < 1
            or document["active_configuration_mutated"] is not False
            or document["service_reloaded"] is not False
        ):
            raise NginxCoordinatorError(
                f"{role} host readback output differs"
            )
        for field in (
            "enabled_inventory_sha256",
            "journal_sha256",
        ):
            _nonzero_sha256(document[field], label=f"{role} {field}")
        return json.loads(canonical_json_bytes(document).decode("utf-8"))

    if readback_challenge is not None:
        raise NginxCoordinatorError(
            "non-readback host result received a readback challenge"
        )

    base_fields = {
        "schema",
        "status",
        "action",
        "generation",
        "state",
        *identity,
        "active_configuration_mutated",
        "service_reloaded",
        "journal_sha256",
    }
    status = document.get("status") if isinstance(document, Mapping) else None
    extra_fields: set[str] = set()
    if action == "test":
        extra_fields = {"inventory_sha256", "candidate_sha256"}
        if status == "tested":
            extra_fields.add("command")
    elif (
        action in {"activate", "rollback-freeze", "restore"}
        and status == "activated"
    ):
        extra_fields = {"from_state", "commands"}
    expected_statuses = {
        "install": {"installed", "already-installed"},
        "test": {"tested", "already-tested"},
        "activate": {"activated", "already-active"},
        "rollback-freeze": {"activated", "already-active"},
        "restore": {"activated", "already-active"},
    }
    effective = "legacy-normal" if action == "restore" else generation
    if (
        not isinstance(document, Mapping)
        or set(document) != base_fields | extra_fields
        or document["schema"] != GENERATION.HOST_ACTION_RESULT_SCHEMA
        or status not in expected_statuses[action]
        or document["action"] != action
        or document["generation"] != effective
        or document["state"] != effective
        or any(document.get(key) != value for key, value in identity.items())
    ):
        raise NginxCoordinatorError(
            f"{role} {action} host result differs"
        )
    _nonzero_sha256(document["journal_sha256"], label="host journal")
    if action == "install" and (
        document["active_configuration_mutated"] is not False
        or document["service_reloaded"] is not False
    ):
        raise NginxCoordinatorError("install unexpectedly mutated Nginx")
    if action == "test":
        for field in ("inventory_sha256", "candidate_sha256"):
            _nonzero_sha256(document[field], label=f"test {field}")
        if status == "tested":
            _validate_command_evidence(document["command"])
        if (
            document["active_configuration_mutated"] is not False
            or document["service_reloaded"] is not False
        ):
            raise NginxCoordinatorError(
                "candidate test unexpectedly mutated Nginx"
            )
    if action in {"activate", "rollback-freeze", "restore"}:
        if status == "activated":
            if (
                document["active_configuration_mutated"] is not True
                or document["service_reloaded"] is not True
                or document["from_state"] not in GENERATION.GENERATION_STATES
                or not isinstance(document["commands"], dict)
                or set(document["commands"])
                != {"test", "reload", "stability"}
            ):
                raise NginxCoordinatorError(
                    "activation mutation evidence differs"
                )
            _validate_command_evidence(document["commands"]["test"])
            _validate_command_evidence(document["commands"]["reload"])
            _validate_stability_evidence(
                document["commands"]["stability"],
                expected_state=str(effective),
            )
        elif (
            document["active_configuration_mutated"] is not False
            or document["service_reloaded"] is not False
        ):
            raise NginxCoordinatorError(
                "already-active result claims a mutation"
            )
    return json.loads(canonical_json_bytes(document).decode("utf-8"))


def _call_host_worker(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    role: str,
    action: str,
    generation: str | None,
    runner: RunFn,
) -> dict[str, Any]:
    controlled = action in GENERATION.CONTROLLED_HOST_ACTIONS
    readback_challenge = (
        _new_host_readback_challenge(inputs, role=role)
        if action == "readback"
        else None
    )
    argv = _worker_arguments(
        inputs,
        role=role,
        action=action,
        generation=generation,
        remote=role == "webapp_fi",
        readback_challenge=readback_challenge,
    )
    read_fd: int | None = None
    write_fd: int | None = None
    try:
        if controlled:
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, False)
            os.set_inheritable(write_fd, False)
        result = _run_audited(
            inputs,
            journal,
            argv,
            runner=runner,
            kind="ssh-worker" if role == "webapp_fi" else "local-worker",
            scope=role,
            action=action,
            target_state=generation,
            timeout=90,
            stdin=(
                read_fd
                if read_fd is not None
                else subprocess.DEVNULL
            ),
            pass_fds=(),
        )
    finally:
        if read_fd is not None:
            os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
    document = _parse_exact_json_stdout(
        result.stdout,
        label=f"{role} host worker",
    )
    return _validate_host_result(
        document,
        inputs=inputs,
        role=role,
        action=action,
        generation=generation,
        readback_challenge=readback_challenge,
    )


def _remote_ssh(
    remote_arguments: Sequence[str],
    *,
    known_hosts: Path,
    ssh_identity: Path,
) -> tuple[str, ...]:
    if (
        not remote_arguments
        or remote_arguments[0] not in REMOTE_EXECUTABLES
    ):
        raise NginxCoordinatorError(
            "remote executable is not allowlisted"
        )
    return (
        *_ssh_prefix(known_hosts, ssh_identity),
        *_safe_remote_command(remote_arguments),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise NginxCoordinatorError(
            "directory creation could not be made durable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _ensure_local_host_control_directory(
    path: Path,
    *,
    create: bool,
    exact_mode: int | None,
) -> str:
    path = _canonical_path(path, label="local host-control directory")
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise NginxCoordinatorError(
                "required local host-control parent is absent"
            )
        parent = path.parent.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or parent.st_gid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise NginxCoordinatorError(
                "local host-control parent is unsafe"
            )
        try:
            path.mkdir(mode=DIRECTORY_MODE)
        except OSError as exc:
            raise NginxCoordinatorError(
                "local host-control directory could not be created"
            ) from exc
        _fsync_directory(path.parent)
        metadata = path.stat(follow_symlinks=False)
        created = True
    else:
        created = False
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or (
            exact_mode is None
            and mode & 0o022
        )
        or (
            exact_mode is not None
            and mode != exact_mode
        )
    ):
        raise NginxCoordinatorError(
            "local host-control directory metadata differs"
        )
    return "created" if created else "reused"


def _bootstrap_local_host_control() -> dict[str, str]:
    parent = _canonical_path(
        HOST_CONTROL_PARENT,
        label="local host-control parent",
    )
    operation_base = _canonical_path(
        HOST_OPERATION_BASE,
        label="local host operation base",
    )
    if operation_base.parent != parent:
        raise NginxCoordinatorError(
            "local host-control layout differs from the worker contract"
        )
    _ensure_local_host_control_directory(
        parent.parent,
        create=False,
        exact_mode=None,
    )
    return {
        os.fspath(parent): _ensure_local_host_control_directory(
            parent,
            create=True,
            exact_mode=DIRECTORY_MODE,
        ),
        os.fspath(operation_base): _ensure_local_host_control_directory(
            operation_base,
            create=True,
            exact_mode=DIRECTORY_MODE,
        ),
    }


def _remote_directory_state(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    path: Path,
    runner: RunFn,
    exact_mode: int | None,
    action: str,
) -> str:
    result = _run_audited(
        inputs,
        journal,
        _remote_ssh(
            (
                "/usr/bin/stat",
                "--printf=%F:%u:%g:%a",
                "--",
                os.fspath(path),
            ),
            known_hosts=inputs.known_hosts,
            ssh_identity=inputs.ssh_identity,
        ),
        runner=runner,
        kind="ssh-directory-stat",
        scope="webapp_fi",
        action=action,
        target_state=None,
        timeout=30,
        accepted_returncodes=frozenset({0, 1}),
    )
    if result.returncode == 1:
        if result.stdout:
            raise NginxCoordinatorError(
                "absent remote directory returned unexpected stdout"
            )
        return "absent"
    try:
        file_type, uid, gid, raw_mode = result.stdout.decode("ascii").split(":")
        mode = int(raw_mode, 8)
    except (UnicodeError, ValueError) as exc:
        raise NginxCoordinatorError(
            "remote directory metadata output is invalid"
        ) from exc
    if (
        file_type != "directory"
        or uid != "0"
        or gid != "0"
        or (
            exact_mode is None
            and mode & 0o022
        )
        or (
            exact_mode is not None
            and mode != exact_mode
        )
    ):
        raise NginxCoordinatorError(
            "remote directory metadata differs"
        )
    return "identical"


def _ensure_remote_host_control_directory(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    path: Path,
    runner: RunFn,
    action: str,
) -> str:
    state = _remote_directory_state(
        inputs,
        journal,
        path=path,
        runner=runner,
        exact_mode=DIRECTORY_MODE,
        action=action,
    )
    if state == "absent":
        _run_audited(
            inputs,
            journal,
            _remote_ssh(
                (
                    "/usr/bin/mkdir",
                    "--mode=0700",
                    "--",
                    os.fspath(path),
                ),
                known_hosts=inputs.known_hosts,
                ssh_identity=inputs.ssh_identity,
            ),
            runner=runner,
            kind="ssh-directory-create",
            scope="webapp_fi",
            action=action,
            target_state=None,
            timeout=30,
        )
        if _remote_directory_state(
            inputs,
            journal,
            path=path,
            runner=runner,
            exact_mode=DIRECTORY_MODE,
            action=action,
        ) != "identical":
            raise NginxCoordinatorError(
                "remote host-control directory was not created exactly"
            )
        return "created"
    return "reused"


def _bootstrap_remote_host_control(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
    action: str,
) -> dict[str, str]:
    parent = _canonical_path(
        HOST_CONTROL_PARENT,
        label="remote host-control parent",
    )
    operation_base = _canonical_path(
        HOST_OPERATION_BASE,
        label="remote host operation base",
    )
    if operation_base.parent != parent:
        raise NginxCoordinatorError(
            "remote host-control layout differs from the worker contract"
        )
    if _remote_directory_state(
        inputs,
        journal,
        path=parent.parent,
        runner=runner,
        exact_mode=None,
        action=action,
    ) != "identical":
        raise NginxCoordinatorError(
            "required remote host-control parent is absent"
        )
    return {
        os.fspath(parent): _ensure_remote_host_control_directory(
            inputs,
            journal,
            path=parent,
            runner=runner,
            action=action,
        ),
        os.fspath(operation_base): _ensure_remote_host_control_directory(
            inputs,
            journal,
            path=operation_base,
            runner=runner,
            action=action,
        ),
    }


def _remote_file_state(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    path: Path,
    expected_sha256: str,
    expected_bytes: int,
    expected_mode: int = FILE_MODE,
    action: str = "install",
    runner: RunFn,
) -> str:
    sha_result = _run_audited(
        inputs,
        journal,
        _remote_ssh(
            ("/usr/bin/sha256sum", "--", os.fspath(path)),
            known_hosts=inputs.known_hosts,
            ssh_identity=inputs.ssh_identity,
        ),
        runner=runner,
        kind="ssh-sha256",
        scope="webapp_fi",
        action=action,
        target_state=None,
        timeout=30,
        accepted_returncodes=frozenset({0, 1}),
    )
    if sha_result.returncode == 1:
        if sha_result.stdout:
            raise NginxCoordinatorError(
                "missing remote material returned unexpected stdout"
            )
        return "absent"
    try:
        observed_sha256, observed_path = (
            sha_result.stdout.decode("ascii").strip().split("  ", 1)
        )
    except (UnicodeError, ValueError) as exc:
        raise NginxCoordinatorError(
            "remote material hash output is invalid"
        ) from exc
    if (
        observed_sha256 != expected_sha256
        or observed_path != os.fspath(path)
    ):
        raise NginxCoordinatorError(
            "existing remote incoming material differs"
        )
    stat_result = _run_audited(
        inputs,
        journal,
        _remote_ssh(
            (
                "/usr/bin/stat",
                "--printf=%u:%g:%a:%h:%s",
                "--",
                os.fspath(path),
            ),
            known_hosts=inputs.known_hosts,
            ssh_identity=inputs.ssh_identity,
        ),
        runner=runner,
        kind="ssh-stat",
        scope="webapp_fi",
        action=action,
        target_state=None,
        timeout=30,
    )
    expected_stat = (
        f"0:0:{expected_mode:o}:1:{expected_bytes}".encode("ascii")
    )
    if stat_result.stdout != expected_stat:
        raise NginxCoordinatorError(
            "remote incoming material metadata differs"
        )
    return "identical"


def _validate_remote_prerequisites(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
    action: str,
) -> dict[str, Any]:
    bootstrap = _bootstrap_remote_host_control(
        inputs,
        journal,
        runner=runner,
        action=action,
    )
    incoming = inputs.remote_manifest_path.parent
    if _remote_directory_state(
        inputs,
        journal,
        path=incoming,
        runner=runner,
        exact_mode=DIRECTORY_MODE,
        action=action,
    ) != "identical":
        raise NginxCoordinatorError(
            "remote release incoming directory is absent"
        )
    worker = _remote_file_state(
        inputs,
        journal,
        path=inputs.worker_path,
        expected_sha256=inputs.worker_sha256,
        expected_bytes=inputs.worker_bytes,
        expected_mode=0o755,
        action=action,
        runner=runner,
    )
    if worker != "identical":
        raise NginxCoordinatorError(
            "remote release-owned Nginx host worker is absent"
        )
    return {
        "host_control": bootstrap,
        "incoming": os.fspath(incoming),
        "worker_sha256": inputs.worker_sha256,
    }


def _install_remote_material(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
) -> dict[str, str]:
    material = inputs.roles["webapp_fi"]
    rows = (
        (
            material.manifest_path,
            inputs.remote_manifest_path,
            material.manifest_sha256,
            len(material.manifest_payload),
        ),
        (
            material.archive_path,
            inputs.remote_archive_path,
            material.manifest["archive"]["sha256"],
            material.manifest["archive"]["bytes"],
        ),
    )
    publications: dict[str, str] = {}
    for source, destination, digest, size in rows:
        state = _remote_file_state(
            inputs,
            journal,
            path=destination,
            expected_sha256=digest,
            expected_bytes=size,
            runner=runner,
        )
        if state == "absent":
            upload = destination.with_name(
                f".{destination.name}.coordinator-upload"
            )
            upload_state = _remote_file_state(
                inputs,
                journal,
                path=upload,
                expected_sha256=digest,
                expected_bytes=size,
                runner=runner,
            )
            remote = (
                f"{WEBAPP_FI_SSH_USER}@{WEBAPP_FI_HOST}:"
                f"{upload}"
            )
            if upload_state == "absent":
                result = _run_audited(
                    inputs,
                    journal,
                    (
                        *_scp_prefix(
                            inputs.known_hosts,
                            inputs.ssh_identity,
                        ),
                        os.fspath(source),
                        remote,
                    ),
                    runner=runner,
                    kind="scp-stage-create",
                    scope="webapp_fi",
                    action="install",
                    target_state=None,
                    timeout=120,
                )
                if result.stdout:
                    raise NginxCoordinatorError(
                        "SCP returned unexpected stdout"
                    )
                upload_state = _remote_file_state(
                    inputs,
                    journal,
                    path=upload,
                    expected_sha256=digest,
                    expected_bytes=size,
                    runner=runner,
                )
            if upload_state != "identical":
                raise NginxCoordinatorError(
                    "remote upload staging material differs"
                )
            _run_audited(
                inputs,
                journal,
                _remote_ssh(
                    (
                        "/usr/bin/mv",
                        "--no-clobber",
                        "--",
                        os.fspath(upload),
                        os.fspath(destination),
                    ),
                    known_hosts=inputs.known_hosts,
                    ssh_identity=inputs.ssh_identity,
                ),
                runner=runner,
                kind="ssh-create-only-publish",
                scope="webapp_fi",
                action="install",
                target_state=None,
                timeout=30,
            )
            observed = _remote_file_state(
                inputs,
                journal,
                path=destination,
                expected_sha256=digest,
                expected_bytes=size,
                runner=runner,
            )
            if observed != "identical":
                raise NginxCoordinatorError(
                    "remote incoming material was not created exactly"
                )
            leftover = _remote_file_state(
                inputs,
                journal,
                path=upload,
                expected_sha256=digest,
                expected_bytes=size,
                runner=runner,
            )
            if leftover == "identical":
                _run_audited(
                    inputs,
                    journal,
                    _remote_ssh(
                        ("/usr/bin/unlink", "--", os.fspath(upload)),
                        known_hosts=inputs.known_hosts,
                        ssh_identity=inputs.ssh_identity,
                    ),
                    runner=runner,
                    kind="ssh-upload-cleanup",
                    scope="webapp_fi",
                    action="install",
                    target_state=None,
                    timeout=30,
                )
                if _remote_file_state(
                    inputs,
                    journal,
                    path=upload,
                    expected_sha256=digest,
                    expected_bytes=size,
                    runner=runner,
                ) != "absent":
                    raise NginxCoordinatorError(
                        "remote upload staging material remains"
                    )
            publications[destination.name] = "created"
        else:
            publications[destination.name] = "reused"
    return publications


def _global_digest_for_readbacks(
    inputs: CoordinatorInputs,
    readbacks: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    states = {readbacks[role]["state"] for role in ROLE_ORDER}
    if len(states) != 1:
        return None, None
    state = next(iter(states))
    for role in ROLE_ORDER:
        expected = inputs.roles[role].manifest["generation_sha256"][state]
        if readbacks[role]["generation_sha256"] != expected:
            raise NginxCoordinatorError(
                f"{role} local generation digest differs"
            )
    rows = {
        f"{role}:{row['destination']}": row["generation_sha256"][state]
        for role in ROLE_ORDER
        for row in inputs.roles[role].manifest["vhosts"]
    }
    observed_global = GENERATION._generation_digest(rows)  # noqa: SLF001
    expected_global = inputs.aggregate["generation_sha256"][state]
    if observed_global != expected_global:
        raise NginxCoordinatorError(
            "two-host global generation digest differs"
        )
    return state, observed_global


def _readback_hosts(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
) -> dict[str, dict[str, Any]]:
    return {
        role: _call_host_worker(
            inputs,
            journal,
            role=role,
            action="readback",
            generation=None,
            runner=runner,
        )
        for role in ROLE_ORDER
    }


def _readback_freshness(
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    aggregate_sha256: str,
    readbacks: Mapping[str, Mapping[str, Any]],
) -> dict[str, int | str]:
    if (
        not isinstance(readbacks, Mapping)
        or set(readbacks) != set(ROLE_ORDER)
    ):
        raise NginxCoordinatorError(
            "fresh readback role closure differs"
        )
    bindings: dict[str, dict[str, Any]] = {}
    for role in ROLE_ORDER:
        row = readbacks[role]
        bindings[role] = {
            field: row.get(field)
            for field in (
                "readback_challenge_nonce",
                "readback_challenge_sha256",
                "issued_at_epoch",
                "expires_at_epoch",
            )
        }
        if (
            not isinstance(bindings[role]["readback_challenge_nonce"], str)
            or SHA256_RE.fullmatch(
                bindings[role]["readback_challenge_nonce"]
            )
            is None
            or bindings[role]["readback_challenge_nonce"] == "0" * 64
            or not isinstance(
                bindings[role]["readback_challenge_sha256"], str
            )
            or SHA256_RE.fullmatch(
                bindings[role]["readback_challenge_sha256"]
            )
            is None
            or bindings[role]["readback_challenge_sha256"] == "0" * 64
            or type(bindings[role]["issued_at_epoch"]) is not int
            or type(bindings[role]["expires_at_epoch"]) is not int
            or bindings[role]["expires_at_epoch"]
            != bindings[role]["issued_at_epoch"]
            + GENERATION.READBACK_CHALLENGE_TTL_SECONDS
            or type(row.get("captured_at_epoch")) is not int
            or row["captured_at_epoch"]
            < bindings[role]["issued_at_epoch"]
            - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
            or row["captured_at_epoch"]
            > bindings[role]["expires_at_epoch"]
        ):
            raise NginxCoordinatorError(
                f"{role} fresh readback timing differs"
            )
        host_challenge = {
            "schema": GENERATION.HOST_READBACK_CHALLENGE_SCHEMA,
            "operation_id": operation_id,
            "role": role,
            "expected_host": row.get("expected_host"),
            "release_sha": release_sha,
            "release_tree_sha": release_tree_sha,
            "manifest_sha256": row.get("manifest_sha256"),
            "archive_sha256": row.get("archive_sha256"),
            "readback_challenge_nonce": bindings[role][
                "readback_challenge_nonce"
            ],
            "issued_at_epoch": bindings[role]["issued_at_epoch"],
            "expires_at_epoch": bindings[role]["expires_at_epoch"],
        }
        if (
            _sha256(canonical_json_bytes(host_challenge))
            != bindings[role]["readback_challenge_sha256"]
        ):
            raise NginxCoordinatorError(
                f"{role} fresh readback challenge binding differs"
            )
    issued = [bindings[role]["issued_at_epoch"] for role in ROLE_ORDER]
    captured = [readbacks[role]["captured_at_epoch"] for role in ROLE_ORDER]
    if (
        issued != sorted(issued)
        or captured[1]
        < captured[0] - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
        or max(captured) - min(captured)
        > READBACK_MAX_CROSS_HOST_SKEW_SECONDS
    ):
        raise NginxCoordinatorError(
            "fresh readback cross-host timing differs"
        )
    challenge_set = {
        "schema": READBACK_CHALLENGE_SET_SCHEMA,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "aggregate_sha256": aggregate_sha256,
        "role_challenges": bindings,
    }
    return {
        "readback_challenge_sha256": _sha256(
            canonical_json_bytes(challenge_set)
        ),
        "issued_at_epoch": min(issued),
        "expires_at_epoch": min(
            bindings[role]["expires_at_epoch"] for role in ROLE_ORDER
        ),
        "captured_at_epoch": max(captured),
    }


def _curl_arguments(
    *,
    vhost: str,
    address: str,
    probe: str,
) -> tuple[str, ...]:
    base = [
        CURL,
        "--disable",
        "--silent",
        "--show-error",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--connect-timeout",
        "5",
        "--max-time",
        "15",
        "--proto",
        "=https",
        "--tlsv1.2",
        "--noproxy",
        "*",
        "--resolve",
        f"{vhost}:443:{address}",
    ]
    if probe == "get":
        base.extend(("--fail", f"https://{vhost}/"))
    elif probe == "post":
        base.extend(
            (
                "--request",
                "POST",
                "--header",
                "Content-Length: 0",
                f"https://{vhost}{PROBE_PATH}",
            )
        )
    elif probe == "websocket":
        base.extend(
            (
                "--http1.1",
                "--header",
                "Connection: Upgrade",
                "--header",
                "Upgrade: websocket",
                f"https://{vhost}{PROBE_PATH}",
            )
        )
    else:
        raise NginxCoordinatorError("external probe kind is invalid")
    return tuple(base)


def _external_readback(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    role_states: Mapping[str, str],
    runner: RunFn,
    action: str,
    target_state: str | None,
    freshness: Mapping[str, int | str],
) -> dict[str, Any]:
    if (
        not isinstance(role_states, Mapping)
        or set(role_states) != set(ROLE_ORDER)
        or any(
            state not in GENERATION.GENERATION_STATES
            for state in role_states.values()
        )
    ):
        raise NginxCoordinatorError(
            "external readback state closure is invalid"
        )
    states = frozenset(role_states.values())
    state_by_host = {
        inputs.roles[role].expected_host: role_states[role]
        for role in ROLE_ORDER
    }
    results: dict[str, Any] = {}
    write_probe_performed = False
    for vhost, address in VHOST_TARGETS:
        host_state = state_by_host[address]
        blocked = host_state in BLOCKED_STATES
        probes = (
            ("get", "post", "websocket")
            if blocked
            else ("get",)
        )
        write_probe_performed = write_probe_performed or blocked
        results[vhost] = {}
        for probe in probes:
            command = _curl_arguments(
                vhost=vhost,
                address=address,
                probe=probe,
            )
            result = _run_audited(
                inputs,
                journal,
                command,
                runner=runner,
                kind=f"curl-{probe}",
                scope=vhost,
                action=action,
                target_state=target_state,
                timeout=20,
            )
            try:
                status = int(result.stdout.decode("ascii"))
            except (UnicodeError, ValueError) as exc:
                raise NginxCoordinatorError(
                    "external readback status is invalid"
                ) from exc
            if probe == "get":
                if not 200 <= status <= 399:
                    raise NginxCoordinatorError(
                        f"{vhost} GET readback did not succeed"
                    )
            elif status != 503:
                raise NginxCoordinatorError(
                    f"{vhost} {probe} was not rejected before upstream"
                )
            results[vhost][probe] = status
    captured_at_epoch = int(time.time())
    if (
        set(freshness)
        != {
            "readback_challenge_sha256",
            "issued_at_epoch",
            "expires_at_epoch",
            "captured_at_epoch",
        }
        or type(freshness["expires_at_epoch"]) is not int
        or type(freshness["captured_at_epoch"]) is not int
        or captured_at_epoch
        < freshness["captured_at_epoch"]
        - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
        or captured_at_epoch > freshness["expires_at_epoch"]
    ):
        raise NginxCoordinatorError(
            "external readback exceeded the fresh challenge window"
        )
    return {
        "states": sorted(states),
        "states_by_role": dict(role_states),
        "blocked_probes_performed": write_probe_performed,
        "write_method_probe_performed": write_probe_performed,
        "vhosts": results,
        "readback_challenge_sha256": freshness[
            "readback_challenge_sha256"
        ],
        "issued_at_epoch": freshness["issued_at_epoch"],
        "expires_at_epoch": freshness["expires_at_epoch"],
        "captured_at_epoch": captured_at_epoch,
    }


def _verified_readback(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
    action: str,
    target_state: str | None,
    allow_partial_states: frozenset[str] | None = None,
) -> tuple[
    dict[str, dict[str, Any]],
    str | None,
    str | None,
    dict[str, Any],
]:
    readbacks = _readback_hosts(inputs, journal, runner=runner)
    freshness = _readback_freshness(
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        release_tree_sha=inputs.release_tree_sha,
        aggregate_sha256=inputs.aggregate_sha256,
        readbacks=readbacks,
    )
    state, global_digest = _global_digest_for_readbacks(inputs, readbacks)
    role_states = {
        role: readbacks[role]["state"] for role in ROLE_ORDER
    }
    states = frozenset(role_states.values())
    if state is None and (
        allow_partial_states is None
        or not states <= allow_partial_states
        or len(states) < 2
    ):
        raise NginxCoordinatorError(
            "cross-host Nginx state drift is not an expected resumable state"
        )
    external = _external_readback(
        inputs,
        journal,
        role_states=role_states,
        runner=runner,
        action=action,
        target_state=target_state,
        freshness=freshness,
    )
    return readbacks, state, global_digest, external


def _set_pending(
    journal: dict[str, Any],
    *,
    action: str,
    target_state: str | None,
    from_state: str | None,
    policy: str,
    completed_roles: Sequence[str],
    source_receipt_sha256: str | None = None,
    lease_claim_sha256: str | None = None,
) -> None:
    previous = journal.get("pending")
    attempt = (
        previous["attempt"] + 1
        if isinstance(previous, dict)
        and previous.get("action") == action
        and previous.get("target_state") == target_state
        else 1
    )
    journal["pending"] = {
        "action": action,
        "target_state": target_state,
        "from_state": from_state,
        "policy": policy,
        "source_receipt_sha256": source_receipt_sha256,
        "lease_claim_sha256": lease_claim_sha256,
        "status": "running",
        "completed_roles": [
            role for role in ROLE_ORDER if role in completed_roles
        ],
        "attempt": attempt,
    }


def _test_both(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    state: str,
    runner: RunFn,
) -> dict[str, Any]:
    results = {
        role: _call_host_worker(
            inputs,
            journal,
            role=role,
            action="test",
            generation=state,
            runner=runner,
        )
        for role in ROLE_ORDER
    }
    journal["tested_states"][state] = list(ROLE_ORDER)
    _append_event(
        journal,
        "tested-both",
        data={
            "state": state,
            "journal_sha256": {
                role: results[role]["journal_sha256"]
                for role in ROLE_ORDER
            },
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return results


def _install_action(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
) -> dict[str, Any]:
    _set_pending(
        journal,
        action="install",
        target_state=None,
        from_state=None,
        policy="complete-both",
        completed_roles=journal["installed_roles"],
    )
    _write_journal(inputs.journal_path, journal, create=False)
    remote = _install_remote_material(inputs, journal, runner=runner)
    results: dict[str, Any] = {}
    for role in ROLE_ORDER:
        if role in journal["installed_roles"]:
            continue
        result = _call_host_worker(
            inputs,
            journal,
            role=role,
            action="install",
            generation=None,
            runner=runner,
        )
        results[role] = result
        journal["installed_roles"].append(role)
        journal["pending"]["completed_roles"] = list(
            journal["installed_roles"]
        )
        _write_journal(inputs.journal_path, journal, create=False)
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="install",
        target_state=None,
    )
    if state != "legacy-normal":
        raise NginxCoordinatorError(
            "installation did not preserve legacy-normal"
        )
    journal["stable_state"] = state
    journal["pending"] = None
    _append_event(
        journal,
        "installed-both",
        data={
            "state": state,
            "global_generation_sha256": global_digest,
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": (
            "already-installed"
            if not results and all(value == "reused" for value in remote.values())
            else "installed"
        ),
        "remote_material": remote,
        "host_results": results,
        "readbacks": readbacks,
        "state": state,
        "global_generation_sha256": global_digest,
        "external_readback": external,
    }


def _compensate_frozen_failure(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    runner: RunFn,
) -> dict[str, Any]:
    compensation: dict[str, Any] = {}
    for role in ROLE_ORDER:
        try:
            readback = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="readback",
                generation=None,
                runner=runner,
            )
        except NginxCoordinatorError:
            continue
        if readback["state"] == "legacy-frozen":
            compensation[role] = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="restore",
                generation=None,
                runner=runner,
            )
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="activate",
        target_state="legacy-frozen",
    )
    if state != "legacy-normal":
        raise NginxCoordinatorError(
            "legacy-frozen compensation did not restore both hosts"
        )
    journal["stable_state"] = "legacy-normal"
    journal["pending"]["status"] = "compensated-failed"
    journal["pending"]["completed_roles"] = []
    _append_event(
        journal,
        "legacy-frozen-compensated",
        data={
            "state": state,
            "global_generation_sha256": global_digest,
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": "compensated-failed",
        "compensation": compensation,
        "readbacks": readbacks,
        "state": state,
        "global_generation_sha256": global_digest,
        "external_readback": external,
        "retry": "restart-legacy-frozen-transition",
    }


def _partial_result(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    target_state: str,
    policy: str,
    runner: RunFn,
) -> dict[str, Any]:
    allowed = (
        frozenset({"legacy-frozen", "shadow-readonly"})
        if target_state == "shadow-readonly"
        else frozenset({"shadow-readonly", "shadow-writable"})
    )
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="activate",
        target_state=target_state,
        allow_partial_states=allowed,
    )
    completed = [
        role
        for role in ROLE_ORDER
        if readbacks[role]["state"] == target_state
    ]
    journal["pending"]["completed_roles"] = completed
    journal["pending"]["status"] = (
        "partial-resumable"
        if policy == "keep-write-blocked"
        else "forward-only-retry"
    )
    _append_event(
        journal,
        "activation-partial",
        data={
            "target_state": target_state,
            "policy": policy,
            "states": {
                role: readbacks[role]["state"] for role in ROLE_ORDER
            },
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": journal["pending"]["status"],
        "policy": policy,
        "readbacks": readbacks,
        "state": state,
        "global_generation_sha256": global_digest,
        "external_readback": external,
        "retry_target": target_state,
        "restore_performed": False,
    }


def _activate_action(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    target_state: str,
    runner: RunFn,
) -> dict[str, Any]:
    previous_by_target = {value: key for key, value in NEXT_STATE.items()}
    if target_state not in previous_by_target:
        raise NginxCoordinatorError(
            "activation target is not an exact forward generation"
        )
    previous = previous_by_target[target_state]
    pending = journal.get("pending")
    resuming = (
        isinstance(pending, dict)
        and pending.get("action") == "activate"
        and pending.get("target_state") == target_state
        and pending.get("status")
        in {"partial-resumable", "forward-only-retry", "running"}
    )
    stable_state = journal.get("stable_state")
    if not resuming:
        if (
            pending is not None
            and not (
                isinstance(pending, dict)
                and pending.get("status") == "compensated-failed"
            )
        ):
            raise NginxCoordinatorError(
                "another Nginx transition must be resumed or restored first"
            )
        if stable_state not in {previous, target_state}:
            raise NginxCoordinatorError(
                "activation target is not adjacent to the durable state"
            )
    elif stable_state != previous:
        raise NginxCoordinatorError(
            "resumable activation predecessor differs from durable state"
        )
    allowed_partial = (
        frozenset({previous, target_state})
        if resuming
        else None
    )
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="activate",
        target_state=target_state,
        allow_partial_states=allowed_partial,
    )
    if state == target_state:
        if stable_state != target_state and not resuming:
            raise NginxCoordinatorError(
                "unrecorded two-host activation drifted to the target"
            )
        journal["stable_state"] = target_state
        journal["pending"] = None
        _write_journal(inputs.journal_path, journal, create=False)
        return {
            "status": "already-active",
            "state": target_state,
            "readbacks": readbacks,
            "global_generation_sha256": global_digest,
            "external_readback": external,
        }
    observed_states = {
        readbacks[role]["state"] for role in ROLE_ORDER
    }
    if state is not None and state != previous:
        raise NginxCoordinatorError(
            "activation starting state is not the exact legal predecessor"
        )
    if state == previous and stable_state != previous:
        raise NginxCoordinatorError(
            "activation predecessor differs from durable state"
        )
    if state is None and observed_states != {previous, target_state}:
        raise NginxCoordinatorError(
            "activation mixed state is not the recorded resumable transition"
        )
    completed = [
        role
        for role in ROLE_ORDER
        if readbacks[role]["state"] == target_state
    ]
    policy = {
        "legacy-frozen": "compensate-legacy-normal",
        "shadow-readonly": "keep-write-blocked",
        "shadow-writable": "forward-only-same-target",
    }[target_state]
    _test_both(inputs, journal, state=target_state, runner=runner)
    if target_state == "legacy-frozen":
        _test_both(
            inputs,
            journal,
            state="legacy-normal",
            runner=runner,
        )
    _set_pending(
        journal,
        action="activate",
        target_state=target_state,
        from_state=previous,
        policy=policy,
        completed_roles=completed,
    )
    _write_journal(inputs.journal_path, journal, create=False)
    host_results: dict[str, Any] = {}
    for role in ROLE_ORDER:
        if role in completed:
            continue
        try:
            host_results[role] = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="activate",
                generation=target_state,
                runner=runner,
            )
            readback = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="readback",
                generation=None,
                runner=runner,
            )
            if readback["state"] != target_state:
                raise NginxCoordinatorError(
                    f"{role} activation readback differs"
                )
            completed.append(role)
            journal["pending"]["completed_roles"] = [
                item for item in ROLE_ORDER if item in completed
            ]
            _write_journal(inputs.journal_path, journal, create=False)
        except BaseException as exc:
            if target_state == "legacy-frozen":
                reconciled = _compensate_frozen_failure(
                    inputs,
                    journal,
                    runner=runner,
                )
            else:
                reconciled = _partial_result(
                    inputs,
                    journal,
                    target_state=target_state,
                    policy=policy,
                    runner=runner,
                )
            if isinstance(exc, NginxCoordinatorError):
                return reconciled
            raise
    final_readbacks, final_state, final_digest, final_external = (
        _verified_readback(
            inputs,
            journal,
            runner=runner,
            action="activate",
            target_state=target_state,
        )
    )
    if final_state != target_state:
        raise NginxCoordinatorError(
            "activation did not converge both hosts"
        )
    journal["stable_state"] = target_state
    journal["pending"] = None
    _append_event(
        journal,
        "activated-both",
        data={
            "from_state": previous,
            "to_state": target_state,
            "global_generation_sha256": final_digest,
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": "activated",
        "state": target_state,
        "host_results": host_results,
        "readbacks": final_readbacks,
        "global_generation_sha256": final_digest,
        "external_readback": final_external,
    }


def _rollback_to_frozen_action(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    source_receipt_sha256: str,
    runner: RunFn,
) -> dict[str, Any]:
    source_receipt_sha256 = _nonzero_sha256(
        source_receipt_sha256,
        label="shadow-readonly source receipt SHA-256",
    )
    pending = journal.get("pending")
    stable_state = journal.get("stable_state")
    if stable_state == "shadow-writable" or (
        isinstance(pending, dict)
        and (
            pending.get("target_state") == "shadow-writable"
            or pending.get("policy") == "forward-only-same-target"
        )
    ):
        raise NginxCoordinatorError(
            "rollback-freeze is forbidden after shadow-writable"
        )
    resuming = (
        isinstance(pending, dict)
        and pending.get("action") == "rollback-freeze"
        and pending.get("target_state") == "legacy-frozen"
        and pending.get("from_state") == "shadow-readonly"
        and pending.get("policy")
        == "rollback-to-frozen-write-blocked"
        and pending.get("source_receipt_sha256")
        == source_receipt_sha256
        and pending.get("status") in {"running", "partial-resumable"}
    )
    if not resuming:
        if pending is not None:
            raise NginxCoordinatorError(
                "another Nginx transition must be resolved first"
            )
        if stable_state != "shadow-readonly":
            raise NginxCoordinatorError(
                "rollback-freeze requires durable shadow-readonly"
            )
    elif stable_state != "shadow-readonly":
        raise NginxCoordinatorError(
            "rollback-freeze durable predecessor differs"
        )
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="rollback-freeze",
        target_state="legacy-frozen",
        allow_partial_states=(
            frozenset({"legacy-frozen", "shadow-readonly"})
            if resuming
            else None
        ),
    )
    states = {readbacks[role]["state"] for role in ROLE_ORDER}
    if state == "legacy-frozen":
        if not resuming:
            raise NginxCoordinatorError(
                "unjournaled rollback-freeze drifted to legacy-frozen"
            )
        journal["stable_state"] = "legacy-frozen"
        journal["pending"] = None
        _append_event(
            journal,
            "rollback-frozen-both",
            data={
                "from_state": "shadow-readonly",
                "to_state": "legacy-frozen",
                "source_receipt_sha256": source_receipt_sha256,
                "global_generation_sha256": global_digest,
            },
        )
        _write_journal(inputs.journal_path, journal, create=False)
        return {
            "status": "rollback-frozen",
            "state": state,
            "readbacks": readbacks,
            "global_generation_sha256": global_digest,
            "external_readback": external,
            "active_configuration_mutated": True,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
        }
    if state == "shadow-readonly":
        if stable_state != "shadow-readonly":
            raise NginxCoordinatorError(
                "rollback-freeze predecessor differs from durable state"
            )
    elif states != {"legacy-frozen", "shadow-readonly"} or not resuming:
        raise NginxCoordinatorError(
            "rollback-freeze mixed state is not resumable"
        )
    completed = [
        role
        for role in ROLE_ORDER
        if readbacks[role]["state"] == "legacy-frozen"
    ]
    _set_pending(
        journal,
        action="rollback-freeze",
        target_state="legacy-frozen",
        from_state="shadow-readonly",
        policy="rollback-to-frozen-write-blocked",
        completed_roles=completed,
        source_receipt_sha256=source_receipt_sha256,
    )
    _write_journal(inputs.journal_path, journal, create=False)
    _test_both(
        inputs,
        journal,
        state="legacy-frozen",
        runner=runner,
    )
    host_results: dict[str, Any] = {}
    for role in ROLE_ORDER:
        if role in completed:
            continue
        try:
            host_results[role] = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="rollback-freeze",
                generation="legacy-frozen",
                runner=runner,
            )
            readback = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="readback",
                generation=None,
                runner=runner,
            )
            if readback["state"] != "legacy-frozen":
                raise NginxCoordinatorError(
                    f"{role} rollback-freeze readback differs"
                )
            completed.append(role)
            journal["pending"]["completed_roles"] = [
                item for item in ROLE_ORDER if item in completed
            ]
            _write_journal(inputs.journal_path, journal, create=False)
        except NginxCoordinatorError:
            partial_readbacks, partial_state, partial_digest, partial_external = (
                _verified_readback(
                    inputs,
                    journal,
                    runner=runner,
                    action="rollback-freeze",
                    target_state="legacy-frozen",
                    allow_partial_states=frozenset(
                        {"legacy-frozen", "shadow-readonly"}
                    ),
                )
            )
            partial_states = {
                partial_readbacks[item]["state"] for item in ROLE_ORDER
            }
            if not partial_states <= BLOCKED_STATES:
                raise NginxCoordinatorError(
                    "rollback-freeze partial state is not write-blocked"
                )
            completed = [
                item
                for item in ROLE_ORDER
                if partial_readbacks[item]["state"] == "legacy-frozen"
            ]
            journal["pending"]["completed_roles"] = completed
            journal["pending"]["status"] = "partial-resumable"
            _append_event(
                journal,
                "rollback-freeze-partial",
                data={
                    "states": {
                        item: partial_readbacks[item]["state"]
                        for item in ROLE_ORDER
                    },
                    "policy": "rollback-to-frozen-write-blocked",
                },
            )
            _write_journal(inputs.journal_path, journal, create=False)
            return {
                "status": "rollback-freeze-partial-resumable",
                "policy": "rollback-to-frozen-write-blocked",
                "state": partial_state,
                "readbacks": partial_readbacks,
                "global_generation_sha256": partial_digest,
                "external_readback": partial_external,
                "retry_target": "legacy-frozen",
                "active_configuration_mutated": True,
                "current_mutated": False,
                "container_mutated": False,
                "volume_mutated": False,
                "data_mutated": False,
            }
    final_readbacks, final_state, final_digest, final_external = (
        _verified_readback(
            inputs,
            journal,
            runner=runner,
            action="rollback-freeze",
            target_state="legacy-frozen",
        )
    )
    if final_state != "legacy-frozen":
        raise NginxCoordinatorError(
            "rollback-freeze did not converge both hosts"
        )
    journal["stable_state"] = "legacy-frozen"
    journal["pending"] = None
    _append_event(
        journal,
        "rollback-frozen-both",
        data={
            "from_state": "shadow-readonly",
            "to_state": "legacy-frozen",
            "source_receipt_sha256": source_receipt_sha256,
            "global_generation_sha256": final_digest,
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": "rollback-frozen",
        "state": "legacy-frozen",
        "host_results": host_results,
        "readbacks": final_readbacks,
        "global_generation_sha256": final_digest,
        "external_readback": final_external,
        "active_configuration_mutated": True,
        "current_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def _restore_action(
    inputs: CoordinatorInputs,
    journal: dict[str, Any],
    *,
    lease_claim_sha256: str,
    runner: RunFn,
) -> dict[str, Any]:
    lease_claim_sha256 = _nonzero_sha256(
        lease_claim_sha256,
        label="live lease claim SHA-256",
    )
    pending = journal.get("pending")
    stable_state = journal.get("stable_state")
    if stable_state == "shadow-writable" or (
        isinstance(pending, dict)
        and (
            pending.get("target_state") == "shadow-writable"
            or pending.get("policy") == "forward-only-same-target"
        )
    ):
        raise NginxCoordinatorError(
            "legacy restore is forbidden after shadow-writable"
        )
    resuming_restore = (
        isinstance(pending, dict)
        and pending.get("action") == "restore"
        and pending.get("target_state") == "legacy-normal"
        and pending.get("from_state") == "legacy-frozen"
        and pending.get("lease_claim_sha256") == lease_claim_sha256
        and pending.get("status") in {"running", "partial-resumable"}
    )
    allow_partial: frozenset[str] | None = None
    if isinstance(pending, dict):
        if resuming_restore:
            allow_partial = frozenset(
                {"legacy-normal", "legacy-frozen"}
            )
        else:
            raise NginxCoordinatorError(
                "legacy restore requires its exact live lease transition"
            )
    readbacks, state, global_digest, external = _verified_readback(
        inputs,
        journal,
        runner=runner,
        action="restore",
        target_state="legacy-normal",
        allow_partial_states=allow_partial,
    )
    states = {readbacks[role]["state"] for role in ROLE_ORDER}
    if "shadow-writable" in states:
        raise NginxCoordinatorError(
            "legacy restore is forbidden after shadow-writable"
        )
    if state == "legacy-normal":
        allowed_normal = (
            stable_state == "legacy-normal"
            or resuming_restore
            or (
                isinstance(pending, dict)
                and pending.get("status") == "compensated-failed"
            )
        )
        if not allowed_normal:
            raise NginxCoordinatorError(
                "unrecorded two-host restore drifted to legacy-normal"
            )
        journal["stable_state"] = state
        journal["pending"] = None
        _write_journal(inputs.journal_path, journal, create=False)
        return {
            "status": "already-restored",
            "state": state,
            "readbacks": readbacks,
            "global_generation_sha256": global_digest,
            "external_readback": external,
        }
    if not states <= {"legacy-normal", "legacy-frozen"}:
        raise NginxCoordinatorError(
            "legacy restore starting state is invalid"
        )
    if (
        state is not None
        and not resuming_restore
        and not (
            state == stable_state == "legacy-frozen"
        )
    ):
        raise NginxCoordinatorError(
            "restore starting state differs from durable state"
        )
    completed = [
        role
        for role in ROLE_ORDER
        if readbacks[role]["state"] == "legacy-normal"
    ]
    _set_pending(
        journal,
        action="restore",
        target_state="legacy-normal",
        from_state="legacy-frozen",
        policy="complete-both",
        completed_roles=completed,
        lease_claim_sha256=lease_claim_sha256,
    )
    _write_journal(inputs.journal_path, journal, create=False)
    _test_both(
        inputs,
        journal,
        state="legacy-normal",
        runner=runner,
    )
    results: dict[str, Any] = {}
    for role in ROLE_ORDER:
        if role in completed:
            continue
        try:
            results[role] = _call_host_worker(
                inputs,
                journal,
                role=role,
                action="restore",
                generation=None,
                runner=runner,
            )
        except NginxCoordinatorError:
            partial_readbacks, partial_state, partial_digest, partial_external = (
                _verified_readback(
                    inputs,
                    journal,
                    runner=runner,
                    action="restore",
                    target_state="legacy-normal",
                    allow_partial_states=frozenset(
                        {"legacy-normal", "legacy-frozen"}
                    ),
                )
            )
            partial_states = {
                partial_readbacks[item]["state"] for item in ROLE_ORDER
            }
            if "shadow-writable" in partial_states:
                raise NginxCoordinatorError(
                    "legacy restore encountered shadow-writable"
                )
            completed = [
                item
                for item in ROLE_ORDER
                if partial_readbacks[item]["state"] == "legacy-normal"
            ]
            journal["pending"]["completed_roles"] = completed
            journal["pending"]["status"] = "partial-resumable"
            _append_event(
                journal,
                "restore-partial",
                data={
                    "states": {
                        item: partial_readbacks[item]["state"]
                        for item in ROLE_ORDER
                    },
                    "policy": "complete-both",
                },
            )
            _write_journal(inputs.journal_path, journal, create=False)
            return {
                "status": "restore-partial-resumable",
                "policy": "complete-both",
                "state": partial_state,
                "readbacks": partial_readbacks,
                "global_generation_sha256": partial_digest,
                "external_readback": partial_external,
                "retry_target": "legacy-normal",
            }
        completed.append(role)
        journal["pending"]["completed_roles"] = [
            item for item in ROLE_ORDER if item in completed
        ]
        _write_journal(inputs.journal_path, journal, create=False)
    final_readbacks, final_state, final_digest, final_external = (
        _verified_readback(
            inputs,
            journal,
            runner=runner,
            action="restore",
            target_state="legacy-normal",
        )
    )
    if final_state != "legacy-normal":
        raise NginxCoordinatorError(
            "legacy restore did not converge both hosts"
        )
    journal["stable_state"] = final_state
    journal["pending"] = None
    _append_event(
        journal,
        "restored-both",
        data={
            "to_state": final_state,
            "global_generation_sha256": final_digest,
        },
    )
    _write_journal(inputs.journal_path, journal, create=False)
    return {
        "status": "restored",
        "state": final_state,
        "host_results": results,
        "readbacks": final_readbacks,
        "global_generation_sha256": final_digest,
        "external_readback": final_external,
    }


def _persist_state_receipt(
    inputs: CoordinatorInputs,
    journal: Mapping[str, Any],
    *,
    action: str,
    target_state: str | None,
    details: Mapping[str, Any],
) -> tuple[Path, str] | None:
    state = details.get("state")
    readbacks = details.get("readbacks")
    global_digest = details.get("global_generation_sha256")
    external = details.get("external_readback")
    if state not in GENERATION.GENERATION_STATES:
        return None
    if (
        journal.get("stable_state") != state
        or journal.get("pending") is not None
    ):
        return None
    if (
        not isinstance(readbacks, Mapping)
        or set(readbacks) != set(ROLE_ORDER)
        or any(readbacks[role].get("state") != state for role in ROLE_ORDER)
        or global_digest != inputs.aggregate["generation_sha256"][state]
        or not isinstance(external, Mapping)
    ):
        raise NginxCoordinatorError(
            "verified state cannot produce an exact coordinator receipt"
        )
    freshness_fields = {
        "readback_challenge_sha256",
        "issued_at_epoch",
        "expires_at_epoch",
        "captured_at_epoch",
    }
    if (
        not freshness_fields <= set(external)
        or external["captured_at_epoch"]
        < max(readbacks[role]["captured_at_epoch"] for role in ROLE_ORDER)
        - GENERATION.READBACK_MAX_CLOCK_SKEW_SECONDS
    ):
        raise NginxCoordinatorError(
            "verified state lacks fresh readback completion"
        )
    vhost_rows: dict[str, dict[str, str]] = {}
    for role in ROLE_ORDER:
        for row in inputs.roles[role].manifest["vhosts"]:
            vhost = row["vhost"]
            expected = VHOST_RECEIPT_LAYOUT.get(vhost)
            if (
                expected is None
                or expected["role"] != role
                or expected["destination"] != row["destination"]
            ):
                raise NginxCoordinatorError(
                    "state receipt vhost layout differs"
                )
            vhost_rows[vhost] = {
                "role": role,
                "destination": row["destination"],
                "generation_sha256": row["generation_sha256"][state],
            }
    if set(vhost_rows) != set(VHOST_RECEIPT_LAYOUT):
        raise NginxCoordinatorError(
            "state receipt vhost closure differs"
        )
    receipt = {
        "schema": PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA,
        "verification_status": "verified",
        "source_action": action,
        "requested_target_state": target_state,
        "coordinator_status": details["status"],
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "role_bindings": _role_bindings(inputs),
        "state": state,
        "vhost_generation_sha256": vhost_rows,
        "global_generation_sha256": global_digest,
        "readbacks": dict(readbacks),
        "external_readback": dict(external),
        "journal_sha256": journal["state_sha256"],
        "evidence_count": journal["evidence_count"],
        "evidence_tail_sha256": journal["evidence_tail_sha256"],
        "production_contacted": True,
        "active_configuration_mutated": False,
        "current_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
        "readback_challenge_sha256": external[
            "readback_challenge_sha256"
        ],
        "issued_at_epoch": external["issued_at_epoch"],
        "expires_at_epoch": external["expires_at_epoch"],
        "captured_at_epoch": external["captured_at_epoch"],
    }
    payload = canonical_json_bytes(receipt)
    digest = _sha256(payload)
    path = inputs.receipts_root / f"{state}-{digest}.json"
    if path.exists() or path.is_symlink():
        observed = _read_private_file(
            path,
            label="existing Nginx coordinator state receipt",
            maximum=GENERATION.MAX_JSON_BYTES,
        )
        if observed != payload:
            raise NginxCoordinatorError(
                "existing Nginx coordinator state receipt differs"
            )
    else:
        try:
            write_secure_new_bytes(
                path,
                payload,
                label="Nginx coordinator state receipt",
                mode=FILE_MODE,
                max_size=GENERATION.MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise NginxCoordinatorError(
                "Nginx coordinator state receipt could not be persisted"
            ) from exc
    loaded, observed_digest = load_state_receipt(
        path,
        state,
        inputs.operation_id,
        inputs.release_sha,
        inputs.release_tree_sha,
        inputs.aggregate_sha256,
    )
    if loaded != receipt or observed_digest != digest:
        raise NginxCoordinatorError(
            "persisted Nginx coordinator state receipt differs"
        )
    return path, digest


def _live_lease_paths(
    inputs: CoordinatorInputs,
) -> tuple[Path, Path, Path, Path]:
    root = inputs.coordinator_root / "live-leases"
    return (
        root,
        root / "claims",
        root / "readiness",
        root / "consumptions",
    )


def _prepare_live_lease_ledger(
    inputs: CoordinatorInputs,
    *,
    create: bool,
) -> bool:
    root, claims, readiness, consumptions = _live_lease_paths(inputs)
    try:
        root.stat(follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return False
    _ensure_private_directory(root, create=create)
    _ensure_private_directory(claims, create=create)
    _ensure_private_directory(readiness, create=create)
    _ensure_private_directory(consumptions, create=create)
    return True


def _canonical_receipt_path(
    *,
    operation_id: str,
    state: str,
    digest: str,
) -> Path:
    return (
        CONTROLLER_SECRET_PREFIX
        / operation_id
        / "nginx-coordinator"
        / "receipts"
        / f"{state}-{digest}.json"
    )


def load_live_lease_claim_material(
    claim_path: Path,
    *,
    state_receipt_path: Path,
    expected_claim_sha256: str,
    expected_state_receipt_sha256: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    aggregate_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Validate copied lease material without claiming controller authority.

    This validates an immutable claim and its copied state receipt. It cannot
    prove that the controller flock remains held or that the controller has
    not subsequently consumed the claim.
    """
    operation_id = _canonical_uuid4(operation_id)
    release_sha = _release_sha(release_sha, label="lease release SHA")
    release_tree_sha = _release_sha(
        release_tree_sha,
        label="lease release tree SHA",
    )
    aggregate_sha256 = _nonzero_sha256(
        aggregate_sha256,
        label="lease aggregate SHA-256",
    )
    expected_claim_sha256 = _nonzero_sha256(
        expected_claim_sha256,
        label="live lease claim SHA-256",
    )
    expected_state_receipt_sha256 = _nonzero_sha256(
        expected_state_receipt_sha256,
        label="legacy-frozen receipt SHA-256",
    )
    receipt, receipt_sha256 = load_state_receipt(
        state_receipt_path,
        "legacy-frozen",
        operation_id,
        release_sha,
        release_tree_sha,
        aggregate_sha256,
        allow_historical=True,
    )
    if receipt_sha256 != expected_state_receipt_sha256:
        raise NginxCoordinatorError(
            "legacy-frozen receipt digest differs"
        )
    document, payload = _load_canonical_json(
        claim_path,
        label="production Nginx live lease claim",
    )
    observed_sha256 = _sha256(payload)
    fields = {
        "schema",
        "status",
        "owner_action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "claim_epoch",
        "previous_claim_sha256",
        "nonce",
        "controller_pid",
        "controller_lock_path",
        "controller_authoritative",
        "remote_copy_authoritative",
        "automatic_expiry_allowed",
        "reconciliation_required_after_crash",
        "legacy_frozen_receipt_path",
        "legacy_frozen_receipt_sha256",
        "receipt_journal_sha256",
        "receipt_journal_sequence",
        "receipt_journal_tail_sha256",
        "controller_journal_event_count",
        "receipt_state",
        "receipt_global_generation_sha256",
        "receipt_role_generation_sha256",
        "receipt_role_bindings",
        "receipt_readbacks",
    }
    identity = {
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "aggregate_sha256": aggregate_sha256,
    }
    canonical_receipt_path = _canonical_receipt_path(
        operation_id=operation_id,
        state="legacy-frozen",
        digest=expected_state_receipt_sha256,
    )
    coordinator_root = canonical_receipt_path.parent.parent
    role_generation = {
        role: receipt["readbacks"][role]["generation_sha256"]
        for role in ROLE_ORDER
    }
    if (
        observed_sha256 != expected_claim_sha256
        or claim_path.name != f"{observed_sha256}.json"
        or set(document) != fields
        or document["schema"] != LIVE_LEASE_CLAIM_SCHEMA
        or document["status"] != "active"
        or document["owner_action"] not in LIVE_LEASE_OWNER_OUTCOMES
        or any(document.get(key) != value for key, value in identity.items())
        or type(document["claim_epoch"]) is not int
        or document["claim_epoch"] < 1
        or (
            document["claim_epoch"] == 1
            and document["previous_claim_sha256"] != "0" * 64
        )
        or (
            document["claim_epoch"] > 1
            and (
                not isinstance(document["previous_claim_sha256"], str)
                or SHA256_RE.fullmatch(
                    document["previous_claim_sha256"]
                )
                is None
                or document["previous_claim_sha256"] == "0" * 64
            )
        )
        or not isinstance(document["nonce"], str)
        or LIVE_LEASE_NONCE_RE.fullmatch(document["nonce"]) is None
        or document["nonce"] == "0" * 64
        or type(document["controller_pid"]) is not int
        or document["controller_pid"] < 1
        or document["controller_lock_path"]
        != os.fspath(coordinator_root / "coordinator.lock")
        or document["controller_authoritative"] is not True
        or document["remote_copy_authoritative"] is not False
        or document["automatic_expiry_allowed"] is not False
        or document["reconciliation_required_after_crash"] is not True
        or document["legacy_frozen_receipt_path"]
        != os.fspath(canonical_receipt_path)
        or document["legacy_frozen_receipt_sha256"]
        != expected_state_receipt_sha256
        or document["receipt_journal_sha256"]
        != receipt["journal_sha256"]
        or document["receipt_journal_sequence"]
        != receipt["evidence_count"]
        or document["receipt_journal_tail_sha256"]
        != receipt["evidence_tail_sha256"]
        or type(document["controller_journal_event_count"]) is not int
        or document["controller_journal_event_count"]
        < receipt["evidence_count"]
        or document["receipt_state"] != "legacy-frozen"
        or document["receipt_global_generation_sha256"]
        != receipt["global_generation_sha256"]
        or document["receipt_role_generation_sha256"]
        != role_generation
        or document["receipt_role_bindings"] != receipt["role_bindings"]
        or document["receipt_readbacks"] != receipt["readbacks"]
    ):
        raise NginxCoordinatorError(
            "production Nginx live lease claim differs"
        )
    return document, observed_sha256


def load_transferred_fresh_state_receipt(
    state_receipt_path: Path,
    expected_state: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    aggregate_sha256: str,
    *,
    live_lease_claim_path: Path,
    expected_state_receipt_sha256: str,
    expected_live_lease_claim_sha256: str,
    expected_owner_action: str,
    observed_at_epoch: int | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any], str]:
    """Validate a fresh transferred receipt plus its immutable lease claim.

    The caller must separately prove interactive controller-lock liveness.
    Unlike ``load_state_receipt``, this does not require a copied controller
    journal, and it never accepts an unchallenged or expired receipt.
    """
    if expected_owner_action not in LIVE_LEASE_OWNER_OUTCOMES:
        raise NginxCoordinatorError(
            "transferred fresh receipt owner action is invalid"
        )
    receipt, receipt_sha256 = load_state_receipt(
        state_receipt_path,
        expected_state,
        operation_id,
        release_sha,
        release_tree_sha,
        aggregate_sha256,
        observed_at_epoch=observed_at_epoch,
        _require_current_journal=False,
    )
    if receipt_sha256 != _nonzero_sha256(
        expected_state_receipt_sha256,
        label="transferred fresh receipt SHA-256",
    ):
        raise NginxCoordinatorError(
            "transferred fresh receipt digest differs"
        )
    claim, claim_sha256 = load_live_lease_claim_material(
        live_lease_claim_path,
        state_receipt_path=state_receipt_path,
        expected_claim_sha256=expected_live_lease_claim_sha256,
        expected_state_receipt_sha256=receipt_sha256,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        aggregate_sha256=aggregate_sha256,
    )
    if (
        expected_state != "legacy-frozen"
        or claim["owner_action"] != expected_owner_action
    ):
        raise NginxCoordinatorError(
            "transferred fresh receipt lease binding differs"
        )
    return receipt, receipt_sha256, claim, claim_sha256


def _load_claim_from_controller(
    inputs: CoordinatorInputs,
    claim_path: Path,
    expected_claim_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_claim_sha256 = _nonzero_sha256(
        expected_claim_sha256,
        label="live lease claim SHA-256",
    )
    expected_path = (
        _live_lease_paths(inputs)[1]
        / f"{expected_claim_sha256}.json"
    )
    if claim_path != expected_path:
        raise NginxCoordinatorError(
            "live lease claim path is not canonical"
        )
    preliminary, _ = _load_canonical_json(
        claim_path,
        label="production Nginx live lease claim",
    )
    receipt_path_value = preliminary.get("legacy_frozen_receipt_path")
    receipt_sha256 = preliminary.get("legacy_frozen_receipt_sha256")
    if (
        not isinstance(receipt_path_value, str)
        or not isinstance(receipt_sha256, str)
    ):
        raise NginxCoordinatorError(
            "live lease receipt binding is invalid"
        )
    receipt_path = Path(receipt_path_value)
    claim, observed = load_live_lease_claim_material(
        claim_path,
        state_receipt_path=receipt_path,
        expected_claim_sha256=expected_claim_sha256,
        expected_state_receipt_sha256=receipt_sha256,
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        release_tree_sha=inputs.release_tree_sha,
        aggregate_sha256=inputs.aggregate_sha256,
    )
    if observed != expected_claim_sha256:
        raise NginxCoordinatorError("live lease claim digest differs")
    receipt, _ = load_state_receipt(
        receipt_path,
        "legacy-frozen",
        inputs.operation_id,
        inputs.release_sha,
        inputs.release_tree_sha,
        inputs.aggregate_sha256,
        allow_historical=True,
    )
    return claim, receipt


def _load_readiness_audit(
    inputs: CoordinatorInputs,
    *,
    claim: Mapping[str, Any],
    claim_sha256: str,
) -> tuple[dict[str, Any], str] | None:
    path = _live_lease_paths(inputs)[2] / f"{claim_sha256}.json"
    try:
        document, payload = _load_canonical_json(
            path,
            label="production Nginx live lease readiness audit",
        )
    except NginxCoordinatorError:
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise
    fields = {
        "schema",
        "status",
        "owner_action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "claim_sha256",
        "claim_epoch",
        "claim_nonce",
        "readiness_receipt_path",
        "readiness_receipt_sha256",
        "readiness_set_roles_sha256",
        "readiness_nonce",
        "controller_lock_path",
        "controller_authoritative",
        "automatic",
    }
    if (
        set(document) != fields
        or document["schema"] != LIVE_LEASE_READINESS_SCHEMA
        or document["status"] != "legacy-writers-ready"
        or document["owner_action"] != claim["owner_action"]
        or document["operation_id"] != inputs.operation_id
        or document["release_sha"] != inputs.release_sha
        or document["release_tree_sha"] != inputs.release_tree_sha
        or document["aggregate_sha256"] != inputs.aggregate_sha256
        or document["claim_sha256"] != claim_sha256
        or document["claim_epoch"] != claim["claim_epoch"]
        or document["claim_nonce"] != claim["nonce"]
        or not isinstance(document["readiness_receipt_path"], str)
        or not Path(document["readiness_receipt_path"]).is_absolute()
        or Path(document["readiness_receipt_path"])
        != Path(
            os.path.abspath(document["readiness_receipt_path"])
        )
        or not isinstance(document["readiness_receipt_sha256"], str)
        or SHA256_RE.fullmatch(
            document["readiness_receipt_sha256"]
        )
        is None
        or document["readiness_receipt_sha256"] == "0" * 64
        or not isinstance(document["readiness_set_roles_sha256"], str)
        or SHA256_RE.fullmatch(
            document["readiness_set_roles_sha256"]
        )
        is None
        or document["readiness_set_roles_sha256"] == "0" * 64
        or not isinstance(document["readiness_nonce"], str)
        or LIVE_LEASE_NONCE_RE.fullmatch(
            document["readiness_nonce"]
        )
        is None
        or document["readiness_nonce"] == "0" * 64
        or document["controller_lock_path"]
        != os.fspath(inputs.coordinator_root / "coordinator.lock")
        or document["controller_authoritative"] is not True
        or document["automatic"] is not False
    ):
        raise NginxCoordinatorError(
            "production Nginx live lease readiness audit differs"
        )
    return document, _sha256(payload)


def _load_consumption_audit(
    inputs: CoordinatorInputs,
    *,
    claim: Mapping[str, Any],
    claim_sha256: str,
) -> tuple[dict[str, Any], str] | None:
    path = _live_lease_paths(inputs)[3] / f"{claim_sha256}.json"
    try:
        document, payload = _load_canonical_json(
            path,
            label="production Nginx live lease consumption audit",
        )
    except NginxCoordinatorError:
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        raise
    fields = {
        "schema",
        "status",
        "owner_action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "claim_sha256",
        "claim_epoch",
        "claim_nonce",
        "outcome",
        "outcome_sha256",
        "readiness_audit_sha256",
        "final_state",
        "final_state_receipt_sha256",
        "controller_journal_sha256",
        "controller_journal_event_count",
        "controller_evidence_count",
        "controller_evidence_tail_sha256",
        "consumer_pid",
        "consumption_nonce",
        "adopted_after_crash",
        "controller_lock_path",
        "controller_authoritative",
        "automatic",
    }
    if (
        set(document) != fields
        or document["schema"] != LIVE_LEASE_CONSUMPTION_SCHEMA
        or document["status"] != "consumed"
        or document["owner_action"] != claim["owner_action"]
        or document["operation_id"] != inputs.operation_id
        or document["release_sha"] != inputs.release_sha
        or document["release_tree_sha"] != inputs.release_tree_sha
        or document["aggregate_sha256"] != inputs.aggregate_sha256
        or document["claim_sha256"] != claim_sha256
        or document["claim_epoch"] != claim["claim_epoch"]
        or document["claim_nonce"] != claim["nonce"]
        or document["outcome"]
        not in LIVE_LEASE_OWNER_OUTCOMES[claim["owner_action"]]
        or not isinstance(document["outcome_sha256"], str)
        or SHA256_RE.fullmatch(document["outcome_sha256"]) is None
        or document["outcome_sha256"] == "0" * 64
        or (
            document["readiness_audit_sha256"] is not None
            and (
                not isinstance(
                    document["readiness_audit_sha256"],
                    str,
                )
                or SHA256_RE.fullmatch(
                    document["readiness_audit_sha256"]
                )
                is None
                or document["readiness_audit_sha256"] == "0" * 64
            )
        )
        or document["final_state"]
        not in {"legacy-frozen", "legacy-normal"}
        or not isinstance(document["final_state_receipt_sha256"], str)
        or SHA256_RE.fullmatch(
            document["final_state_receipt_sha256"]
        )
        is None
        or document["final_state_receipt_sha256"] == "0" * 64
        or not isinstance(document["controller_journal_sha256"], str)
        or SHA256_RE.fullmatch(
            document["controller_journal_sha256"]
        )
        is None
        or document["controller_journal_sha256"] == "0" * 64
        or type(document["controller_journal_event_count"]) is not int
        or document["controller_journal_event_count"] < 0
        or type(document["controller_evidence_count"]) is not int
        or document["controller_evidence_count"] < 1
        or not isinstance(
            document["controller_evidence_tail_sha256"],
            str,
        )
        or SHA256_RE.fullmatch(
            document["controller_evidence_tail_sha256"]
        )
        is None
        or document["controller_evidence_tail_sha256"] == "0" * 64
        or type(document["consumer_pid"]) is not int
        or document["consumer_pid"] < 1
        or not isinstance(document["consumption_nonce"], str)
        or LIVE_LEASE_NONCE_RE.fullmatch(
            document["consumption_nonce"]
        )
        is None
        or document["consumption_nonce"] == "0" * 64
        or type(document["adopted_after_crash"]) is not bool
        or document["controller_lock_path"]
        != os.fspath(inputs.coordinator_root / "coordinator.lock")
        or document["controller_authoritative"] is not True
        or document["automatic"] is not False
        or (
            document["outcome"]
            in {
                "handoff-shadow-readonly",
                "current-frozen-verified",
                "frozen-final-shadow-restored",
            }
            and (
                document["final_state"] != "legacy-frozen"
                or document["readiness_audit_sha256"] is not None
            )
        )
        or (
            document["outcome"] == "legacy-restored"
            and (
                document["final_state"] != "legacy-normal"
                or document["readiness_audit_sha256"] is None
            )
        )
    ):
        raise NginxCoordinatorError(
            "production Nginx live lease consumption audit differs"
        )
    readiness = _load_readiness_audit(
        inputs,
        claim=claim,
        claim_sha256=claim_sha256,
    )
    if (
        (readiness is None)
        != (document["readiness_audit_sha256"] is None)
        or (
            readiness is not None
            and readiness[1] != document["readiness_audit_sha256"]
        )
    ):
        raise NginxCoordinatorError(
            "live lease consumption readiness binding differs"
        )
    final_receipt_path = _canonical_receipt_path(
        operation_id=inputs.operation_id,
        state=document["final_state"],
        digest=document["final_state_receipt_sha256"],
    )
    final_receipt, final_receipt_sha256 = load_state_receipt(
        final_receipt_path,
        document["final_state"],
        inputs.operation_id,
        inputs.release_sha,
        inputs.release_tree_sha,
        inputs.aggregate_sha256,
        allow_historical=True,
    )
    if (
        final_receipt_sha256
        != document["final_state_receipt_sha256"]
        or final_receipt["journal_sha256"]
        != document["controller_journal_sha256"]
        or final_receipt["evidence_count"]
        != document["controller_evidence_count"]
        or final_receipt["evidence_tail_sha256"]
        != document["controller_evidence_tail_sha256"]
        or (
            document["outcome"]
            in {
                "handoff-shadow-readonly",
                "current-frozen-verified",
                "frozen-final-shadow-restored",
            }
            and final_receipt_sha256
            != claim["legacy_frozen_receipt_sha256"]
        )
    ):
        raise NginxCoordinatorError(
            "live lease consumption final receipt binding differs"
        )
    return document, _sha256(payload)


def _scan_live_lease_ledger(
    inputs: CoordinatorInputs,
) -> tuple[
    list[tuple[dict[str, Any], str, dict[str, Any]]],
    list[tuple[dict[str, Any], str, dict[str, Any]]],
]:
    if not _prepare_live_lease_ledger(inputs, create=False):
        return [], []
    _, claims_root, readiness_root, consumptions_root = _live_lease_paths(
        inputs
    )
    entries: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    claim_names: set[str] = set()
    for path in claims_root.iterdir():
        if (
            not path.name.endswith(".json")
            or SHA256_RE.fullmatch(path.name[:-5]) is None
        ):
            raise NginxCoordinatorError(
                "live lease claim ledger contains an unexpected entry"
            )
        digest = path.name[:-5]
        claim, receipt = _load_claim_from_controller(
            inputs,
            path,
            digest,
        )
        entries.append((claim, digest, receipt))
        claim_names.add(digest)
    entries.sort(key=lambda row: row[0]["claim_epoch"])
    previous = "0" * 64
    for index, (claim, digest, _) in enumerate(entries, 1):
        if (
            claim["claim_epoch"] != index
            or claim["previous_claim_sha256"] != previous
        ):
            raise NginxCoordinatorError(
                "live lease claim chain differs"
            )
        previous = digest
    for root, label in (
        (readiness_root, "readiness"),
        (consumptions_root, "consumption"),
    ):
        for path in root.iterdir():
            if (
                not path.name.endswith(".json")
                or SHA256_RE.fullmatch(path.name[:-5]) is None
                or path.name[:-5] not in claim_names
            ):
                raise NginxCoordinatorError(
                    f"live lease {label} ledger contains an unexpected entry"
                )
    unresolved: list[
        tuple[dict[str, Any], str, dict[str, Any]]
    ] = []
    for claim, digest, receipt in entries:
        _load_readiness_audit(
            inputs,
            claim=claim,
            claim_sha256=digest,
        )
        consumed = _load_consumption_audit(
            inputs,
            claim=claim,
            claim_sha256=digest,
        )
        if consumed is None:
            unresolved.append((claim, digest, receipt))
    if len(unresolved) > 1:
        raise NginxCoordinatorError(
            "multiple unresolved live lease claims require reconciliation"
        )
    return entries, unresolved


def _lease_phase(
    inputs: CoordinatorInputs,
    journal: Mapping[str, Any],
    *,
    claim: Mapping[str, Any],
    claim_sha256: str,
) -> tuple[str, tuple[dict[str, Any], str] | None]:
    readiness = _load_readiness_audit(
        inputs,
        claim=claim,
        claim_sha256=claim_sha256,
    )
    pending = journal["pending"]
    if (
        journal["stable_state"] == "legacy-frozen"
        and pending is None
        and journal["state_sha256"]
        == claim["receipt_journal_sha256"]
        and journal["evidence_count"]
        == claim["receipt_journal_sequence"]
        and journal["evidence_tail_sha256"]
        == claim["receipt_journal_tail_sha256"]
        and len(journal["events"])
        == claim["controller_journal_event_count"]
    ):
        return (
            "legacy-frozen-ready"
            if readiness is not None
            else "legacy-frozen"
        ), readiness
    if (
        readiness is not None
        and journal["stable_state"] == "legacy-frozen"
        and isinstance(pending, dict)
        and pending.get("action") == "restore"
        and pending.get("target_state") == "legacy-normal"
        and pending.get("from_state") == "legacy-frozen"
        and pending.get("policy") == "complete-both"
        and pending.get("lease_claim_sha256") == claim_sha256
        and pending.get("status") in {"running", "partial-resumable"}
    ):
        return "legacy-restore-pending", readiness
    if (
        readiness is not None
        and journal["stable_state"] == "legacy-normal"
        and pending is None
    ):
        return "legacy-restored", readiness
    raise NginxCoordinatorError(
        "unresolved live lease state requires explicit reconciliation"
    )


def load_unconsumed_live_lease_claim(
    inputs: CoordinatorInputs,
    *,
    claim_path: Path,
    expected_claim_sha256: str,
    expected_nonce: str,
) -> dict[str, Any]:
    """Observe a current unconsumed controller claim without lock authority."""
    if not isinstance(inputs, CoordinatorInputs):
        raise NginxCoordinatorError(
            "live lease inputs are invalid"
        )
    expected_claim_sha256 = _nonzero_sha256(
        expected_claim_sha256,
        label="live lease claim SHA-256",
    )
    if (
        not isinstance(expected_nonce, str)
        or LIVE_LEASE_NONCE_RE.fullmatch(expected_nonce) is None
        or expected_nonce == "0" * 64
    ):
        raise NginxCoordinatorError("live lease nonce is invalid")
    _, unresolved = _scan_live_lease_ledger(inputs)
    if len(unresolved) != 1:
        raise NginxCoordinatorError(
            "exactly one unconsumed live lease claim is required"
        )
    claim, digest, receipt = unresolved[0]
    if (
        digest != expected_claim_sha256
        or claim_path
        != _live_lease_paths(inputs)[1] / f"{digest}.json"
        or claim["nonce"] != expected_nonce
    ):
        raise NginxCoordinatorError(
            "unconsumed live lease identity differs"
        )
    journal = _load_journal(inputs)
    phase, readiness = _lease_phase(
        inputs,
        journal,
        claim=claim,
        claim_sha256=digest,
    )
    return {
        "claim": claim,
        "claim_path": os.fspath(claim_path),
        "claim_sha256": digest,
        "state_receipt": receipt,
        "phase": phase,
        "readiness_audit": (
            readiness[0] if readiness is not None else None
        ),
        "controller_lock_authority_observed": False,
        "unconsumed_observed": True,
    }


def _assert_no_unconsumed_live_lease(
    inputs: CoordinatorInputs,
) -> None:
    _, unresolved = _scan_live_lease_ledger(inputs)
    if unresolved:
        raise NginxCoordinatorError(
            "an unresolved live lease claim blocks coordinator transitions; "
            "explicit lease resume and reconciliation are required"
        )


def _validate_current_receipt(
    inputs: CoordinatorInputs,
    journal: Mapping[str, Any],
    *,
    receipt_path: Path,
    receipt_sha256: str,
    state: str,
) -> tuple[dict[str, Any], str]:
    receipt_sha256 = _nonzero_sha256(
        receipt_sha256,
        label=f"{state} state receipt SHA-256",
    )
    expected_path = _canonical_receipt_path(
        operation_id=inputs.operation_id,
        state=state,
        digest=receipt_sha256,
    )
    if receipt_path != expected_path:
        raise NginxCoordinatorError(
            f"{state} state receipt path is not canonical"
        )
    receipt, observed = load_state_receipt(
        receipt_path,
        state,
        inputs.operation_id,
        inputs.release_sha,
        inputs.release_tree_sha,
        inputs.aggregate_sha256,
        allow_historical=True,
    )
    if (
        observed != receipt_sha256
        or journal["stable_state"] != state
        or journal["pending"] is not None
        or journal["state_sha256"] != receipt["journal_sha256"]
        or journal["evidence_count"] != receipt["evidence_count"]
        or journal["evidence_tail_sha256"]
        != receipt["evidence_tail_sha256"]
    ):
        raise NginxCoordinatorError(
            f"{state} state receipt is not the current coordinator state"
        )
    return receipt, observed


def _create_live_lease_claim(
    inputs: CoordinatorInputs,
    *,
    owner_action: str,
    journal: Mapping[str, Any],
    receipt_path: Path,
    receipt: Mapping[str, Any],
    receipt_sha256: str,
    entries: Sequence[
        tuple[Mapping[str, Any], str, Mapping[str, Any]]
    ],
) -> tuple[dict[str, Any], Path, str]:
    if owner_action not in LIVE_LEASE_OWNER_OUTCOMES:
        raise NginxCoordinatorError(
            "live lease owner action is not allowlisted"
        )
    epoch = len(entries) + 1
    previous = entries[-1][1] if entries else "0" * 64
    claim = {
        "schema": LIVE_LEASE_CLAIM_SCHEMA,
        "status": "active",
        "owner_action": owner_action,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "claim_epoch": epoch,
        "previous_claim_sha256": previous,
        "nonce": secrets.token_hex(32),
        "controller_pid": os.getpid(),
        "controller_lock_path": os.fspath(
            inputs.coordinator_root / "coordinator.lock"
        ),
        "controller_authoritative": True,
        "remote_copy_authoritative": False,
        "automatic_expiry_allowed": False,
        "reconciliation_required_after_crash": True,
        "legacy_frozen_receipt_path": os.fspath(receipt_path),
        "legacy_frozen_receipt_sha256": receipt_sha256,
        "receipt_journal_sha256": receipt["journal_sha256"],
        "receipt_journal_sequence": receipt["evidence_count"],
        "receipt_journal_tail_sha256": receipt[
            "evidence_tail_sha256"
        ],
        "controller_journal_event_count": len(journal["events"]),
        "receipt_state": "legacy-frozen",
        "receipt_global_generation_sha256": receipt[
            "global_generation_sha256"
        ],
        "receipt_role_generation_sha256": {
            role: receipt["readbacks"][role]["generation_sha256"]
            for role in ROLE_ORDER
        },
        "receipt_role_bindings": receipt["role_bindings"],
        "receipt_readbacks": receipt["readbacks"],
    }
    payload = canonical_json_bytes(claim)
    digest = _sha256(payload)
    path = _live_lease_paths(inputs)[1] / f"{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="production Nginx live lease claim",
            mode=FILE_MODE,
            max_size=GENERATION.MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise NginxCoordinatorError(
            "production Nginx live lease claim could not be created"
        ) from exc
    loaded, observed = load_live_lease_claim_material(
        path,
        state_receipt_path=receipt_path,
        expected_claim_sha256=digest,
        expected_state_receipt_sha256=receipt_sha256,
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        release_tree_sha=inputs.release_tree_sha,
        aggregate_sha256=inputs.aggregate_sha256,
    )
    if loaded != claim or observed != digest:
        raise NginxCoordinatorError(
            "production Nginx live lease claim readback differs"
        )
    return claim, path, digest


def _legacy_writer_transcript_entry_sha256(
    entry: Mapping[str, Any],
) -> str:
    unsigned = dict(entry)
    unsigned["entry_sha256"] = "0" * 64
    return _sha256(canonical_json_bytes(unsigned))


def _validate_legacy_writer_live_transcript(
    restored_result: Mapping[str, Any],
    *,
    inputs: CoordinatorInputs,
    role: str,
    claim: Mapping[str, Any],
    claim_sha256: str,
) -> None:
    transcript = restored_result["interactive_lease_transcript"]
    count = restored_result["interactive_lease_checkpoint_count"]
    expected_kinds = LEGACY_WRITER_KINDS[role]
    if (
        not isinstance(transcript, list)
        or type(count) is not int
        or not 1 <= count <= 10_000
        or len(transcript) != count
        or restored_result[
            "interactive_lease_authority_handoff_complete"
        ]
        is not True
    ):
        raise NginxCoordinatorError(
            f"{role} interactive live lease transcript differs"
        )
    previous = "0" * 64
    checkpoints: list[str] = []
    for sequence, entry in enumerate(transcript, 1):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"challenge", "response", "entry_sha256"}
            or not isinstance(entry["challenge"], dict)
            or not isinstance(entry["response"], dict)
        ):
            raise NginxCoordinatorError(
                f"{role} interactive live lease transcript row differs"
            )
        challenge = entry["challenge"]
        response = entry["response"]
        checkpoint = challenge.get("checkpoint")
        challenge_fields = {
            "schema",
            "status",
            "operation_id",
            "release_sha",
            "role",
            "live_lease_claim_sha256",
            "live_lease_claim_epoch",
            "sequence",
            "checkpoint",
            "challenge_nonce",
            "previous_transcript_sha256",
        }
        response_fields = {
            *challenge_fields,
            "challenge_sha256",
            "controller_flock_verified",
            "response_nonce",
        }
        if (
            set(challenge) != challenge_fields
            or challenge["schema"]
            != LEGACY_WRITER_LIVE_CHALLENGE_SCHEMA
            or challenge["status"] != "controller-response-required"
            or challenge["operation_id"] != inputs.operation_id
            or challenge["release_sha"] != inputs.release_sha
            or challenge["role"] != role
            or challenge["live_lease_claim_sha256"] != claim_sha256
            or challenge["live_lease_claim_epoch"]
            != claim["claim_epoch"]
            or challenge["sequence"] != sequence
            or not isinstance(checkpoint, str)
            or LEGACY_WRITER_CHECKPOINT_RE.fullmatch(checkpoint) is None
            or not isinstance(challenge["challenge_nonce"], str)
            or LIVE_LEASE_NONCE_RE.fullmatch(
                challenge["challenge_nonce"]
            )
            is None
            or challenge["challenge_nonce"] == "0" * 64
            or challenge["previous_transcript_sha256"] != previous
            or set(response) != response_fields
            or response["schema"]
            != LEGACY_WRITER_LIVE_RESPONSE_SCHEMA
            or response["status"] != "controller-flock-verified"
            or any(
                response[field] != challenge[field]
                for field in challenge_fields - {"schema", "status"}
            )
            or response["challenge_sha256"]
            != _sha256(canonical_json_bytes(challenge))
            or response["controller_flock_verified"] is not True
            or not isinstance(response["response_nonce"], str)
            or LIVE_LEASE_NONCE_RE.fullmatch(
                response["response_nonce"]
            )
            is None
            or response["response_nonce"] == "0" * 64
            or entry["entry_sha256"]
            != _legacy_writer_transcript_entry_sha256(entry)
        ):
            raise NginxCoordinatorError(
                f"{role} interactive live lease transcript binding differs"
            )
        checkpoints.append(checkpoint)
        previous = entry["entry_sha256"]
    if (
        restored_result["interactive_lease_transcript_sha256"] != previous
        or checkpoints[-1] != "before-result"
        or checkpoints.count("before-result") != 1
    ):
        raise NginxCoordinatorError(
            f"{role} interactive live lease finalization differs"
        )
    observed_stop_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.startswith(("before-stop:", "after-stop:"))
    ]
    stopped_kinds: list[str] = []
    if len(observed_stop_checkpoints) % 2 == 0:
        for index in range(0, len(observed_stop_checkpoints), 2):
            before = observed_stop_checkpoints[index]
            after = observed_stop_checkpoints[index + 1]
            kind = before.removeprefix("before-stop:")
            if after != f"after-stop:{kind}":
                stopped_kinds = []
                break
            stopped_kinds.append(kind)
    expected_stop_checkpoints = [
        checkpoint
        for kind in stopped_kinds
        for checkpoint in (f"before-stop:{kind}", f"after-stop:{kind}")
    ]
    expected_start_checkpoints = [
        checkpoint
        for kind in expected_kinds
        for checkpoint in (f"before-start:{kind}", f"after-start:{kind}")
    ]
    observed_start_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.startswith(("before-start:", "after-start:"))
    ]
    http_samples = [
        int(checkpoint.rsplit(":", 1)[1])
        for checkpoint in checkpoints
        if checkpoint.startswith("readiness-http:")
    ]
    stability_samples = [
        int(checkpoint.rsplit(":", 1)[1])
        for checkpoint in checkpoints
        if checkpoint.startswith("readiness-stability:")
    ]
    expected_checkpoints = [
        *expected_stop_checkpoints,
        *expected_start_checkpoints,
        *(
            f"readiness-http:{sample}"
            for sample in range(1, len(http_samples) + 1)
        ),
        *(
            f"readiness-stability:{sample}"
            for sample in range(1, len(stability_samples) + 1)
        ),
        "before-result",
    ]
    if (
        observed_stop_checkpoints != expected_stop_checkpoints
        or len(stopped_kinds) != len(set(stopped_kinds))
        or tuple(stopped_kinds)
        != tuple(kind for kind in expected_kinds if kind in stopped_kinds)
        or observed_start_checkpoints != expected_start_checkpoints
        or http_samples != list(range(1, len(http_samples) + 1))
        or not http_samples
        or stability_samples
        != list(range(1, len(stability_samples) + 1))
        or len(stability_samples)
        < restored_result["stable_sample_count"]
        or restored_result["stable_sample_count"] != 3
        or checkpoints != expected_checkpoints
    ):
        raise NginxCoordinatorError(
            f"{role} interactive live lease checkpoint semantics differ"
        )


def load_legacy_writer_readiness_set(
    path: Path,
    *,
    inputs: CoordinatorInputs,
    claim: Mapping[str, Any],
    claim_sha256: str,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Load the exact two-role readiness set required before Nginx restore."""
    claim_sha256 = _nonzero_sha256(
        claim_sha256,
        label="readiness live lease claim SHA-256",
    )
    expected_sha256 = _nonzero_sha256(
        expected_sha256,
        label="legacy writer readiness set SHA-256",
    )
    document, payload = _load_canonical_json(
        path,
        label="legacy writer readiness set",
    )
    observed_sha256 = _sha256(payload)
    fields = {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "aggregate_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_nonce",
        "live_lease_claim_epoch",
        "legacy_frozen_receipt_sha256",
        "roles",
        "roles_sha256",
    }
    identity = {
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
    }
    if (
        observed_sha256 != expected_sha256
        or set(document) != fields
        or document["schema"] != LEGACY_WRITER_READINESS_SET_SCHEMA
        or document["status"] != "legacy-writers-ready"
        or any(document.get(key) != value for key, value in identity.items())
        or document["live_lease_claim_sha256"] != claim_sha256
        or document["live_lease_claim_nonce"] != claim["nonce"]
        or document["live_lease_claim_epoch"] != claim["claim_epoch"]
        or document["legacy_frozen_receipt_sha256"]
        != claim["legacy_frozen_receipt_sha256"]
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLE_ORDER)
        or document["roles_sha256"]
        != _sha256(canonical_json_bytes(document["roles"]))
    ):
        raise NginxCoordinatorError(
            "legacy writer readiness set identity differs"
        )
    role_fields = {
        "restored_ready_result",
        "restored_ready_result_sha256",
        "status",
        "legacy_ready_for_nginx_restore",
        "freeze_evidence_sha256",
        "freeze_evidence_revoked",
        "all_exact_writer_containers_ready",
        "expected_writer_container_count",
        "ready_writer_container_count",
        "readiness_sha256",
        "stable_sample_count",
        "application_http_status",
        "database_container_running",
        "redis_container_running",
        "production_mutated",
    }
    restored_result_fields = {
        "schema",
        "status",
        "action",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "binding_sha256",
        "nginx_manifest_sha256",
        "nginx_aggregate_sha256",
        "coordinated_state_receipt_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "role_freeze_generation_sha256",
        "freeze_generation_sha256",
        "journal_sha256",
        "freeze_evidence_sha256",
        "freeze_evidence_revoked",
        "all_exact_writer_containers_ready",
        "expected_writer_container_count",
        "legacy_writer_process_count",
        "writer_database_client_count",
        "file_mutator_process_count",
        "database_container_running",
        "redis_container_running",
        "application_http_status",
        "legacy_ready_for_nginx_restore",
        "ready_writer_container_count",
        "stable_sample_count",
        "readiness_sha256",
        "interactive_lease_checkpoint_count",
        "interactive_lease_transcript",
        "interactive_lease_transcript_sha256",
        "interactive_lease_authority_handoff_complete",
        "production_mutated",
    }
    legacy_release_sha: str | None = None
    binding_sha256: set[str] = set()
    for role in ROLE_ORDER:
        row = document["roles"][role]
        expected_count = LEGACY_WRITER_COUNTS[role]
        if not isinstance(row, dict) or set(row) != role_fields:
            raise NginxCoordinatorError(
                f"{role} legacy writer readiness semantics differ"
            )
        restored_result = row["restored_ready_result"]
        if (
            not isinstance(restored_result, dict)
            or set(restored_result) != restored_result_fields
        ):
            raise NginxCoordinatorError(
                f"{role} restored-ready result fields differ"
            )
        expected_result_sha256 = _nonzero_sha256(
            row["restored_ready_result_sha256"],
            label=f"{role} restored-ready result SHA-256",
        )
        if (
            _sha256(canonical_json_bytes(restored_result))
            != expected_result_sha256
        ):
            raise NginxCoordinatorError(
                f"{role} restored-ready result digest differs"
            )
        observed_legacy_release_sha = _release_sha(
            restored_result["legacy_release_sha"],
            label=f"{role} legacy release SHA",
        )
        observed_binding_sha256 = _nonzero_sha256(
            restored_result["binding_sha256"],
            label=f"{role} source binding SHA-256",
        )
        if (
            observed_legacy_release_sha == inputs.release_sha
            or (
                legacy_release_sha is not None
                and observed_legacy_release_sha != legacy_release_sha
            )
            or observed_binding_sha256 in binding_sha256
        ):
            raise NginxCoordinatorError(
                f"{role} restored-ready source binding differs"
            )
        legacy_release_sha = observed_legacy_release_sha
        binding_sha256.add(observed_binding_sha256)
        if (
            restored_result["schema"] != LEGACY_WRITER_RESULT_SCHEMA
            or restored_result["status"] != "restored-ready"
            or restored_result["action"] != "restore"
            or restored_result["operation_id"] != inputs.operation_id
            or restored_result["release_sha"] != inputs.release_sha
            or restored_result["role"] != role
            or restored_result["nginx_manifest_sha256"]
            != inputs.roles[role].manifest_sha256
            or restored_result["nginx_aggregate_sha256"]
            != inputs.aggregate_sha256
            or restored_result["coordinated_state_receipt_sha256"]
            != claim["legacy_frozen_receipt_sha256"]
            or restored_result["live_lease_claim_sha256"]
            != claim_sha256
            or type(restored_result["live_lease_claim_epoch"]) is not int
            or restored_result["live_lease_claim_epoch"]
            != claim["claim_epoch"]
            or restored_result["role_freeze_generation_sha256"]
            != claim["receipt_role_generation_sha256"][role]
            or restored_result["freeze_generation_sha256"]
            != claim["receipt_global_generation_sha256"]
            or restored_result["freeze_evidence_sha256"] is not None
            or restored_result["freeze_evidence_revoked"] is not True
            or restored_result["all_exact_writer_containers_ready"]
            is not True
            or restored_result["expected_writer_container_count"]
            != expected_count
            or restored_result["legacy_writer_process_count"] is not None
            or restored_result["writer_database_client_count"] is not None
            or restored_result["file_mutator_process_count"] is not None
            or restored_result["database_container_running"] is not True
            or restored_result["redis_container_running"] is not True
            or type(restored_result["application_http_status"]) is not int
            or not 200
            <= restored_result["application_http_status"]
            <= 299
            or restored_result["legacy_ready_for_nginx_restore"]
            is not True
            or restored_result["ready_writer_container_count"]
            != expected_count
            or restored_result["stable_sample_count"] != 3
            or type(
                restored_result["interactive_lease_checkpoint_count"]
            )
            is not int
            or not isinstance(
                restored_result["interactive_lease_transcript"],
                list,
            )
            or not isinstance(
                restored_result[
                    "interactive_lease_transcript_sha256"
                ],
                str,
            )
            or restored_result[
                "interactive_lease_authority_handoff_complete"
            ]
            is not True
            or restored_result["production_mutated"] is not True
        ):
            raise NginxCoordinatorError(
                f"{role} restored-ready result semantics differ"
            )
        _nonzero_sha256(
            restored_result["journal_sha256"],
            label=f"{role} restored-ready journal SHA-256",
        )
        _nonzero_sha256(
            restored_result["readiness_sha256"],
            label=f"{role} exact writer readiness SHA-256",
        )
        _validate_legacy_writer_live_transcript(
            restored_result,
            inputs=inputs,
            role=role,
            claim=claim,
            claim_sha256=claim_sha256,
        )
        row_result_fields = role_fields - {
            "restored_ready_result",
            "restored_ready_result_sha256",
        }
        if (
            any(
                row[field] != restored_result[field]
                for field in row_result_fields
            )
            or row["status"] != "restored-ready"
            or row["legacy_ready_for_nginx_restore"] is not True
            or row["freeze_evidence_sha256"] is not None
            or row["freeze_evidence_revoked"] is not True
            or row["all_exact_writer_containers_ready"] is not True
            or row["expected_writer_container_count"] != expected_count
            or row["ready_writer_container_count"] != expected_count
            or row["stable_sample_count"] != 3
            or type(row["application_http_status"]) is not int
            or not 200 <= row["application_http_status"] <= 299
            or row["database_container_running"] is not True
            or row["redis_container_running"] is not True
            or row["production_mutated"] is not True
        ):
            raise NginxCoordinatorError(
                f"{role} legacy writer readiness semantics differ"
            )
        _nonzero_sha256(
            row["readiness_sha256"],
            label=f"{role} exact writer readiness SHA-256",
        )
    return document, observed_sha256


def _create_or_load_readiness_audit(
    inputs: CoordinatorInputs,
    *,
    claim: Mapping[str, Any],
    claim_sha256: str,
    readiness_receipt_path: Path,
    expected_readiness_receipt_sha256: str,
) -> tuple[dict[str, Any], str]:
    expected_readiness_receipt_sha256 = _nonzero_sha256(
        expected_readiness_receipt_sha256,
        label="legacy writer readiness receipt SHA-256",
    )
    readiness_set, observed_readiness_sha256 = (
        load_legacy_writer_readiness_set(
            readiness_receipt_path,
            inputs=inputs,
            claim=claim,
            claim_sha256=claim_sha256,
            expected_sha256=expected_readiness_receipt_sha256,
        )
    )
    if observed_readiness_sha256 != expected_readiness_receipt_sha256:
        raise NginxCoordinatorError(
            "legacy writer readiness set digest differs"
        )
    existing = _load_readiness_audit(
        inputs,
        claim=claim,
        claim_sha256=claim_sha256,
    )
    if existing is not None:
        if (
            existing[0]["readiness_receipt_path"]
            != os.fspath(readiness_receipt_path)
            or existing[0]["readiness_receipt_sha256"]
            != expected_readiness_receipt_sha256
            or existing[0]["readiness_set_roles_sha256"]
            != readiness_set["roles_sha256"]
        ):
            raise NginxCoordinatorError(
                "existing legacy writer readiness binding differs"
            )
        return existing
    audit = {
        "schema": LIVE_LEASE_READINESS_SCHEMA,
        "status": "legacy-writers-ready",
        "owner_action": claim["owner_action"],
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "claim_sha256": claim_sha256,
        "claim_epoch": claim["claim_epoch"],
        "claim_nonce": claim["nonce"],
        "readiness_receipt_path": os.fspath(readiness_receipt_path),
        "readiness_receipt_sha256": expected_readiness_receipt_sha256,
        "readiness_set_roles_sha256": readiness_set["roles_sha256"],
        "readiness_nonce": secrets.token_hex(32),
        "controller_lock_path": os.fspath(
            inputs.coordinator_root / "coordinator.lock"
        ),
        "controller_authoritative": True,
        "automatic": False,
    }
    payload = canonical_json_bytes(audit)
    path = _live_lease_paths(inputs)[2] / f"{claim_sha256}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="production Nginx live lease readiness audit",
            mode=FILE_MODE,
            max_size=GENERATION.MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise NginxCoordinatorError(
            "legacy writer readiness audit could not be created"
        ) from exc
    loaded = _load_readiness_audit(
        inputs,
        claim=claim,
        claim_sha256=claim_sha256,
    )
    if loaded is None or loaded[0] != audit:
        raise NginxCoordinatorError(
            "legacy writer readiness audit readback differs"
        )
    return loaded


def _find_current_state_receipt(
    inputs: CoordinatorInputs,
    journal: Mapping[str, Any],
    *,
    state: str,
) -> tuple[Path, dict[str, Any], str]:
    matches: list[tuple[Path, dict[str, Any], str]] = []
    for path in inputs.receipts_root.iterdir():
        prefix = f"{state}-"
        if (
            not path.name.startswith(prefix)
            or not path.name.endswith(".json")
            or SHA256_RE.fullmatch(
                path.name[len(prefix) : -5]
            )
            is None
        ):
            continue
        expected = path.name[len(prefix) : -5]
        receipt, digest = load_state_receipt(
            path,
            state,
            inputs.operation_id,
            inputs.release_sha,
            inputs.release_tree_sha,
            inputs.aggregate_sha256,
            allow_historical=True,
        )
        if digest != expected:
            raise NginxCoordinatorError(
                "state receipt filename digest differs"
            )
        if (
            receipt["journal_sha256"] == journal["state_sha256"]
            and receipt["evidence_count"] == journal["evidence_count"]
            and receipt["evidence_tail_sha256"]
            == journal["evidence_tail_sha256"]
        ):
            matches.append((path, receipt, digest))
    if len(matches) != 1:
        raise NginxCoordinatorError(
            f"exactly one current {state} state receipt is required"
        )
    return matches[0]


class NginxCoordinatorRollbackPending(NginxCoordinatorError):
    """A write-blocked rollback-freeze transition must be resumed."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(
            "rollback-freeze is partial, write-blocked, and resumable"
        )
        self.result = json.loads(
            canonical_json_bytes(result).decode("utf-8")
        )


class CoordinatorLiveLease:
    """Controller-authoritative live lease held under `_CoordinatorLock`."""

    def __init__(
        self,
        *,
        inputs: CoordinatorInputs,
        lock: _CoordinatorLock,
        claim: Mapping[str, Any],
        claim_path: Path,
        claim_sha256: str,
        adopted_after_crash: bool,
        transition_result: Mapping[str, Any] | None = None,
    ) -> None:
        self._inputs = inputs
        self._lock = lock
        self._claim = json.loads(
            canonical_json_bytes(claim).decode("utf-8")
        )
        self._claim_path = claim_path
        self._claim_sha256 = claim_sha256
        self._adopted_after_crash = adopted_after_crash
        self._transition_result = (
            json.loads(canonical_json_bytes(transition_result).decode("utf-8"))
            if transition_result is not None
            else None
        )
        self._consumed = False
        self._consumption_path: Path | None = None
        self._consumption_sha256: str | None = None

    @property
    def claim(self) -> dict[str, Any]:
        return json.loads(
            canonical_json_bytes(self._claim).decode("utf-8")
        )

    @property
    def claim_path(self) -> Path:
        return self._claim_path

    @property
    def claim_sha256(self) -> str:
        return self._claim_sha256

    @property
    def claim_payload(self) -> bytes:
        return _read_private_file(
            self._claim_path,
            label="production Nginx live lease claim",
            maximum=GENERATION.MAX_JSON_BYTES,
        )

    @property
    def verifier(self) -> Callable[[], dict[str, Any]]:
        return self.verify

    @property
    def consumed(self) -> bool:
        return self._consumed

    @property
    def transition_result(self) -> dict[str, Any] | None:
        if self._transition_result is None:
            return None
        return json.loads(
            canonical_json_bytes(self._transition_result).decode("utf-8")
        )

    @property
    def consumption_path(self) -> Path | None:
        return self._consumption_path

    @property
    def consumption_sha256(self) -> str | None:
        return self._consumption_sha256

    def _require_holder(self) -> None:
        if not self._lock.held:
            raise NginxCoordinatorError(
                "live lease controller lock is not held"
            )
        if self._consumed:
            raise NginxCoordinatorError(
                "live lease claim has already been consumed"
            )

    def _observe(self) -> dict[str, Any]:
        self._require_holder()
        observation = load_unconsumed_live_lease_claim(
            self._inputs,
            claim_path=self._claim_path,
            expected_claim_sha256=self._claim_sha256,
            expected_nonce=self._claim["nonce"],
        )
        if observation["claim"] != self._claim:
            raise NginxCoordinatorError(
                "live lease claim changed while held"
            )
        observation["controller_lock_authority_observed"] = True
        return observation

    def verify(self) -> dict[str, Any]:
        observation = self._observe()
        if observation["phase"] not in {
            "legacy-frozen",
            "legacy-frozen-ready",
        }:
            raise NginxCoordinatorError(
                "live lease callback requires current legacy-frozen state"
            )
        return observation

    def restore_legacy_normal(
        self,
        *,
        readiness_receipt_path: Path,
        readiness_receipt_sha256: str,
        runner: RunFn = _subprocess_runner,
    ) -> dict[str, Any]:
        if self._claim["owner_action"] != "restore-legacy-writers":
            raise NginxCoordinatorError(
                "live lease owner action cannot restore legacy writers"
            )
        observation = self._observe()
        if observation["phase"] not in {
            "legacy-frozen",
            "legacy-frozen-ready",
            "legacy-restore-pending",
            "legacy-restored",
        }:
            raise NginxCoordinatorError(
                "legacy restore lease phase is invalid"
            )
        readiness = _create_or_load_readiness_audit(
            self._inputs,
            claim=self._claim,
            claim_sha256=self._claim_sha256,
            readiness_receipt_path=readiness_receipt_path,
            expected_readiness_receipt_sha256=(
                readiness_receipt_sha256
            ),
        )
        journal = _load_journal(self._inputs)
        details = _restore_action(
            self._inputs,
            journal,
            lease_claim_sha256=self._claim_sha256,
            runner=runner,
        )
        final_journal = _load_journal(self._inputs)
        receipt = _persist_state_receipt(
            self._inputs,
            final_journal,
            action="restore",
            target_state=None,
            details=details,
        )
        return {
            **details,
            "readiness_audit_sha256": readiness[1],
            "state_receipt_path": (
                os.fspath(receipt[0]) if receipt is not None else None
            ),
            "state_receipt_sha256": (
                receipt[1] if receipt is not None else None
            ),
        }

    def consume(
        self,
        *,
        outcome: str,
        outcome_sha256: str,
    ) -> tuple[Path, str]:
        observation = self._observe()
        if outcome not in LIVE_LEASE_OWNER_OUTCOMES[
            self._claim["owner_action"]
        ]:
            raise NginxCoordinatorError(
                "live lease outcome is outside its owner action"
            )
        outcome_sha256 = _nonzero_sha256(
            outcome_sha256,
            label="live lease outcome SHA-256",
        )
        journal = _load_journal(self._inputs)
        readiness = _load_readiness_audit(
            self._inputs,
            claim=self._claim,
            claim_sha256=self._claim_sha256,
        )
        if outcome in {
            "handoff-shadow-readonly",
            "current-frozen-verified",
            "frozen-final-shadow-restored",
        }:
            if (
                observation["phase"] != "legacy-frozen"
                or readiness is not None
            ):
                raise NginxCoordinatorError(
                    "frozen-state outcome requires an untouched "
                    "legacy-frozen lease"
                )
            final_state = "legacy-frozen"
            final_receipt_sha256 = self._claim[
                "legacy_frozen_receipt_sha256"
            ]
        elif outcome == "legacy-restored":
            if (
                observation["phase"] != "legacy-restored"
                or readiness is None
            ):
                raise NginxCoordinatorError(
                    "legacy-restored consumption requires ready writers "
                    "and verified legacy-normal"
                )
            final_state = "legacy-normal"
            _, _, final_receipt_sha256 = _find_current_state_receipt(
                self._inputs,
                journal,
                state=final_state,
            )
        else:
            raise NginxCoordinatorError(
                "live lease outcome is not allowlisted"
            )
        audit = {
            "schema": LIVE_LEASE_CONSUMPTION_SCHEMA,
            "status": "consumed",
            "owner_action": self._claim["owner_action"],
            "operation_id": self._inputs.operation_id,
            "release_sha": self._inputs.release_sha,
            "release_tree_sha": self._inputs.release_tree_sha,
            "aggregate_sha256": self._inputs.aggregate_sha256,
            "claim_sha256": self._claim_sha256,
            "claim_epoch": self._claim["claim_epoch"],
            "claim_nonce": self._claim["nonce"],
            "outcome": outcome,
            "outcome_sha256": outcome_sha256,
            "readiness_audit_sha256": (
                readiness[1] if readiness is not None else None
            ),
            "final_state": final_state,
            "final_state_receipt_sha256": final_receipt_sha256,
            "controller_journal_sha256": journal["state_sha256"],
            "controller_journal_event_count": len(journal["events"]),
            "controller_evidence_count": journal["evidence_count"],
            "controller_evidence_tail_sha256": journal[
                "evidence_tail_sha256"
            ],
            "consumer_pid": os.getpid(),
            "consumption_nonce": secrets.token_hex(32),
            "adopted_after_crash": self._adopted_after_crash,
            "controller_lock_path": os.fspath(
                self._inputs.coordinator_root / "coordinator.lock"
            ),
            "controller_authoritative": True,
            "automatic": False,
        }
        payload = canonical_json_bytes(audit)
        path = (
            _live_lease_paths(self._inputs)[3]
            / f"{self._claim_sha256}.json"
        )
        try:
            write_secure_new_bytes(
                path,
                payload,
                label="production Nginx live lease consumption audit",
                mode=FILE_MODE,
                max_size=GENERATION.MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise NginxCoordinatorError(
                "live lease consumption audit could not be created"
            ) from exc
        loaded = _load_consumption_audit(
            self._inputs,
            claim=self._claim,
            claim_sha256=self._claim_sha256,
        )
        if loaded is None or loaded[0] != audit:
            raise NginxCoordinatorError(
                "live lease consumption audit readback differs"
            )
        self._consumed = True
        self._consumption_path = path
        self._consumption_sha256 = loaded[1]
        return path, loaded[1]


class _CoordinatorLiveLeaseContext:
    def __init__(
        self,
        *,
        inputs: CoordinatorInputs,
        mode: str,
        owner_action: str,
        receipt_path: Path | None = None,
        receipt_sha256: str | None = None,
        claim_path: Path | None = None,
        claim_sha256: str | None = None,
        claim_nonce: str | None = None,
        runner: RunFn = _subprocess_runner,
    ) -> None:
        self._inputs = inputs
        self._mode = mode
        if owner_action not in LIVE_LEASE_OWNER_OUTCOMES:
            raise NginxCoordinatorError(
                "live lease owner action is not allowlisted"
            )
        self._owner_action = owner_action
        self._receipt_path = receipt_path
        self._receipt_sha256 = receipt_sha256
        self._claim_path = claim_path
        self._claim_sha256 = claim_sha256
        self._claim_nonce = claim_nonce
        self._runner = runner
        self._lock = _CoordinatorLock(inputs.coordinator_root)
        self._lease: CoordinatorLiveLease | None = None

    def _enter_new(
        self,
        *,
        entries: Sequence[
            tuple[dict[str, Any], str, dict[str, Any]]
        ],
        unresolved: Sequence[
            tuple[dict[str, Any], str, dict[str, Any]]
        ],
    ) -> CoordinatorLiveLease:
        if unresolved:
            raise NginxCoordinatorError(
                "an unresolved live lease requires explicit resume"
            )
        if self._receipt_path is None or self._receipt_sha256 is None:
            raise NginxCoordinatorError(
                "legacy-frozen receipt binding is required"
            )
        journal = _load_journal(self._inputs)
        receipt, digest = _validate_current_receipt(
            self._inputs,
            journal,
            receipt_path=self._receipt_path,
            receipt_sha256=self._receipt_sha256,
            state="legacy-frozen",
        )
        claim, path, claim_sha256 = _create_live_lease_claim(
            self._inputs,
            owner_action=self._owner_action,
            journal=journal,
            receipt_path=self._receipt_path,
            receipt=receipt,
            receipt_sha256=digest,
            entries=entries,
        )
        return CoordinatorLiveLease(
            inputs=self._inputs,
            lock=self._lock,
            claim=claim,
            claim_path=path,
            claim_sha256=claim_sha256,
            adopted_after_crash=False,
        )

    def _enter_resume(
        self,
        *,
        unresolved: Sequence[
            tuple[dict[str, Any], str, dict[str, Any]]
        ],
    ) -> CoordinatorLiveLease:
        if (
            self._claim_path is None
            or self._claim_sha256 is None
            or self._claim_nonce is None
            or len(unresolved) != 1
        ):
            raise NginxCoordinatorError(
                "resume requires the exact unresolved claim"
            )
        claim, digest, _ = unresolved[0]
        if (
            digest != self._claim_sha256
            or self._claim_path
            != _live_lease_paths(self._inputs)[1] / f"{digest}.json"
            or claim["nonce"] != self._claim_nonce
            or claim["owner_action"] != self._owner_action
        ):
            raise NginxCoordinatorError(
                "resume claim digest or nonce differs"
            )
        journal = _load_journal(self._inputs)
        _lease_phase(
            self._inputs,
            journal,
            claim=claim,
            claim_sha256=digest,
        )
        return CoordinatorLiveLease(
            inputs=self._inputs,
            lock=self._lock,
            claim=claim,
            claim_path=self._claim_path,
            claim_sha256=digest,
            adopted_after_crash=True,
        )

    def _enter_rollback(
        self,
        *,
        entries: Sequence[
            tuple[dict[str, Any], str, dict[str, Any]]
        ],
        unresolved: Sequence[
            tuple[dict[str, Any], str, dict[str, Any]]
        ],
    ) -> CoordinatorLiveLease:
        if unresolved:
            raise NginxCoordinatorError(
                "an unresolved live lease requires explicit resume"
            )
        if self._receipt_path is None or self._receipt_sha256 is None:
            raise NginxCoordinatorError(
                "shadow-readonly receipt binding is required"
            )
        receipt_sha256 = _nonzero_sha256(
            self._receipt_sha256,
            label="shadow-readonly receipt SHA-256",
        )
        expected_path = _canonical_receipt_path(
            operation_id=self._inputs.operation_id,
            state="shadow-readonly",
            digest=receipt_sha256,
        )
        if self._receipt_path != expected_path:
            raise NginxCoordinatorError(
                "shadow-readonly receipt path is not canonical"
            )
        receipt, observed = load_state_receipt(
            self._receipt_path,
            "shadow-readonly",
            self._inputs.operation_id,
            self._inputs.release_sha,
            self._inputs.release_tree_sha,
            self._inputs.aggregate_sha256,
            allow_historical=True,
        )
        if observed != receipt_sha256:
            raise NginxCoordinatorError(
                "shadow-readonly receipt digest differs"
            )
        journal = _load_journal(self._inputs)
        pending = journal["pending"]
        completed_before_receipt = False
        if pending is None:
            if journal["stable_state"] == "shadow-readonly":
                _validate_current_receipt(
                    self._inputs,
                    journal,
                    receipt_path=self._receipt_path,
                    receipt_sha256=receipt_sha256,
                    state="shadow-readonly",
                )
            elif (
                journal["stable_state"] == "legacy-frozen"
                and any(
                    event["kind"]
                    in {
                        "rollback-frozen-both",
                        "rollback-frozen-reconciled",
                    }
                    and event["data"].get("source_receipt_sha256")
                    == receipt_sha256
                    for event in journal["events"]
                )
            ):
                completed_before_receipt = True
            else:
                raise NginxCoordinatorError(
                    "rollback-freeze source receipt is not current or "
                    "durably linked to the frozen result"
                )
        elif not (
            isinstance(pending, dict)
            and journal["stable_state"] == "shadow-readonly"
            and pending.get("action") == "rollback-freeze"
            and pending.get("target_state") == "legacy-frozen"
            and pending.get("from_state") == "shadow-readonly"
            and pending.get("source_receipt_sha256")
            == receipt_sha256
            and pending.get("policy")
            == "rollback-to-frozen-write-blocked"
            and pending.get("status") in {"running", "partial-resumable"}
        ):
            raise NginxCoordinatorError(
                "rollback-freeze resume journal differs"
            )
        if completed_before_receipt:
            readbacks, state, digest, external = _verified_readback(
                self._inputs,
                journal,
                runner=self._runner,
                action="rollback-freeze",
                target_state="legacy-frozen",
            )
            if state != "legacy-frozen":
                raise NginxCoordinatorError(
                    "durable rollback-freeze result no longer reads frozen"
                )
            _append_event(
                journal,
                "rollback-frozen-reconciled",
                data={
                    "source_receipt_sha256": receipt_sha256,
                    "global_generation_sha256": digest,
                },
            )
            _write_journal(
                self._inputs.journal_path,
                journal,
                create=False,
            )
            details = {
                "status": "rollback-frozen",
                "state": "legacy-frozen",
                "readbacks": readbacks,
                "global_generation_sha256": digest,
                "external_readback": external,
                "active_configuration_mutated": False,
                "current_mutated": False,
                "container_mutated": False,
                "volume_mutated": False,
                "data_mutated": False,
            }
        else:
            details = _rollback_to_frozen_action(
                self._inputs,
                journal,
                source_receipt_sha256=receipt_sha256,
                runner=self._runner,
            )
        if details["status"] == "rollback-freeze-partial-resumable":
            raise NginxCoordinatorRollbackPending(details)
        final_journal = _load_journal(self._inputs)
        persisted = _persist_state_receipt(
            self._inputs,
            final_journal,
            action="rollback-freeze",
            target_state="legacy-frozen",
            details=details,
        )
        if persisted is None:
            raise NginxCoordinatorError(
                "rollback-freeze did not produce a frozen R2 receipt"
            )
        r2_path, r2_sha256 = persisted
        r2, _ = _validate_current_receipt(
            self._inputs,
            final_journal,
            receipt_path=r2_path,
            receipt_sha256=r2_sha256,
            state="legacy-frozen",
        )
        claim, path, claim_sha256 = _create_live_lease_claim(
            self._inputs,
            owner_action="restore-legacy-writers",
            journal=final_journal,
            receipt_path=r2_path,
            receipt=r2,
            receipt_sha256=r2_sha256,
            entries=entries,
        )
        return CoordinatorLiveLease(
            inputs=self._inputs,
            lock=self._lock,
            claim=claim,
            claim_path=path,
            claim_sha256=claim_sha256,
            adopted_after_crash=False,
            transition_result=details,
        )

    def __enter__(self) -> CoordinatorLiveLease:
        if not isinstance(self._inputs, CoordinatorInputs):
            raise NginxCoordinatorError(
                "live lease inputs are invalid"
            )
        self._lock.__enter__()
        try:
            _prepare_live_lease_ledger(self._inputs, create=True)
            entries, unresolved = _scan_live_lease_ledger(self._inputs)
            if self._mode == "new":
                self._lease = self._enter_new(
                    entries=entries,
                    unresolved=unresolved,
                )
            elif self._mode == "resume":
                self._lease = self._enter_resume(
                    unresolved=unresolved,
                )
            elif self._mode == "rollback":
                self._lease = self._enter_rollback(
                    entries=entries,
                    unresolved=unresolved,
                )
            else:
                raise NginxCoordinatorError(
                    "live lease context mode is invalid"
                )
            return self._lease
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
        unconsumed = (
            self._lease is not None and not self._lease.consumed
        )
        self._lock.__exit__(exc_type, exc, traceback)
        if unconsumed and exc_type is None:
            raise NginxCoordinatorError(
                "live lease exited unconsumed; explicit resume and "
                "reconciliation are required"
            )
        return False


def hold_coordinator_live_lease(
    *,
    inputs: CoordinatorInputs,
    owner_action: str,
    legacy_frozen_receipt_path: Path,
    legacy_frozen_receipt_sha256: str,
) -> _CoordinatorLiveLeaseContext:
    """Create and hold a new controller-authoritative frozen-state lease."""
    return _CoordinatorLiveLeaseContext(
        inputs=inputs,
        mode="new",
        owner_action=owner_action,
        receipt_path=legacy_frozen_receipt_path,
        receipt_sha256=legacy_frozen_receipt_sha256,
    )


def resume_coordinator_live_lease(
    *,
    inputs: CoordinatorInputs,
    expected_owner_action: str,
    claim_path: Path,
    expected_claim_sha256: str,
    expected_nonce: str,
) -> _CoordinatorLiveLeaseContext:
    """Adopt the exact unresolved claim under the same controller flock."""
    return _CoordinatorLiveLeaseContext(
        inputs=inputs,
        mode="resume",
        owner_action=expected_owner_action,
        claim_path=claim_path,
        claim_sha256=expected_claim_sha256,
        claim_nonce=expected_nonce,
    )


def reconcile_coordinator_live_lease(
    *,
    inputs: CoordinatorInputs,
    expected_owner_action: str,
    claim_path: Path,
    expected_claim_sha256: str,
    expected_nonce: str,
) -> _CoordinatorLiveLeaseContext:
    """Explicitly reconcile by adopting only the exact safe resume state."""
    return resume_coordinator_live_lease(
        inputs=inputs,
        expected_owner_action=expected_owner_action,
        claim_path=claim_path,
        expected_claim_sha256=expected_claim_sha256,
        expected_nonce=expected_nonce,
    )


def hold_coordinator_rollback_live_lease(
    *,
    inputs: CoordinatorInputs,
    shadow_readonly_receipt_path: Path,
    shadow_readonly_receipt_sha256: str,
    runner: RunFn = _subprocess_runner,
) -> _CoordinatorLiveLeaseContext:
    """Rollback readonly to frozen R2 and claim it without releasing flock."""
    return _CoordinatorLiveLeaseContext(
        inputs=inputs,
        mode="rollback",
        owner_action="restore-legacy-writers",
        receipt_path=shadow_readonly_receipt_path,
        receipt_sha256=shadow_readonly_receipt_sha256,
        runner=runner,
    )


def confirmation_phrase(
    *,
    operation_id: str,
    release_sha: str,
    action: str,
    target_state: str | None,
) -> str:
    suffix = f":{target_state}" if target_state is not None else ""
    return (
        "APPLY-PRODUCTION-NGINX-COORDINATOR:"
        f"{operation_id}:{release_sha}:{action}{suffix}"
    )


def _plan_summary(
    inputs: CoordinatorInputs,
    *,
    action: str,
    target_state: str | None,
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "planned",
        "action": action,
        "target_state": target_state,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "aggregate_sha256": inputs.aggregate_sha256,
        "worker_path": os.fspath(inputs.worker_path),
        "worker_sha256": inputs.worker_sha256,
        "ssh_identity_sha256": inputs.ssh_identity_sha256,
        "host_control_bootstrap": {
            "bot_fi": [
                os.fspath(HOST_CONTROL_PARENT),
                os.fspath(HOST_OPERATION_BASE),
            ],
            "webapp_fi": [
                os.fspath(HOST_CONTROL_PARENT),
                os.fspath(HOST_OPERATION_BASE),
            ],
            "create_only_if_absent": True,
            "required_mode": "0700",
        },
        "role_order": list(ROLE_ORDER),
        "role_manifest_sha256": {
            role: inputs.roles[role].manifest_sha256
            for role in ROLE_ORDER
        },
        "role_archive_sha256": {
            role: inputs.roles[role].manifest["archive"]["sha256"]
            for role in ROLE_ORDER
        },
        "global_generation_sha256": dict(
            inputs.aggregate["generation_sha256"]
        ),
        "required_confirmation": confirmation_phrase(
            operation_id=inputs.operation_id,
            release_sha=inputs.release_sha,
            action=action,
            target_state=target_state,
        ),
        "runner_invoked": False,
        "network_contacted": False,
        "controller_mutated": False,
        "active_configuration_mutated": False,
        "current_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def execute_coordinator(
    *,
    aggregate_path: Path,
    bot_fi_manifest: Path,
    bot_fi_archive: Path,
    webapp_fi_manifest: Path,
    webapp_fi_archive: Path,
    action: str,
    target_state: str | None = None,
    apply: bool = False,
    confirm: str | None = None,
    runner: RunFn = _subprocess_runner,
    known_hosts: Path = KNOWN_HOSTS,
    ssh_identity: Path = DEFAULT_SSH_IDENTITY,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise NginxCoordinatorError("coordinator action is not allowlisted")
    if action in {"test", "activate"}:
        if target_state not in GENERATION.GENERATION_STATES:
            raise NginxCoordinatorError(
                f"{action} requires an exact generation state"
            )
    elif action == "rollback-freeze":
        if target_state != "legacy-frozen":
            raise NginxCoordinatorError(
                "rollback-freeze target is always legacy-frozen"
            )
    elif target_state is not None:
        raise NginxCoordinatorError(
            f"{action} does not accept a target state"
        )
    inputs = load_inputs(
        aggregate_path=aggregate_path,
        bot_fi_manifest=bot_fi_manifest,
        bot_fi_archive=bot_fi_archive,
        webapp_fi_manifest=webapp_fi_manifest,
        webapp_fi_archive=webapp_fi_archive,
        known_hosts=known_hosts,
        ssh_identity=ssh_identity,
    )
    required = confirmation_phrase(
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        action=action,
        target_state=target_state,
    )
    if not apply:
        if confirm is not None:
            raise NginxCoordinatorError(
                "--confirm is valid only with --apply"
            )
        return _plan_summary(
            inputs,
            action=action,
            target_state=target_state,
        )
    if os.geteuid() != 0:
        raise NginxCoordinatorError(
            "mutating Nginx coordination requires root ownership"
        )
    if threading.current_thread() is not threading.main_thread():
        raise NginxCoordinatorError(
            "mutating Nginx coordination must run in the main thread"
        )
    if confirm != required:
        raise NginxCoordinatorError(
            f"apply requires --confirm {required}"
        )
    journal = _prepare_controller_state(inputs)
    with _CoordinatorLock(inputs.coordinator_root):
        _assert_no_unconsumed_live_lease(inputs)
        journal = _load_journal(inputs)
        if action in {"rollback-freeze", "restore"}:
            raise NginxCoordinatorError(
                f"{action} requires the locked live lease API"
            )
        if (
            isinstance(journal["pending"], dict)
            and journal["pending"].get("action") == "rollback-freeze"
        ):
            raise NginxCoordinatorError(
                "pending rollback-freeze requires the locked rollback "
                "live lease API"
            )
        if action != "install" and journal["installed_roles"] != list(
            ROLE_ORDER
        ):
            raise NginxCoordinatorError(
                "both host generation archives must be installed first"
            )
        local_prerequisites = _bootstrap_local_host_control()
        _append_event(
            journal,
            "local-host-prerequisites",
            data=local_prerequisites,
        )
        _write_journal(inputs.journal_path, journal, create=False)
        remote_prerequisites = _validate_remote_prerequisites(
            inputs,
            journal,
            runner=runner,
            action=action,
        )
        _append_event(
            journal,
            "remote-host-prerequisites",
            data=remote_prerequisites,
        )
        _write_journal(inputs.journal_path, journal, create=False)
        if action == "install":
            details = _install_action(inputs, journal, runner=runner)
        elif action == "test":
            host_results = _test_both(
                inputs,
                journal,
                state=str(target_state),
                runner=runner,
            )
            readbacks, observed_state, digest, external = (
                _verified_readback(
                    inputs,
                    journal,
                    runner=runner,
                    action="test",
                    target_state=target_state,
                )
            )
            if (
                journal["stable_state"] is not None
                and observed_state != journal["stable_state"]
            ):
                raise NginxCoordinatorError(
                    "candidate test found active two-host state drift"
                )
            details = {
                "status": "tested",
                "state": observed_state,
                "target_state": target_state,
                "host_results": host_results,
                "readbacks": readbacks,
                "global_generation_sha256": digest,
                "external_readback": external,
            }
        elif action == "activate":
            details = _activate_action(
                inputs,
                journal,
                target_state=str(target_state),
                runner=runner,
            )
        else:
            if journal["pending"] is not None:
                raise NginxCoordinatorError(
                    "pending Nginx transition requires its explicit action"
                )
            readbacks, state, digest, external = _verified_readback(
                inputs,
                journal,
                runner=runner,
                action="readback",
                target_state=None,
            )
            if state is None:
                raise NginxCoordinatorError(
                    "readback found cross-host drift"
                )
            if state != journal["stable_state"]:
                raise NginxCoordinatorError(
                    "readback differs from the durable two-host state"
                )
            _append_event(
                journal,
                "read-back-both",
                data={
                    "state": state,
                    "global_generation_sha256": digest,
                },
            )
            _write_journal(inputs.journal_path, journal, create=False)
            details = {
                "status": "read-back",
                "state": state,
                "readbacks": readbacks,
                "global_generation_sha256": digest,
                "external_readback": external,
            }
        final_journal = _load_journal(inputs)
        receipt = _persist_state_receipt(
            inputs,
            final_journal,
            action=action,
            target_state=target_state,
            details=details,
        )
        result = {
            "schema": RESULT_SCHEMA,
            "action": action,
            "target_state": target_state,
            "operation_id": inputs.operation_id,
            "release_sha": inputs.release_sha,
            "release_tree_sha": inputs.release_tree_sha,
            "aggregate_sha256": inputs.aggregate_sha256,
            "journal_sha256": final_journal["state_sha256"],
            "evidence_count": final_journal["evidence_count"],
            "host_prerequisites": {
                "bot_fi": local_prerequisites,
                "webapp_fi": remote_prerequisites,
            },
            "active_configuration_mutated": action
            in {"activate", "restore"}
            and details["status"]
            in {
                "activated",
                "restored",
                "partial-resumable",
                "forward-only-retry",
                "restore-partial-resumable",
            },
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
            "state_receipt_path": (
                os.fspath(receipt[0]) if receipt is not None else None
            ),
            "state_receipt_sha256": (
                receipt[1] if receipt is not None else None
            ),
            **details,
        }
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--bot-fi-manifest", type=Path, required=True)
    parser.add_argument("--bot-fi-archive", type=Path, required=True)
    parser.add_argument("--webapp-fi-manifest", type=Path, required=True)
    parser.add_argument("--webapp-fi-archive", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, default=KNOWN_HOSTS)
    parser.add_argument(
        "--ssh-identity",
        type=Path,
        default=DEFAULT_SSH_IDENTITY,
    )
    parser.add_argument("--action", choices=ACTIONS, required=True)
    parser.add_argument(
        "--target-state",
        choices=GENERATION.GENERATION_STATES,
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(
            sys.argv[1:] if argv is None else argv
        )
        result = execute_coordinator(
            aggregate_path=args.aggregate,
            bot_fi_manifest=args.bot_fi_manifest,
            bot_fi_archive=args.bot_fi_archive,
            webapp_fi_manifest=args.webapp_fi_manifest,
            webapp_fi_archive=args.webapp_fi_archive,
            action=args.action,
            target_state=args.target_state,
            apply=args.apply,
            confirm=args.confirm,
            known_hosts=args.known_hosts,
            ssh_identity=args.ssh_identity,
        )
        sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        return 0 if result["status"] in SUCCESS_STATUSES else 2
    except NginxCoordinatorError as exc:
        error = {
            "status": "blocked",
            "error": str(exc),
            "error_class": type(exc).__name__,
            "active_configuration_mutated": False,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
        }
        sys.stdout.buffer.write(canonical_json_bytes(error) + b"\n")
        return 2
    except Exception:
        error = {
            "status": "blocked",
            "error": "production Nginx coordination failed closed",
            "error_class": "NginxCoordinatorError",
            "active_configuration_mutated": False,
            "current_mutated": False,
            "container_mutated": False,
            "volume_mutated": False,
            "data_mutated": False,
        }
        sys.stdout.buffer.write(canonical_json_bytes(error) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
