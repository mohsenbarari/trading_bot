#!/usr/bin/env python3
"""Orchestrate the three-role frozen-final production-shadow restore.

The controller owns the Nginx coordinator flock for the complete operation.
Hosts receive only a bounded control request.  Every mutation boundary asks
the controller for a fresh, unpredictable proof that the exact live lease is
still held.  WebApp-IR payloads must already have arrived through an
age-encrypted, private, versioned Arvan object and an exact-VersionId readback;
this program never carries those payloads over SSH.

The CLI is plan-only by default.  Apply is exposed through the Python API so a
caller must provide the operation-specific payload-preparation hook before a
live lease is acquired or resumed.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import select
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Protocol, Sequence
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    install_production_shadow_frozen_final_restore_inputs as INSTALLER,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as WORKER,
)
from scripts import production_shadow_finland_stage as FINLAND_STAGE  # noqa: E402
PLAN_SCHEMA = "production-shadow-frozen-final-restore-plan-v1"
HOST_REQUEST_SCHEMA = (
    "production-shadow-frozen-final-restore-host-request-v1"
)
HOST_RESULT_SCHEMA = "production-shadow-frozen-final-restore-host-result-v1"
CHALLENGE_SCHEMA = (
    "production-shadow-frozen-final-live-authority-challenge-v1"
)
RESPONSE_SCHEMA = (
    "production-shadow-frozen-final-live-authority-response-v1"
)
TRANSCRIPT_ENTRY_SCHEMA = (
    "production-shadow-frozen-final-live-authority-transcript-entry-v1"
)
COMPLETION_SCHEMA = (
    "production-shadow-frozen-final-restore-completion-v1"
)
POST_CONSUMPTION_SCHEMA = (
    "production-shadow-frozen-final-restore-consumption-receipt-v1"
)
JOURNAL_SCHEMA = (
    "production-shadow-frozen-final-restore-controller-journal-v1"
)
JOURNAL_EVENT_SCHEMA = (
    "production-shadow-frozen-final-restore-controller-event-v1"
)
WA_CONTROL_RECEIPT_SCHEMA = (
    "production-shadow-frozen-final-wa-control-transfer-receipt-v1"
)

ROLES = WORKER.ROLE_NAMES
ROLE_HOSTS = {
    "bot_fi": "65.109.216.187",
    "webapp_fi": "65.109.220.59",
    "webapp_ir": "95.38.164.29",
}
ROLE_PORTS = {
    "bot_fi": None,
    "webapp_fi": 37067,
    "webapp_ir": 22,
}
ROLE_TRANSPORTS = {
    "bot_fi": "host-local-create-only",
    "webapp_fi": "ssh-control",
    "webapp_ir": "arvan-private-versioned-age",
}

PYTHON = "/usr/bin/python3"
ENV = "/usr/bin/env"
SSH = "/usr/bin/ssh"
MAX_CONTROL_BYTES = 256 * 1024
MAX_HOST_RESULT_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_TRANSCRIPT_ENTRIES = 20_000
MAX_POST_RESULT_EXIT_SECONDS = 15.0
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,1024}$")
BOUNDARY_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,255}$")
SAFE_REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=+-]{1,4096}$")

AGENT_RELATIVE = Path(
    "scripts/orchestrate_production_shadow_frozen_final_restore.py"
)
INSTALLER_RELATIVE = Path(
    "scripts/install_production_shadow_frozen_final_restore_inputs.py"
)
WORKER_RELATIVE = Path(
    "scripts/production_shadow_frozen_final_restore_worker.py"
)

INPUT_PATH_FIELDS = frozenset(
    {
        "controller_manifest",
        "restore_set",
        "role_material",
        "database_backup",
        "uploads_archive",
        "audit_archive",
        "canonical_compose",
        "worker",
        "execution_envelope",
        "fresh_live_lease_claim",
        "legacy_frozen_receipt",
        "webapp_ir_transport_manifest",
        "webapp_ir_readback_receipt",
        "webapp_ir_control_transfer_receipt",
    }
)
AUTHORITY_FIELDS = frozenset(
    {
        "claim_path",
        "claim_sha256",
        "claim_epoch",
        "claim_nonce",
        "legacy_frozen_receipt_path",
        "legacy_frozen_receipt_sha256",
    }
)
WA_VERSION_FIELDS = frozenset(
    {
        "provider",
        "private",
        "versioned",
        "encryption",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "readback_receipt_sha256",
        "exact_version_readback_verified",
        "payload_bytes_over_ssh",
        "presigned_url_persisted",
    }
)
WA_FRESH_CONTROL_FIELDS = frozenset(
    {
        *WA_VERSION_FIELDS,
        "publication_mode",
        "object_key_binding_sha256",
        "second_upload_performed",
    }
)
INSTALLATION_AUTHORITY_EVENT_FIELDS = frozenset(
    {
        "schema",
        "index",
        "boundary",
        "verification",
        "previous_event_sha256",
        "event_sha256",
    }
)
INSTALLATION_PUBLICATION_FIELDS = frozenset(
    {
        "controller-manifest",
        "restore-set",
        "canonical-compose",
        "role-compose",
        "environment",
        "database-backup",
        "uploads-archive",
        "audit-archive",
        "installer-receipt",
        "role-manifest",
    }
)
INSTALLATION_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "source_role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "role_manifest_sha256",
        "installer_receipt_sha256",
        "fresh_claim_sha256",
        "fresh_claim_epoch",
        "fresh_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "owner_action",
        "intended_outcome",
        "authority_verifications",
        "authority_verification_count",
        "authority_verification_tail_sha256",
        "authority_transcript_sha256",
        "publications",
        "worker_copied",
        "redis_restore_bytes",
        "network_io_performed",
        "docker_invoked",
        "object_storage_contacted",
        "service_mutated",
        "current_mutated",
        "legacy_mutated",
        "attestation_sha256",
    }
)
HOST_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "role",
        "expected_host",
        "expected_port",
        "transport",
        "release_root",
        "agent_path",
        "agent_sha256",
        "installer_path",
        "installer_sha256",
        "worker_path",
        "worker_sha256",
        "inputs",
        "authority",
        "wa_exact_version",
        "wa_fresh_control_exact_version",
        "payload_bytes_over_control",
        "pull_policy",
        "build_allowed",
        "app_services_allowed",
    }
)
CHALLENGE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "role",
        "claim_sha256",
        "claim_epoch",
        "claim_nonce",
        "legacy_frozen_receipt_sha256",
        "sequence",
        "boundary",
        "challenge_nonce",
        "previous_transcript_sha256",
    }
)
RESPONSE_FIELDS = frozenset(
    {
        *CHALLENGE_FIELDS,
        "schema",
        "status",
        "challenge_sha256",
        "response_nonce",
        "controller_lock_held",
        "controller_authoritative",
    }
)
TRANSCRIPT_ENTRY_FIELDS = frozenset(
    {
        "schema",
        "index",
        "challenge",
        "response",
        "verification",
        "previous_entry_sha256",
        "entry_sha256",
    }
)
FILE_READBACK_FIELDS = frozenset(
    {
        "path",
        "content_sha256",
        "bytes",
        "canonical_document_sha256",
        "newline_terminated",
        "read_from_held_descriptor",
        "document",
    }
)
HOST_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "source_role",
        "transport",
        "installation_attestation",
        "worker_return",
        "role_manifest",
        "installer_receipt",
        "journal_prefix_event_count",
        "journal_prefix_tail_sha256",
        "journal_prefix_completed_actions",
        "journal_prefix_active_action",
        "journal_events",
        "action_evidence",
        "restore_result",
        "authority_transcript",
        "authority_transcript_count",
        "authority_transcript_sha256",
        "authority_transcript_tail_sha256",
        "observed_host_ipv4",
        "expected_host_verified",
        "payload_bytes_over_ssh",
        "presigned_url_persisted",
        "pull_performed",
        "build_performed",
        "app_services_started",
        "redis_restored",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
    }
)
STABLE_ROLE_CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "source_role",
        "transport",
        "observed_host_ipv4",
        "role_manifest_sha256",
        "installer_receipt_sha256",
        "restore_result_sha256",
        "action_evidence_sha256",
        "database",
        "file_trees",
        "redis_restore_bytes",
        "redis_pristine",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "final_evidence_sha256",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
        "app_services_started",
    }
)
WORKER_RETURN_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "restore_set_sha256",
        "restore_generation_sha256",
        "installer_receipt_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "required_confirmation",
        "plan_only_default",
        "static_claim_authoritative",
        "controller_live_verifier_required",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
        "status",
        "runtime_mutated",
        "completed_actions",
        "action_evidence_sha256",
        "result",
        "result_sha256",
        "result_path",
        "result_publication",
        "bootstrap_authority_sha256",
        "completed_readback",
        "claim_consumed",
        "aggregate_three_role_receipt_required",
    }
)


class FrozenFinalRestoreOrchestratorError(RuntimeError):
    """The frozen-final restore could not be proven safe and exact."""


class ConsumptionAuditAbsent(FrozenFinalRestoreOrchestratorError):
    """The exact live claim has not yet been consumed."""


class InteractiveProcess(Protocol):
    stdin: BinaryIO
    stdout: BinaryIO

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the child and return its exit status."""

    def kill(self) -> None:
        """Stop the child."""


SessionFactory = Callable[[Sequence[str]], InteractiveProcess]
HostInvoker = Callable[
    [Mapping[str, Any], Callable[[Mapping[str, Any]], Mapping[str, Any]]],
    Mapping[str, Any],
]
RoleRequestPreparer = Callable[
    [str, Mapping[str, Any], Any], Mapping[str, Any]
]


@dataclass(frozen=True)
class DocumentReadback:
    path: Path
    content_sha256: str
    bytes: int
    canonical_document_sha256: str
    newline_terminated: bool
    document: Mapping[str, Any]

    def evidence(self) -> dict[str, Any]:
        return {
            "path": os.fspath(self.path),
            "content_sha256": self.content_sha256,
            "bytes": self.bytes,
            "canonical_document_sha256": (
                self.canonical_document_sha256
            ),
            "newline_terminated": self.newline_terminated,
            "read_from_held_descriptor": True,
            "document": _json_clone(self.document),
        }


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "document is not canonical JSON"
        ) from exc


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value).decode("ascii"))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant {token}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(document, dict) or canonical_json(document) != payload:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not canonical JSON"
        )
    return document


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not a canonical UUID"
        )
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not a canonical UUID"
        ) from exc
    if str(parsed) != value or parsed.version != 4:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not a canonical UUIDv4"
        )
    return value


def _path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not an absolute path"
        )
    result = Path(value)
    if (
        not result.is_absolute()
        or result != Path(os.path.abspath(value))
        or result.name in {"", ".", ".."}
        or ".." in result.parts
        or "\x00" in value
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is not an absolute canonical path"
        )
    return result


def _bounded_control(document: Mapping[str, Any], *, label: str) -> bytes:
    payload = canonical_json(document)
    if not 1 <= len(payload) <= MAX_CONTROL_BYTES:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} exceeds the bounded control channel"
        )
    lowered = payload.lower()
    forbidden = (
        b"https://",
        b"http://",
        b"x-amz-",
        b"aws4_request",
        b"signature=",
        b"credential=",
        b"-----begin private key-----",
        b"age-encryption.org/v1",
    )
    if any(marker in lowered for marker in forbidden):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} contains payload or ephemeral transport material"
        )
    return payload


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _assert_root_directory(
    descriptor: int,
    *,
    label: str,
    private: bool = False,
) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or (
            private
            and stat.S_IMODE(metadata.st_mode) != 0o700
        )
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} directory ancestry is unsafe"
        )
    return metadata


def _open_parent_no_follow(
    path: Path,
    *,
    label: str,
    private_parent: bool = False,
) -> tuple[int, str]:
    if (
        not path.is_absolute()
        or path != Path(os.path.abspath(path))
        or path.name in {"", ".", ".."}
        or ".." in path.parts
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} path is not canonical"
        )
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        _assert_root_directory(descriptor, label=f"{label} root")
        for component in path.parts[1:-1]:
            child = os.open(
                component,
                _directory_flags(),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
            _assert_root_directory(
                descriptor,
                label=f"{label} ancestor",
            )
        _assert_root_directory(
            descriptor,
            label=f"{label} parent",
            private=private_parent,
        )
        return descriptor, path.name
    except FrozenFinalRestoreOrchestratorError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} ancestry is unavailable or contains a symlink"
        ) from exc


def _read_document_file(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_DOCUMENT_BYTES,
    newline: bool | None = None,
    allowed_modes: frozenset[int] = frozenset({0o600, 0o644, 0o755}),
) -> DocumentReadback:
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label=label,
        )
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or not 1 <= before.st_size <= maximum
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} is not an exact root-owned regular file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        stable = (
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
                for field in stable
            )
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} changed while being read"
            )
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)
    terminated = payload.endswith(b"\n")
    if newline is not None and terminated is not newline:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} newline contract differs"
        )
    body = payload[:-1] if terminated else payload
    document = strict_json(body, label=label)
    return DocumentReadback(
        path=path,
        content_sha256=_sha256(payload),
        bytes=len(payload),
        canonical_document_sha256=_sha256(body),
        newline_terminated=terminated,
        document=document,
    )


def _verify_release_file(path: Path, expected_sha256: str, *, label: str) -> None:
    expected_sha256 = _nonzero_sha256(expected_sha256, label=label)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o644, 0o755}
            or not 1 <= before.st_size <= 16 * 1024 * 1024
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} is not an immutable release file"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            digest.hexdigest() != expected_sha256
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} identity differs"
            )
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _expected_release_paths(
    operation_id: str,
    release_sha: str,
) -> dict[str, Path]:
    release_root = (
        WORKER.PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
    )
    return {
        "release_root": release_root,
        "agent_path": release_root / AGENT_RELATIVE,
        "installer_path": release_root / INSTALLER_RELATIVE,
        "worker_path": release_root / WORKER_RELATIVE,
    }


def validate_wa_exact_version(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != WA_VERSION_FIELDS
        or value["provider"] != "arvan-s3"
        or value["private"] is not True
        or value["versioned"] is not True
        or value["encryption"] != "age"
        or not isinstance(value["object_key"], str)
        or not 1 <= len(value["object_key"]) <= 1024
        or value["object_key"].startswith("/")
        or ".." in Path(value["object_key"]).parts
        or not isinstance(value["version_id"], str)
        or VERSION_ID_RE.fullmatch(value["version_id"]) is None
        or value["exact_version_readback_verified"] is not True
        or value["payload_bytes_over_ssh"] is not False
        or value["presigned_url_persisted"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "WebApp-IR exact-VersionId transport binding differs"
        )
    _nonzero_sha256(
        value["ciphertext_sha256"],
        label="WebApp-IR ciphertext SHA-256",
    )
    _nonzero_sha256(
        value["readback_receipt_sha256"],
        label="WebApp-IR readback receipt SHA-256",
    )
    return _json_clone(value)


def validate_wa_fresh_control_version(
    value: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != WA_FRESH_CONTROL_FIELDS:
        raise FrozenFinalRestoreOrchestratorError(
            "WebApp-IR fresh-control exact-VersionId object fields differ"
        )
    base = validate_wa_exact_version(
        {field: value[field] for field in WA_VERSION_FIELDS}
    )
    authority = request.get("authority")
    if not isinstance(authority, dict):
        raise FrozenFinalRestoreOrchestratorError(
            "fresh-control object requires exact live authority"
        )
    basis = {
        "schema": "production-shadow-frozen-final-wa-control-object-key-v1",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "role": "webapp_ir",
        "claim_sha256": authority["claim_sha256"],
        "claim_epoch": authority["claim_epoch"],
    }
    binding = _sha256(canonical_json(basis))
    if (
        value["publication_mode"] != "create-if-absent"
        or value["object_key_binding_sha256"] != binding
        or not base["object_key"].endswith(f"/{binding}.age")
        or value["second_upload_performed"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "WebApp-IR fresh-control object is not deterministic and "
            "create-if-absent"
        )
    return _json_clone(value)


def validate_host_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != HOST_REQUEST_FIELDS
        or value["schema"] != HOST_REQUEST_SCHEMA
        or value["action"] not in {"plan", "apply"}
        or value["role"] not in ROLES
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host request fields are not exact"
        )
    document = _json_clone(value)
    role = document["role"]
    campaign_id = _uuid(document["campaign_id"], label="campaign ID")
    operation_id = _uuid(document["operation_id"], label="operation ID")
    if campaign_id == operation_id:
        raise FrozenFinalRestoreOrchestratorError(
            "campaign and operation identities must be distinct"
        )
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["expected_host"] != ROLE_HOSTS[role]
        or document["expected_port"] != ROLE_PORTS[role]
        or document["transport"] != ROLE_TRANSPORTS[role]
        or document["payload_bytes_over_control"] is not False
        or document["pull_policy"] != "never"
        or document["build_allowed"] is not False
        or document["app_services_allowed"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host request immutable identity differs"
        )
    for field in (
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "agent_sha256",
        "installer_sha256",
        "worker_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    expected_paths = _expected_release_paths(
        operation_id,
        document["release_sha"],
    )
    for field, expected in expected_paths.items():
        if _path(document[field], label=field) != expected:
            raise FrozenFinalRestoreOrchestratorError(
                f"host request {field} is not release-derived"
            )
    inputs = document["inputs"]
    if not isinstance(inputs, dict) or set(inputs) != INPUT_PATH_FIELDS:
        raise FrozenFinalRestoreOrchestratorError(
            "host request input path closure differs"
        )
    ir_only = {
        "webapp_ir_transport_manifest",
        "webapp_ir_readback_receipt",
        "webapp_ir_control_transfer_receipt",
    }
    for field, raw in inputs.items():
        if field in ir_only and raw is None:
            continue
        _path(raw, label=f"host input {field}")
    authority = document["authority"]
    if document["action"] == "plan":
        if authority is not None:
            raise FrozenFinalRestoreOrchestratorError(
                "plan request unexpectedly carries live authority"
            )
    elif not isinstance(authority, dict) or set(authority) != AUTHORITY_FIELDS:
        raise FrozenFinalRestoreOrchestratorError(
            "apply request lacks exact live authority"
        )
    if authority is not None:
        for field in ("claim_path", "legacy_frozen_receipt_path"):
            _path(authority[field], label=field)
        for field in (
            "claim_sha256",
            "claim_nonce",
            "legacy_frozen_receipt_sha256",
        ):
            _nonzero_sha256(authority[field], label=field)
        if (
            type(authority["claim_epoch"]) is not int
            or authority["claim_epoch"] < 1
            or inputs["fresh_live_lease_claim"] != authority["claim_path"]
            or inputs["legacy_frozen_receipt"]
            != authority["legacy_frozen_receipt_path"]
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "host request authority path or epoch differs"
            )
    if role == "webapp_ir":
        validate_wa_exact_version(document["wa_exact_version"])
        if any(
            inputs[field] is None
            for field in (
                "webapp_ir_transport_manifest",
                "webapp_ir_readback_receipt",
            )
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "WebApp-IR transport evidence paths are incomplete"
            )
        if document["action"] == "apply":
            validate_wa_fresh_control_version(
                document["wa_fresh_control_exact_version"],
                request=document,
            )
            if inputs["webapp_ir_control_transfer_receipt"] is None:
                raise FrozenFinalRestoreOrchestratorError(
                    "WebApp-IR fresh-control receipt path is absent"
                )
        elif (
            document["wa_fresh_control_exact_version"] is not None
            or inputs["webapp_ir_control_transfer_receipt"] is not None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "WebApp-IR plan carries premature fresh-control material"
            )
    elif (
        document["wa_exact_version"] is not None
        or document["wa_fresh_control_exact_version"] is not None
        or any(inputs[field] is not None for field in ir_only)
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "Finland role unexpectedly carries WebApp-IR transport evidence"
        )
    _bounded_control(document, label="host request")
    return document


def encode_host_request(document: Mapping[str, Any]) -> str:
    validated = validate_host_request(document)
    return base64.urlsafe_b64encode(
        _bounded_control(validated, label="host request")
    ).decode("ascii")


def decode_host_request(encoded: str) -> dict[str, Any]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded) <= (MAX_CONTROL_BYTES * 2)
        or re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded) is None
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "encoded host request is invalid"
        )
    try:
        payload = base64.b64decode(
            encoded,
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "encoded host request is invalid"
        ) from exc
    return validate_host_request(
        strict_json(payload, label="host request")
    )


def _lease_values(
    lease: Any,
    *,
    expected_claim_sha256: str | None = None,
) -> tuple[str, int, str, str]:
    document = (
        lease.document
        if hasattr(lease, "document")
        else lease
    )
    if not isinstance(document, Mapping):
        raise FrozenFinalRestoreOrchestratorError(
            "live lease callback binding is invalid"
        )
    claim_sha256 = getattr(lease, "sha256", None)
    epoch = getattr(lease, "epoch", None)
    nonce = getattr(lease, "nonce", None)
    receipt_sha256 = getattr(lease, "receipt_sha256", None)
    if claim_sha256 is None:
        claim_sha256 = (
            document.get("_claim_sha256")
            if expected_claim_sha256 is None
            else expected_claim_sha256
        )
    if epoch is None:
        epoch = document.get("claim_epoch")
    if nonce is None:
        nonce = document.get("nonce")
    if receipt_sha256 is None:
        receipt_sha256 = document.get("legacy_frozen_receipt_sha256")
    return (
        _nonzero_sha256(claim_sha256, label="callback claim SHA-256"),
        epoch,
        _nonzero_sha256(nonce, label="callback claim nonce"),
        _nonzero_sha256(
            receipt_sha256,
            label="callback frozen receipt SHA-256",
        ),
    )


def _entry_sha256(entry: Mapping[str, Any]) -> str:
    unsigned = dict(entry)
    unsigned["entry_sha256"] = ZERO_SHA256
    return _sha256(canonical_json(unsigned))


class HostAuthorityProtocol:
    """Host-created challenges with controller-held lease responses."""

    def __init__(
        self,
        *,
        request: Mapping[str, Any],
        exchange: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self.request = validate_host_request(request)
        if self.request["action"] != "apply":
            raise FrozenFinalRestoreOrchestratorError(
                "live authority protocol requires an apply request"
            )
        self.exchange = exchange
        self.nonce_factory = nonce_factory or (lambda: secrets.token_hex(32))
        self.sequence = 0
        self.tail_sha256 = ZERO_SHA256
        self.transcript: list[dict[str, Any]] = []

    def verify(self, lease: Any, boundary: str) -> dict[str, Any]:
        authority = self.request["authority"]
        assert isinstance(authority, dict)
        claim_sha256, epoch, nonce, receipt_sha256 = _lease_values(
            lease,
            expected_claim_sha256=authority["claim_sha256"],
        )
        if (
            claim_sha256 != authority["claim_sha256"]
            or epoch != authority["claim_epoch"]
            or nonce != authority["claim_nonce"]
            or receipt_sha256
            != authority["legacy_frozen_receipt_sha256"]
            or not isinstance(boundary, str)
            or BOUNDARY_RE.fullmatch(boundary) is None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "host callback lease or boundary differs"
            )
        challenge_nonce = self.nonce_factory()
        _nonzero_sha256(
            challenge_nonce,
            label="live authority challenge nonce",
        )
        self.sequence += 1
        challenge = {
            "schema": CHALLENGE_SCHEMA,
            "status": "controller-response-required",
            "operation_id": self.request["operation_id"],
            "release_sha": self.request["release_sha"],
            "role": self.request["role"],
            "claim_sha256": claim_sha256,
            "claim_epoch": epoch,
            "claim_nonce": nonce,
            "legacy_frozen_receipt_sha256": receipt_sha256,
            "sequence": self.sequence,
            "boundary": boundary,
            "challenge_nonce": challenge_nonce,
            "previous_transcript_sha256": self.tail_sha256,
        }
        response = _json_clone(self.exchange(challenge))
        expected = {
            **challenge,
            "schema": RESPONSE_SCHEMA,
            "status": "controller-flock-verified",
            "challenge_sha256": _sha256(canonical_json(challenge)),
            "controller_lock_held": True,
            "controller_authoritative": True,
        }
        if (
            set(response) != RESPONSE_FIELDS
            or any(response.get(key) != value for key, value in expected.items())
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller live-authority response differs"
            )
        response_nonce = _nonzero_sha256(
            response["response_nonce"],
            label="controller response nonce",
        )
        verification = {
            "schema": WORKER.LIVE_AUTHORITY_SCHEMA,
            "status": "verified-live",
            "boundary": boundary,
            "claim_sha256": claim_sha256,
            "claim_epoch": epoch,
            "claim_nonce": nonce,
            "legacy_frozen_receipt_sha256": receipt_sha256,
            "controller_lock_held": True,
            "controller_authoritative": True,
            "verification_sequence": self.sequence,
            "verification_nonce": response_nonce,
        }
        entry = {
            "schema": TRANSCRIPT_ENTRY_SCHEMA,
            "index": self.sequence,
            "challenge": challenge,
            "response": response,
            "verification": verification,
            "previous_entry_sha256": self.tail_sha256,
            "entry_sha256": ZERO_SHA256,
        }
        entry["entry_sha256"] = _entry_sha256(entry)
        self.tail_sha256 = entry["entry_sha256"]
        self.transcript.append(entry)
        return _json_clone(verification)

    def evidence(self) -> dict[str, Any]:
        return {
            "authority_transcript": _json_clone(self.transcript),
            "authority_transcript_count": len(self.transcript),
            "authority_transcript_sha256": _sha256(
                canonical_json(self.transcript)
            ),
            "authority_transcript_tail_sha256": self.tail_sha256,
        }


def controller_authority_response(
    challenge: Mapping[str, Any],
    *,
    lease: Any,
    operation_id: str,
    release_sha: str,
    role: str,
    expected_previous: str,
    expected_sequence: int,
    response_nonce: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(challenge, Mapping)
        or set(challenge) != CHALLENGE_FIELDS
        or challenge["schema"] != CHALLENGE_SCHEMA
        or challenge["status"] != "controller-response-required"
        or challenge["operation_id"] != operation_id
        or challenge["release_sha"] != release_sha
        or challenge["role"] != role
        or challenge["sequence"] != expected_sequence
        or challenge["previous_transcript_sha256"] != expected_previous
        or not isinstance(challenge["boundary"], str)
        or BOUNDARY_RE.fullmatch(challenge["boundary"]) is None
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host live-authority challenge differs"
        )
    claim_sha256, epoch, nonce, receipt_sha256 = _lease_values_for_controller(
        lease
    )
    if (
        challenge["claim_sha256"] != claim_sha256
        or challenge["claim_epoch"] != epoch
        or challenge["claim_nonce"] != nonce
        or challenge["legacy_frozen_receipt_sha256"] != receipt_sha256
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host challenge is bound to a stale lease"
        )
    _nonzero_sha256(
        challenge["challenge_nonce"],
        label="host challenge nonce",
    )
    observation = lease.verify()
    if (
        not isinstance(observation, Mapping)
        or observation.get("controller_lock_authority_observed") is not True
        or observation.get("phase") not in {
            "legacy-frozen",
            "legacy-frozen-ready",
        }
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "controller lease did not re-prove its frozen flock"
        )
    generated = response_nonce or secrets.token_hex(32)
    _nonzero_sha256(generated, label="controller response nonce")
    return {
        **challenge,
        "schema": RESPONSE_SCHEMA,
        "status": "controller-flock-verified",
        "challenge_sha256": _sha256(canonical_json(challenge)),
        "response_nonce": generated,
        "controller_lock_held": True,
        "controller_authoritative": True,
    }


def _lease_values_for_controller(lease: Any) -> tuple[str, int, str, str]:
    claim = lease.claim
    if not isinstance(claim, Mapping):
        raise FrozenFinalRestoreOrchestratorError(
            "controller lease claim is invalid"
        )
    return (
        _nonzero_sha256(
            lease.claim_sha256,
            label="controller claim SHA-256",
        ),
        claim["claim_epoch"],
        _nonzero_sha256(claim["nonce"], label="controller claim nonce"),
        _nonzero_sha256(
            claim["legacy_frozen_receipt_sha256"],
            label="controller frozen receipt SHA-256",
        ),
    )


def _validate_exact_controller_live_lease(
    lease: Any,
    *,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
) -> None:
    if not isinstance(lease, NGINX.CoordinatorLiveLease):
        raise FrozenFinalRestoreOrchestratorError(
            "apply requires an exact Nginx CoordinatorLiveLease"
        )
    claim = lease.claim
    claim_sha256, epoch, nonce, receipt_sha256 = (
        _lease_values_for_controller(lease)
    )
    claim_path = getattr(lease, "claim_path", None)
    consumed = getattr(lease, "consumed", None)
    expected_coordinator_root = (
        NGINX.CONTROLLER_SECRET_PREFIX
        / operation_id
        / "nginx-coordinator"
    )
    if (
        not isinstance(claim, Mapping)
        or claim.get("schema") != NGINX.LIVE_LEASE_CLAIM_SCHEMA
        or claim.get("status") != "active"
        or claim.get("owner_action") != WORKER.LIVE_LEASE_OWNER_ACTION
        or claim.get("operation_id") != operation_id
        or claim.get("release_sha") != release_sha
        or claim.get("release_tree_sha") != release_tree_sha
        or claim.get("claim_epoch") != epoch
        or claim.get("nonce") != nonce
        or claim.get("legacy_frozen_receipt_sha256") != receipt_sha256
        or claim.get("controller_lock_path")
        != os.fspath(expected_coordinator_root / "coordinator.lock")
        or claim.get("controller_authoritative") is not True
        or claim.get("remote_copy_authoritative") is not False
        or claim.get("automatic_expiry_allowed") is not False
        or claim.get("reconciliation_required_after_crash") is not True
        or consumed is not False
        or not isinstance(claim_path, Path)
        or claim_path
        != expected_coordinator_root
        / "live-leases"
        / "claims"
        / f"{claim_sha256}.json"
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "Nginx CoordinatorLiveLease owner or identity differs"
        )


class ControllerAuthoritySession:
    """Controller-owned ordering state for one host control connection."""

    def __init__(
        self,
        *,
        lease: Any,
        request: Mapping[str, Any],
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self.lease = lease
        self.request = validate_host_request(request)
        _validate_exact_controller_live_lease(
            lease,
            operation_id=self.request["operation_id"],
            release_sha=self.request["release_sha"],
            release_tree_sha=self.request["release_tree_sha"],
        )
        self.nonce_factory = nonce_factory
        self.sequence = 0
        self.tail_sha256 = ZERO_SHA256
        self.seen_challenge_nonces: set[str] = set()
        self.transcript: list[dict[str, Any]] = []

    def respond(self, challenge: Mapping[str, Any]) -> dict[str, Any]:
        candidate_nonce = challenge.get("challenge_nonce")
        if (
            not isinstance(candidate_nonce, str)
            or candidate_nonce in self.seen_challenge_nonces
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "host challenge nonce was replayed"
            )
        response = controller_authority_response(
            challenge,
            lease=self.lease,
            operation_id=self.request["operation_id"],
            release_sha=self.request["release_sha"],
            role=self.request["role"],
            expected_previous=self.tail_sha256,
            expected_sequence=self.sequence + 1,
            response_nonce=(
                self.nonce_factory()
                if self.nonce_factory is not None
                else None
            ),
        )
        self.sequence += 1
        verification = {
            "schema": WORKER.LIVE_AUTHORITY_SCHEMA,
            "status": "verified-live",
            "boundary": challenge["boundary"],
            "claim_sha256": challenge["claim_sha256"],
            "claim_epoch": challenge["claim_epoch"],
            "claim_nonce": challenge["claim_nonce"],
            "legacy_frozen_receipt_sha256": challenge[
                "legacy_frozen_receipt_sha256"
            ],
            "controller_lock_held": True,
            "controller_authoritative": True,
            "verification_sequence": self.sequence,
            "verification_nonce": response["response_nonce"],
        }
        entry = {
            "schema": TRANSCRIPT_ENTRY_SCHEMA,
            "index": self.sequence,
            "challenge": _json_clone(challenge),
            "response": response,
            "verification": verification,
            "previous_entry_sha256": self.tail_sha256,
            "entry_sha256": ZERO_SHA256,
        }
        entry["entry_sha256"] = _entry_sha256(entry)
        self.tail_sha256 = entry["entry_sha256"]
        self.seen_challenge_nonces.add(candidate_nonce)
        self.transcript.append(entry)
        return response


class DeadlineLineReader:
    """Incremental newline framing with a real absolute descriptor deadline."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.buffer = bytearray()
        try:
            self.descriptor: int | None = stream.fileno()
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            self.descriptor = None

    @property
    def has_buffered(self) -> bool:
        return bool(self.buffer)

    def read_line(self, *, maximum: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[: newline + 1])
                del self.buffer[: newline + 1]
                return line
            if len(self.buffer) > maximum:
                raise FrozenFinalRestoreOrchestratorError(
                    "control line is oversized"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenFinalRestoreOrchestratorError(
                    "control line timed out before newline"
                )
            if self.descriptor is None:
                # In-memory streams are used only by unit tests and cannot
                # block independently of the current process.
                chunk = self.stream.read(maximum + 1 - len(self.buffer))
            else:
                readable, _, _ = select.select(
                    [self.descriptor],
                    [],
                    [],
                    remaining,
                )
                if not readable:
                    raise FrozenFinalRestoreOrchestratorError(
                        "control line timed out before newline"
                    )
                try:
                    chunk = os.read(
                        self.descriptor,
                        min(65536, maximum + 1 - len(self.buffer)),
                    )
                except BlockingIOError:
                    continue
            if not chunk:
                return b""
            self.buffer.extend(chunk)


class StdioAuthorityExchange:
    """Host-side, one-challenge-at-a-time exchange over the control stream."""

    def __init__(
        self,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        *,
        timeout: float = 120.0,
        line_reader: DeadlineLineReader | None = None,
    ) -> None:
        self.input = input_stream
        self.output = output_stream
        self.timeout = timeout
        self.reader = line_reader or DeadlineLineReader(input_stream)

    def __call__(self, challenge: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            descriptor = self.reader.descriptor
            readable = []
            if descriptor is not None:
                readable, _, _ = select.select([descriptor], [], [], 0)
            if self.reader.has_buffered or readable:
                raise FrozenFinalRestoreOrchestratorError(
                    "prebuffered live-authority responses are forbidden"
                )
            self.output.write(canonical_json(challenge) + b"\n")
            self.output.flush()
            raw = self.reader.read_line(
                maximum=MAX_CONTROL_BYTES,
                timeout=self.timeout,
            )
        except FrozenFinalRestoreOrchestratorError:
            raise
        except (OSError, ValueError) as exc:
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority stdio failed"
            ) from exc
        if (
            not raw
            or len(raw) > MAX_CONTROL_BYTES + 1
            or not raw.endswith(b"\n")
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller live-authority response is missing or oversized"
            )
        return strict_json(raw[:-1], label="live-authority response")


def _installer_kwargs(request: Mapping[str, Any]) -> dict[str, Any]:
    inputs = request["inputs"]
    return {
        "controller_manifest": Path(inputs["controller_manifest"]),
        "restore_set": Path(inputs["restore_set"]),
        "role_material": Path(inputs["role_material"]),
        "database_backup": Path(inputs["database_backup"]),
        "uploads_archive": Path(inputs["uploads_archive"]),
        "audit_archive": Path(inputs["audit_archive"]),
        "canonical_compose": Path(inputs["canonical_compose"]),
        "worker": Path(inputs["worker"]),
        "expected_role": request["role"],
        "webapp_ir_transport_manifest": (
            Path(inputs["webapp_ir_transport_manifest"])
            if inputs["webapp_ir_transport_manifest"] is not None
            else None
        ),
        "webapp_ir_readback_receipt": (
            Path(inputs["webapp_ir_readback_receipt"])
            if inputs["webapp_ir_readback_receipt"] is not None
            else None
        ),
    }


def _wa_control_receipt(request: Mapping[str, Any]) -> DocumentReadback | None:
    if request["role"] != "webapp_ir":
        return None
    path = Path(
        request["inputs"]["webapp_ir_control_transfer_receipt"]
    )
    readback = _read_document_file(
        path,
        label="WebApp-IR fresh-control transfer receipt",
        newline=None,
    )
    expected = request["wa_fresh_control_exact_version"]
    document = readback.document
    authority = request["authority"]
    if (
        set(document)
        != {
            "schema",
            "status",
            "operation_id",
            "role",
            "provider",
            "private",
            "versioned",
            "encryption",
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "readback_receipt_sha256",
            "exact_version_readback_verified",
            "installed_claim_sha256",
            "installed_receipt_sha256",
            "installed_envelope_sha256",
            "payload_bytes_over_ssh",
            "presigned_url_persisted",
            "object_storage_object_overwritten",
            "object_storage_object_deleted",
            "publication_mode",
            "object_key_binding_sha256",
            "second_upload_performed",
        }
        or document["schema"] != WA_CONTROL_RECEIPT_SCHEMA
        or document["status"] != "installed-and-verified"
        or document["operation_id"] != request["operation_id"]
        or document["role"] != "webapp_ir"
        or any(
            document[field] != expected[field]
            for field in (
                "provider",
                "private",
                "versioned",
                "encryption",
                "object_key",
                "version_id",
                "ciphertext_sha256",
                "readback_receipt_sha256",
                "exact_version_readback_verified",
                "payload_bytes_over_ssh",
                "presigned_url_persisted",
                "publication_mode",
                "object_key_binding_sha256",
                "second_upload_performed",
            )
        )
        or document["installed_claim_sha256"]
        != authority["claim_sha256"]
        or document["installed_receipt_sha256"]
        != authority["legacy_frozen_receipt_sha256"]
        or document["payload_bytes_over_ssh"] is not False
        or document["presigned_url_persisted"] is not False
        or document["object_storage_object_overwritten"] is not False
        or document["object_storage_object_deleted"] is not False
        or document["publication_mode"] != "create-if-absent"
        or document["second_upload_performed"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "WebApp-IR fresh-control exact-VersionId receipt differs"
        )
    _nonzero_sha256(
        document["installed_envelope_sha256"],
        label="installed execution envelope SHA-256",
    )
    envelope = _read_document_file(
        Path(request["inputs"]["execution_envelope"]),
        label="installed WebApp-IR execution envelope",
        newline=None,
    )
    if (
        envelope.canonical_document_sha256
        != document["installed_envelope_sha256"]
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "WebApp-IR control receipt does not bind the installed envelope"
        )
    return readback


def _collect_worker_closure(
    manifest: Any,
    lease: Any,
    worker_return: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    events, completed, active, evidence = WORKER._read_events(  # noqa: SLF001
        manifest,
        lease,
    )
    if completed != list(WORKER.ACTIONS) or active is not None:
        raise FrozenFinalRestoreOrchestratorError(
            "worker journal is not durably complete"
        )
    action_evidence: dict[str, dict[str, Any]] = {}
    for action in WORKER.ACTIONS:
        document = WORKER._load_action_evidence(  # noqa: SLF001
            manifest,
            lease,
            action=action,
            digest=evidence[action],
        )
        path = (
            manifest.paths.evidence
            / f"{action}-{evidence[action]}.json"
        )
        action_evidence[action] = _read_document_file(
            path,
            label=f"{action} worker evidence",
            newline=True,
        ).evidence()
        if action_evidence[action]["document"] != document:
            raise FrozenFinalRestoreOrchestratorError(
                f"{action} evidence changed after worker validation"
            )
    result_path = Path(worker_return["result_path"])
    result = _read_document_file(
        result_path,
        label="frozen-final restore result",
        newline=True,
    ).evidence()
    return _json_clone(events), action_evidence, result


def execute_host_request(
    request_value: Mapping[str, Any],
    *,
    exchange: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    installer_module: Any = INSTALLER,
    worker_module: Any = WORKER,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    request = validate_host_request(request_value)
    if os.geteuid() != 0 or os.getegid() != 0:
        raise FrozenFinalRestoreOrchestratorError(
            "frozen-final host agent requires root:root"
        )
    if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1":
        raise FrozenFinalRestoreOrchestratorError(
            "immutable release execution requires PYTHONDONTWRITEBYTECODE=1"
        )
    addresses = (
        FINLAND_STAGE.observe_local_ipv4_addresses()
        if observed_host_addresses is None
        else set(observed_host_addresses)
    )
    if (
        not addresses
        or request["expected_host"] not in addresses
        or any(
            not isinstance(address, str)
            or re.fullmatch(
                r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}",
                address,
            )
            is None
            for address in addresses
        )
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "local IPv4 identity differs from the requested role host"
        )
    observed_ipv4 = sorted(addresses)
    for path_field, hash_field, label in (
        ("agent_path", "agent_sha256", "restore orchestrator"),
        ("installer_path", "installer_sha256", "restore installer"),
        ("worker_path", "worker_sha256", "restore worker"),
    ):
        _verify_release_file(
            Path(request[path_field]),
            request[hash_field],
            label=label,
        )
    if Path(__file__).resolve() != Path(request["agent_path"]):
        raise FrozenFinalRestoreOrchestratorError(
            "host agent is not running from the exact immutable release"
        )
    wa_receipt = _wa_control_receipt(request)
    kwargs = _installer_kwargs(request)
    plan = installer_module.preflight_installation(**kwargs)
    if (
        plan.role != request["role"]
        or plan.controller["campaign_id"] != request["campaign_id"]
        or plan.controller["operation_id"] != request["operation_id"]
        or plan.controller["release_sha"] != request["release_sha"]
        or plan.controller["release_tree_sha"]
        != request["release_tree_sha"]
        or plan.controller_sha256
        != request["controller_manifest_sha256"]
        or plan.restore_set_sha256 != request["restore_set_sha256"]
        or plan.restore_set["restore_generation_sha256"]
        != request["restore_generation_sha256"]
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host installer plan differs from the controller request"
        )
    if request["action"] == "plan":
        return {
            "schema": HOST_RESULT_SCHEMA,
            "status": "planned",
            "operation_id": request["operation_id"],
            "role": request["role"],
            "release_sha": request["release_sha"],
            "release_tree_sha": request["release_tree_sha"],
            "controller_manifest_sha256": (
                request["controller_manifest_sha256"]
            ),
            "restore_set_sha256": request["restore_set_sha256"],
            "restore_generation_sha256": (
                request["restore_generation_sha256"]
            ),
            "source_role": plan.source_role,
            "transport": _json_clone(plan.transport_summary),
            "runtime_mutated": False,
            "required_confirmation": (
                installer_module.confirmation_phrase(plan)
            ),
            "observed_host_ipv4": observed_ipv4,
            "expected_host_verified": True,
            "payload_bytes_over_ssh": False,
            "presigned_url_persisted": False,
            "pull_performed": False,
            "build_performed": False,
            "app_services_started": False,
            "redis_restored": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "object_storage_mutated": False,
        }
    if exchange is None:
        raise FrozenFinalRestoreOrchestratorError(
            "apply requires an interactive live-authority exchange"
        )
    protocol = HostAuthorityProtocol(request=request, exchange=exchange)
    authority = request["authority"]
    assert isinstance(authority, dict)
    installation = installer_module.execute_installation(
        **kwargs,
        apply=True,
        confirm=installer_module.confirmation_phrase(plan),
        execution_envelope=Path(
            request["inputs"]["execution_envelope"]
        ),
        fresh_live_lease_claim=Path(authority["claim_path"]),
        legacy_frozen_receipt=Path(
            authority["legacy_frozen_receipt_path"]
        ),
        live_authority_verifier=protocol.verify,
    )
    role_manifest_path = (
        plan.paths.secret_generation_root / "restore-role-manifest.json"
    )
    role_manifest = worker_module.load_role_manifest(role_manifest_path)
    lease = worker_module.load_live_lease(
        manifest=role_manifest,
        claim_path=Path(authority["claim_path"]),
        claim_sha256=authority["claim_sha256"],
        claim_epoch=authority["claim_epoch"],
        receipt_path=Path(authority["legacy_frozen_receipt_path"]),
    )
    prefix_events, prefix_completed, prefix_active, _prefix_evidence = (
        worker_module._read_events(  # noqa: SLF001
            role_manifest,
            lease,
        )
    )
    prefix_tail = (
        prefix_events[-1]["event_sha256"] if prefix_events else ZERO_SHA256
    )
    worker_return = worker_module.execute(
        role_manifest_path=role_manifest_path,
        live_lease_claim_path=Path(authority["claim_path"]),
        live_lease_claim_sha256=authority["claim_sha256"],
        live_lease_claim_epoch=authority["claim_epoch"],
        legacy_frozen_receipt_path=Path(
            authority["legacy_frozen_receipt_path"]
        ),
        apply=True,
        confirm=worker_module.confirmation_phrase(role_manifest, lease),
        authority_verifier=protocol.verify,
    )
    protocol.verify(
        lease,
        f"after:{request['role']}:host-result-readback",
    )
    manifest_readback = _read_document_file(
        role_manifest_path,
        label="installed role manifest",
        newline=False,
    )
    receipt_readback = _read_document_file(
        Path(role_manifest.document["installer_receipt_path"]),
        label="installed installer receipt",
        newline=False,
    )
    events, evidence, result_readback = _collect_worker_closure(
        role_manifest,
        lease,
        worker_return,
    )
    transcript = protocol.evidence()
    result = {
        "schema": HOST_RESULT_SCHEMA,
        "status": "restored-and-read-back",
        "operation_id": request["operation_id"],
        "role": request["role"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "controller_manifest_sha256": request[
            "controller_manifest_sha256"
        ],
        "restore_set_sha256": request["restore_set_sha256"],
        "restore_generation_sha256": request[
            "restore_generation_sha256"
        ],
        "source_role": plan.source_role,
        "transport": {
            **_json_clone(plan.transport_summary),
            "fresh_control_exact_version": (
                _json_clone(request["wa_fresh_control_exact_version"])
                if request["role"] == "webapp_ir"
                else None
            ),
            "fresh_control_transfer_receipt_sha256": (
                wa_receipt.canonical_document_sha256
                if wa_receipt is not None
                else None
            ),
        },
        "installation_attestation": _json_clone(installation),
        "worker_return": _json_clone(worker_return),
        "role_manifest": manifest_readback.evidence(),
        "installer_receipt": receipt_readback.evidence(),
        "journal_prefix_event_count": len(prefix_events),
        "journal_prefix_tail_sha256": prefix_tail,
        "journal_prefix_completed_actions": list(prefix_completed),
        "journal_prefix_active_action": prefix_active,
        "journal_events": events,
        "action_evidence": evidence,
        "restore_result": result_readback,
        **transcript,
        "observed_host_ipv4": observed_ipv4,
        "expected_host_verified": True,
        "payload_bytes_over_ssh": False,
        "presigned_url_persisted": False,
        "pull_performed": False,
        "build_performed": False,
        "app_services_started": False,
        "redis_restored": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
    }
    validate_host_result(result, request=request)
    return result


def _validate_readback(
    value: Any,
    *,
    label: str,
    newline: bool,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != FILE_READBACK_FIELDS
        or value["newline_terminated"] is not newline
        or value["read_from_held_descriptor"] is not True
        or not isinstance(value["document"], dict)
        or type(value["bytes"]) is not int
        or value["bytes"] < 1
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} readback fields differ"
        )
    _path(value["path"], label=f"{label} path")
    body = canonical_json(value["document"])
    payload = body + (b"\n" if newline else b"")
    if (
        value["bytes"] != len(payload)
        or value["canonical_document_sha256"] != _sha256(body)
        or value["content_sha256"] != _sha256(payload)
    ):
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} actual-byte binding differs"
        )
    return _json_clone(value)


def validate_authority_transcript(
    value: Any,
    *,
    request: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_TRANSCRIPT_ENTRIES
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "live-authority transcript is empty or oversized"
        )
    authority = request["authority"]
    assert isinstance(authority, dict)
    previous = ZERO_SHA256
    seen_nonces: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value, 1):
        if (
            not isinstance(raw, dict)
            or set(raw) != TRANSCRIPT_ENTRY_FIELDS
            or raw["schema"] != TRANSCRIPT_ENTRY_SCHEMA
            or raw["index"] != index
            or raw["previous_entry_sha256"] != previous
            or raw["entry_sha256"] != _entry_sha256(raw)
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority transcript chain differs"
            )
        challenge = raw["challenge"]
        response = raw["response"]
        verification = raw["verification"]
        if (
            not isinstance(challenge, dict)
            or set(challenge) != CHALLENGE_FIELDS
            or challenge["schema"] != CHALLENGE_SCHEMA
            or challenge["status"] != "controller-response-required"
            or challenge["operation_id"] != request["operation_id"]
            or challenge["release_sha"] != request["release_sha"]
            or challenge["role"] != request["role"]
            or challenge["claim_sha256"] != authority["claim_sha256"]
            or challenge["claim_epoch"] != authority["claim_epoch"]
            or challenge["claim_nonce"] != authority["claim_nonce"]
            or challenge["legacy_frozen_receipt_sha256"]
            != authority["legacy_frozen_receipt_sha256"]
            or challenge["sequence"] != index
            or challenge["previous_transcript_sha256"] != previous
            or not isinstance(challenge["boundary"], str)
            or BOUNDARY_RE.fullmatch(challenge["boundary"]) is None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority transcript challenge differs"
            )
        challenge_nonce = _nonzero_sha256(
            challenge["challenge_nonce"],
            label="transcript challenge nonce",
        )
        if (
            not isinstance(response, dict)
            or set(response) != RESPONSE_FIELDS
            or response["schema"] != RESPONSE_SCHEMA
            or response["status"] != "controller-flock-verified"
            or any(
                response[field] != challenge[field]
                for field in CHALLENGE_FIELDS - {"schema", "status"}
            )
            or response["challenge_sha256"]
            != _sha256(canonical_json(challenge))
            or response["controller_lock_held"] is not True
            or response["controller_authoritative"] is not True
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority transcript response differs"
            )
        response_nonce = _nonzero_sha256(
            response["response_nonce"],
            label="transcript response nonce",
        )
        if challenge_nonce in seen_nonces or response_nonce in seen_nonces:
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority transcript nonce was replayed"
            )
        seen_nonces.update({challenge_nonce, response_nonce})
        expected_verification = {
            "schema": WORKER.LIVE_AUTHORITY_SCHEMA,
            "status": "verified-live",
            "boundary": challenge["boundary"],
            "claim_sha256": authority["claim_sha256"],
            "claim_epoch": authority["claim_epoch"],
            "claim_nonce": authority["claim_nonce"],
            "legacy_frozen_receipt_sha256": authority[
                "legacy_frozen_receipt_sha256"
            ],
            "controller_lock_held": True,
            "controller_authoritative": True,
            "verification_sequence": index,
            "verification_nonce": response_nonce,
        }
        if verification != expected_verification:
            raise FrozenFinalRestoreOrchestratorError(
                "live-authority transcript verification differs"
            )
        previous = raw["entry_sha256"]
        result.append(_json_clone(raw))
    expected_last = f"after:{request['role']}:host-result-readback"
    if result[-1]["challenge"]["boundary"] != expected_last:
        raise FrozenFinalRestoreOrchestratorError(
            "live-authority transcript lacks final host readback"
        )
    return result, previous


def _validate_worker_evidence(
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    transcript: Sequence[Mapping[str, Any]],
) -> None:
    worker_return = result["worker_return"]
    authority = request["authority"]
    assert isinstance(authority, dict)
    required_confirmation = (
        "restore-production-shadow-frozen-final:"
        f"{request['operation_id']}:{request['role']}:"
        f"{request['restore_generation_sha256']}:"
        f"{authority['claim_sha256']}:{authority['claim_epoch']}"
    )
    if (
        not isinstance(worker_return, dict)
        or set(worker_return) != WORKER_RETURN_FIELDS
        or worker_return["schema"] != WORKER.RESULT_SCHEMA
        or worker_return["operation_id"] != request["operation_id"]
        or worker_return["role"] != request["role"]
        or worker_return["release_sha"] != request["release_sha"]
        or worker_return["restore_set_sha256"]
        != request["restore_set_sha256"]
        or worker_return["restore_generation_sha256"]
        != request["restore_generation_sha256"]
        or worker_return["installer_receipt_sha256"]
        != result["installer_receipt"]["canonical_document_sha256"]
        or worker_return["live_lease_claim_sha256"]
        != authority["claim_sha256"]
        or worker_return["live_lease_claim_epoch"]
        != authority["claim_epoch"]
        or worker_return["live_lease_claim_nonce"]
        != authority["claim_nonce"]
        or worker_return["legacy_frozen_receipt_sha256"]
        != authority["legacy_frozen_receipt_sha256"]
        or worker_return["required_confirmation"] != required_confirmation
        or worker_return["plan_only_default"] is not True
        or worker_return["static_claim_authoritative"] is not False
        or worker_return["controller_live_verifier_required"] is not True
        or worker_return["current_mutated"] is not False
        or worker_return["legacy_mutated"] is not False
        or worker_return["object_storage_mutated"] is not False
        or worker_return["status"] != "restored"
        or worker_return["runtime_mutated"] is not True
        or worker_return["completed_actions"] != list(WORKER.ACTIONS)
        or not isinstance(
            worker_return["action_evidence_sha256"],
            dict,
        )
        or set(worker_return["action_evidence_sha256"])
        != set(WORKER.ACTIONS)
        or worker_return["result"]
        != result["restore_result"]["document"]
        or worker_return["result_sha256"]
        != result["restore_result"]["canonical_document_sha256"]
        or worker_return["result_path"] != result["restore_result"]["path"]
        or worker_return["result_publication"]
        not in {"created", "reused"}
        or worker_return["claim_consumed"] is not False
        or worker_return["aggregate_three_role_receipt_required"] is not True
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "worker return differs from actual restore result bytes"
        )
    restore = result["restore_result"]["document"]
    if (
        set(restore) != WORKER.RESULT_FIELDS
        or restore["schema"] != WORKER.RESULT_SCHEMA
        or restore["status"] != "frozen-final-shadow-restored"
        or restore["operation_id"] != request["operation_id"]
        or restore["role"] != request["role"]
        or restore["release_sha"] != request["release_sha"]
        or restore["release_tree_sha"] != request["release_tree_sha"]
        or restore["controller_manifest_sha256"]
        != request["controller_manifest_sha256"]
        or restore["restore_set_sha256"]
        != request["restore_set_sha256"]
        or restore["restore_generation_sha256"]
        != request["restore_generation_sha256"]
        or restore["live_lease_claim_sha256"]
        != authority["claim_sha256"]
        or restore["live_lease_claim_epoch"]
        != authority["claim_epoch"]
        or restore["live_lease_claim_nonce"]
        != authority["claim_nonce"]
        or restore["legacy_frozen_receipt_sha256"]
        != authority["legacy_frozen_receipt_sha256"]
        or restore["redis_restore_bytes"] != 0
        or restore["redis_pristine"] is not True
        or restore["public_or_private_app_started"] is not False
        or restore["current_mutated"] is not False
        or restore["legacy_mutated"] is not False
        or restore["object_storage_mutated"] is not False
        or restore["nginx_state"] != "legacy-frozen"
        or restore["claim_consume_outcome"]
        != WORKER.LIVE_LEASE_SUCCESS_OUTCOME
        or restore["aggregate_three_role_receipt_required"] is not True
        or restore["claim_consumed_by_worker"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "actual restore result closure differs"
        )
    evidence = result["action_evidence"]
    if not isinstance(evidence, dict) or set(evidence) != set(WORKER.ACTIONS):
        raise FrozenFinalRestoreOrchestratorError(
            "worker action evidence closure differs"
        )
    evidence_documents: dict[str, dict[str, Any]] = {}
    for action in WORKER.ACTIONS:
        row = _validate_readback(
            evidence[action],
            label=f"{action} evidence",
            newline=True,
        )
        document = row["document"]
        if (
            row["canonical_document_sha256"]
            != worker_return["action_evidence_sha256"][action]
            or not isinstance(document, dict)
            or set(document) != WORKER.EVIDENCE_FIELDS
            or document["schema"] != WORKER.EVIDENCE_SCHEMA
            or document["status"] != "completed"
            or document["action"] != action
            or document["operation_id"] != request["operation_id"]
            or document["role"] != request["role"]
            or document["release_sha"] != request["release_sha"]
            or document["release_tree_sha"]
            != request["release_tree_sha"]
            or document["controller_manifest_sha256"]
            != request["controller_manifest_sha256"]
            or document["restore_set_sha256"]
            != request["restore_set_sha256"]
            or document["restore_generation_sha256"]
            != request["restore_generation_sha256"]
            or document["role_manifest_sha256"]
            != result["role_manifest"]["canonical_document_sha256"]
            or document["installer_receipt_sha256"]
            != result["installer_receipt"]["canonical_document_sha256"]
            or document["legacy_frozen_receipt_sha256"]
            != authority["legacy_frozen_receipt_sha256"]
            or document["live_lease_claim_sha256"]
            != authority["claim_sha256"]
            or document["live_lease_claim_epoch"]
            != authority["claim_epoch"]
            or document["live_lease_claim_nonce"]
            != authority["claim_nonce"]
            or document["business_write_allowed"] is not False
            or document["public_or_private_app_started"] is not False
            or document["redis_restored"] is not False
            or document["current_mutated"] is not False
            or document["legacy_mutated"] is not False
            or document["object_storage_mutated"] is not False
            or not isinstance(document["semantic"], dict)
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{action} evidence digest or identity differs"
            )
        evidence_documents[action] = document
    final = evidence["verify-final"]
    if (
        final["canonical_document_sha256"]
        != restore["final_evidence_sha256"]
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "restore result does not bind the actual final evidence"
        )
    bootstrap_boundary = f"before:{request['role']}:journal-bootstrap"
    bootstrap_indexes = [
        index
        for index, entry in enumerate(transcript)
        if entry["challenge"]["boundary"] == bootstrap_boundary
    ]
    if len(bootstrap_indexes) != 1:
        raise FrozenFinalRestoreOrchestratorError(
            "current authority session lacks one exact journal bootstrap"
        )
    worker_transcript = transcript[bootstrap_indexes[0] :]
    verification_digests = {
        _sha256(canonical_json(entry["verification"]))
        for entry in worker_transcript
    }
    events = result["journal_events"]
    prefix_count = result["journal_prefix_event_count"]
    prefix_tail = result["journal_prefix_tail_sha256"]
    if (
        not isinstance(events, list)
        or not events
        or type(prefix_count) is not int
        or not 0 <= prefix_count <= len(events)
        or (
            prefix_count == 0
            and prefix_tail != ZERO_SHA256
        )
        or (
            prefix_count > 0
            and (
                not isinstance(events[prefix_count - 1], dict)
                or events[prefix_count - 1].get("event_sha256")
                != prefix_tail
            )
        )
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "worker journal prefix or event closure differs"
        )
    previous = ZERO_SHA256
    completed: list[str] = []
    active: str | None = None
    attempts: dict[str, int] = {}
    prefix_completed: list[str] = []
    prefix_active: str | None = None
    current_action_boundaries: list[str] = []
    verification_by_digest = {
        _sha256(canonical_json(entry["verification"])): entry
        for entry in worker_transcript
    }
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event) != WORKER.JOURNAL_EVENT_FIELDS
            or event["schema"] != WORKER.JOURNAL_EVENT_SCHEMA
            or event["operation_id"] != request["operation_id"]
            or event["role"] != request["role"]
            or event["release_sha"] != request["release_sha"]
            or event["restore_set_sha256"]
            != request["restore_set_sha256"]
            or event["restore_generation_sha256"]
            != request["restore_generation_sha256"]
            or event["role_manifest_sha256"]
            != result["role_manifest"]["canonical_document_sha256"]
            or event["installer_receipt_sha256"]
            != result["installer_receipt"]["canonical_document_sha256"]
            or event["legacy_frozen_receipt_sha256"]
            != authority["legacy_frozen_receipt_sha256"]
            or event["live_lease_claim_path"] != authority["claim_path"]
            or event["live_lease_claim_sha256"]
            != authority["claim_sha256"]
            or event["live_lease_claim_epoch"]
            != authority["claim_epoch"]
            or event["live_lease_claim_nonce"]
            != authority["claim_nonce"]
            or event["index"] != index
            or event["previous_event_sha256"] != previous
            or event["event_sha256"]
            != WORKER._event_hash(event)  # noqa: SLF001
            or event["action"] not in WORKER.ACTIONS
            or event["kind"] not in {"started", "resumed", "completed"}
            or type(event["attempt"]) is not int
            or not 1 <= event["attempt"] <= 100
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "worker journal event chain differs"
            )
        action = event["action"]
        if event["kind"] == "started":
            if (
                active is not None
                or action != WORKER.ACTIONS[len(completed)]
                or event["attempt"] != 1
                or event["evidence_sha256"] is not None
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "worker journal start ordering differs"
                )
            active = action
            attempts[action] = 1
        elif event["kind"] == "resumed":
            if (
                active != action
                or event["attempt"] != attempts[action] + 1
                or event["evidence_sha256"] is not None
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "worker journal resume ordering differs"
                )
            attempts[action] = event["attempt"]
        else:
            if (
                active != action
                or event["attempt"] != attempts[action]
                or event["evidence_sha256"]
                != evidence[action]["canonical_document_sha256"]
                or event["authority_verification_sha256"] is not None
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "worker journal completion binding differs"
                )
            completed.append(action)
            active = None
        if event["kind"] in {"started", "resumed"}:
            digest = _nonzero_sha256(
                event["authority_verification_sha256"],
                label="worker journal authority verification",
            )
            if index > prefix_count:
                before_boundary = f"before:{request['role']}:{action}"
                after_boundary = f"after:{request['role']}:{action}"
                before_entry = verification_by_digest.get(digest)
                semantic = evidence_documents[action]["semantic"]
                after_digest = semantic.get("authority_after_sha256")
                after_entry = verification_by_digest.get(after_digest)
                if (
                    digest not in verification_digests
                    or before_entry is None
                    or before_entry["challenge"]["boundary"]
                    != before_boundary
                    or semantic.get("authority_before_sha256") != digest
                    or semantic.get("authority_before_sequence")
                    != before_entry["verification"]["verification_sequence"]
                    or after_entry is None
                    or after_entry["challenge"]["boundary"]
                    != after_boundary
                    or semantic.get("authority_after_sequence")
                    != after_entry["verification"]["verification_sequence"]
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        "worker evidence is outside the current authority "
                        "session"
                    )
                current_action_boundaries.extend(
                    (before_boundary, after_boundary)
                )
        if index == prefix_count:
            prefix_completed = list(completed)
            prefix_active = active
        previous = event["event_sha256"]
    if prefix_count == 0:
        prefix_completed = []
        prefix_active = None
    if (
        result["journal_prefix_completed_actions"] != prefix_completed
        or result["journal_prefix_active_action"] != prefix_active
        or prefix_completed
        != list(WORKER.ACTIONS[: len(prefix_completed)])
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "historical journal prefix semantic state differs"
        )
    if completed != list(WORKER.ACTIONS) or active is not None:
        raise FrozenFinalRestoreOrchestratorError(
            "worker journal does not prove every action completed"
        )
    boundaries = [
        entry["challenge"]["boundary"] for entry in worker_transcript
    ]
    bootstrap_digest = _sha256(
        canonical_json(worker_transcript[0]["verification"])
    )
    if worker_return["bootstrap_authority_sha256"] != bootstrap_digest:
        raise FrozenFinalRestoreOrchestratorError(
            "worker bootstrap authority differs"
        )
    if prefix_count == len(events):
        expected_readback_boundaries = [
            f"before:{request['role']}:completed-readback",
            f"after:{request['role']}:completed-readback",
        ]
    else:
        expected_readback_boundaries = current_action_boundaries
        if worker_return["completed_readback"] is not None:
            raise FrozenFinalRestoreOrchestratorError(
                "newly completed restore has premature readback metadata"
            )
    expected_boundaries = [
        bootstrap_boundary,
        *expected_readback_boundaries,
        f"after:{request['role']}:host-result-readback",
    ]
    if boundaries != expected_boundaries:
        raise FrozenFinalRestoreOrchestratorError(
            "worker authority boundaries are incomplete or reordered"
        )
    if prefix_count == len(events):
        readback = worker_return["completed_readback"]
        if (
            not isinstance(readback, dict)
            or set(readback)
            != {
                "authority_before_sha256",
                "authority_after_sha256",
                "final_state_reverified",
            }
            or readback["final_state_reverified"] is not True
            or readback["authority_before_sha256"]
            != _sha256(
                canonical_json(worker_transcript[1]["verification"])
            )
            or readback["authority_after_sha256"]
            != _sha256(
                canonical_json(worker_transcript[2]["verification"])
            )
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "completed restore readback authority differs"
            )


def _validate_installation_attestation(
    value: Any,
    *,
    request: Mapping[str, Any],
    source_role: str,
    role_manifest_sha256: str,
    installer_receipt_sha256: str,
    transcript: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    authority = request["authority"]
    assert isinstance(authority, dict)
    if (
        not isinstance(value, dict)
        or set(value) != INSTALLATION_ATTESTATION_FIELDS
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installation attestation fields are not exact"
        )
    installation = _json_clone(value)
    publications = installation["publications"]
    status = installation["status"]
    if (
        installation["schema"]
        != INSTALLER.INSTALLATION_ATTESTATION_SCHEMA
        or status not in {"installed", "already-installed"}
        or installation["campaign_id"] != request["campaign_id"]
        or installation["operation_id"] != request["operation_id"]
        or installation["role"] != request["role"]
        or installation["source_role"] != source_role
        or installation["release_sha"] != request["release_sha"]
        or installation["release_tree_sha"]
        != request["release_tree_sha"]
        or installation["controller_manifest_sha256"]
        != request["controller_manifest_sha256"]
        or installation["restore_set_sha256"]
        != request["restore_set_sha256"]
        or installation["restore_generation_sha256"]
        != request["restore_generation_sha256"]
        or installation["role_manifest_sha256"]
        != role_manifest_sha256
        or installation["installer_receipt_sha256"]
        != installer_receipt_sha256
        or installation["fresh_claim_sha256"]
        != authority["claim_sha256"]
        or installation["fresh_claim_epoch"]
        != authority["claim_epoch"]
        or installation["fresh_claim_nonce"]
        != authority["claim_nonce"]
        or installation["legacy_frozen_receipt_sha256"]
        != authority["legacy_frozen_receipt_sha256"]
        or installation["owner_action"]
        != WORKER.LIVE_LEASE_OWNER_ACTION
        or installation["intended_outcome"]
        != WORKER.LIVE_LEASE_SUCCESS_OUTCOME
        or not isinstance(publications, dict)
        or set(publications) != INSTALLATION_PUBLICATION_FIELDS
        or any(
            publication not in {"created", "reused"}
            for publication in publications.values()
        )
        or (
            status == "already-installed"
            and any(
                publication != "reused"
                for publication in publications.values()
            )
        )
        or (
            status == "installed"
            and all(
                publication == "reused"
                for publication in publications.values()
            )
        )
        or installation["worker_copied"] is not False
        or installation["redis_restore_bytes"] != 0
        or installation["network_io_performed"] is not False
        or installation["docker_invoked"] is not False
        or installation["object_storage_contacted"] is not False
        or installation["service_mutated"] is not False
        or installation["current_mutated"] is not False
        or installation["legacy_mutated"] is not False
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installation attestation identity or safety closure differs"
        )
    events = installation["authority_verifications"]
    if (
        not isinstance(events, list)
        or not events
        or len(events) > MAX_TRANSCRIPT_ENTRIES
        or installation["authority_verification_count"] != len(events)
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installation authority event count differs"
        )
    previous = ZERO_SHA256
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event) != INSTALLATION_AUTHORITY_EVENT_FIELDS
            or event["schema"] != INSTALLER.AUTHORITY_EVENT_SCHEMA
            or event["index"] != index
            or event["previous_event_sha256"] != previous
            or not isinstance(event["boundary"], str)
            or BOUNDARY_RE.fullmatch(event["boundary"]) is None
            or not isinstance(event["verification"], dict)
            or event["verification"].get("boundary")
            != event["boundary"]
            or event["event_sha256"]
            != _sha256(
                canonical_json(
                    {
                        key: item
                        for key, item in event.items()
                        if key != "event_sha256"
                    }
                )
            )
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "installation authority event chain differs"
            )
        previous = event["event_sha256"]
    if (
        installation["authority_verification_tail_sha256"] != previous
        or installation["authority_transcript_sha256"]
        != _sha256(canonical_json(events))
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installation authority transcript summary differs"
        )
    bootstrap_boundary = f"before:{request['role']}:journal-bootstrap"
    bootstrap_indexes = [
        index
        for index, entry in enumerate(transcript)
        if entry["challenge"]["boundary"] == bootstrap_boundary
    ]
    if len(bootstrap_indexes) != 1 or bootstrap_indexes[0] != len(events):
        raise FrozenFinalRestoreOrchestratorError(
            "installation authority transcript is not the exact host prefix"
        )
    for event, entry in zip(events, transcript[: len(events)], strict=True):
        if event["verification"] != entry["verification"]:
            raise FrozenFinalRestoreOrchestratorError(
                "installation authority event differs from host transcript"
            )
    unsigned = {
        key: item
        for key, item in installation.items()
        if key != "attestation_sha256"
    }
    if installation["attestation_sha256"] != _sha256(
        canonical_json(unsigned)
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installation attestation digest differs"
        )
    return installation


def validate_host_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    request = validate_host_request(request)
    if (
        request["action"] != "apply"
        or not isinstance(value, Mapping)
        or set(value) != HOST_RESULT_FIELDS
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host apply result fields are not exact"
        )
    result = _json_clone(value)
    if (
        result["schema"] != HOST_RESULT_SCHEMA
        or result["status"] != "restored-and-read-back"
        or any(
            result[field] != request[field]
            for field in (
                "operation_id",
                "role",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "restore_set_sha256",
                "restore_generation_sha256",
            )
        )
        or result["payload_bytes_over_ssh"] is not False
        or result["presigned_url_persisted"] is not False
        or result["pull_performed"] is not False
        or result["build_performed"] is not False
        or result["app_services_started"] is not False
        or result["redis_restored"] is not False
        or result["current_mutated"] is not False
        or result["legacy_mutated"] is not False
        or result["object_storage_mutated"] is not False
        or not isinstance(result["observed_host_ipv4"], list)
        or result["observed_host_ipv4"]
        != sorted(set(result["observed_host_ipv4"]))
        or request["expected_host"] not in result["observed_host_ipv4"]
        or result["expected_host_verified"] is not True
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host result identity or safety boundary differs"
        )
    role_manifest = _validate_readback(
        result["role_manifest"],
        label="role manifest",
        newline=False,
    )
    receipt = _validate_readback(
        result["installer_receipt"],
        label="installer receipt",
        newline=False,
    )
    restore = _validate_readback(
        result["restore_result"],
        label="restore result",
        newline=True,
    )
    if (
        role_manifest["document"].get("schema")
        != WORKER.ROLE_MANIFEST_SCHEMA
        or role_manifest["document"].get("status") != "installed"
        or role_manifest["document"].get("role") != request["role"]
        or role_manifest["document"].get("installer_receipt_sha256")
        != receipt["canonical_document_sha256"]
        or role_manifest["document"].get("restore_set_sha256")
        != request["restore_set_sha256"]
        or role_manifest["document"].get("restore_generation_sha256")
        != request["restore_generation_sha256"]
        or receipt["document"].get("schema")
        != WORKER.INSTALLER_RECEIPT_SCHEMA
        or receipt["document"].get("status") != "installed"
        or receipt["document"].get("role") != request["role"]
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "installed manifest or receipt actual bytes differ"
        )
    transcript, tail = validate_authority_transcript(
        result["authority_transcript"],
        request=request,
    )
    if (
        result["authority_transcript_count"] != len(transcript)
        or result["authority_transcript_sha256"]
        != _sha256(canonical_json(transcript))
        or result["authority_transcript_tail_sha256"] != tail
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host authority transcript summary differs"
        )
    _validate_installation_attestation(
        result["installation_attestation"],
        request=request,
        source_role=result["source_role"],
        role_manifest_sha256=role_manifest[
            "canonical_document_sha256"
        ],
        installer_receipt_sha256=receipt[
            "canonical_document_sha256"
        ],
        transcript=transcript,
    )
    _validate_worker_evidence(result, request=request, transcript=transcript)
    if request["role"] == "webapp_ir":
        expected = validate_wa_exact_version(request["wa_exact_version"])
        if any(
            result["transport"].get(field) != expected[field]
            for field in (
                "provider",
                "object_key",
                "version_id",
                "ciphertext_sha256",
                "readback_receipt_sha256",
            )
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "WebApp-IR host result VersionId differs"
            )
        _nonzero_sha256(
            result["transport"].get(
                "fresh_control_transfer_receipt_sha256"
            ),
            label="WebApp-IR fresh-control receipt",
        )
        if result["transport"].get(
            "fresh_control_exact_version"
        ) != validate_wa_fresh_control_version(
            request["wa_fresh_control_exact_version"],
            request=request,
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "WebApp-IR fresh-control VersionId differs"
            )
    elif result["transport"].get(
        "fresh_control_transfer_receipt_sha256"
    ) is not None or result["transport"].get(
        "fresh_control_exact_version"
    ) is not None:
        raise FrozenFinalRestoreOrchestratorError(
            "Finland host result carries an IR transfer receipt"
        )
    # The readback was validated above; retain this assignment to make the
    # nested actual-byte requirement explicit to future refactors.
    del restore
    return result


def build_completion(
    requests: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], str]:
    if set(requests) != set(ROLES) or set(results) != set(ROLES):
        raise FrozenFinalRestoreOrchestratorError(
            "completion requires exactly three role results"
        )
    validated_requests = {
        role: validate_host_request(requests[role]) for role in ROLES
    }
    validated_results = {
        role: validate_host_result(
            results[role],
            request=validated_requests[role],
        )
        for role in ROLES
    }
    first = validated_requests[ROLES[0]]
    identity_fields = (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
    )
    if any(
        request[field] != first[field]
        for request in validated_requests.values()
        for field in identity_fields
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "three role requests do not share one restore identity"
        )
    authorities = [request["authority"] for request in validated_requests.values()]
    if any(authority != authorities[0] for authority in authorities[1:]):
        raise FrozenFinalRestoreOrchestratorError(
            "three role results do not share the exact live claim"
        )
    authority = authorities[0]
    assert isinstance(authority, dict)
    role_closure = {}
    for role in ROLES:
        result = validated_results[role]
        role_closure[role] = {
            "source_role": result["source_role"],
            "transport": result["transport"],
            "host_result": result,
            "host_result_sha256": _sha256(canonical_json(result)),
            "role_manifest_sha256": result["role_manifest"][
                "canonical_document_sha256"
            ],
            "installer_receipt_sha256": result["installer_receipt"][
                "canonical_document_sha256"
            ],
            "restore_result_sha256": result["restore_result"][
                "canonical_document_sha256"
            ],
            "final_evidence_sha256": result["restore_result"]["document"][
                "final_evidence_sha256"
            ],
        }
    completion = {
        "schema": COMPLETION_SCHEMA,
        "status": "three-role-frozen-final-restored",
        **{field: first[field] for field in identity_fields},
        "live_lease_claim_sha256": authority["claim_sha256"],
        "live_lease_claim_epoch": authority["claim_epoch"],
        "live_lease_claim_nonce": authority["claim_nonce"],
        "legacy_frozen_receipt_sha256": authority[
            "legacy_frozen_receipt_sha256"
        ],
        "roles": role_closure,
        "role_order": list(ROLES),
        "claim_consume_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "claim_consumed": False,
        "consumption_receipt_included": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated_by_restore": False,
        "app_services_started": False,
        "redis_restored": False,
    }
    payload = canonical_json(completion)
    return completion, _sha256(payload)


def persist_completion(
    directory: Path,
    completion: Mapping[str, Any],
) -> tuple[Path, str]:
    payload = canonical_json(completion)
    digest = _sha256(payload)
    path = directory / f"completion-{digest}.json"
    _persist_create_only(path, payload, label="restore completion")
    observed = _read_document_file(
        path,
        label="persisted restore completion",
        newline=False,
    )
    if observed.document != completion or observed.content_sha256 != digest:
        raise FrozenFinalRestoreOrchestratorError(
            "persisted restore completion differs"
        )
    return path, digest


def build_post_consumption_receipt(
    *,
    completion_path: Path,
    completion_sha256: str,
    completion: Mapping[str, Any],
    consumption_path: Path,
    consumption_sha256: str,
) -> tuple[dict[str, Any], str]:
    completion_sha256 = _nonzero_sha256(
        completion_sha256,
        label="completion SHA-256",
    )
    consumption_sha256 = _nonzero_sha256(
        consumption_sha256,
        label="consumption SHA-256",
    )
    document = {
        "schema": POST_CONSUMPTION_SCHEMA,
        "status": "lease-consumed-after-three-role-restore",
        "operation_id": completion["operation_id"],
        "release_sha": completion["release_sha"],
        "restore_generation_sha256": completion[
            "restore_generation_sha256"
        ],
        "live_lease_claim_sha256": completion[
            "live_lease_claim_sha256"
        ],
        "live_lease_claim_epoch": completion["live_lease_claim_epoch"],
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "consumption_path": os.fspath(consumption_path),
        "consumption_sha256": consumption_sha256,
        "outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "outcome_sha256": completion_sha256,
        "completion_preceded_consumption": True,
        "aggregate_rewritten_after_consumption": False,
        "current_mutated": False,
        "legacy_mutated": False,
    }
    return document, _sha256(canonical_json(document))


def stable_role_closure(
    result_value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the nonce-independent closure compared across crash resumes."""
    result = validate_host_result(result_value, request=request)
    restore = result["restore_result"]["document"]
    return {
        "schema": "production-shadow-frozen-final-role-stable-closure-v1",
        "operation_id": result["operation_id"],
        "role": result["role"],
        "release_sha": result["release_sha"],
        "release_tree_sha": result["release_tree_sha"],
        "controller_manifest_sha256": result[
            "controller_manifest_sha256"
        ],
        "restore_set_sha256": result["restore_set_sha256"],
        "restore_generation_sha256": result[
            "restore_generation_sha256"
        ],
        "source_role": result["source_role"],
        "transport": result["transport"],
        "observed_host_ipv4": result["observed_host_ipv4"],
        "role_manifest_sha256": result["role_manifest"][
            "canonical_document_sha256"
        ],
        "installer_receipt_sha256": result["installer_receipt"][
            "canonical_document_sha256"
        ],
        "restore_result_sha256": result["restore_result"][
            "canonical_document_sha256"
        ],
        "action_evidence_sha256": {
            action: result["action_evidence"][action][
                "canonical_document_sha256"
            ]
            for action in WORKER.ACTIONS
        },
        "database": restore["database"],
        "file_trees": restore["file_trees"],
        "redis_restore_bytes": restore["redis_restore_bytes"],
        "redis_pristine": restore["redis_pristine"],
        "live_lease_claim_sha256": restore[
            "live_lease_claim_sha256"
        ],
        "live_lease_claim_epoch": restore["live_lease_claim_epoch"],
        "live_lease_claim_nonce": restore["live_lease_claim_nonce"],
        "legacy_frozen_receipt_sha256": restore[
            "legacy_frozen_receipt_sha256"
        ],
        "final_evidence_sha256": restore["final_evidence_sha256"],
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "app_services_started": False,
    }


def _journal_state_sha256(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned["state_sha256"] = ZERO_SHA256
    return _sha256(canonical_json(unsigned))


def _controller_event(
    journal: Mapping[str, Any],
    *,
    kind: str,
    role: str | None,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    body = {
        "schema": JOURNAL_EVENT_SCHEMA,
        "index": len(journal["events"]) + 1,
        "kind": kind,
        "role": role,
        "details": _json_clone(details),
        "previous_event_sha256": journal["event_tail_sha256"],
    }
    return {
        **body,
        "event_sha256": _sha256(canonical_json(body)),
    }


def _ensure_private_directory(path: Path) -> None:
    directory_fd = -1
    child_fd = -1
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label="controller directory",
        )
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileExistsError:
            pass
        child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
        _assert_root_directory(
            child_fd,
            label="controller directory",
            private=True,
        )
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "controller directory is unavailable or unsafe"
        ) from exc
    finally:
        if child_fd >= 0:
            os.close(child_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


@contextmanager
def controller_journal_lock(directory: Path) -> Iterator[None]:
    _ensure_private_directory(directory)
    path = directory / "controller.lock"
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label="controller journal lock",
            private_parent=True,
        )
        descriptor = os.open(
            name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_dev != visible.st_dev
            or metadata.st_ino != visible.st_ino
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal lock is unsafe"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "controller journal lock failed"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


class ControllerJournalStore:
    """Root-only atomic journal plus create-only role/result artifacts."""

    def __init__(
        self,
        directory: Path,
        requests: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if set(requests) != set(ROLES):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal requires exactly three requests"
            )
        self.directory = directory
        self.requests = {
            role: validate_host_request(requests[role]) for role in ROLES
        }
        if any(
            request["action"] != "apply"
            for request in self.requests.values()
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal requires apply requests"
            )
        _validate_controller_output_directory(directory, self.requests)
        _ensure_private_directory(directory)
        self.role_directory = directory / "role-results"
        _ensure_private_directory(self.role_directory)
        self.path = directory / "controller-journal.json"
        self.document = self._load_or_create()

    def _identity(self) -> dict[str, Any]:
        first = self.requests[ROLES[0]]
        authority = first["authority"]
        assert isinstance(authority, dict)
        return {
            "campaign_id": first["campaign_id"],
            "operation_id": first["operation_id"],
            "release_sha": first["release_sha"],
            "release_tree_sha": first["release_tree_sha"],
            "controller_manifest_sha256": first[
                "controller_manifest_sha256"
            ],
            "restore_set_sha256": first["restore_set_sha256"],
            "restore_generation_sha256": first[
                "restore_generation_sha256"
            ],
            "claim": {
                "path": authority["claim_path"],
                "sha256": authority["claim_sha256"],
                "epoch": authority["claim_epoch"],
                "nonce": authority["claim_nonce"],
                "legacy_frozen_receipt_path": authority[
                    "legacy_frozen_receipt_path"
                ],
                "legacy_frozen_receipt_sha256": authority[
                    "legacy_frozen_receipt_sha256"
                ],
            },
        }

    def _initial(self) -> dict[str, Any]:
        return {
            "schema": JOURNAL_SCHEMA,
            "status": "active",
            **self._identity(),
            "roles": {role: None for role in ROLES},
            "completion": None,
            "consumption": None,
            "post_consumption": None,
            "events": [],
            "event_tail_sha256": ZERO_SHA256,
            "state_sha256": ZERO_SHA256,
        }

    def _validate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        fields = {
            "schema",
            "status",
            *self._identity(),
            "roles",
            "completion",
            "consumption",
            "post_consumption",
            "events",
            "event_tail_sha256",
            "state_sha256",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or value["schema"] != JOURNAL_SCHEMA
            or value["status"]
            not in {
                "active",
                "completion-persisted",
                "consumed",
                "complete",
            }
            or any(
                value[key] != expected
                for key, expected in self._identity().items()
            )
            or not isinstance(value["roles"], dict)
            or set(value["roles"]) != set(ROLES)
            or not isinstance(value["events"], list)
            or len(value["events"]) > 100_000
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal identity or fields differ"
            )
        previous = ZERO_SHA256
        role_events: dict[str, list[dict[str, Any]]] = {
            role: [] for role in ROLES
        }
        phase_events: dict[str, list[dict[str, Any]]] = {
            "completion-persisted": [],
            "claim-consumption-read-back": [],
            "post-consumption-receipt-persisted": [],
        }
        for index, event in enumerate(value["events"], 1):
            if (
                not isinstance(event, dict)
                or set(event)
                != {
                    "schema",
                    "index",
                    "kind",
                    "role",
                    "details",
                    "previous_event_sha256",
                    "event_sha256",
                }
                or event["schema"] != JOURNAL_EVENT_SCHEMA
                or event["index"] != index
                or event["previous_event_sha256"] != previous
                or event["event_sha256"]
                != _sha256(
                    canonical_json(
                        {
                            key: item
                            for key, item in event.items()
                            if key != "event_sha256"
                        }
                    )
                )
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "controller journal event chain differs"
                )
            kind = event["kind"]
            details = event["details"]
            if kind == "role-read-back":
                if (
                    event["role"] not in ROLES
                    or not isinstance(details, dict)
                    or set(details)
                    != {
                        "path",
                        "sha256",
                        "stable_closure_sha256",
                    }
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        "controller role event fields differ"
                    )
                role_events[event["role"]].append(_json_clone(details))
            elif kind in phase_events:
                if (
                    event["role"] is not None
                    or not isinstance(details, dict)
                    or set(details) != {"path", "sha256"}
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        "controller phase event fields differ"
                    )
                phase_events[kind].append(_json_clone(details))
            else:
                raise FrozenFinalRestoreOrchestratorError(
                    "controller journal event kind differs"
                )
            previous = event["event_sha256"]
        if (
            value["event_tail_sha256"] != previous
            or value["state_sha256"] != _journal_state_sha256(value)
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal state digest differs"
            )
        for role, row in value["roles"].items():
            if row is None:
                if role_events[role]:
                    raise FrozenFinalRestoreOrchestratorError(
                        f"{role} has journal events without a closure"
                    )
                continue
            stable = (
                row.get("stable_closure")
                if isinstance(row, dict)
                else None
            )
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "stable_closure",
                    "stable_closure_sha256",
                    "observations",
                }
                or not isinstance(stable, dict)
                or set(stable) != STABLE_ROLE_CLOSURE_FIELDS
                or stable["schema"]
                != "production-shadow-frozen-final-role-stable-closure-v1"
                or stable["operation_id"] != value["operation_id"]
                or stable["role"] != role
                or stable["release_sha"] != value["release_sha"]
                or stable["release_tree_sha"]
                != value["release_tree_sha"]
                or stable["controller_manifest_sha256"]
                != value["controller_manifest_sha256"]
                or stable["restore_set_sha256"]
                != value["restore_set_sha256"]
                or stable["restore_generation_sha256"]
                != value["restore_generation_sha256"]
                or stable["live_lease_claim_sha256"]
                != value["claim"]["sha256"]
                or stable["live_lease_claim_epoch"]
                != value["claim"]["epoch"]
                or stable["live_lease_claim_nonce"]
                != value["claim"]["nonce"]
                or stable["legacy_frozen_receipt_sha256"]
                != value["claim"]["legacy_frozen_receipt_sha256"]
                or stable["current_mutated"] is not False
                or stable["legacy_mutated"] is not False
                or stable["object_storage_mutated"] is not False
                or stable["app_services_started"] is not False
                or row["stable_closure_sha256"]
                != _sha256(canonical_json(stable))
                or not isinstance(row["observations"], list)
                or not row["observations"]
                or len(row["observations"]) > 100_000
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} controller journal closure differs"
                )
            expected_events: list[dict[str, Any]] = []
            seen_observations: set[tuple[str, str]] = set()
            for observation in row["observations"]:
                if (
                    not isinstance(observation, dict)
                    or set(observation) != {"path", "sha256"}
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        f"{role} controller observation differs"
                    )
                path = _path(
                    observation["path"],
                    label=f"{role} observation",
                )
                digest = _nonzero_sha256(
                    observation["sha256"],
                    label=f"{role} observation SHA-256",
                )
                if (
                    path.parent != self.role_directory
                    or path.name != f"{role}-{digest}.json"
                    or (os.fspath(path), digest) in seen_observations
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        f"{role} controller observation path differs"
                    )
                seen_observations.add((os.fspath(path), digest))
                expected_events.append(
                    {
                        **observation,
                        "stable_closure_sha256": row[
                            "stable_closure_sha256"
                        ],
                    }
                )
            if role_events[role] != expected_events:
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} observation events are incomplete or reordered"
                )
        references = {
            "completion": (
                self.directory,
                "completion",
                "completion-persisted",
            ),
            "consumption": (
                Path(value["claim"]["path"]).parent.parent
                / "consumptions",
                value["claim"]["sha256"],
                "claim-consumption-read-back",
            ),
            "post_consumption": (
                self.directory,
                "consumption",
                "post-consumption-receipt-persisted",
            ),
        }
        for field, (parent, prefix, event_kind) in references.items():
            reference = value[field]
            if reference is None:
                if phase_events[event_kind]:
                    raise FrozenFinalRestoreOrchestratorError(
                        f"controller {field} event precedes its reference"
                    )
                continue
            if (
                not isinstance(reference, dict)
                or set(reference) != {"path", "sha256"}
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"controller {field} reference differs"
                )
            path = _path(
                reference["path"],
                label=f"controller {field} reference",
            )
            digest = _nonzero_sha256(
                reference["sha256"],
                label=f"controller {field} SHA-256",
            )
            expected_name = (
                f"{prefix}-{digest}.json"
                if field != "consumption"
                else f"{prefix}.json"
            )
            if (
                path.parent != parent
                or path.name != expected_name
                or phase_events[event_kind] != [reference]
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"controller {field} path or event differs"
                )
        if value["completion"] is not None and any(
            value["roles"][role] is None for role in ROLES
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller completion precedes role closure"
            )
        if (
            value["consumption"] is not None
            and value["completion"] is None
        ) or (
            value["post_consumption"] is not None
            and value["consumption"] is None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal phase ordering differs"
            )
        expected_status = (
            "complete"
            if value["post_consumption"] is not None
            else "consumed"
            if value["consumption"] is not None
            else "completion-persisted"
            if value["completion"] is not None
            else "active"
        )
        if value["status"] != expected_status:
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal status differs from its closure"
            )
        return _json_clone(value)

    def _load_or_create(self) -> dict[str, Any]:
        try:
            os.lstat(self.path)
        except FileNotFoundError:
            document = self._initial()
            document["state_sha256"] = _journal_state_sha256(document)
            _write_create_only_no_follow(
                self.path,
                canonical_json(document),
                label="frozen-final controller journal",
            )
            return self._validate(document)
        except OSError as exc:
            raise FrozenFinalRestoreOrchestratorError(
                "controller journal is unavailable"
            ) from exc
        readback = _read_document_file(
            self.path,
            label="frozen-final controller journal",
            newline=False,
            allowed_modes=frozenset({0o600}),
        )
        return self._validate(readback.document)

    def _write(self) -> None:
        self.document["state_sha256"] = _journal_state_sha256(
            self.document
        )
        candidate = self._validate(self.document)
        _write_atomic_no_follow(
            self.path,
            canonical_json(candidate),
            label="frozen-final controller journal",
        )
        loaded = _read_document_file(
            self.path,
            label="updated frozen-final controller journal",
            newline=False,
            allowed_modes=frozenset({0o600}),
        )
        self.document = self._validate(loaded.document)

    def _event(
        self,
        *,
        kind: str,
        role: str | None,
        details: Mapping[str, Any],
    ) -> None:
        event = _controller_event(
            self.document,
            kind=kind,
            role=role,
            details=details,
        )
        self.document["events"].append(event)
        self.document["event_tail_sha256"] = event["event_sha256"]

    def record_role(
        self,
        role: str,
        result_value: Mapping[str, Any],
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise FrozenFinalRestoreOrchestratorError(
                "controller role is invalid"
            )
        result = validate_host_result(
            result_value,
            request=self.requests[role],
        )
        stable = stable_role_closure(
            result,
            request=self.requests[role],
        )
        stable_sha256 = _sha256(canonical_json(stable))
        payload = canonical_json(result)
        result_sha256 = _sha256(payload)
        path = (
            self.role_directory
            / f"{role}-{result_sha256}.json"
        )
        existing = self.document["roles"][role]
        if existing is None:
            existing = {
                "stable_closure": stable,
                "stable_closure_sha256": stable_sha256,
                "observations": [],
            }
            self.document["roles"][role] = existing
        elif (
            existing["stable_closure"] != stable
            or existing["stable_closure_sha256"] != stable_sha256
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{role} resumed stable semantic closure drifted"
            )
        _persist_create_only(
            path,
            payload,
            label=f"{role} controller result observation",
        )
        observation = {
            "path": os.fspath(path),
            "sha256": result_sha256,
        }
        if observation not in existing["observations"]:
            existing["observations"].append(observation)
            self._event(
                kind="role-read-back",
                role=role,
                details={
                    **observation,
                    "stable_closure_sha256": stable_sha256,
                },
            )
            self._write()
        return result

    def load_latest_results(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for role in ROLES:
            row = self.document["roles"][role]
            if row is None:
                continue
            reference = row["observations"][-1]
            readback = _read_document_file(
                Path(reference["path"]),
                label=f"{role} persisted controller observation",
                newline=False,
                allowed_modes=frozenset({0o600}),
            )
            if readback.content_sha256 != reference["sha256"]:
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} persisted observation digest differs"
                )
            result = validate_host_result(
                readback.document,
                request=self.requests[role],
            )
            if stable_role_closure(
                result,
                request=self.requests[role],
            ) != row["stable_closure"]:
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} persisted stable closure differs"
                )
            results[role] = result
        return results

    def record_completion(self, path: Path, sha256: str) -> None:
        reference = {
            "path": os.fspath(path),
            "sha256": _nonzero_sha256(
                sha256,
                label="journal completion SHA-256",
            ),
        }
        if self.document["completion"] is None:
            self.document["completion"] = reference
            self.document["status"] = "completion-persisted"
            self._event(
                kind="completion-persisted",
                role=None,
                details=reference,
            )
            self._write()
        elif self.document["completion"] != reference:
            raise FrozenFinalRestoreOrchestratorError(
                "controller completion reference differs"
            )

    def record_consumption(self, path: Path, sha256: str) -> None:
        reference = {
            "path": os.fspath(path),
            "sha256": _nonzero_sha256(
                sha256,
                label="journal consumption SHA-256",
            ),
        }
        if self.document["completion"] is None:
            raise FrozenFinalRestoreOrchestratorError(
                "claim consumption precedes completion"
            )
        if self.document["consumption"] is None:
            self.document["consumption"] = reference
            self.document["status"] = "consumed"
            self._event(
                kind="claim-consumption-read-back",
                role=None,
                details=reference,
            )
            self._write()
        elif self.document["consumption"] != reference:
            raise FrozenFinalRestoreOrchestratorError(
                "controller consumption reference differs"
            )

    def record_post_consumption(self, path: Path, sha256: str) -> None:
        reference = {
            "path": os.fspath(path),
            "sha256": _nonzero_sha256(
                sha256,
                label="post-consumption receipt SHA-256",
            ),
        }
        if self.document["consumption"] is None:
            raise FrozenFinalRestoreOrchestratorError(
                "post-consumption receipt precedes consumption"
            )
        if self.document["post_consumption"] is None:
            self.document["post_consumption"] = reference
            self.document["status"] = "complete"
            self._event(
                kind="post-consumption-receipt-persisted",
                role=None,
                details=reference,
            )
            self._write()
        elif self.document["post_consumption"] != reference:
            raise FrozenFinalRestoreOrchestratorError(
                "post-consumption journal reference differs"
            )


ConsumptionReadback = Callable[
    [
        Path | None,
        str | None,
        Mapping[str, Any],
        str,
    ],
    tuple[Path, str, Mapping[str, Any]],
]


def coordinator_consumption_readback(
    inputs: NGINX.CoordinatorInputs,
    *,
    claim_path: Path,
    claim_sha256: str,
    claimed_path: Path | None,
    claimed_sha256: str | None,
    completion: Mapping[str, Any],
    completion_sha256: str,
) -> tuple[Path, str, Mapping[str, Any]]:
    try:
        claim, _receipt = NGINX._load_claim_from_controller(  # noqa: SLF001
            inputs,
            claim_path,
            claim_sha256,
        )
        loaded = NGINX._load_consumption_audit(  # noqa: SLF001
            inputs,
            claim=claim,
            claim_sha256=claim_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "coordinator consumption audit is invalid"
        ) from exc
    if loaded is None:
        raise ConsumptionAuditAbsent(
            "coordinator consumption audit is absent"
        )
    document, digest = loaded
    path = NGINX._live_lease_paths(inputs)[3] / f"{claim_sha256}.json"  # noqa: SLF001
    if (
        (claimed_path is not None and claimed_path != path)
        or (claimed_sha256 is not None and claimed_sha256 != digest)
        or document["owner_action"] != WORKER.LIVE_LEASE_OWNER_ACTION
        or document["outcome"] != WORKER.LIVE_LEASE_SUCCESS_OUTCOME
        or document["outcome_sha256"] != completion_sha256
        or document["claim_sha256"] != claim_sha256
        or document["claim_epoch"] != completion["live_lease_claim_epoch"]
        or document["operation_id"] != completion["operation_id"]
        or document["release_sha"] != completion["release_sha"]
        or document["final_state"] != "legacy-frozen"
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "coordinator consumption audit outcome binding differs"
        )
    return path, digest, document


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise FrozenFinalRestoreOrchestratorError(
                "secure file write made no progress"
            )
        view = view[written:]


def _write_create_only_no_follow(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> None:
    directory_fd = -1
    temporary_fd = -1
    temporary_name = (
        f".{path.name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    )
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label=label,
            private_parent=True,
        )
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(temporary_fd, payload)
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} already exists"
            ) from exc
        os.fsync(directory_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} could not be created safely"
        ) from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
            os.close(directory_fd)


def _write_atomic_no_follow(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> None:
    directory_fd = -1
    existing_fd = -1
    temporary_fd = -1
    temporary_name = (
        f".{path.name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    )
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label=label,
            private_parent=True,
        )
        existing_fd = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(existing_fd)
        visible = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or (before.st_dev, before.st_ino)
            != (visible.st_dev, visible.st_ino)
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} existing identity is unsafe"
            )
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        _write_all(temporary_fd, payload)
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            (current.st_dev, current.st_ino)
            != (before.st_dev, before.st_ino)
            or current.st_nlink != 1
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} changed before atomic replacement"
            )
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} could not be replaced safely"
        ) from exc
    finally:
        if existing_fd >= 0:
            os.close(existing_fd)
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
            os.close(directory_fd)


def _persist_create_only(path: Path, payload: bytes, *, label: str) -> None:
    directory_fd = -1
    try:
        directory_fd, name = _open_parent_no_follow(
            path,
            label=label,
            private_parent=True,
        )
        try:
            metadata = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_gid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"existing {label} is not an exact root-only file"
                )
            os.close(directory_fd)
            directory_fd = -1
            observed = _read_document_file(
                path,
                label=f"existing {label}",
                newline=False,
                allowed_modes=frozenset({0o600}),
            )
            if canonical_json(observed.document) != payload:
                raise FrozenFinalRestoreOrchestratorError(
                    f"existing {label} differs"
                )
            return
        os.close(directory_fd)
        directory_fd = -1
        _write_create_only_no_follow(path, payload, label=label)
        observed = _read_document_file(
            path,
            label=f"new {label}",
            newline=False,
            allowed_modes=frozenset({0o600}),
        )
        if canonical_json(observed.document) != payload:
            raise FrozenFinalRestoreOrchestratorError(
                f"new {label} differs after create-only publication"
            )
    except FrozenFinalRestoreOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{label} could not be persisted"
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def consume_after_completion(
    *,
    lease: Any,
    requests: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
    output_directory: Path,
    journal: ControllerJournalStore,
    consumption_readback: ConsumptionReadback,
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    callback = checkpoint or (lambda _name: None)
    completion, expected_digest = build_completion(requests, results)
    completion_path, completion_sha256 = persist_completion(
        output_directory,
        completion,
    )
    if completion_sha256 != expected_digest:
        raise FrozenFinalRestoreOrchestratorError(
            "completion persistence digest differs"
        )
    journal.record_completion(completion_path, completion_sha256)
    callback("after-completion-before-consume")
    lease.verify()
    claimed_path, claimed_sha256 = lease.consume(
        outcome=WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        outcome_sha256=completion_sha256,
    )
    callback("after-consume-before-receipt")
    (
        consumption_path,
        consumption_sha256,
        _consumption,
    ) = consumption_readback(
        claimed_path,
        claimed_sha256,
        completion,
        completion_sha256,
    )
    journal.record_consumption(
        consumption_path,
        consumption_sha256,
    )
    receipt, receipt_sha256 = build_post_consumption_receipt(
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        completion=completion,
        consumption_path=consumption_path,
        consumption_sha256=consumption_sha256,
    )
    receipt_path = (
        output_directory / f"consumption-{receipt_sha256}.json"
    )
    _persist_create_only(
        receipt_path,
        canonical_json(receipt),
        label="post-consumption receipt",
    )
    journal.record_post_consumption(receipt_path, receipt_sha256)
    callback("after-post-consumption-receipt")
    return {
        "status": "complete",
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "consumption_path": os.fspath(consumption_path),
        "consumption_sha256": consumption_sha256,
        "post_consumption_receipt_path": os.fspath(receipt_path),
        "post_consumption_receipt_sha256": receipt_sha256,
    }


def recover_consumed_completion(
    *,
    journal: ControllerJournalStore,
    consumption_readback: ConsumptionReadback,
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Recover only a claim already consumed for the persisted completion."""
    reference = journal.document["completion"]
    if reference is None:
        return None
    completion_readback = _read_document_file(
        Path(reference["path"]),
        label="recovery restore completion",
        newline=False,
        allowed_modes=frozenset({0o600}),
    )
    if completion_readback.content_sha256 != reference["sha256"]:
        raise FrozenFinalRestoreOrchestratorError(
            "recovery completion digest differs"
        )
    completion = completion_readback.document
    completion_sha256 = reference["sha256"]
    persisted_results = journal.load_latest_results()
    expected_completion, expected_completion_sha256 = build_completion(
        journal.requests,
        persisted_results,
    )
    if (
        completion != expected_completion
        or completion_sha256 != expected_completion_sha256
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "recovery completion differs from persisted role results"
        )
    claimed = journal.document["consumption"]
    (
        consumption_path,
        consumption_sha256,
        _consumption,
    ) = consumption_readback(
        Path(claimed["path"]) if claimed is not None else None,
        claimed["sha256"] if claimed is not None else None,
        completion,
        completion_sha256,
    )
    journal.record_consumption(consumption_path, consumption_sha256)
    receipt, receipt_sha256 = build_post_consumption_receipt(
        completion_path=Path(reference["path"]),
        completion_sha256=completion_sha256,
        completion=completion,
        consumption_path=consumption_path,
        consumption_sha256=consumption_sha256,
    )
    receipt_path = (
        journal.directory / f"consumption-{receipt_sha256}.json"
    )
    _persist_create_only(
        receipt_path,
        canonical_json(receipt),
        label="recovered post-consumption receipt",
    )
    journal.record_post_consumption(receipt_path, receipt_sha256)
    if checkpoint is not None:
        checkpoint("after-recovered-post-consumption-receipt")
    return {
        "status": "complete-recovered-after-consume",
        "completion_path": reference["path"],
        "completion_sha256": completion_sha256,
        "consumption_path": os.fspath(consumption_path),
        "consumption_sha256": consumption_sha256,
        "post_consumption_receipt_path": os.fspath(receipt_path),
        "post_consumption_receipt_sha256": receipt_sha256,
        "second_consume_performed": False,
    }


class _PersistedLeaseIdentity:
    def __init__(self, claim: Mapping[str, Any]) -> None:
        self.claim_sha256 = claim["sha256"]
        self.claim = {
            "claim_epoch": claim["epoch"],
            "nonce": claim["nonce"],
            "legacy_frozen_receipt_sha256": claim[
                "legacy_frozen_receipt_sha256"
            ],
        }


def recover_consumed_controller_operation(
    *,
    inputs: NGINX.CoordinatorInputs,
    output_directory: Path,
    requests: Mapping[str, Mapping[str, Any]],
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Recover post-consume without acquiring or creating any live claim."""
    controller_plan(requests)
    _validate_controller_output_directory(output_directory, requests)
    expected_coordinator_root = output_directory.parent
    first_request = validate_host_request(requests[ROLES[0]])
    if (
        inputs.operation_id != first_request["operation_id"]
        or inputs.release_sha != first_request["release_sha"]
        or inputs.release_tree_sha != first_request["release_tree_sha"]
        or inputs.coordinator_root != expected_coordinator_root
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "recovery coordinator inputs differ from the output contract"
        )
    journal_path = output_directory / "controller-journal.json"
    try:
        os.lstat(journal_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            "recovery controller journal is unavailable"
        ) from exc
    # The Nginx coordinator flock serializes this audit readback with every
    # claim transition.  It does not acquire/resume/create a live lease.
    with NGINX._CoordinatorLock(inputs.coordinator_root):  # noqa: SLF001
        with controller_journal_lock(output_directory):
            preliminary = _read_document_file(
                journal_path,
                label="recovery controller journal",
                newline=False,
                allowed_modes=frozenset({0o600}),
            ).document
            claim = preliminary.get("claim")
            if (
                not isinstance(claim, dict)
                or set(claim)
                != {
                    "path",
                    "sha256",
                    "epoch",
                    "nonce",
                    "legacy_frozen_receipt_path",
                    "legacy_frozen_receipt_sha256",
                }
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "recovery persisted claim identity differs"
                )
            persisted_lease = _PersistedLeaseIdentity(claim)
            prepared: dict[str, dict[str, Any]] = {}
            for role in ROLES:
                path = _prepared_request_path(
                    output_directory,
                    role=role,
                    claim_sha256=claim["sha256"],
                )
                readback = _read_document_file(
                    path,
                    label=f"{role} recovery prepared request",
                    newline=False,
                    allowed_modes=frozenset({0o600}),
                )
                prepared[role] = _prepared_apply_request(
                    requests[role],
                    readback.document,
                    lease=persisted_lease,
                )
            journal = ControllerJournalStore(
                output_directory,
                prepared,
            )
            if journal.document["completion"] is None:
                return None

            def readback(
                claimed_path: Path | None,
                claimed_sha256: str | None,
                completion: Mapping[str, Any],
                completion_sha256: str,
            ) -> tuple[Path, str, Mapping[str, Any]]:
                return coordinator_consumption_readback(
                    inputs,
                    claim_path=Path(claim["path"]),
                    claim_sha256=claim["sha256"],
                    claimed_path=claimed_path,
                    claimed_sha256=claimed_sha256,
                    completion=completion,
                    completion_sha256=completion_sha256,
                )

            return recover_consumed_completion(
                journal=journal,
                consumption_readback=readback,
                checkpoint=checkpoint,
            )


def _safe_remote_command(arguments: Sequence[str]) -> str:
    if not arguments:
        raise FrozenFinalRestoreOrchestratorError(
            "remote command is empty"
        )
    for argument in arguments:
        if (
            not isinstance(argument, str)
            or SAFE_REMOTE_TOKEN_RE.fullmatch(argument) is None
            or any(character in argument for character in "\r\n\x00")
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "remote command token is unsafe"
            )
    return " ".join(shlex.quote(argument) for argument in arguments)


def session_arguments(
    request: Mapping[str, Any],
    *,
    ssh_identity: Path,
    known_hosts: Path,
) -> list[str]:
    request = validate_host_request(request)
    agent = request["agent_path"]
    host_arguments = [
        ENV,
        "PYTHONDONTWRITEBYTECODE=1",
        PYTHON,
        "-B",
        agent,
        "--host-stdio",
    ]
    if request["role"] == "bot_fi":
        return host_arguments
    for path, label in (
        (ssh_identity, "SSH identity"),
        (known_hosts, "SSH known-hosts"),
    ):
        if not path.is_absolute() or path != Path(os.path.abspath(path)):
            raise FrozenFinalRestoreOrchestratorError(
                f"{label} path is not canonical"
            )
    return [
        SSH,
        "-T",
        "-p",
        str(request["expected_port"]),
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
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        f"root@{request['expected_host']}",
        _safe_remote_command(host_arguments),
    ]


def _default_session_factory(arguments: Sequence[str]) -> InteractiveProcess:
    # A temporary file prevents an untrusted remote stderr stream from
    # deadlocking the authority channel.  Its contents are never persisted.
    stderr = tempfile.TemporaryFile()
    process = subprocess.Popen(  # noqa: S603
        list(arguments),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise FrozenFinalRestoreOrchestratorError(
            "interactive host process lacks bounded stdio"
        )
    return process


def run_interactive_host_with_authority(
    request_value: Mapping[str, Any],
    *,
    authority_responder: Callable[
        [Mapping[str, Any]], Mapping[str, Any]
    ],
    ssh_identity: Path,
    known_hosts: Path,
    session_factory: SessionFactory = _default_session_factory,
    timeout: float = 8 * 60 * 60,
    line_timeout: float = 120.0,
) -> dict[str, Any]:
    request = validate_host_request(request_value)
    if request["action"] != "apply":
        raise FrozenFinalRestoreOrchestratorError(
            "interactive host session requires apply"
        )
    process = session_factory(
        session_arguments(
            request,
            ssh_identity=ssh_identity,
            known_hosts=known_hosts,
        )
    )
    previous = ZERO_SHA256
    sequence = 0
    controller_transcript: list[dict[str, Any]] = []
    overall_deadline = time.monotonic() + timeout
    reader = DeadlineLineReader(process.stdout)
    try:
        process.stdin.write(canonical_json(request) + b"\n")
        process.stdin.flush()
        while True:
            remaining = min(
                line_timeout,
                overall_deadline - time.monotonic(),
            )
            if remaining <= 0:
                raise FrozenFinalRestoreOrchestratorError(
                    "interactive host control deadline expired"
                )
            raw = reader.read_line(
                maximum=MAX_HOST_RESULT_BYTES,
                timeout=remaining,
            )
            if (
                not raw
                or len(raw) > MAX_HOST_RESULT_BYTES + 1
                or not raw.endswith(b"\n")
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "remote host EOF or oversized control response"
                )
            document = strict_json(raw[:-1], label="host control response")
            if document.get("schema") == CHALLENGE_SCHEMA:
                sequence += 1
                response = _json_clone(authority_responder(document))
                if (
                    not isinstance(response, dict)
                    or set(response) != RESPONSE_FIELDS
                    or response["schema"] != RESPONSE_SCHEMA
                    or response["status"] != "controller-flock-verified"
                    or any(
                        response[field] != document[field]
                        for field in CHALLENGE_FIELDS - {"schema", "status"}
                    )
                    or response["challenge_sha256"]
                    != _sha256(canonical_json(document))
                    or response["controller_lock_held"] is not True
                    or response["controller_authoritative"] is not True
                ):
                    raise FrozenFinalRestoreOrchestratorError(
                        "authority responder returned a mismatched response"
                    )
                _nonzero_sha256(
                    response["response_nonce"],
                    label="authority responder nonce",
                )
                verification = {
                    "schema": WORKER.LIVE_AUTHORITY_SCHEMA,
                    "status": "verified-live",
                    "boundary": document["boundary"],
                    "claim_sha256": document["claim_sha256"],
                    "claim_epoch": document["claim_epoch"],
                    "claim_nonce": document["claim_nonce"],
                    "legacy_frozen_receipt_sha256": document[
                        "legacy_frozen_receipt_sha256"
                    ],
                    "controller_lock_held": True,
                    "controller_authoritative": True,
                    "verification_sequence": sequence,
                    "verification_nonce": response["response_nonce"],
                }
                entry = {
                    "schema": TRANSCRIPT_ENTRY_SCHEMA,
                    "index": sequence,
                    "challenge": document,
                    "response": response,
                    "verification": verification,
                    "previous_entry_sha256": previous,
                    "entry_sha256": ZERO_SHA256,
                }
                entry["entry_sha256"] = _entry_sha256(entry)
                previous = entry["entry_sha256"]
                controller_transcript.append(entry)
                process.stdin.write(canonical_json(response) + b"\n")
                process.stdin.flush()
                continue
            if document.get("schema") != HOST_RESULT_SCHEMA:
                raise FrozenFinalRestoreOrchestratorError(
                    "host emitted an unexpected control document"
                )
            process.stdin.close()
            if reader.has_buffered:
                raise FrozenFinalRestoreOrchestratorError(
                    "host emitted trailing bytes after its result"
                )
            post_result_remaining = min(
                line_timeout,
                MAX_POST_RESULT_EXIT_SECONDS,
                overall_deadline - time.monotonic(),
            )
            if post_result_remaining <= 0:
                raise FrozenFinalRestoreOrchestratorError(
                    "host result EOF deadline expired"
                )
            trailing = reader.read_line(
                maximum=MAX_CONTROL_BYTES,
                timeout=post_result_remaining,
            )
            if trailing or reader.has_buffered:
                raise FrozenFinalRestoreOrchestratorError(
                    "host emitted trailing bytes after its result"
                )
            wait_remaining = min(
                MAX_POST_RESULT_EXIT_SECONDS,
                overall_deadline - time.monotonic(),
            )
            if (
                wait_remaining <= 0
                or process.wait(timeout=wait_remaining) != 0
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "host agent exited unsuccessfully"
                )
            result = validate_host_result(document, request=request)
            if result["authority_transcript"] != controller_transcript:
                raise FrozenFinalRestoreOrchestratorError(
                    "host transcript differs from the controller exchange"
                )
            return result
    except BaseException:
        try:
            process.kill()
        except BaseException:
            pass
        try:
            process.wait(timeout=MAX_POST_RESULT_EXIT_SECONDS)
        except BaseException:
            pass
        raise
    finally:
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except BaseException:
                pass


def run_interactive_host(
    request_value: Mapping[str, Any],
    *,
    lease: Any,
    ssh_identity: Path,
    known_hosts: Path,
    session_factory: SessionFactory = _default_session_factory,
    timeout: float = 8 * 60 * 60,
    line_timeout: float = 120.0,
) -> dict[str, Any]:
    request = validate_host_request(request_value)
    authority_session = ControllerAuthoritySession(
        lease=lease,
        request=request,
    )
    result = run_interactive_host_with_authority(
        request,
        authority_responder=authority_session.respond,
        ssh_identity=ssh_identity,
        known_hosts=known_hosts,
        session_factory=session_factory,
        timeout=timeout,
        line_timeout=line_timeout,
    )
    if (
        result["authority_transcript"] != authority_session.transcript
        or result["authority_transcript_count"]
        != authority_session.sequence
        or result["authority_transcript_tail_sha256"]
        != authority_session.tail_sha256
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "interactive host escaped its controller authority session"
        )
    return result


def controller_plan(
    requests: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if set(requests) != set(ROLES):
        raise FrozenFinalRestoreOrchestratorError(
            "plan requires exactly three role requests"
        )
    validated = {
        role: validate_host_request(requests[role]) for role in ROLES
    }
    if any(request["action"] != "plan" for request in validated.values()):
        raise FrozenFinalRestoreOrchestratorError(
            "controller plan accepts only plan host requests"
        )
    first = validated[ROLES[0]]
    identity = (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
    )
    if any(
        request[field] != first[field]
        for request in validated.values()
        for field in identity
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "plan role identities differ"
        )
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        **{field: first[field] for field in identity},
        "roles": {
            role: {
                "host": validated[role]["expected_host"],
                "port": validated[role]["expected_port"],
                "transport": validated[role]["transport"],
                "payload_bytes_over_ssh": False,
            }
            for role in ROLES
        },
        "owner_action": WORKER.LIVE_LEASE_OWNER_ACTION,
        "success_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "plan_only_default": True,
        "fresh_live_lease_required": True,
        "explicit_exact_resume_required_after_crash": True,
        "completion_precedes_claim_consumption": True,
        "post_consumption_receipt_separate": True,
        "pull_allowed": False,
        "build_allowed": False,
        "app_services_allowed": False,
        "redis_restore_allowed": False,
        "current_mutation_allowed": False,
        "legacy_mutation_allowed": False,
    }


def canonical_controller_output_directory(
    requests: Mapping[str, Mapping[str, Any]],
) -> Path:
    """Derive the sole controller evidence root from the Nginx contract."""
    if set(requests) != set(ROLES):
        raise FrozenFinalRestoreOrchestratorError(
            "controller output derivation requires exactly three requests"
        )
    validated = {
        role: validate_host_request(requests[role]) for role in ROLES
    }
    first = validated[ROLES[0]]
    coordinator_root = (
        NGINX.CONTROLLER_SECRET_PREFIX
        / first["operation_id"]
        / "nginx-coordinator"
    )
    expected_receipts_root = coordinator_root / "receipts"
    for role, request in validated.items():
        if (
            request["campaign_id"] != first["campaign_id"]
            or request["operation_id"] != first["operation_id"]
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller output role identity differs"
            )
        receipt_path = Path(
            request["inputs"]["legacy_frozen_receipt"]
        )
        if (
            receipt_path.parent != expected_receipts_root
            or re.fullmatch(
                r"legacy-frozen-[0-9a-f]{64}\.json",
                receipt_path.name,
            )
            is None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                f"{role} legacy receipt escapes the Nginx coordinator"
            )
        authority = request["authority"]
        if authority is not None:
            claim_path = Path(authority["claim_path"])
            if (
                claim_path.parent
                != coordinator_root / "live-leases" / "claims"
                or claim_path.name
                != f"{authority['claim_sha256']}.json"
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} live claim escapes the Nginx coordinator"
                )
    return coordinator_root / (
        f"frozen-final-restore-{first['campaign_id']}"
    )


def _validate_controller_output_directory(
    output_directory: Path,
    requests: Mapping[str, Mapping[str, Any]],
) -> Path:
    if (
        not isinstance(output_directory, Path)
        or not output_directory.is_absolute()
        or output_directory
        != Path(os.path.abspath(os.fspath(output_directory)))
        or output_directory.name in {"", ".", ".."}
        or ".." in output_directory.parts
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "controller output directory is not an absolute canonical path"
        )
    expected = canonical_controller_output_directory(requests)
    prohibited = {"current", "staging", ".staging", "legacy"}
    if (
        output_directory != expected
        or prohibited & set(output_directory.parts)
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "controller output directory is not the campaign-scoped "
            "Nginx evidence root"
        )
    input_paths: set[Path] = set()
    for request_value in requests.values():
        request = validate_host_request(request_value)
        input_paths.update(
            Path(raw)
            for raw in request["inputs"].values()
            if raw is not None
        )
        input_paths.update(
            Path(request[field])
            for field in (
                "release_root",
                "agent_path",
                "installer_path",
                "worker_path",
            )
        )
    for path in input_paths:
        if (
            path == output_directory
            or path in output_directory.parents
            or output_directory in path.parents
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "controller output aliases an input or immutable release"
            )
    return output_directory


def _prepared_apply_request(
    template_value: Mapping[str, Any],
    prepared_value: Mapping[str, Any],
    *,
    lease: Any,
) -> dict[str, Any]:
    template = validate_host_request(template_value)
    prepared = validate_host_request(prepared_value)
    if template["action"] != "plan" or prepared["action"] != "apply":
        raise FrozenFinalRestoreOrchestratorError(
            "payload-preparation hook did not materialize plan to apply"
        )
    for field in HOST_REQUEST_FIELDS - {
        "action",
        "authority",
        "wa_fresh_control_exact_version",
        "inputs",
    }:
        if prepared[field] != template[field]:
            raise FrozenFinalRestoreOrchestratorError(
                f"payload-preparation hook changed immutable {field}"
            )
    for field in INPUT_PATH_FIELDS - {
        "execution_envelope",
        "fresh_live_lease_claim",
        "legacy_frozen_receipt",
        "webapp_ir_control_transfer_receipt",
    }:
        if prepared["inputs"][field] != template["inputs"][field]:
            raise FrozenFinalRestoreOrchestratorError(
                f"payload-preparation hook changed input path {field}"
            )
    claim_sha256, epoch, nonce, receipt_sha256 = (
        _lease_values_for_controller(lease)
    )
    authority = prepared["authority"]
    if (
        authority
        != {
            "claim_path": prepared["inputs"][
                "fresh_live_lease_claim"
            ],
            "claim_sha256": claim_sha256,
            "claim_epoch": epoch,
            "claim_nonce": nonce,
            "legacy_frozen_receipt_path": prepared["inputs"][
                "legacy_frozen_receipt"
            ],
            "legacy_frozen_receipt_sha256": receipt_sha256,
        }
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "payload-preparation hook bound a different live lease"
        )
    if prepared["role"] == "webapp_ir":
        restore_version = validate_wa_exact_version(
            prepared["wa_exact_version"]
        )
        fresh_version = validate_wa_fresh_control_version(
            prepared["wa_fresh_control_exact_version"],
            request=prepared,
        )
        if (
            fresh_version["version_id"] == restore_version["version_id"]
            or fresh_version["object_key"] == restore_version["object_key"]
            or prepared["inputs"][
                "webapp_ir_control_transfer_receipt"
            ]
            is None
        ):
            raise FrozenFinalRestoreOrchestratorError(
                "WebApp-IR fresh-control object is not new and distinct"
            )
    return prepared


def _prepared_request_path(
    directory: Path,
    *,
    role: str,
    claim_sha256: str,
) -> Path:
    return (
        directory
        / "prepared-requests"
        / f"{role}-{claim_sha256}.json"
    )


def _load_or_prepare_request(
    *,
    directory: Path,
    role: str,
    template: Mapping[str, Any],
    lease: Any,
    prepare_request: RoleRequestPreparer,
    checkpoint: Callable[[str], None],
) -> dict[str, Any]:
    claim_sha256, _epoch, _nonce, _receipt = (
        _lease_values_for_controller(lease)
    )
    prepared_directory = directory / "prepared-requests"
    _ensure_private_directory(prepared_directory)
    path = _prepared_request_path(
        directory,
        role=role,
        claim_sha256=claim_sha256,
    )
    try:
        os.lstat(path)
    except FileNotFoundError:
        prepared = _prepared_apply_request(
            template,
            prepare_request(role, template, lease),
            lease=lease,
        )
        checkpoint(f"after-payload-published-before-request-journal:{role}")
        _persist_create_only(
            path,
            canonical_json(prepared),
            label=f"{role} prepared request",
        )
        checkpoint(f"after-prepared-request-journal:{role}")
        return prepared
    except OSError as exc:
        raise FrozenFinalRestoreOrchestratorError(
            f"{role} prepared request is unavailable"
        ) from exc
    readback = _read_document_file(
        path,
        label=f"{role} persisted prepared request",
        newline=False,
        allowed_modes=frozenset({0o600}),
    )
    prepared = _prepared_apply_request(
        template,
        readback.document,
        lease=lease,
    )
    checkpoint(f"reused-prepared-request:{role}")
    return prepared


def run_three_roles_under_lease(
    *,
    lease: Any,
    requests: Mapping[str, Mapping[str, Any]],
    prepare_request: RoleRequestPreparer,
    invoke: HostInvoker,
    output_directory: Path,
    consumption_readback: ConsumptionReadback,
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if set(requests) != set(ROLES):
        raise FrozenFinalRestoreOrchestratorError(
            "restore execution requires exactly three role requests"
        )
    callback = checkpoint or (lambda _name: None)
    plan = controller_plan(requests)
    _validate_controller_output_directory(output_directory, requests)
    _validate_exact_controller_live_lease(
        lease,
        operation_id=plan["operation_id"],
        release_sha=plan["release_sha"],
        release_tree_sha=plan["release_tree_sha"],
    )
    with controller_journal_lock(output_directory):
        prepared_requests: dict[str, dict[str, Any]] = {}
        for role in ROLES:
            lease.verify()
            callback(f"before-payload-preparation:{role}")
            prepared_requests[role] = _load_or_prepare_request(
                directory=output_directory,
                role=role,
                template=requests[role],
                lease=lease,
                prepare_request=prepare_request,
                checkpoint=callback,
            )
            lease.verify()
            callback(f"after-payload-preparation:{role}")
        journal = ControllerJournalStore(
            output_directory,
            prepared_requests,
        )
        results: dict[str, Mapping[str, Any]] = (
            journal.load_latest_results()
        )
        if journal.document["completion"] is not None:
            try:
                recovered = recover_consumed_completion(
                    journal=journal,
                    consumption_readback=consumption_readback,
                    checkpoint=callback,
                )
            except ConsumptionAuditAbsent:
                recovered = None
            if recovered is not None:
                return recovered
            # The exact completion exists but the canonical coordinator audit
            # proves the claim is still unconsumed.  Only this typed absence
            # permits the first consume; malformed/mismatched audit failures
            # are never treated as absence.
            return consume_after_completion(
                lease=lease,
                requests=prepared_requests,
                results=results,
                output_directory=output_directory,
                journal=journal,
                consumption_readback=consumption_readback,
                checkpoint=callback,
            )
        for role in ROLES:
            request = prepared_requests[role]
            lease.verify()
            callback(f"before-role:{role}")
            authority_session = ControllerAuthoritySession(
                lease=lease,
                request=request,
            )
            observed = invoke(
                request,
                authority_session.respond,
            )
            validated = validate_host_result(
                observed,
                request=request,
            )
            if (
                not authority_session.transcript
                or validated["authority_transcript"]
                != authority_session.transcript
                or validated["authority_transcript_count"]
                != authority_session.sequence
                or validated["authority_transcript_tail_sha256"]
                != authority_session.tail_sha256
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    f"{role} result was not produced by the current "
                    "controller authority session"
                )
            # record_role compares only the persisted stable semantic and
            # actual artifact closure.  Fresh nonces/transcripts are retained
            # as a new observation rather than compared byte-for-byte.
            results[role] = journal.record_role(role, validated)
            lease.verify()
            callback(f"after-role:{role}")
        return consume_after_completion(
            lease=lease,
            requests=prepared_requests,
            results=results,
            output_directory=output_directory,
            journal=journal,
            consumption_readback=consumption_readback,
            checkpoint=callback,
        )


def _host_stdio() -> dict[str, Any]:
    reader = DeadlineLineReader(sys.stdin.buffer)
    raw = reader.read_line(
        maximum=MAX_CONTROL_BYTES,
        timeout=120.0,
    )
    if (
        not raw
        or len(raw) > MAX_CONTROL_BYTES
        or not raw.endswith(b"\n")
    ):
        raise FrozenFinalRestoreOrchestratorError(
            "host control request is missing or oversized"
        )
    request = validate_host_request(
        strict_json(raw[:-1], label="host control request")
    )
    exchange = (
        StdioAuthorityExchange(
            sys.stdin.buffer,
            sys.stdout.buffer,
            line_reader=reader,
        )
        if request["action"] == "apply"
        else None
    )
    return execute_host_request(request, exchange=exchange)


def _load_request(path: Path) -> dict[str, Any]:
    readback = _read_document_file(
        path,
        label="controller host request",
        newline=None,
    )
    return validate_host_request(readback.document)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-fi-request", type=Path)
    parser.add_argument("--webapp-fi-request", type=Path)
    parser.add_argument("--webapp-ir-request", type=Path)
    parser.add_argument("--host-stdio", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.host_stdio:
            if (
                any(
                    value is not None
                    for value in (
                        args.bot_fi_request,
                        args.webapp_fi_request,
                        args.webapp_ir_request,
                        args.confirm,
                    )
                )
                or args.apply
            ):
                raise FrozenFinalRestoreOrchestratorError(
                    "host stdio cannot be combined with controller arguments"
                )
            result = _host_stdio()
        else:
            request_paths = (
                args.bot_fi_request,
                args.webapp_fi_request,
                args.webapp_ir_request,
            )
            if any(path is None for path in request_paths):
                raise FrozenFinalRestoreOrchestratorError(
                    "all three plan request files are required"
                )
            if args.apply or args.confirm is not None:
                raise FrozenFinalRestoreOrchestratorError(
                    "CLI apply is unavailable: use the controller Python API "
                    "with a payload-preparation hook and exact live lease"
                )
            requests = {
                role: _load_request(path)
                for role, path in zip(ROLES, request_paths, strict=True)
            }
            result = controller_plan(requests)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except FrozenFinalRestoreOrchestratorError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "current_mutated": False,
                    "legacy_mutated": False,
                    "object_storage_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
