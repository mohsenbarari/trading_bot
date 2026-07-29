#!/usr/bin/env python3
"""Execute the four frozen production-shadow preparation phases safely.

The controller process owns phase ordering, the cutover journal, runtime
approval, and every live-authority response.  Each host process receives only
control arguments, derives its digest-named worker request from already
installed inputs, and runs the immutable prepare worker in its main thread.

The WebApp-IR control connection never carries a file or application payload.
Its installed release, role material, restore completion, and restore evidence
must each be bound to an exact private, versioned, age-encrypted Object Storage
VersionId before the host may create a prepare request.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import select
import selectors
import secrets
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Protocol, Sequence
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_final_restore as RESTORE_ORCHESTRATOR,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as RESTORE_WORKER,
)
from scripts import production_shadow_frozen_prepare_worker as WORKER  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


ORCHESTRATOR_RELATIVE_PATH = Path(
    "scripts/orchestrate_production_shadow_frozen_prepare.py"
)
WORKER_RELATIVE_PATH = Path(
    "scripts/production_shadow_frozen_prepare_worker.py"
)
CONTROLLER_RELATIVE_PATH = Path(
    "scripts/production_shadow_cutover_controller.py"
)

PHASES = tuple(WORKER.PHASES)
PREPARE_ROLES = tuple(RESTORE_WORKER.ROLE_NAMES)
FIRST_PHASE_INDEX = CONTROLLER.PHASES.index(PHASES[0])
PREPARE_PREFIX = tuple(CONTROLLER.PHASES[:FIRST_PHASE_INDEX])
if (
    tuple(
        CONTROLLER.PHASES[
            FIRST_PHASE_INDEX : FIRST_PHASE_INDEX + len(PHASES)
        ]
    )
    != PHASES
    or PREPARE_ROLES != ("bot_fi", "webapp_fi", "webapp_ir")
    or {
        role
        for phase in PHASES
        for role in WORKER.PHASE_ROLES[phase]
    }
    != set(PREPARE_ROLES)
    or any(
        tuple(WORKER.PHASE_ROLES[phase])
        != tuple(
            next(
                spec.roles
                for spec in CONTROLLER.PHASE_SPECS
                if spec.phase == phase
            )
        )
        for phase in PHASES
    )
):
    raise RuntimeError(
        "frozen prepare phases and roles differ from the release controller"
    )

ORCHESTRATION_REQUEST_SCHEMA = (
    "production-shadow-frozen-prepare-orchestration-request-v1"
)
HOST_INPUT_SCHEMA = "production-shadow-frozen-prepare-host-input-v1"
TRANSPORT_MANIFEST_SCHEMA = (
    "production-shadow-frozen-prepare-object-transport-v1"
)
HOST_INTENT_SCHEMA = "production-shadow-frozen-prepare-host-intent-v1"
HOST_RESULT_SCHEMA = "production-shadow-frozen-prepare-host-result-v1"
HOST_BLOCKED_SCHEMA = "production-shadow-frozen-prepare-host-blocked-v1"
PHASE_AGGREGATE_SCHEMA = (
    "production-shadow-frozen-prepare-phase-aggregate-v1"
)
FINAL_AGGREGATE_SCHEMA = (
    "production-shadow-frozen-prepare-four-phase-aggregate-v1"
)
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
AUTHORITY_TRANSCRIPT_SCHEMA = (
    "production-shadow-frozen-prepare-authority-transcript-entry-v1"
)

ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:@+-]{1,4096}$")
SAFE_BOUNDARY_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,512}$")
REQUEST_FILENAME_RE = re.compile(
    r"^(shadow_[a-z_]+)-([0-9a-f]{64})\.json$"
)
RESULT_FILENAME_RE = re.compile(
    r"^(shadow_[a-z_]+)-([0-9a-f]{64})\.json$"
)

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_CONTROL_FRAME_BYTES = 4 * 1024 * 1024
MAX_TOTAL_CONTROL_BYTES = 8 * 1024 * 1024
MAX_HOST_STDOUT_BYTES = 32 * 1024 * 1024
MAX_HOST_STDERR_BYTES = 128 * 1024
MAX_CONTROLLER_STDOUT_BYTES = 4 * 1024 * 1024
MAX_VALIDATION_STDOUT_BYTES = 512 * 1024
MAX_AUTHORITY_FRAMES = 256
CONTROL_RESPONSE_TIMEOUT_SECONDS = 180.0
PROCESS_TERM_GRACE_SECONDS = 2.0
PROCESS_KILL_GRACE_SECONDS = 2.0
POST_RESULT_EXIT_SECONDS = 5.0
PROCESS_TREE_QUIESCENCE_SECONDS = 0.10
PROCESS_POLL_SECONDS = 0.05
MAX_PROCESS_SNAPSHOT_MEMBERS = 131_072
MAX_PROCESS_TREE_MEMBERS = 4_096
PR_SET_CHILD_SUBREAPER = 36
_PROCESS_TREE_LOCK = threading.RLock()
PHASE_SESSION_TIMEOUT_SECONDS = {
    "shadow_roles_pre_migration": 30 * 60.0,
    "shadow_migrate": 75 * 60.0,
    "shadow_roles_post_migration": 35 * 60.0,
    "shadow_fence": 40 * 60.0,
}

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
}

ORCHESTRATION_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_path",
        "controller_manifest_sha256",
        "plan_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "output_root",
        "ssh_identity_path",
        "ssh_identity_sha256",
        "known_hosts_path",
        "known_hosts_sha256",
        "host_inputs",
        "prior_phase_evidence",
        "constraints",
    }
)
ORCHESTRATION_CONSTRAINT_FIELDS = frozenset(
    {
        "root_main_thread_required",
        "immutable_detached_release_required",
        "canonical_digest_named_requests_required",
        "live_journal_authority_required",
        "controller_liveness_pipe_required",
        "business_write_forbidden",
        "current_mutation_forbidden",
        "legacy_mutation_forbidden",
        "production_traffic_mutation_forbidden",
        "external_payload_over_ssh_forbidden",
        "object_storage_mutation_forbidden",
        "private_versioned_age_transport_required_for_webapp_ir",
        "process_group_cleanup_required",
        "partial_phase_completion_forbidden",
        "witness_prepare_role_forbidden",
    }
)
HOST_INPUT_REFERENCE_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "transport",
        "object_versions",
    }
)
PRIOR_EVIDENCE_REFERENCE_FIELDS = frozenset({"path", "sha256"})
HOST_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_path",
        "controller_manifest_sha256",
        "plan_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "restore_completion_path",
        "restore_completion_sha256",
        "restore_phase_evidence_path",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prepare_worker_path",
        "prepare_worker_sha256",
        "transport",
        "transport_manifest_path",
        "transport_manifest_sha256",
        "constraints",
    }
)
HOST_INPUT_CONSTRAINT_FIELDS = frozenset(
    {
        "installed_inputs_read_only",
        "request_create_only",
        "payload_over_control_forbidden",
        "external_network_forbidden",
        "object_storage_mutation_forbidden",
        "current_mutation_forbidden",
        "legacy_mutation_forbidden",
        "production_traffic_mutation_forbidden",
    }
)
TRANSPORT_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
        "objects",
        "payload_bytes_over_ssh",
        "object_storage_mutated",
        "installed_readback_verified",
    }
)
TRANSPORT_OBJECT_FIELDS = frozenset(
    {
        *RESTORE_ORCHESTRATOR.WA_VERSION_FIELDS,
        "artifact_kind",
        "artifact_sha256",
    }
)
TRANSPORT_OBJECT_KINDS = (
    "release_bundle",
    "role_material",
    "restore_completion",
    "restore_phase_evidence",
)
HOST_INTENT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "plan_sha256",
        "restore_generation_sha256",
        "host_input_sha256",
        "request_path",
        "request_sha256",
        "prior_result_path",
        "prior_result_sha256",
        "orchestrator_sha256",
        "prepare_worker_sha256",
        "release_attestation",
        "release_attestation_sha256",
        "transport",
        "transport_manifest_sha256",
        "object_versions",
        "expected_host",
        "observed_host_addresses",
        "host_identity_observed",
        "payload_bytes_over_ssh",
        "request_persisted",
        "prepare_journal_event_count",
        "prepare_journal_authority_tail_sha256",
        "prepare_journal_finalized",
    }
)
HOST_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "role",
        "phase",
        "operation",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "plan_sha256",
        "restore_generation_sha256",
        "host_input_sha256",
        "request_path",
        "request_sha256",
        "orchestrator_sha256",
        "prepare_worker_sha256",
        "release_attestation",
        "release_attestation_sha256",
        "transport",
        "transport_manifest_sha256",
        "object_versions",
        "expected_host",
        "observed_host_addresses",
        "host_identity_observed",
        "worker_return",
        "worker_result_sha256",
        "authority_transcript_count",
        "authority_transcript_tail_sha256",
        "authority_transcript_sha256",
        "payload_bytes_over_ssh",
        "control_bytes_received",
        "current_mutated",
        "legacy_mutated",
        "production_traffic_mutated",
        "business_write_observed",
        "external_network_contacted",
        "object_storage_mutated",
        "app_service_started",
    }
)
TRANSCRIPT_ENTRY_FIELDS = frozenset(
    {
        "schema",
        "index",
        "challenge_sha256",
        "response_sha256",
        "boundary",
        "sequence",
        "previous_entry_sha256",
        "entry_sha256",
    }
)
ROLE_VALIDATION_FIELDS = frozenset(VERIFY.HOST_AGENT_VALIDATION_FIELDS)
CLAIM_SOURCE_FIELDS = frozenset(VERIFY.CLAIM_SOURCE_FIELDS)


class FrozenPrepareOrchestratorError(RuntimeError):
    """The production frozen-prepare orchestration cannot safely advance."""


class FrozenPrepareOrchestratorCancellation(
    FrozenPrepareOrchestratorError
):
    """A controlling connection, process, or signal was lost."""


@dataclass(frozen=True)
class LoadedOrchestration:
    document: dict[str, Any]
    sha256: str
    path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    output_root: Path
    prior_paths: dict[str, Path]


@dataclass(frozen=True)
class LoadedHostInput:
    document: dict[str, Any]
    sha256: str
    path: Path
    role_manifest: Any
    controller_manifest: dict[str, Any]
    plan: dict[str, Any]
    transport_manifest: dict[str, Any] | None


@dataclass(frozen=True)
class ProcessControl:
    argv: tuple[str, ...]
    stdin: bytes
    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    start_new_session: bool = True
    terminate_process_group_on_exit: bool = True
    kill_process_group_after_seconds: float = PROCESS_TERM_GRACE_SECONDS


@dataclass(frozen=True)
class BoundedProcessResult:
    control_sha256: str
    returncode: int
    stdout: bytes
    stderr: bytes
    stdin_bytes_sent: int
    deadline_enforced: bool
    stdout_limit_enforced: bool
    stderr_limit_enforced: bool
    timed_out: bool
    stdout_limit_exceeded: bool
    stderr_limit_exceeded: bool
    process_group_cleanup_performed: bool
    process_group_terminated: bool


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    session_id: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


@dataclass(frozen=True)
class HostSessionResult:
    document: dict[str, Any]
    stdout_bytes: int
    stderr_bytes: int
    response_bytes: int
    process_tree_clean: bool
    deadline_enforced: bool
    stream_limits_enforced: bool


class ProcessRunner(Protocol):
    def __call__(self, control: ProcessControl) -> BoundedProcessResult:
        """Run one bounded noninteractive process."""


class InteractiveProcess(Protocol):
    stdin: BinaryIO
    stdout: BinaryIO
    stderr: BinaryIO
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class SessionFactory(Protocol):
    def __call__(self, argv: Sequence[str]) -> InteractiveProcess:
        """Start one isolated host control process."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrozenPrepareOrchestratorError(
            "document is not canonical ASCII JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenPrepareOrchestratorError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _strict_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FrozenPrepareOrchestratorError(
            f"{label} is not strict ASCII JSON"
        ) from exc
    if not isinstance(value, dict):
        raise FrozenPrepareOrchestratorError(
            f"{label} root is not an object"
        )
    return value


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise FrozenPrepareOrchestratorError(
            f"{label} is not a canonical UUID"
        )
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FrozenPrepareOrchestratorError(
            f"{label} is not a canonical UUID"
        ) from exc
    if str(parsed) != value:
        raise FrozenPrepareOrchestratorError(
            f"{label} is not a canonical UUID"
        )
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenPrepareOrchestratorError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _absolute_path(
    value: Any,
    *,
    label: str,
    prohibit_runtime_aliases: bool = True,
) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise FrozenPrepareOrchestratorError(f"{label} path is invalid")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
        or (
            prohibit_runtime_aliases
            and any(part in {"current", "staging"} for part in path.parts)
        )
    ):
        raise FrozenPrepareOrchestratorError(
            f"{label} must be an absolute normalized production path"
        )
    return path


def _read_secure_payload(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
    allowed_modes: frozenset[int] = frozenset({0o600}),
) -> bytes:
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
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or not 1 <= before.st_size <= maximum
        ):
            raise FrozenPrepareOrchestratorError(
                f"{label} ownership, mode, link count, or size is unsafe"
            )
        chunks: list[bytes] = []
        size = 0
        while size <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
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
        if len(payload) > maximum or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise FrozenPrepareOrchestratorError(
                f"{label} changed while being read"
            )
        return payload
    except FrozenPrepareOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _secure_json(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes, str]:
    payload = _read_secure_payload(path, label=label, maximum=maximum)
    document = _strict_json(payload, label=label)
    canonical = _canonical_json(document) + b"\n"
    if payload != canonical:
        raise FrozenPrepareOrchestratorError(
            f"{label} is not canonical newline-terminated JSON"
        )
    return document, payload, _sha256(payload)


def _hash_secure_file(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
    allowed_modes: frozenset[int] = frozenset({0o600}),
) -> str:
    return _sha256(
        _read_secure_payload(
            path,
            label=label,
            maximum=maximum,
            allowed_modes=allowed_modes,
        )
    )


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_path(
        path,
        label="private output directory",
        prohibit_runtime_aliases=False,
    )
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            "private output directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FrozenPrepareOrchestratorError(
            "private output directory must be root-owned mode 0700"
        )


def _persist_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str, str]:
    if (
        not isinstance(prefix, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", prefix) is None
    ):
        raise FrozenPrepareOrchestratorError(
            "publication prefix is invalid"
        )
    _ensure_private_directory(directory)
    payload = _canonical_json(dict(document)) + b"\n"
    digest = _sha256(payload)
    path = directory / f"{prefix}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=prefix,
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
        publication = "created"
    except SecureFileError:
        observed = _read_secure_payload(
            path,
            label=prefix,
            maximum=MAX_JSON_BYTES,
        )
        if observed != payload:
            raise FrozenPrepareOrchestratorError(
                f"existing {prefix} publication differs"
            )
        publication = "reused"
    readback = _read_secure_payload(
        path,
        label=f"{prefix} readback",
        maximum=MAX_JSON_BYTES,
    )
    if readback != payload:
        raise FrozenPrepareOrchestratorError(
            f"{prefix} publication readback differs"
        )
    return path, digest, publication


def _validate_object_versions(
    value: Any,
    *,
    expected_artifacts: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(
        TRANSPORT_OBJECT_KINDS
    ):
        raise FrozenPrepareOrchestratorError(
            "WebApp-IR Object Storage version set is not exact"
        )
    result: dict[str, dict[str, Any]] = {}
    identities: set[tuple[str, str, str]] = set()
    for kind in TRANSPORT_OBJECT_KINDS:
        row = value[kind]
        if (
            not isinstance(row, dict)
            or set(row) != TRANSPORT_OBJECT_FIELDS
            or row.get("artifact_kind") != kind
        ):
            raise FrozenPrepareOrchestratorError(
                f"WebApp-IR {kind} transport fields are not exact"
            )
        artifact_sha256 = _nonzero_sha256(
            row["artifact_sha256"],
            label=f"WebApp-IR {kind} artifact",
        )
        try:
            base = RESTORE_ORCHESTRATOR.validate_wa_exact_version(
                {
                    field: row[field]
                    for field in RESTORE_ORCHESTRATOR.WA_VERSION_FIELDS
                }
            )
        except RESTORE_ORCHESTRATOR.FrozenFinalRestoreOrchestratorError as exc:
            raise FrozenPrepareOrchestratorError(
                f"WebApp-IR {kind} exact VersionId is invalid"
            ) from exc
        if (
            expected_artifacts is not None
            and artifact_sha256 != expected_artifacts[kind]
        ):
            raise FrozenPrepareOrchestratorError(
                f"WebApp-IR {kind} artifact digest differs"
            )
        identity = (
            str(base["bucket"]),
            str(base["object_key"]),
            str(base["version_id"]),
        )
        if identity in identities:
            raise FrozenPrepareOrchestratorError(
                "WebApp-IR Object Storage VersionIds are not distinct"
            )
        identities.add(identity)
        result[kind] = dict(row)
    return result


def _validate_transport_manifest(
    value: Any,
    *,
    host_input: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != TRANSPORT_MANIFEST_FIELDS
        or value.get("schema") != TRANSPORT_MANIFEST_SCHEMA
        or value.get("status") != "installed-exact-version-readback"
        or value.get("role") != "webapp_ir"
        or value.get("payload_bytes_over_ssh") != 0
        or value.get("object_storage_mutated") is not False
        or value.get("installed_readback_verified") is not True
    ):
        raise FrozenPrepareOrchestratorError(
            "WebApp-IR transport manifest fields or safety closure differ"
        )
    for field in (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "restore_generation_sha256",
    ):
        if value.get(field) != host_input[field]:
            raise FrozenPrepareOrchestratorError(
                "WebApp-IR transport manifest identity differs"
            )
    expected = {
        "release_bundle": manifest["artifacts"]["release_bundle_sha256"],
        "role_material": manifest["artifacts"]["role_materials"][
            "webapp_ir"
        ]["sha256"],
        "restore_completion": host_input["restore_completion_sha256"],
        "restore_phase_evidence": host_input[
            "restore_phase_evidence_sha256"
        ],
    }
    result = dict(value)
    result["objects"] = _validate_object_versions(
        value["objects"],
        expected_artifacts=expected,
    )
    return result


def _transport_for_role(role: str) -> str:
    try:
        return str(CONTROLLER.EXPECTED_TOPOLOGY[role]["transport"])
    except KeyError as exc:
        raise FrozenPrepareOrchestratorError(
            "prepare role is not in the canonical topology"
        ) from exc


def _expected_release_root(operation_id: str, release_sha: str) -> Path:
    return (
        RESTORE_WORKER.PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
    )


def _validate_host_input_document(
    document: Any,
    *,
    path: Path,
    digest: str,
    expected_role: str,
    expected_orchestration: Mapping[str, Any] | None = None,
) -> LoadedHostInput:
    if (
        not isinstance(document, dict)
        or set(document) != HOST_INPUT_FIELDS
        or document.get("schema") != HOST_INPUT_SCHEMA
        or document.get("status") != "installed-read-only-inputs"
        or document.get("role") != expected_role
        or expected_role not in PREPARE_ROLES
    ):
        raise FrozenPrepareOrchestratorError(
            "host input manifest fields are not exact"
        )
    campaign_id = _uuid(document["campaign_id"], label="campaign ID")
    operation_id = _uuid(document["operation_id"], label="operation ID")
    if (
        campaign_id == operation_id
        or SHA40_RE.fullmatch(str(document["release_sha"])) is None
        or SHA40_RE.fullmatch(str(document["release_tree_sha"])) is None
        or document["transport"] != _transport_for_role(expected_role)
    ):
        raise FrozenPrepareOrchestratorError(
            "host input identity or transport differs"
        )
    for field in (
        "controller_manifest_sha256",
        "plan_sha256",
        "role_manifest_sha256",
        "restore_completion_sha256",
        "restore_phase_evidence_sha256",
        "restore_generation_sha256",
        "prepare_worker_sha256",
    ):
        _nonzero_sha256(document[field], label=f"host input {field}")
    constraints = document["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != HOST_INPUT_CONSTRAINT_FIELDS
        or any(value is not True for value in constraints.values())
    ):
        raise FrozenPrepareOrchestratorError(
            "host input constraints are not fail-closed"
        )

    runtime = RESTORE_WORKER.runtime_paths(
        operation_id,
        document["release_sha"],
        document["restore_generation_sha256"],
        expected_role,
    )
    role_manifest_path = _absolute_path(
        document["role_manifest_path"],
        label="role manifest",
    )
    controller_path = _absolute_path(
        document["controller_manifest_path"],
        label="controller manifest",
    )
    worker_path = _absolute_path(
        document["prepare_worker_path"],
        label="prepare worker",
    )
    expected_paths = {
        "role_manifest_path": runtime.secret_generation_root
        / "role-manifest.json",
        "controller_manifest_path": runtime.secret_generation_root
        / "controller-manifest.json",
        "prepare_worker_path": runtime.release_root / WORKER_RELATIVE_PATH,
    }
    for field, expected in expected_paths.items():
        if Path(document[field]) != expected:
            raise FrozenPrepareOrchestratorError(
                f"host input {field} is not generation-derived"
            )
    expected_input_path = (
        runtime.secret_generation_root
        / "prepare-inputs"
        / f"{digest}.json"
    )
    if path != expected_input_path:
        raise FrozenPrepareOrchestratorError(
            "host input manifest path is not digest-derived"
        )
    try:
        role_manifest = RESTORE_WORKER.load_role_manifest(role_manifest_path)
        controller_manifest, controller_sha256 = (
            CONTROLLER.read_root_only_manifest(controller_path)
        )
        plan = CONTROLLER.render_plan(
            controller_manifest,
            manifest_sha256=controller_sha256,
            manifest_path=controller_path,
        )
    except (
        RESTORE_WORKER.FrozenFinalRestoreWorkerError,
        CONTROLLER.CutoverContractError,
    ) as exc:
        raise FrozenPrepareOrchestratorError(
            "installed host input bindings are invalid"
        ) from exc
    if (
        role_manifest.canonical_sha256
        != document["role_manifest_sha256"]
        or role_manifest.operation_id != operation_id
        or role_manifest.role != expected_role
        or role_manifest.release_sha != document["release_sha"]
        or role_manifest.release_tree_sha != document["release_tree_sha"]
        or role_manifest.restore_generation_sha256
        != document["restore_generation_sha256"]
        or role_manifest.controller_manifest_sha256
        != document["controller_manifest_sha256"]
        or controller_sha256 != document["controller_manifest_sha256"]
        or plan["plan_sha256"] != document["plan_sha256"]
    ):
        raise FrozenPrepareOrchestratorError(
            "host input manifest, role manifest, or plan differs"
        )
    for field in (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
    ):
        if controller_manifest[field] != document[field]:
            raise FrozenPrepareOrchestratorError(
                "host input controller identity differs"
            )
    if (
        _hash_secure_file(
            _absolute_path(
                document["restore_completion_path"],
                label="restore completion",
            ),
            label="restore completion",
            maximum=WORKER.MAX_COMPLETION_BYTES,
        )
        != document["restore_completion_sha256"]
        or _hash_secure_file(
            _absolute_path(
                document["restore_phase_evidence_path"],
                label="restore phase evidence",
            ),
            label="restore phase evidence",
        )
        != document["restore_phase_evidence_sha256"]
        or _hash_secure_file(
            worker_path,
            label="prepare worker",
            maximum=MAX_JSON_BYTES,
            allowed_modes=frozenset({0o644, 0o755}),
        )
        != document["prepare_worker_sha256"]
    ):
        raise FrozenPrepareOrchestratorError(
            "installed host input file digest differs"
        )

    transport_manifest: dict[str, Any] | None = None
    if expected_role == "webapp_ir":
        transport_path = _absolute_path(
            document["transport_manifest_path"],
            label="WebApp-IR transport manifest",
        )
        transport_document, _payload, transport_sha256 = _secure_json(
            transport_path,
            label="WebApp-IR transport manifest",
        )
        if (
            transport_sha256 != document["transport_manifest_sha256"]
            or transport_path
            != runtime.secret_generation_root
            / "prepare-inputs"
            / f"transport-{transport_sha256}.json"
        ):
            raise FrozenPrepareOrchestratorError(
                "WebApp-IR transport manifest path or digest differs"
            )
        transport_manifest = _validate_transport_manifest(
            transport_document,
            host_input=document,
            manifest=controller_manifest,
        )
    elif (
        document["transport_manifest_path"] is not None
        or document["transport_manifest_sha256"] is not None
    ):
        raise FrozenPrepareOrchestratorError(
            "non-IR host input carries Object Storage transport metadata"
        )

    if expected_orchestration is not None:
        expected_fields = (
            "campaign_id",
            "operation_id",
            "release_sha",
            "release_tree_sha",
            "controller_manifest_sha256",
            "plan_sha256",
        )
        if any(
            document[field] != expected_orchestration[field]
            for field in expected_fields
        ):
            raise FrozenPrepareOrchestratorError(
                "host input differs from orchestration identity"
            )
    return LoadedHostInput(
        document=dict(document),
        sha256=digest,
        path=path,
        role_manifest=role_manifest,
        controller_manifest=controller_manifest,
        plan=plan,
        transport_manifest=transport_manifest,
    )


def load_host_input(
    path: Path,
    *,
    expected_sha256: str,
    expected_role: str,
    expected_orchestration: Mapping[str, Any] | None = None,
) -> LoadedHostInput:
    path = _absolute_path(path, label="host input manifest")
    expected_sha256 = _nonzero_sha256(
        expected_sha256,
        label="host input manifest",
    )
    document, _payload, observed = _secure_json(
        path,
        label="host input manifest",
    )
    if observed != expected_sha256:
        raise FrozenPrepareOrchestratorError(
            "host input manifest digest differs"
        )
    return _validate_host_input_document(
        document,
        path=path,
        digest=observed,
        expected_role=expected_role,
        expected_orchestration=expected_orchestration,
    )


def load_orchestration_request(path: Path) -> LoadedOrchestration:
    path = _absolute_path(path, label="orchestration request")
    document, payload, digest = _secure_json(
        path,
        label="orchestration request",
    )
    if (
        set(document) != ORCHESTRATION_REQUEST_FIELDS
        or document.get("schema") != ORCHESTRATION_REQUEST_SCHEMA
        or document.get("status") != "authorized-input"
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration request fields are not exact"
        )
    campaign_id = _uuid(document["campaign_id"], label="campaign ID")
    operation_id = _uuid(document["operation_id"], label="operation ID")
    if (
        campaign_id == operation_id
        or SHA40_RE.fullmatch(str(document["release_sha"])) is None
        or SHA40_RE.fullmatch(str(document["release_tree_sha"])) is None
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration release or operation identity is invalid"
        )
    for field in (
        "controller_manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "approval_policy_sha256",
        "ssh_identity_sha256",
        "known_hosts_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    constraints = document["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != ORCHESTRATION_CONSTRAINT_FIELDS
        or any(value is not True for value in constraints.values())
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration constraints are not fail-closed"
        )

    manifest_path = _absolute_path(
        document["controller_manifest_path"],
        label="controller manifest",
    )
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            manifest_path
        )
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=manifest_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenPrepareOrchestratorError(
            "controller manifest or plan is invalid"
        ) from exc
    if (
        manifest_sha256 != document["controller_manifest_sha256"]
        or plan["plan_sha256"] != document["plan_sha256"]
        or any(
            manifest[field] != document[field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
            )
        )
        or manifest["topology"] != CONTROLLER.EXPECTED_TOPOLOGY
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration manifest, plan, release, or topology differs"
        )
    approval_path = _absolute_path(
        document["approval_path"],
        label="production approval",
        prohibit_runtime_aliases=False,
    )
    approval_policy_path = _absolute_path(
        document["approval_policy_path"],
        label="production approval policy",
        prohibit_runtime_aliases=False,
    )
    if (
        _hash_secure_file(
            approval_path,
            label="production approval",
            maximum=16 * 1024 * 1024,
        )
        != document["approval_sha256"]
        or _hash_secure_file(
            approval_policy_path,
            label="production approval policy",
            maximum=4 * 1024 * 1024,
        )
        != document["approval_policy_sha256"]
        or document["approval_sha256"]
        != manifest["artifacts"]["cutover_approval_sha256"]
        or document["approval_policy_sha256"]
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise FrozenPrepareOrchestratorError(
            "production approval or policy binding differs"
        )

    output_root = _absolute_path(
        document["output_root"],
        label="orchestration output root",
        prohibit_runtime_aliases=False,
    )
    expected_output = (
        Path(manifest["deployment"]["controller_evidence_root"])
        / "frozen-prepare"
    )
    if (
        output_root != expected_output
        or path
        != output_root / "requests" / f"orchestrate.{digest}.json"
        or payload != _canonical_json(document) + b"\n"
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration request or output path is not digest-derived"
        )

    host_inputs = document["host_inputs"]
    if not isinstance(host_inputs, dict) or set(host_inputs) != set(
        PREPARE_ROLES
    ):
        raise FrozenPrepareOrchestratorError(
            "orchestration host input roles are not exact"
        )
    for role in PREPARE_ROLES:
        row = host_inputs[role]
        if (
            not isinstance(row, dict)
            or set(row) != HOST_INPUT_REFERENCE_FIELDS
            or row["transport"] != _transport_for_role(role)
        ):
            raise FrozenPrepareOrchestratorError(
                f"{role} host input reference differs"
            )
        input_path = _absolute_path(
            row["path"],
            label=f"{role} host input",
        )
        input_sha256 = _nonzero_sha256(
            row["sha256"],
            label=f"{role} host input",
        )
        runtime_root = (
            RESTORE_WORKER.SECRET_ROOT_PREFIX
            / operation_id
            / "frozen-final-generations"
        )
        try:
            relative = input_path.relative_to(runtime_root)
        except ValueError as exc:
            raise FrozenPrepareOrchestratorError(
                f"{role} host input is outside the operation secret root"
            ) from exc
        if (
            len(relative.parts) != 4
            or relative.parts[1] != RESTORE_WORKER.ROLE_PATHS[role]
            or relative.parts[2] != "prepare-inputs"
            or relative.parts[3] != f"{input_sha256}.json"
        ):
            raise FrozenPrepareOrchestratorError(
                f"{role} host input path is not role and digest derived"
            )
        if role == "webapp_ir":
            _validate_object_versions(row["object_versions"])
        elif row["object_versions"] != {}:
            raise FrozenPrepareOrchestratorError(
                f"{role} unexpectedly carries Object Storage versions"
            )

    prior = document["prior_phase_evidence"]
    if not isinstance(prior, dict) or set(prior) != set(PREPARE_PREFIX):
        raise FrozenPrepareOrchestratorError(
            "orchestration prior evidence prefix is not exact"
        )
    prior_paths: dict[str, Path] = {}
    for phase in PREPARE_PREFIX:
        row = prior[phase]
        if (
            not isinstance(row, dict)
            or set(row) != PRIOR_EVIDENCE_REFERENCE_FIELDS
        ):
            raise FrozenPrepareOrchestratorError(
                f"prior phase {phase} reference fields differ"
            )
        evidence_path = _absolute_path(
            row["path"],
            label=f"prior phase {phase} evidence",
            prohibit_runtime_aliases=False,
        )
        expected_sha256 = _nonzero_sha256(
            row["sha256"],
            label=f"prior phase {phase} evidence",
        )
        try:
            evidence, observed_sha256 = VERIFY.read_root_only_evidence(
                evidence_path
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise FrozenPrepareOrchestratorError(
                f"prior phase {phase} evidence is unsafe"
            ) from exc
        if (
            observed_sha256 != expected_sha256
            or evidence.get("phase") != phase
            or any(
                evidence.get(field) != document[field]
                for field in (
                    "campaign_id",
                    "operation_id",
                    "release_sha",
                )
            )
            or evidence.get("legacy_release_sha")
            != manifest["legacy_release_sha"]
            or evidence.get("manifest_sha256") != manifest_sha256
            or evidence.get("plan_sha256") != plan["plan_sha256"]
            or evidence.get("status") != "passed"
            or evidence.get("business_write_observed") is not False
        ):
            raise FrozenPrepareOrchestratorError(
                f"prior phase {phase} evidence binding differs"
            )
        prior_paths[phase] = evidence_path

    ssh_identity = _absolute_path(
        document["ssh_identity_path"],
        label="SSH identity",
        prohibit_runtime_aliases=False,
    )
    known_hosts = _absolute_path(
        document["known_hosts_path"],
        label="SSH known-hosts",
        prohibit_runtime_aliases=False,
    )
    if (
        _hash_secure_file(
            ssh_identity,
            label="SSH identity",
            maximum=1024 * 1024,
        )
        != document["ssh_identity_sha256"]
        or _hash_secure_file(
            known_hosts,
            label="SSH known-hosts",
            maximum=4 * 1024 * 1024,
            allowed_modes=frozenset({0o600, 0o644}),
        )
        != document["known_hosts_sha256"]
    ):
        raise FrozenPrepareOrchestratorError(
            "SSH identity or known-hosts digest differs"
        )

    # Bot-FI is the controller host.  Its installed input is therefore a
    # local, read-only anchor for the release and stable prepare inputs.
    bot_row = host_inputs["bot_fi"]
    load_host_input(
        Path(bot_row["path"]),
        expected_sha256=bot_row["sha256"],
        expected_role="bot_fi",
        expected_orchestration=document,
    )
    return LoadedOrchestration(
        document=dict(document),
        sha256=digest,
        path=path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        output_root=output_root,
        prior_paths=prior_paths,
    )


def orchestration_confirmation(context: LoadedOrchestration) -> str:
    return (
        "apply-production-shadow-frozen-prepare-orchestration:"
        f"{context.document['operation_id']}:{context.sha256}"
    )


def _find_prior_result(
    loaded: LoadedHostInput,
    *,
    phase: str,
) -> tuple[Path | None, str | None]:
    role = loaded.document["role"]
    try:
        prior_phase = WORKER._expected_prior_phase(phase, role)  # noqa: SLF001
    except WORKER.FrozenPrepareWorkerError as exc:
        raise FrozenPrepareOrchestratorError(
            "prepare prior phase contract is invalid"
        ) from exc
    if prior_phase is None:
        return None, None
    directory = (
        loaded.role_manifest.paths.secret_generation_root
        / "prepare-phases"
        / prior_phase
        / "results"
    )
    try:
        names = sorted(entry.name for entry in os.scandir(directory))
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            f"{role} prior prepare result is unavailable"
        ) from exc
    if len(names) != 1:
        raise FrozenPrepareOrchestratorError(
            f"{role} prior prepare result namespace is not exact"
        )
    match = RESULT_FILENAME_RE.fullmatch(names[0])
    if match is None or match.group(1) != prior_phase:
        raise FrozenPrepareOrchestratorError(
            f"{role} prior prepare result filename differs"
        )
    result_path = directory / names[0]
    _document, payload, observed = _secure_json(
        result_path,
        label=f"{role} prior prepare result",
    )
    if (
        observed != match.group(2)
        or payload != _canonical_json(_document) + b"\n"
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} prior prepare result digest differs"
        )
    return result_path, observed


def _build_prepare_request(
    loaded: LoadedHostInput,
    *,
    phase: str,
) -> tuple[dict[str, Any], bytes, str, Path]:
    if (
        phase not in PHASES
        or loaded.document["role"] not in WORKER.PHASE_ROLES[phase]
    ):
        raise FrozenPrepareOrchestratorError(
            "host phase/role pair is not an exact prepare operation"
        )
    role = loaded.document["role"]
    prior_path, prior_sha256 = _find_prior_result(loaded, phase=phase)
    output_root = (
        loaded.role_manifest.paths.secret_generation_root
        / "prepare-phases"
        / phase
    )
    document = {
        "schema": WORKER.REQUEST_SCHEMA,
        "status": "authorized-input",
        "campaign_id": loaded.document["campaign_id"],
        "operation_id": loaded.document["operation_id"],
        "role": role,
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "release_sha": loaded.document["release_sha"],
        "release_tree_sha": loaded.document["release_tree_sha"],
        "controller_manifest_path": loaded.document[
            "controller_manifest_path"
        ],
        "controller_manifest_sha256": loaded.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": loaded.document["plan_sha256"],
        "role_manifest_path": loaded.document["role_manifest_path"],
        "role_manifest_sha256": loaded.document["role_manifest_sha256"],
        "restore_completion_path": loaded.document[
            "restore_completion_path"
        ],
        "restore_completion_sha256": loaded.document[
            "restore_completion_sha256"
        ],
        "restore_phase_evidence_path": loaded.document[
            "restore_phase_evidence_path"
        ],
        "restore_phase_evidence_sha256": loaded.document[
            "restore_phase_evidence_sha256"
        ],
        "restore_generation_sha256": loaded.document[
            "restore_generation_sha256"
        ],
        "prepare_worker_path": loaded.document["prepare_worker_path"],
        "prepare_worker_sha256": loaded.document[
            "prepare_worker_sha256"
        ],
        "prior_result_path": (
            os.fspath(prior_path) if prior_path is not None else None
        ),
        "prior_result_sha256": prior_sha256,
        "output_root": os.fspath(output_root),
        "constraints": {
            field: True for field in WORKER.CONSTRAINT_FIELDS
        },
    }
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    request_path = (
        loaded.role_manifest.paths.secret_generation_root
        / "prepare-requests"
        / f"{phase}-{digest}.json"
    )
    directory = request_path.parent
    try:
        names = (
            sorted(entry.name for entry in os.scandir(directory))
            if directory.exists()
            else []
        )
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare request namespace is unavailable"
        ) from exc
    phase_names = [
        name
        for name in names
        if (match := REQUEST_FILENAME_RE.fullmatch(name)) is not None
        and match.group(1) == phase
    ]
    if phase_names not in ([], [request_path.name]):
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare request namespace contains a foreign request"
        )
    return document, payload, digest, request_path


def _persist_prepare_request(
    loaded: LoadedHostInput,
    *,
    document: Mapping[str, Any],
    payload: bytes,
    digest: str,
    path: Path,
) -> None:
    expected = (
        loaded.role_manifest.paths.secret_generation_root
        / "prepare-requests"
        / f"{document['phase']}-{digest}.json"
    )
    if (
        path != expected
        or payload != _canonical_json(dict(document)) + b"\n"
        or _sha256(payload) != digest
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare request persistence binding differs"
        )
    _ensure_private_directory(path.parent)
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="frozen prepare request",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        existing = _read_secure_payload(
            path,
            label="existing frozen prepare request",
        )
        if existing != payload:
            raise FrozenPrepareOrchestratorError(
                "existing frozen prepare request differs"
            )
    if _read_secure_payload(
        path,
        label="frozen prepare request readback",
    ) != payload:
        raise FrozenPrepareOrchestratorError(
            "frozen prepare request readback differs"
        )


def _git_readonly(arguments: Sequence[str]) -> str:
    try:
        return RESTORE_WORKER._run_readonly(  # noqa: SLF001
            [RESTORE_WORKER.GIT, *arguments]
        )
    except RESTORE_WORKER.FrozenFinalRestoreWorkerError as exc:
        raise FrozenPrepareOrchestratorError(
            "immutable release Git attestation failed"
        ) from exc


def _attest_immutable_release(
    loaded: LoadedHostInput,
) -> tuple[dict[str, Any], str]:
    release_root = loaded.role_manifest.paths.release_root
    expected_root = _expected_release_root(
        loaded.document["operation_id"],
        loaded.document["release_sha"],
    )
    caller_path = Path(__file__).resolve()
    worker_path = Path(WORKER.__file__).resolve()
    controller_path = Path(CONTROLLER.__file__).resolve()
    verifier_path = Path(VERIFY.__file__).resolve()
    expected_paths = {
        "orchestrator": release_root / ORCHESTRATOR_RELATIVE_PATH,
        "prepare_worker": release_root / WORKER_RELATIVE_PATH,
        "controller": release_root / CONTROLLER_RELATIVE_PATH,
        "phase_verifier": (
            release_root / CONTROLLER.PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH
        ),
    }
    observed_paths = {
        "orchestrator": caller_path,
        "prepare_worker": worker_path,
        "controller": controller_path,
        "phase_verifier": verifier_path,
    }
    if (
        release_root != expected_root
        or observed_paths != expected_paths
        or loaded.document["prepare_worker_path"]
        != os.fspath(expected_paths["prepare_worker"])
    ):
        raise FrozenPrepareOrchestratorError(
            "running prepare caller or imported module is outside "
            "the exact operation release"
        )
    head = _git_readonly(("-C", os.fspath(release_root), "rev-parse", "HEAD"))
    tree = _git_readonly(
        ("-C", os.fspath(release_root), "rev-parse", "HEAD^{tree}")
    )
    branch = _git_readonly(
        ("-C", os.fspath(release_root), "branch", "--show-current")
    )
    status_output = _git_readonly(
        (
            "-C",
            os.fspath(release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
    )
    relative_paths = {
        os.fspath(path.relative_to(release_root))
        for path in expected_paths.values()
    }
    tracked_rows = _git_readonly(
        (
            "-C",
            os.fspath(release_root),
            "ls-files",
            "--stage",
            "--",
            *sorted(relative_paths),
        )
    ).splitlines()
    tracked_paths: set[str] = set()
    for row in tracked_rows:
        match = re.fullmatch(r"100(644|755) [0-9a-f]{40} 0\t(.+)", row)
        if match is None:
            raise FrozenPrepareOrchestratorError(
                "immutable caller release tracking row is invalid"
            )
        tracked_paths.add(match.group(2))
    if (
        head != loaded.document["release_sha"]
        or tree != loaded.document["release_tree_sha"]
        or branch != ""
        or status_output != ""
        or tracked_paths != relative_paths
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare caller release is not detached, exact, clean, and tracked"
        )
    hashes = {
        name: _hash_secure_file(
            path,
            label=f"release {name}",
            maximum=MAX_JSON_BYTES,
            allowed_modes=frozenset({0o644, 0o755}),
        )
        for name, path in expected_paths.items()
    }
    if (
        hashes["prepare_worker"]
        != loaded.document["prepare_worker_sha256"]
        or hashes["phase_verifier"]
        != loaded.controller_manifest["artifacts"][
            "phase_evidence_verifier_sha256"
        ]
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare caller release artifact hash differs"
        )
    attestation = {
        "schema": "production-shadow-frozen-prepare-release-attestation-v1",
        "status": "verified",
        "operation_id": loaded.document["operation_id"],
        "role": loaded.document["role"],
        "release_sha": head,
        "release_tree_sha": tree,
        "detached": True,
        "clean": True,
        "tracked_paths": sorted(tracked_paths),
        "artifact_sha256": hashes,
    }
    return attestation, _sha256(_canonical_json(attestation))


def _process_control_sha256(control: ProcessControl) -> str:
    return _sha256(
        _canonical_json(
            {
                "argv": list(control.argv),
                "stdin_sha256": _sha256(control.stdin),
                "stdin_bytes": len(control.stdin),
                "timeout_seconds": control.timeout_seconds,
                "max_stdout_bytes": control.max_stdout_bytes,
                "max_stderr_bytes": control.max_stderr_bytes,
                "start_new_session": control.start_new_session,
                "terminate_process_group_on_exit": (
                    control.terminate_process_group_on_exit
                ),
                "kill_process_group_after_seconds": (
                    control.kill_process_group_after_seconds
                ),
            }
        )
    )


def _validate_process_control(control: ProcessControl) -> None:
    if (
        type(control) is not ProcessControl
        or not control.argv
        or any(
            not isinstance(token, str) or not token
            for token in control.argv
        )
        or not isinstance(control.stdin, bytes)
        or type(control.timeout_seconds) not in {int, float}
        or not 0 < control.timeout_seconds <= 8 * 60 * 60
        or type(control.max_stdout_bytes) is not int
        or not 1 <= control.max_stdout_bytes <= 64 * 1024 * 1024
        or type(control.max_stderr_bytes) is not int
        or not 1 <= control.max_stderr_bytes <= 4 * 1024 * 1024
        or control.start_new_session is not True
        or control.terminate_process_group_on_exit is not True
        or type(control.kill_process_group_after_seconds)
        not in {int, float}
        or not 0.1
        <= control.kill_process_group_after_seconds
        <= 10.0
        or control.argv[0] not in {"/usr/bin/python3", "/usr/bin/ssh"}
    ):
        raise FrozenPrepareOrchestratorError(
            "bounded process control is invalid"
        )


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise FrozenPrepareOrchestratorError(
            f"child subreaper setup failed with errno {error}"
        )


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
            session_id=int(fields[3], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _process_snapshot() -> dict[int, ProcessIdentity]:
    result: dict[int, ProcessIdentity] = {}
    scanned = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_PROCESS_SNAPSHOT_MEMBERS:
                raise FrozenPrepareOrchestratorError(
                    "subprocess inventory exceeds its process bound"
                )
            identity = _process_identity(int(entry.name, 10))
            if identity is not None:
                result[identity.pid] = identity
    except FrozenPrepareOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            "subprocess ownership inventory is unavailable"
        ) from exc
    return result


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_pid == owner
    )


def _owned_processes(
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity] | None = None,
) -> set[ProcessIdentity]:
    snapshot = _process_snapshot()
    owned_pids: set[int] = set()
    observed_root = snapshot.get(root_identity.pid)
    if (
        observed_root is not None
        and observed_root.start_time == root_identity.start_time
    ):
        owned_pids.add(root_identity.pid)
    if tracked is not None:
        for identity in tracked:
            current = snapshot.get(identity.pid)
            if (
                current is not None
                and current.start_time == identity.start_time
            ):
                owned_pids.add(identity.pid)
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.pid != root_identity.pid
            and identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_pids.add(identity.pid)
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.pid not in owned_pids
                and identity.parent_pid in owned_pids
            ):
                owned_pids.add(identity.pid)
                changed = True
    owned = {
        identity
        for pid, identity in snapshot.items()
        if pid in owned_pids
    }
    if tracked is not None:
        if len(tracked | owned) > MAX_PROCESS_TREE_MEMBERS:
            raise FrozenPrepareOrchestratorError(
                "subprocess tree exceeds its process bound"
            )
        tracked.update(owned)
    return owned


def _identity_is_live(identity: ProcessIdentity) -> bool:
    observed = _process_identity(identity.pid)
    return (
        observed is not None
        and observed.start_time == identity.start_time
        and observed.state != "Z"
    )


def _signal_identity(identity: ProcessIdentity, signum: int) -> None:
    observed = _process_identity(identity.pid)
    if observed is None or observed.start_time != identity.start_time:
        return
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise FrozenPrepareOrchestratorError(
            "identity-bound pidfd process control is unavailable"
        )
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            "identity-bound subprocess handle cannot be opened"
        ) from exc
    try:
        refreshed = _process_identity(identity.pid)
        if (
            refreshed is not None
            and refreshed.start_time == identity.start_time
        ):
            _signal_process_handle(descriptor, signum)
    except ProcessLookupError:
        return
    finally:
        os.close(descriptor)


def _signal_process_handle(descriptor: int, signum: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            "identity-bound subprocess signal failed"
        ) from exc


def _signal_owned_process(
    identity: ProcessIdentity,
    signum: int,
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
) -> None:
    if identity.key == root_identity.key and root_descriptor is not None:
        _signal_process_handle(root_descriptor, signum)
        return
    _signal_identity(identity, signum)


def _terminate_process_tree(
    process: subprocess.Popen[bytes] | InteractiveProcess,
    tracked: set[ProcessIdentity],
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    root_group = root_identity.process_group

    def refresh() -> set[ProcessIdentity]:
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        return {
            identity for identity in tracked if _identity_is_live(identity)
        }

    live = refresh()
    for identity in live:
        _signal_owned_process(
            identity,
            (
                signal.SIGTERM
                if identity.process_group == root_group
                else signal.SIGKILL
            ),
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
    deadline = time.monotonic() + PROCESS_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        if not refresh():
            break
        time.sleep(PROCESS_POLL_SECONDS)
    live = refresh()
    for identity in live:
        _signal_owned_process(
            identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
    try:
        process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_owned_process(
            root_identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
        try:
            process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise FrozenPrepareOrchestratorError(
                "subprocess root survived identity-bound cleanup"
            ) from exc

    def reap_tracked_children() -> None:
        for identity in tuple(tracked):
            if identity.pid == process.pid:
                continue
            observed = _process_identity(identity.pid)
            if (
                observed is None
                or observed.start_time != identity.start_time
                or observed.parent_pid != os.getpid()
            ):
                continue
            try:
                os.waitpid(identity.pid, os.WNOHANG)
            except ChildProcessError:
                pass

    absence_deadline = (
        time.monotonic()
        + PROCESS_KILL_GRACE_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        reap_tracked_children()
        live = refresh()
        if live:
            stable_since = None
            for identity in live:
                _signal_owned_process(
                    identity,
                    signal.SIGKILL,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                )
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= PROCESS_TREE_QUIESCENCE_SECONDS
        ):
            return
        time.sleep(PROCESS_POLL_SECONDS)
    reap_tracked_children()
    if refresh():
        raise FrozenPrepareOrchestratorError(
            "subprocess process tree survived forced cleanup"
        )


def _run_bounded_process_locked(
    control: ProcessControl,
) -> BoundedProcessResult:
    """Run a noninteractive subprocess with hard stream and group bounds."""

    _validate_process_control(control)
    process: subprocess.Popen[bytes] | None = None
    root_identity: ProcessIdentity | None = None
    root_descriptor: int | None = None
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    stdin_offset = 0
    timed_out = False
    stdout_exceeded = False
    stderr_exceeded = False
    cleanup_performed = False
    group_terminated = False
    tracked: set[ProcessIdentity] = set()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    try:
        process = subprocess.Popen(
            list(control.argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(SAFE_ENV),
            close_fds=True,
            start_new_session=True,
        )
        root_descriptor = os.pidfd_open(process.pid, 0)
        root_identity = _process_identity(process.pid)
        if root_identity is None:
            raise FrozenPrepareOrchestratorError(
                "bounded process identity is unavailable"
            )
        if (
            root_identity.process_group != process.pid
            or root_identity.session_id != process.pid
        ):
            raise FrozenPrepareOrchestratorError(
                "bounded process is not in its own session"
            )
        tracked.add(root_identity)
        if (
            process.stdin is None
            or process.stdout is None
            or process.stderr is None
        ):
            raise FrozenPrepareOrchestratorError(
                "bounded process pipes are unavailable"
            )
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if control.stdin:
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        open_outputs = {"stdout", "stderr"}
        deadline = time.monotonic() + control.timeout_seconds
        while open_outputs or process.poll() is None:
            tracked.update(
                _owned_processes(
                    root_identity,
                    baseline_children=baseline_children,
                    tracked=tracked,
                )
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(remaining, 0.1))
            for key, mask in events:
                label = key.data
                stream = key.fileobj
                if label == "stdin" and mask & selectors.EVENT_WRITE:
                    try:
                        sent = os.write(
                            stream.fileno(),
                            control.stdin[stdin_offset : stdin_offset + 65536],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        sent = 0
                    stdin_offset += sent
                    if sent == 0 or stdin_offset == len(control.stdin):
                        selector.unregister(stream)
                        stream.close()
                    continue
                if not mask & selectors.EVENT_READ:
                    continue
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    open_outputs.discard(label)
                    continue
                target = stdout if label == "stdout" else stderr
                maximum = (
                    control.max_stdout_bytes
                    if label == "stdout"
                    else control.max_stderr_bytes
                )
                available = max(0, maximum - len(target))
                target.extend(chunk[:available])
                if len(chunk) > available:
                    if label == "stdout":
                        stdout_exceeded = True
                    else:
                        stderr_exceeded = True
            if stdout_exceeded or stderr_exceeded:
                break
            if process.poll() is not None and not events:
                # Pipes may need one final selector turn after process exit.
                if not open_outputs:
                    break
        had_live_tree = any(
            _identity_is_live(identity) for identity in tracked
        )
        _terminate_process_tree(
            process,
            tracked,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
            baseline_children=baseline_children,
        )
        cleanup_performed = True
        group_terminated = had_live_tree
        returncode = process.poll()
        if returncode is None:
            raise FrozenPrepareOrchestratorError(
                "bounded process retained no final return code"
            )
        return BoundedProcessResult(
            control_sha256=_process_control_sha256(control),
            returncode=returncode,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            stdin_bytes_sent=stdin_offset,
            deadline_enforced=True,
            stdout_limit_enforced=True,
            stderr_limit_enforced=True,
            timed_out=timed_out,
            stdout_limit_exceeded=stdout_exceeded,
            stderr_limit_exceeded=stderr_exceeded,
            process_group_cleanup_performed=cleanup_performed,
            process_group_terminated=group_terminated,
        )
    except FrozenPrepareOrchestratorError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrozenPrepareOrchestratorError(
            "bounded process execution failed"
        ) from exc
    finally:
        original_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        if process is not None:
            try:
                if not cleanup_performed:
                    if root_identity is None:
                        if root_descriptor is None:
                            root_identity = _process_identity(process.pid)
                            if root_identity is None:
                                raise FrozenPrepareOrchestratorError(
                                    "unidentified bounded process root "
                                    "cannot be recovered"
                                )
                        else:
                            _signal_process_handle(
                                root_descriptor,
                                signal.SIGKILL,
                            )
                            try:
                                process.wait(
                                    timeout=PROCESS_KILL_GRACE_SECONDS
                                )
                            except subprocess.TimeoutExpired as exc:
                                raise FrozenPrepareOrchestratorError(
                                    "unidentified bounded process root "
                                    "survived forced cleanup"
                                ) from exc
                            root_identity = ProcessIdentity(
                                pid=process.pid,
                                parent_pid=os.getpid(),
                                process_group=process.pid,
                                session_id=process.pid,
                                start_time=-1,
                                state="?",
                            )
                    _terminate_process_tree(
                        process,
                        tracked,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline_children,
                    )
            except BaseException as exc:
                cleanup_errors.append(exc)
                if root_descriptor is not None:
                    try:
                        _signal_process_handle(
                            root_descriptor,
                            signal.SIGKILL,
                        )
                        process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
                    except ProcessLookupError:
                        pass
                    except BaseException as fallback_exc:
                        cleanup_errors.append(fallback_exc)
        try:
            selector.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    if not stream.closed:
                        stream.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            cleanup_error = cleanup_errors[0]
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def run_bounded_process(control: ProcessControl) -> BoundedProcessResult:
    with _PROCESS_TREE_LOCK:
        return _run_bounded_process_locked(control)


def _validate_bounded_result(
    result: Any,
    *,
    control: ProcessControl,
    label: str,
) -> BoundedProcessResult:
    if (
        type(result) is not BoundedProcessResult
        or result.control_sha256 != _process_control_sha256(control)
        or type(result.returncode) is not int
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or result.stdin_bytes_sent != len(control.stdin)
        or result.deadline_enforced is not True
        or result.stdout_limit_enforced is not True
        or result.stderr_limit_enforced is not True
        or result.process_group_cleanup_performed is not True
        or len(result.stdout) > control.max_stdout_bytes
        or len(result.stderr) > control.max_stderr_bytes
    ):
        raise FrozenPrepareOrchestratorError(
            f"{label} bounded process result is invalid"
        )
    return result


def _invoke_process(
    runner: ProcessRunner,
    control: ProcessControl,
    *,
    label: str,
) -> BoundedProcessResult:
    _validate_process_control(control)
    try:
        observed = runner(control)
    except Exception as exc:
        raise FrozenPrepareOrchestratorError(
            f"{label} execution failed"
        ) from exc
    return _validate_bounded_result(
        observed,
        control=control,
        label=label,
    )


def _default_session_factory(argv: Sequence[str]) -> InteractiveProcess:
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(SAFE_ENV),
        close_fds=True,
        start_new_session=True,
    )
    descriptor: int | None = None
    try:
        descriptor = os.pidfd_open(process.pid, 0)
        identity = _process_identity(process.pid)
        if identity is None:
            raise FrozenPrepareOrchestratorError(
                "host control process identity is unavailable"
            )
        setattr(
            process,
            "_production_shadow_root_descriptor",
            descriptor,
        )
        setattr(
            process,
            "_production_shadow_root_identity",
            identity,
        )
        return process
    except BaseException:
        if descriptor is not None:
            try:
                _signal_process_handle(descriptor, signal.SIGKILL)
                process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
            finally:
                os.close(descriptor)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    pass
        raise


def _release_artifact_hashes(
    context: LoadedOrchestration,
) -> dict[str, str]:
    release_root = _expected_release_root(
        context.document["operation_id"],
        context.document["release_sha"],
    )
    result = {
        "orchestrator": _hash_secure_file(
            release_root / ORCHESTRATOR_RELATIVE_PATH,
            label="local immutable prepare orchestrator",
            allowed_modes=frozenset({0o644, 0o755}),
        ),
        "prepare_worker": _hash_secure_file(
            release_root / WORKER_RELATIVE_PATH,
            label="local immutable prepare worker",
            allowed_modes=frozenset({0o644, 0o755}),
        ),
    }
    bot_ref = context.document["host_inputs"]["bot_fi"]
    bot_input = load_host_input(
        Path(bot_ref["path"]),
        expected_sha256=bot_ref["sha256"],
        expected_role="bot_fi",
        expected_orchestration=context.document,
    )
    _attestation, _digest = _attest_immutable_release(bot_input)
    if result["prepare_worker"] != bot_input.document[
        "prepare_worker_sha256"
    ]:
        raise FrozenPrepareOrchestratorError(
            "local worker hash differs from Bot-FI installed input"
        )
    return result


def session_arguments(
    context: LoadedOrchestration,
    *,
    phase: str,
    role: str,
    orchestrator_sha256: str,
) -> tuple[str, ...]:
    if (
        phase not in PHASES
        or role not in WORKER.PHASE_ROLES[phase]
        or role == "witness"
    ):
        raise FrozenPrepareOrchestratorError(
            "host session phase/role is invalid"
        )
    orchestrator_sha256 = _nonzero_sha256(
        orchestrator_sha256,
        label="orchestrator artifact",
    )
    row = context.document["host_inputs"][role]
    release_root = _expected_release_root(
        context.document["operation_id"],
        context.document["release_sha"],
    )
    remote = (
        "/usr/bin/python3",
        "-I",
        "-B",
        os.fspath(release_root / ORCHESTRATOR_RELATIVE_PATH),
        "host",
        "--input-manifest",
        row["path"],
        "--input-sha256",
        row["sha256"],
        "--role",
        role,
        "--phase",
        phase,
        "--expected-orchestrator-sha256",
        orchestrator_sha256,
    )
    if any(SAFE_REMOTE_TOKEN_RE.fullmatch(token) is None for token in remote):
        raise FrozenPrepareOrchestratorError(
            "host session command contains an unsafe remote token"
        )
    topology = context.manifest["topology"][role]
    if topology["transport"] == "local-controller":
        return remote
    ssh_identity = context.document["ssh_identity_path"]
    known_hosts = context.document["known_hosts_path"]
    argv = (
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
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
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-T",
        "-i",
        ssh_identity,
        "-p",
        str(topology["ssh_port"]),
        f"{topology['ssh_user']}@{topology['host']}",
        *remote,
    )
    lowered = " ".join(argv).lower()
    if (
        any(
            token in argv
            for token in ("/usr/bin/scp", "scp", "rsync", "sftp")
        )
        or "--execute" in argv
        or "payload" in lowered
        or "presigned" in lowered
        or "version_id=" in lowered
    ):
        raise FrozenPrepareOrchestratorError(
            "host session command attempts to carry a payload"
        )
    return argv


def _validate_authority_response(
    response: Any,
    *,
    challenge: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(response, dict)
        or set(response) != WORKER.AUTHORITY_RESPONSE_FIELDS
        or response.get("schema") != WORKER.AUTHORITY_RESPONSE_SCHEMA
        or response.get("status") != "verified-live"
        or any(
            response.get(field) != value
            for field, value in challenge.items()
            if field not in {"schema", "status"}
        )
        or response.get("challenge_sha256")
        != _sha256(_canonical_json(dict(challenge)))
        or response.get("controller_lock_held") is not True
        or response.get("controller_authoritative") is not True
        or response.get("journal_status") != "phase_started"
        or response.get("started_phase") != challenge["phase"]
        or response.get("completed_phases")
        != list(
            CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(str(challenge["phase"]))
            ]
        )
        or response.get("business_write_allowed") is not False
        or response.get("current_mutation_allowed") is not False
        or response.get("legacy_mutation_allowed") is not False
        or response.get("production_traffic_mutation_allowed") is not False
        or response.get("external_network_payload_allowed") is not False
        or response.get("object_storage_mutation_allowed") is not False
        or type(response.get("journal_event_count")) is not int
        or response["journal_event_count"] < 1
    ):
        raise FrozenPrepareOrchestratorError(
            "controller authority response is not exact"
        )
    for field in (
        "challenge_nonce",
        "response_nonce",
        "journal_state_sha256",
        "journal_event_tail_sha256",
    ):
        _nonzero_sha256(response[field], label=f"authority {field}")
    if response["challenge_nonce"] == response["response_nonce"]:
        raise FrozenPrepareOrchestratorError(
            "controller authority response reused the challenge nonce"
        )
    return dict(response)


class HostControlReader:
    """Own all host stdin reads and close liveness on EOF or bad control."""

    _EOF = object()

    def __init__(self, input_stream: BinaryIO, liveness_write_fd: int):
        try:
            descriptor = input_stream.fileno()
            metadata = os.fstat(descriptor)
            pipe_metadata = os.fstat(liveness_write_fd)
        except (AttributeError, OSError, ValueError) as exc:
            raise FrozenPrepareOrchestratorError(
                "host control or liveness descriptor is unavailable"
            ) from exc
        if (
            descriptor < 0
            or not stat.S_ISFIFO(pipe_metadata.st_mode)
        ):
            raise FrozenPrepareOrchestratorError(
                "host controller liveness is not an anonymous pipe"
            )
        self.input = input_stream
        self.descriptor = descriptor
        self.write_fd = liveness_write_fd
        self.responses: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.expected: dict[str, Any] | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.failed = threading.Event()
        self.control_bytes_received = 0
        self.thread = threading.Thread(
            target=self._run,
            name="frozen-prepare-host-control-reader",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def _close_liveness(self) -> None:
        descriptor = self.write_fd
        self.write_fd = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _fail(self, error: BaseException) -> None:
        self.failed.set()
        self._close_liveness()
        try:
            self.responses.put_nowait(error)
        except queue.Full:
            pass

    def _run(self) -> None:
        buffer = bytearray()
        try:
            os.set_blocking(self.descriptor, False)
            while not self.stop_event.is_set():
                readable, _, _ = select.select(
                    [self.descriptor],
                    [],
                    [],
                    0.05,
                )
                if not readable:
                    continue
                try:
                    chunk = os.read(self.descriptor, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    if not self.stop_event.is_set():
                        self._fail(
                            FrozenPrepareOrchestratorCancellation(
                                "controller control stream reached EOF"
                            )
                        )
                    return
                self.control_bytes_received += len(chunk)
                if self.control_bytes_received > MAX_TOTAL_CONTROL_BYTES:
                    self._fail(
                        FrozenPrepareOrchestratorError(
                            "controller control stream exceeded its total "
                            "byte bound"
                        )
                    )
                    return
                buffer.extend(chunk)
                if len(buffer) > MAX_CONTROL_FRAME_BYTES + 1:
                    self._fail(
                        FrozenPrepareOrchestratorError(
                            "controller response frame is oversized"
                        )
                    )
                    return
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    raw = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    with self.lock:
                        expected = self.expected
                        self.expected = None
                    if expected is None:
                        self._fail(
                            FrozenPrepareOrchestratorError(
                                "prebuffered or unsolicited authority "
                                "response is forbidden"
                            )
                        )
                        return
                    try:
                        response = _strict_json(
                            raw,
                            label="controller authority response",
                        )
                        validated = _validate_authority_response(
                            response,
                            challenge=expected,
                        )
                        self.responses.put_nowait(validated)
                    except BaseException as exc:
                        self._fail(exc)
                        return
                    if buffer:
                        self._fail(
                            FrozenPrepareOrchestratorError(
                                "multiple authority responses were "
                                "prebuffered"
                            )
                        )
                        return
        except BaseException as exc:
            if not self.stop_event.is_set():
                self._fail(exc)
        finally:
            self._close_liveness()

    def arm(self, challenge: Mapping[str, Any]) -> None:
        if self.failed.is_set() or not self.thread.is_alive():
            raise FrozenPrepareOrchestratorCancellation(
                "controller control reader is not live"
            )
        with self.lock:
            if self.expected is not None or not self.responses.empty():
                raise FrozenPrepareOrchestratorError(
                    "authority response exchange is already active"
                )
            self.expected = dict(challenge)

    def wait(self, *, timeout: float) -> dict[str, Any]:
        try:
            value = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            self._fail(
                FrozenPrepareOrchestratorCancellation(
                    "controller authority response timed out"
                )
            )
            raise FrozenPrepareOrchestratorCancellation(
                "controller authority response timed out"
            ) from exc
        if isinstance(value, BaseException):
            if isinstance(value, FrozenPrepareOrchestratorError):
                raise value
            raise FrozenPrepareOrchestratorError(
                "controller control reader failed"
            ) from value
        if not isinstance(value, dict):
            raise FrozenPrepareOrchestratorError(
                "controller response queue contained an invalid value"
            )
        return value

    def check(self) -> None:
        if self.failed.is_set():
            raise FrozenPrepareOrchestratorCancellation(
                "controller liveness was lost"
            )

    def stop(self) -> None:
        self.stop_event.set()
        self._close_liveness()
        self.thread.join(timeout=1.0)
        if self.thread.is_alive():
            raise FrozenPrepareOrchestratorError(
                "host control reader did not stop within its deadline"
            )


class HostAuthorityExchange:
    """Synchronous challenge exchange backed by one stdin reader."""

    def __init__(
        self,
        reader: HostControlReader,
        output_stream: BinaryIO,
        *,
        timeout: float = CONTROL_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        self.reader = reader
        self.output = output_stream
        self.timeout = timeout
        self.transcript: list[dict[str, Any]] = []
        self.tail_sha256 = ZERO_SHA256
        self.challenge_bytes_sent = 0

    def __call__(
        self,
        challenge: Mapping[str, Any],
        boundary: str,
    ) -> Mapping[str, Any]:
        if (
            not isinstance(challenge, Mapping)
            or set(challenge) != WORKER.AUTHORITY_CHALLENGE_FIELDS
            or challenge.get("schema") != WORKER.AUTHORITY_CHALLENGE_SCHEMA
            or challenge.get("status") != "challenge"
            or challenge.get("boundary") != boundary
            or SAFE_BOUNDARY_RE.fullmatch(str(boundary)) is None
            or len(self.transcript) >= MAX_AUTHORITY_FRAMES
        ):
            raise FrozenPrepareOrchestratorError(
                "host authority challenge is invalid"
            )
        document = dict(challenge)
        payload = _canonical_json(document) + b"\n"
        if (
            len(payload) > MAX_CONTROL_FRAME_BYTES
            or self.challenge_bytes_sent + len(payload)
            > MAX_TOTAL_CONTROL_BYTES
        ):
            raise FrozenPrepareOrchestratorError(
                "host authority challenge stream exceeded its byte bound"
            )
        self.reader.arm(document)
        try:
            self.output.write(payload)
            self.output.flush()
        except (OSError, ValueError) as exc:
            raise FrozenPrepareOrchestratorCancellation(
                "host authority challenge could not reach the controller"
            ) from exc
        self.challenge_bytes_sent += len(payload)
        response = self.reader.wait(timeout=self.timeout)
        challenge_sha256 = _sha256(_canonical_json(document))
        response_sha256 = _sha256(_canonical_json(response))
        entry = {
            "schema": AUTHORITY_TRANSCRIPT_SCHEMA,
            "index": len(self.transcript) + 1,
            "challenge_sha256": challenge_sha256,
            "response_sha256": response_sha256,
            "boundary": boundary,
            "sequence": document["sequence"],
            "previous_entry_sha256": self.tail_sha256,
            "entry_sha256": "",
        }
        entry["entry_sha256"] = _sha256(
            _canonical_json(
                {
                    key: value
                    for key, value in entry.items()
                    if key != "entry_sha256"
                }
            )
        )
        self.tail_sha256 = entry["entry_sha256"]
        self.transcript.append(entry)
        return response


@contextmanager
def _signal_cancellation_guard() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise FrozenPrepareOrchestratorError(
            "mutating orchestration must run in the main thread"
        )
    previous = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }
    cancellation_started = False

    def cancel(signum: int, _frame: Any) -> None:
        nonlocal cancellation_started
        if cancellation_started:
            return
        cancellation_started = True
        raise FrozenPrepareOrchestratorCancellation(
            "orchestration received "
            f"{signal.Signals(signum).name}"
        )

    try:
        for signum in previous:
            signal.signal(signum, cancel)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _request_authority_challenge(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    challenge = {
        "schema": WORKER.AUTHORITY_CHALLENGE_SCHEMA,
        "status": "challenge",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "role": request["role"],
        "phase": request["phase"],
        "operation": request["operation"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "controller_manifest_sha256": request[
            "controller_manifest_sha256"
        ],
        "plan_sha256": request["plan_sha256"],
        "request_sha256": _sha256(_canonical_json(dict(request)) + b"\n"),
        "restore_generation_sha256": request[
            "restore_generation_sha256"
        ],
        "boundary": "persist:prepare-request",
        "sequence": 1,
        "challenge_nonce": secrets.token_hex(32),
        "previous_authority_sha256": ZERO_SHA256,
        "publication_kind": None,
        "publication_payload_sha256": None,
    }
    if set(challenge) != WORKER.AUTHORITY_CHALLENGE_FIELDS:
        raise FrozenPrepareOrchestratorError(
            "internal prepare-request challenge fields differ"
        )
    return challenge


def _emit_frame(output_stream: BinaryIO, document: Mapping[str, Any]) -> None:
    payload = _canonical_json(dict(document)) + b"\n"
    if len(payload) > MAX_CONTROL_FRAME_BYTES:
        raise FrozenPrepareOrchestratorError(
            "host control frame exceeds its byte bound"
        )
    try:
        output_stream.write(payload)
        output_stream.flush()
    except (OSError, ValueError) as exc:
        raise FrozenPrepareOrchestratorCancellation(
            "host control frame could not reach the controller"
        ) from exc


def _observe_local_ipv4_addresses() -> set[str]:
    addresses: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
        handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as exc:
        raise FrozenPrepareOrchestratorError(
            "cannot inspect local host network identity"
        ) from exc
    try:
        for _index, name in interfaces:
            try:
                packed = struct.pack("256s", name.encode("ascii")[:15])
                result = fcntl.ioctl(handle.fileno(), 0x8915, packed)
            except (OSError, UnicodeEncodeError):
                continue
            addresses.add(socket.inet_ntoa(result[20:24]))
    finally:
        handle.close()
    if not addresses:
        raise FrozenPrepareOrchestratorError(
            "local host has no observable IPv4 identity"
        )
    return addresses


def _host_intent(
    loaded: LoadedHostInput,
    *,
    phase: str,
    request: Mapping[str, Any],
    request_path: Path,
    request_sha256: str,
    attestation: Mapping[str, Any],
    attestation_sha256: str,
    request_persisted: bool,
    prepare_journal_event_count: int,
    prepare_journal_authority_tail_sha256: str,
    prepare_journal_finalized: bool,
    expected_host: str,
    observed_host_addresses: Sequence[str],
) -> dict[str, Any]:
    versions = (
        loaded.transport_manifest["objects"]
        if loaded.transport_manifest is not None
        else {}
    )
    intent = {
        "schema": HOST_INTENT_SCHEMA,
        "status": "authority-required-before-create",
        "campaign_id": loaded.document["campaign_id"],
        "operation_id": loaded.document["operation_id"],
        "role": loaded.document["role"],
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "release_sha": loaded.document["release_sha"],
        "release_tree_sha": loaded.document["release_tree_sha"],
        "controller_manifest_sha256": loaded.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": loaded.document["plan_sha256"],
        "restore_generation_sha256": loaded.document[
            "restore_generation_sha256"
        ],
        "host_input_sha256": loaded.sha256,
        "request_path": os.fspath(request_path),
        "request_sha256": request_sha256,
        "prior_result_path": request["prior_result_path"],
        "prior_result_sha256": request["prior_result_sha256"],
        "orchestrator_sha256": attestation["artifact_sha256"][
            "orchestrator"
        ],
        "prepare_worker_sha256": loaded.document[
            "prepare_worker_sha256"
        ],
        "release_attestation": dict(attestation),
        "release_attestation_sha256": attestation_sha256,
        "transport": loaded.document["transport"],
        "transport_manifest_sha256": loaded.document[
            "transport_manifest_sha256"
        ],
        "object_versions": versions,
        "expected_host": expected_host,
        "observed_host_addresses": list(observed_host_addresses),
        "host_identity_observed": True,
        "payload_bytes_over_ssh": 0,
        "request_persisted": request_persisted,
        "prepare_journal_event_count": prepare_journal_event_count,
        "prepare_journal_authority_tail_sha256": (
            prepare_journal_authority_tail_sha256
        ),
        "prepare_journal_finalized": prepare_journal_finalized,
    }
    if set(intent) != HOST_INTENT_FIELDS:
        raise FrozenPrepareOrchestratorError(
            "internal host intent fields differ"
        )
    return intent


def _validate_worker_return(
    value: Any,
    *,
    context: WORKER.LoadedRequest,
) -> tuple[dict[str, Any], str]:
    if (
        not isinstance(value, dict)
        or value.get("schema") != WORKER.RESULT_SCHEMA
        or value.get("status") != "completed"
        or value.get("campaign_id") != context.document["campaign_id"]
        or value.get("operation_id") != context.document["operation_id"]
        or value.get("role") != context.document["role"]
        or value.get("phase") != context.document["phase"]
        or value.get("operation") != context.document["operation"]
        or value.get("release_sha") != context.document["release_sha"]
        or value.get("release_tree_sha")
        != context.document["release_tree_sha"]
        or value.get("request_sha256") != context.sha256
        or value.get("output_mutated") is not True
        or value.get("business_write_allowed") is not False
        or value.get("external_network_allowed") is not False
        or value.get("ssh_allowed") is not False
        or value.get("object_storage_allowed") is not False
        or value.get("current_mutation_allowed") is not False
        or value.get("legacy_mutation_allowed") is not False
        or value.get("production_traffic_mutation_allowed") is not False
        or not isinstance(value.get("result"), dict)
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare worker return safety or identity differs"
        )
    result = value["result"]
    if (
        set(result) != WORKER.RESULT_FIELDS
        or result.get("schema") != WORKER.RESULT_SCHEMA
        or result.get("status") != "completed"
        or result.get("request_sha256") != context.sha256
        or any(
            result.get(field) is not False
            for field in (
                "business_write_observed",
                "app_service_started",
                "current_mutated",
                "legacy_mutated",
                "production_traffic_mutated",
                "external_network_contacted",
                "ssh_contacted",
                "object_storage_contacted",
            )
        )
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare worker result closure differs"
        )
    result_path = _absolute_path(
        value.get("result_path"),
        label="prepare worker result",
    )
    document, _payload, observed_sha256 = _secure_json(
        result_path,
        label="prepare worker result",
    )
    if (
        observed_sha256 != value.get("result_sha256")
        or document != result
        or result_path
        != context.output_root
        / "results"
        / f"{context.document['phase']}-{observed_sha256}.json"
    ):
        raise FrozenPrepareOrchestratorError(
            "prepare worker result publication readback differs"
        )
    return dict(value), observed_sha256


def execute_host_session(
    *,
    input_manifest_path: Path,
    input_sha256: str,
    role: str,
    phase: str,
    expected_orchestrator_sha256: str,
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    worker_execute: Callable[..., dict[str, Any]] = WORKER.execute,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    """Run one role/phase under controller stdin liveness and authority."""

    if os.geteuid() != 0:
        raise FrozenPrepareOrchestratorError(
            "frozen prepare host session must run as root"
        )
    if threading.current_thread() is not threading.main_thread():
        raise FrozenPrepareOrchestratorError(
            "frozen prepare host session must run in the main thread"
        )
    if role not in PREPARE_ROLES or role not in WORKER.PHASE_ROLES.get(
        phase, ()
    ):
        raise FrozenPrepareOrchestratorError(
            "host session role is not required by the prepare phase"
        )
    expected_orchestrator_sha256 = _nonzero_sha256(
        expected_orchestrator_sha256,
        label="expected orchestrator",
    )
    loaded = load_host_input(
        input_manifest_path,
        expected_sha256=input_sha256,
        expected_role=role,
    )
    attestation, attestation_sha256 = _attest_immutable_release(loaded)
    if (
        attestation["artifact_sha256"]["orchestrator"]
        != expected_orchestrator_sha256
    ):
        raise FrozenPrepareOrchestratorError(
            "host orchestrator differs from the controller release"
        )
    expected_host = str(
        loaded.controller_manifest["topology"][role]["host"]
    )
    observed_addresses = sorted(
        _observe_local_ipv4_addresses()
        if observed_host_addresses is None
        else set(observed_host_addresses)
    )
    if (
        expected_host not in observed_addresses
        or any(
            not isinstance(address, str)
            or re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", address)
            is None
            for address in observed_addresses
        )
    ):
        raise FrozenPrepareOrchestratorError(
            "local host identity differs from the prepare role"
        )
    request, request_payload, request_sha256, request_path = (
        _build_prepare_request(loaded, phase=phase)
    )
    request_persisted = request_path.exists()
    prepare_journal_event_count = 0
    prepare_journal_authority_tail_sha256 = ZERO_SHA256
    prepare_journal_finalized = False
    if request_persisted:
        try:
            existing_context = WORKER.load_request(request_path)
            existing_journal = WORKER._load_journal(  # noqa: SLF001
                existing_context
            )
        except WORKER.FrozenPrepareWorkerError as exc:
            raise FrozenPrepareOrchestratorError(
                "existing prepare request or journal is invalid"
            ) from exc
        prepare_journal_event_count = len(existing_journal.events)
        prepare_journal_authority_tail_sha256 = (
            existing_journal.events[-1]["authority_sha256"]
            if existing_journal.events
            else ZERO_SHA256
        )
        prepare_journal_finalized = existing_journal.finalized
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, False)
    os.set_inheritable(write_fd, False)
    reader = HostControlReader(input_stream, write_fd)
    exchange = HostAuthorityExchange(reader, output_stream)
    reader.start()
    try:
        with _signal_cancellation_guard():
            reader.check()
            intent = _host_intent(
                loaded,
                phase=phase,
                request=request,
                request_path=request_path,
                request_sha256=request_sha256,
                attestation=attestation,
                attestation_sha256=attestation_sha256,
                request_persisted=request_persisted,
                prepare_journal_event_count=(
                    prepare_journal_event_count
                ),
                prepare_journal_authority_tail_sha256=(
                    prepare_journal_authority_tail_sha256
                ),
                prepare_journal_finalized=prepare_journal_finalized,
                expected_host=expected_host,
                observed_host_addresses=observed_addresses,
            )
            _emit_frame(output_stream, intent)
            exchange(
                _request_authority_challenge(request),
                "persist:prepare-request",
            )
            reader.check()
            _persist_prepare_request(
                loaded,
                document=request,
                payload=request_payload,
                digest=request_sha256,
                path=request_path,
            )
            try:
                worker_context = WORKER.load_request(request_path)
            except WORKER.FrozenPrepareWorkerError as exc:
                raise FrozenPrepareOrchestratorError(
                    "persisted prepare request failed immutable readback"
                ) from exc
            if (
                worker_context.sha256 != request_sha256
                or worker_context.document != request
            ):
                raise FrozenPrepareOrchestratorError(
                    "persisted prepare request context differs"
                )
            worker_return = worker_execute(
                request_path=request_path,
                apply=True,
                confirm=WORKER.confirmation_phrase(worker_context),
                authority_verifier=exchange,
                control_fd=read_fd,
            )
            validated_return, worker_result_sha256 = (
                _validate_worker_return(
                    worker_return,
                    context=worker_context,
                )
            )
            reader.check()
    finally:
        try:
            reader.stop()
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass
    transcript_sha256 = _sha256(_canonical_json(exchange.transcript))
    versions = (
        loaded.transport_manifest["objects"]
        if loaded.transport_manifest is not None
        else {}
    )
    result = {
        "schema": HOST_RESULT_SCHEMA,
        "status": "completed",
        "campaign_id": loaded.document["campaign_id"],
        "operation_id": loaded.document["operation_id"],
        "role": role,
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "release_sha": loaded.document["release_sha"],
        "release_tree_sha": loaded.document["release_tree_sha"],
        "controller_manifest_sha256": loaded.document[
            "controller_manifest_sha256"
        ],
        "plan_sha256": loaded.document["plan_sha256"],
        "restore_generation_sha256": loaded.document[
            "restore_generation_sha256"
        ],
        "host_input_sha256": loaded.sha256,
        "request_path": os.fspath(request_path),
        "request_sha256": request_sha256,
        "orchestrator_sha256": expected_orchestrator_sha256,
        "prepare_worker_sha256": loaded.document[
            "prepare_worker_sha256"
        ],
        "release_attestation": attestation,
        "release_attestation_sha256": attestation_sha256,
        "transport": loaded.document["transport"],
        "transport_manifest_sha256": loaded.document[
            "transport_manifest_sha256"
        ],
        "object_versions": versions,
        "expected_host": expected_host,
        "observed_host_addresses": observed_addresses,
        "host_identity_observed": True,
        "worker_return": validated_return,
        "worker_result_sha256": worker_result_sha256,
        "authority_transcript_count": len(exchange.transcript),
        "authority_transcript_tail_sha256": exchange.tail_sha256,
        "authority_transcript_sha256": transcript_sha256,
        "payload_bytes_over_ssh": 0,
        "control_bytes_received": reader.control_bytes_received,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "business_write_observed": False,
        "external_network_contacted": False,
        "object_storage_mutated": False,
        "app_service_started": False,
    }
    if (
        set(result) != HOST_RESULT_FIELDS
        or result["authority_transcript_count"] < 2
    ):
        raise FrozenPrepareOrchestratorError(
            "host result closure is invalid"
        )
    _emit_frame(output_stream, result)
    return result


def _journal_bindings(context: LoadedOrchestration) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "release_sha": context.document["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _assert_phase_journal_state(
    state: Mapping[str, Any],
    *,
    context: LoadedOrchestration,
    phase: str,
) -> dict[str, Any]:
    expected_prefix = list(
        CONTROLLER.PHASES[: CONTROLLER.PHASES.index(phase)]
    )
    if (
        not isinstance(state, Mapping)
        or any(state.get(key) != value for key, value in _journal_bindings(context).items())
        or state.get("status") != "phase_started"
        or state.get("started_phase") != phase
        or state.get("completed_phases") != expected_prefix
        or set(state.get("phase_evidence_sha256", {}))
        != set(expected_prefix)
        or state.get("rollback_eligible") is not True
        or state.get("first_business_write_allowed") is not False
        or not isinstance(state.get("events"), list)
        or type(state.get("state_sha256")) is not str
        or type(state.get("event_tail_sha256")) is not str
    ):
        raise FrozenPrepareOrchestratorError(
            "cutover journal is not durably started at the exact prepare phase"
        )
    _nonzero_sha256(
        state["state_sha256"],
        label="cutover journal state",
    )
    _nonzero_sha256(
        state["event_tail_sha256"],
        label="cutover journal event tail",
    )
    return dict(state)


def _validate_host_intent(
    value: Any,
    *,
    context: LoadedOrchestration,
    phase: str,
    role: str,
    orchestrator_sha256: str,
    prepare_worker_sha256: str,
) -> dict[str, Any]:
    row = context.document["host_inputs"][role]
    if (
        not isinstance(value, dict)
        or set(value) != HOST_INTENT_FIELDS
        or value.get("schema") != HOST_INTENT_SCHEMA
        or value.get("status") != "authority-required-before-create"
        or value.get("role") != role
        or value.get("phase") != phase
        or value.get("operation") != WORKER.PHASE_OPERATIONS[phase]
        or any(
            value.get(field) != context.document[field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
            )
        )
        or value.get("host_input_sha256") != row["sha256"]
        or value.get("orchestrator_sha256") != orchestrator_sha256
        or value.get("prepare_worker_sha256") != prepare_worker_sha256
        or value.get("transport") != row["transport"]
        or value.get("object_versions") != row["object_versions"]
        or value.get("payload_bytes_over_ssh") != 0
        or value.get("request_persisted") not in {True, False}
        or type(value.get("prepare_journal_event_count")) is not int
        or value["prepare_journal_event_count"] < 0
        or value.get("prepare_journal_finalized") not in {True, False}
        or value.get("expected_host")
        != context.manifest["topology"][role]["host"]
        or value.get("host_identity_observed") is not True
        or not isinstance(value.get("observed_host_addresses"), list)
        or value["expected_host"] not in value["observed_host_addresses"]
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare intent identity or safety closure differs"
        )
    request_path = _absolute_path(
        value["request_path"],
        label=f"{role} prepare request",
    )
    request_sha256 = _nonzero_sha256(
        value["request_sha256"],
        label=f"{role} prepare request",
    )
    expected_root = (
        RESTORE_WORKER.SECRET_ROOT_PREFIX
        / context.document["operation_id"]
        / "frozen-final-generations"
    )
    try:
        relative = request_path.relative_to(expected_root)
    except ValueError as exc:
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare request is outside the operation secret root"
        ) from exc
    if (
        len(relative.parts) != 4
        or relative.parts[1] != RESTORE_WORKER.ROLE_PATHS[role]
        or relative.parts[2] != "prepare-requests"
        or relative.parts[3] != f"{phase}-{request_sha256}.json"
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare request path is not phase and digest derived"
        )
    attestation = value.get("release_attestation")
    if (
        not isinstance(attestation, dict)
        or value.get("release_attestation_sha256")
        != _sha256(_canonical_json(attestation))
        or attestation.get("role") != role
        or attestation.get("release_sha") != context.document["release_sha"]
        or attestation.get("release_tree_sha")
        != context.document["release_tree_sha"]
        or attestation.get("detached") is not True
        or attestation.get("clean") is not True
        or attestation.get("artifact_sha256", {}).get("orchestrator")
        != orchestrator_sha256
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} immutable release attestation differs"
        )
    tail = value["prepare_journal_authority_tail_sha256"]
    if tail != ZERO_SHA256:
        _nonzero_sha256(tail, label=f"{role} prepare journal authority")
    if (
        value["prepare_journal_finalized"]
        and value["prepare_journal_event_count"] < 1
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} finalized prepare journal is empty"
        )
    return dict(value)


def _authority_response(
    challenge: Any,
    *,
    context: LoadedOrchestration,
    state: Mapping[str, Any],
    intent: Mapping[str, Any],
    seen_nonces: set[str],
) -> dict[str, Any]:
    if (
        not isinstance(challenge, dict)
        or set(challenge) != WORKER.AUTHORITY_CHALLENGE_FIELDS
        or challenge.get("schema") != WORKER.AUTHORITY_CHALLENGE_SCHEMA
        or challenge.get("status") != "challenge"
        or any(
            challenge.get(field) != intent[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "phase",
                "operation",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
                "request_sha256",
                "restore_generation_sha256",
            )
        )
        or type(challenge.get("sequence")) is not int
        or not 1 <= challenge["sequence"] <= 1_000_000
        or SAFE_BOUNDARY_RE.fullmatch(str(challenge.get("boundary"))) is None
    ):
        raise FrozenPrepareOrchestratorError(
            "host authority challenge identity differs"
        )
    challenge_nonce = _nonzero_sha256(
        challenge["challenge_nonce"],
        label="host authority challenge nonce",
    )
    if challenge_nonce in seen_nonces:
        raise FrozenPrepareOrchestratorError(
            "host authority challenge nonce was replayed"
        )
    seen_nonces.add(challenge_nonce)
    previous = challenge["previous_authority_sha256"]
    if previous != ZERO_SHA256:
        _nonzero_sha256(previous, label="previous host authority")
    publication_kind = challenge["publication_kind"]
    publication_digest = challenge["publication_payload_sha256"]
    expected_kind = {
        "publish:evidence": "evidence",
        "publish:result": "result",
    }.get(challenge["boundary"])
    if (
        expected_kind is None
        and (publication_kind is not None or publication_digest is not None)
    ) or (
        expected_kind is not None
        and (
            publication_kind != expected_kind
            or publication_digest is None
        )
    ):
        raise FrozenPrepareOrchestratorError(
            "host authority publication binding differs"
        )
    if publication_digest is not None:
        _nonzero_sha256(
            publication_digest,
            label="host authority publication payload",
        )
    state = _assert_phase_journal_state(
        state,
        context=context,
        phase=str(intent["phase"]),
    )
    response_nonce = secrets.token_hex(32)
    response = {
        **challenge,
        "schema": WORKER.AUTHORITY_RESPONSE_SCHEMA,
        "status": "verified-live",
        "challenge_sha256": _sha256(_canonical_json(challenge)),
        "response_nonce": response_nonce,
        "controller_lock_held": True,
        "controller_authoritative": True,
        "journal_status": state["status"],
        "journal_state_sha256": state["state_sha256"],
        "journal_event_tail_sha256": state["event_tail_sha256"],
        "journal_event_count": len(state["events"]),
        "completed_phases": list(state["completed_phases"]),
        "started_phase": state["started_phase"],
        "business_write_allowed": False,
        "current_mutation_allowed": False,
        "legacy_mutation_allowed": False,
        "production_traffic_mutation_allowed": False,
        "external_network_payload_allowed": False,
        "object_storage_mutation_allowed": False,
    }
    return _validate_authority_response(
        response,
        challenge=challenge,
    )


def _transcript_entry(
    challenge: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    previous: str,
    index: int,
) -> dict[str, Any]:
    entry = {
        "schema": AUTHORITY_TRANSCRIPT_SCHEMA,
        "index": index,
        "challenge_sha256": _sha256(_canonical_json(dict(challenge))),
        "response_sha256": _sha256(_canonical_json(dict(response))),
        "boundary": challenge["boundary"],
        "sequence": challenge["sequence"],
        "previous_entry_sha256": previous,
        "entry_sha256": "",
    }
    entry["entry_sha256"] = _sha256(
        _canonical_json(
            {
                key: value
                for key, value in entry.items()
                if key != "entry_sha256"
            }
        )
    )
    return entry


def _validate_host_result(
    value: Any,
    *,
    context: LoadedOrchestration,
    intent: Mapping[str, Any],
    transcript: Sequence[Mapping[str, Any]],
    control_bytes_sent: int,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != HOST_RESULT_FIELDS
        or value.get("schema") != HOST_RESULT_SCHEMA
        or value.get("status") != "completed"
        or any(
            value.get(field) != intent[field]
            for field in (
                "campaign_id",
                "operation_id",
                "role",
                "phase",
                "operation",
                "release_sha",
                "release_tree_sha",
                "controller_manifest_sha256",
                "plan_sha256",
                "restore_generation_sha256",
                "host_input_sha256",
                "request_path",
                "request_sha256",
                "orchestrator_sha256",
                "prepare_worker_sha256",
                "release_attestation",
                "release_attestation_sha256",
                "transport",
                "transport_manifest_sha256",
                "object_versions",
                "expected_host",
                "observed_host_addresses",
                "host_identity_observed",
            )
        )
        or value.get("authority_transcript_count") != len(transcript)
        or len(transcript) < 2
        or value.get("authority_transcript_tail_sha256")
        != (transcript[-1]["entry_sha256"] if transcript else ZERO_SHA256)
        or value.get("authority_transcript_sha256")
        != _sha256(_canonical_json(list(transcript)))
        or value.get("control_bytes_received") != control_bytes_sent
        or value.get("payload_bytes_over_ssh") != 0
        or any(
            value.get(field) is not False
            for field in (
                "current_mutated",
                "legacy_mutated",
                "production_traffic_mutated",
                "business_write_observed",
                "external_network_contacted",
                "object_storage_mutated",
                "app_service_started",
            )
        )
    ):
        raise FrozenPrepareOrchestratorError(
            "host prepare result identity, transcript, or safety differs"
        )
    worker_return = value.get("worker_return")
    worker_result = (
        worker_return.get("result")
        if isinstance(worker_return, dict)
        else None
    )
    if (
        not isinstance(worker_result, dict)
        or set(worker_result) != WORKER.RESULT_FIELDS
        or worker_result.get("status") != "completed"
        or worker_result.get("request_sha256") != intent["request_sha256"]
        or value.get("worker_result_sha256")
        != worker_return.get("result_sha256")
        or not isinstance(worker_result.get("semantic"), dict)
    ):
        raise FrozenPrepareOrchestratorError(
            "host prepare worker closure differs"
        )
    _nonzero_sha256(
        value["worker_result_sha256"],
        label="host prepare worker result",
    )
    return dict(value)


def _write_control_response(
    stream: BinaryIO,
    document: Mapping[str, Any],
) -> int:
    payload = _canonical_json(dict(document)) + b"\n"
    if len(payload) > MAX_CONTROL_FRAME_BYTES:
        raise FrozenPrepareOrchestratorError(
            "controller authority response exceeds its byte bound"
        )
    try:
        stream.write(payload)
        stream.flush()
    except (OSError, ValueError) as exc:
        raise FrozenPrepareOrchestratorCancellation(
            "controller authority response could not reach the host"
        ) from exc
    return len(payload)


def _run_host_session_locked(
    context: LoadedOrchestration,
    *,
    journal: CONTROLLER.ProductionCutoverJournal,
    phase: str,
    role: str,
    orchestrator_sha256: str,
    prepare_worker_sha256: str,
    session_factory: SessionFactory = _default_session_factory,
) -> HostSessionResult:
    argv = session_arguments(
        context,
        phase=phase,
        role=role,
        orchestrator_sha256=orchestrator_sha256,
    )
    _enable_child_subreaper()
    baseline = _direct_child_baseline()
    process: InteractiveProcess | None = None
    root_identity: ProcessIdentity | None = None
    root_descriptor: int | None = None
    selector = selectors.DefaultSelector()
    tracked: set[ProcessIdentity] = set()
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    total_stdout = 0
    response_bytes = 0
    intent: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] = []
    transcript_tail = ZERO_SHA256
    seen_nonces: set[str] = set()
    completed_cleanly = False
    deadline = time.monotonic() + PHASE_SESSION_TIMEOUT_SECONDS[phase]
    try:
        process = session_factory(argv)
        if (
            process.stdin is None
            or process.stdout is None
            or process.stderr is None
            or type(process.pid) is not int
            or process.pid <= 1
        ):
            raise FrozenPrepareOrchestratorError(
                "host control process lacks exact bounded stdio"
            )
        attached_descriptor = getattr(
            process,
            "_production_shadow_root_descriptor",
            None,
        )
        attached_identity = getattr(
            process,
            "_production_shadow_root_identity",
            None,
        )
        if (attached_descriptor is None) != (attached_identity is None):
            raise FrozenPrepareOrchestratorError(
                "host control process ownership is incomplete"
            )
        if attached_descriptor is None:
            root_descriptor = os.pidfd_open(process.pid, 0)
            root_identity = _process_identity(process.pid)
        else:
            if (
                type(attached_descriptor) is not int
                or attached_descriptor < 0
                or not isinstance(attached_identity, ProcessIdentity)
            ):
                raise FrozenPrepareOrchestratorError(
                    "host control process ownership is invalid"
                )
            root_descriptor = attached_descriptor
            root_identity = attached_identity
        if (
            root_identity is None
            or root_identity.pid != process.pid
            or root_identity.process_group != process.pid
            or root_identity.session_id != process.pid
        ):
            raise FrozenPrepareOrchestratorError(
                "host control process is not in its own session"
            )
        tracked.add(root_identity)
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        open_streams = {"stdout", "stderr"}
        while result is None:
            tracked.update(
                _owned_processes(
                    root_identity,
                    baseline_children=baseline,
                    tracked=tracked,
                )
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenPrepareOrchestratorCancellation(
                    f"{role} prepare host session timed out"
                )
            events = selector.select(min(PROCESS_POLL_SECONDS, remaining))
            if not events:
                if process.poll() is not None:
                    raise FrozenPrepareOrchestratorCancellation(
                        f"{role} prepare host exited before its result"
                    )
                continue
            for key, _mask in events:
                label = str(key.data)
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    open_streams.discard(label)
                    if label == "stdout" and result is None:
                        raise FrozenPrepareOrchestratorCancellation(
                            f"{role} prepare host control reached EOF"
                        )
                    continue
                if label == "stderr":
                    if len(stderr_buffer) + len(chunk) > MAX_HOST_STDERR_BYTES:
                        raise FrozenPrepareOrchestratorError(
                            f"{role} prepare host stderr is oversized"
                        )
                    stderr_buffer.extend(chunk)
                    raise FrozenPrepareOrchestratorError(
                        f"{role} prepare host emitted stderr"
                    )
                total_stdout += len(chunk)
                if total_stdout > MAX_HOST_STDOUT_BYTES:
                    raise FrozenPrepareOrchestratorError(
                        f"{role} prepare host stdout is oversized"
                    )
                stdout_buffer.extend(chunk)
                if len(stdout_buffer) > MAX_CONTROL_FRAME_BYTES:
                    raise FrozenPrepareOrchestratorError(
                        f"{role} prepare host frame is oversized"
                    )
                frames: list[bytes] = []
                while (newline := stdout_buffer.find(b"\n")) >= 0:
                    frames.append(bytes(stdout_buffer[:newline]))
                    del stdout_buffer[: newline + 1]
                for index, raw in enumerate(frames):
                    document = _strict_json(
                        raw,
                        label=f"{role} prepare host frame",
                    )
                    if intent is None:
                        if document.get("schema") != HOST_INTENT_SCHEMA:
                            raise FrozenPrepareOrchestratorError(
                                f"{role} host omitted its prepare intent"
                            )
                        intent = _validate_host_intent(
                            document,
                            context=context,
                            phase=phase,
                            role=role,
                            orchestrator_sha256=orchestrator_sha256,
                            prepare_worker_sha256=prepare_worker_sha256,
                        )
                        continue
                    if document.get("schema") == WORKER.AUTHORITY_CHALLENGE_SCHEMA:
                        if index != len(frames) - 1 or stdout_buffer:
                            raise FrozenPrepareOrchestratorError(
                                f"{role} host prebuffered work past authority"
                            )
                        descriptor = journal._lock()  # noqa: SLF001
                        try:
                            try:
                                CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
                                    context.manifest,
                                    approval_path=Path(
                                        context.document["approval_path"]
                                    ),
                                    approval_policy_path=Path(
                                        context.document[
                                            "approval_policy_path"
                                        ]
                                    ),
                                )
                            except CONTROLLER.CutoverContractError as exc:
                                raise FrozenPrepareOrchestratorCancellation(
                                    "production authorization expired "
                                    "during host authority exchange"
                                ) from exc
                            state = journal._read()  # noqa: SLF001
                            response = _authority_response(
                                document,
                                context=context,
                                state=state,
                                intent=intent,
                                seen_nonces=seen_nonces,
                            )
                            response_size = len(
                                _canonical_json(response)
                            ) + 1
                            if (
                                response_bytes + response_size
                                > MAX_TOTAL_CONTROL_BYTES
                            ):
                                raise FrozenPrepareOrchestratorError(
                                    "controller authority responses exceeded "
                                    "their total byte bound"
                                )
                            sent = _write_control_response(
                                process.stdin,
                                response,
                            )
                            if sent != response_size:
                                raise FrozenPrepareOrchestratorError(
                                    "controller authority response size "
                                    "changed during write"
                                )
                            response_bytes += sent
                        finally:
                            os.close(descriptor)
                        entry = _transcript_entry(
                            document,
                            response,
                            previous=transcript_tail,
                            index=len(transcript) + 1,
                        )
                        transcript_tail = entry["entry_sha256"]
                        transcript.append(entry)
                        continue
                    if document.get("schema") == HOST_RESULT_SCHEMA:
                        if index != len(frames) - 1 or stdout_buffer:
                            raise FrozenPrepareOrchestratorError(
                                f"{role} host emitted bytes after its result"
                            )
                        result = _validate_host_result(
                            document,
                            context=context,
                            intent=intent,
                            transcript=transcript,
                            control_bytes_sent=response_bytes,
                        )
                        break
                    raise FrozenPrepareOrchestratorError(
                        f"{role} host emitted an unexpected control frame"
                    )
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        exit_deadline = min(
            deadline,
            time.monotonic() + POST_RESULT_EXIT_SECONDS,
        )
        while process.poll() is None or open_streams:
            remaining = exit_deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenPrepareOrchestratorError(
                    f"{role} host did not exit after its result"
                )
            events = selector.select(min(PROCESS_POLL_SECONDS, remaining))
            for key, _mask in events:
                label = str(key.data)
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    open_streams.discard(label)
                elif label == "stderr":
                    if len(stderr_buffer) + len(chunk) > MAX_HOST_STDERR_BYTES:
                        raise FrozenPrepareOrchestratorError(
                            f"{role} prepare host stderr is oversized"
                        )
                    stderr_buffer.extend(chunk)
                    raise FrozenPrepareOrchestratorError(
                        f"{role} prepare host emitted stderr"
                    )
                else:
                    raise FrozenPrepareOrchestratorError(
                        f"{role} host emitted trailing stdout"
                    )
            if process.poll() is not None and not open_streams:
                break
        returncode = process.wait(
            timeout=max(0.1, exit_deadline - time.monotonic())
        )
        if returncode != 0 or stderr_buffer:
            raise FrozenPrepareOrchestratorError(
                f"{role} prepare host exited unsuccessfully"
            )
        tracked.update(
            _owned_processes(
                root_identity,
                baseline_children=baseline,
                tracked=tracked,
            )
        )
        residual = {
            identity
            for identity in _owned_processes(
                root_identity,
                baseline_children=baseline,
                tracked=tracked,
            )
            if identity.pid != process.pid
        }
        if residual:
            tracked.update(residual)
            _terminate_process_tree(
                process,
                tracked,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
                baseline_children=baseline,
            )
            raise FrozenPrepareOrchestratorError(
                f"{role} prepare host retained a descendant"
            )
        completed_cleanly = True
        if result is None:
            raise FrozenPrepareOrchestratorError(
                f"{role} prepare host retained no validated result"
            )
        return HostSessionResult(
            document=result,
            stdout_bytes=total_stdout,
            stderr_bytes=len(stderr_buffer),
            response_bytes=response_bytes,
            process_tree_clean=True,
            deadline_enforced=True,
            stream_limits_enforced=True,
        )
    except FrozenPrepareOrchestratorError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare host control failed"
        ) from exc
    finally:
        original_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        if process is not None:
            try:
                if not completed_cleanly:
                    if root_identity is None:
                        if root_descriptor is None:
                            root_identity = _process_identity(process.pid)
                            if root_identity is None:
                                raise FrozenPrepareOrchestratorError(
                                    "unidentified host control process "
                                    "cannot be recovered"
                                )
                        else:
                            _signal_process_handle(
                                root_descriptor,
                                signal.SIGKILL,
                            )
                            try:
                                process.wait(
                                    timeout=PROCESS_KILL_GRACE_SECONDS
                                )
                            except subprocess.TimeoutExpired as exc:
                                raise FrozenPrepareOrchestratorError(
                                    "unidentified host control process "
                                    "survived forced cleanup"
                                ) from exc
                            root_identity = ProcessIdentity(
                                pid=process.pid,
                                parent_pid=os.getpid(),
                                process_group=process.pid,
                                session_id=process.pid,
                                start_time=-1,
                                state="?",
                            )
                    _terminate_process_tree(
                        process,
                        tracked,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline,
                    )
            except BaseException as exc:
                cleanup_errors.append(exc)
                if root_descriptor is not None:
                    try:
                        _signal_process_handle(
                            root_descriptor,
                            signal.SIGKILL,
                        )
                        process.wait(timeout=PROCESS_KILL_GRACE_SECONDS)
                    except ProcessLookupError:
                        pass
                    except BaseException as fallback_exc:
                        cleanup_errors.append(fallback_exc)
        try:
            selector.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if process is not None:
            for stream in (
                process.stdin,
                process.stdout,
                process.stderr,
            ):
                if stream is None:
                    continue
                try:
                    if not stream.closed:
                        stream.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            cleanup_error = cleanup_errors[0]
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def _run_host_session(
    context: LoadedOrchestration,
    *,
    journal: CONTROLLER.ProductionCutoverJournal,
    phase: str,
    role: str,
    orchestrator_sha256: str,
    prepare_worker_sha256: str,
    session_factory: SessionFactory = _default_session_factory,
) -> HostSessionResult:
    with _PROCESS_TREE_LOCK:
        return _run_host_session_locked(
            context,
            journal=journal,
            phase=phase,
            role=role,
            orchestrator_sha256=orchestrator_sha256,
            prepare_worker_sha256=prepare_worker_sha256,
            session_factory=session_factory,
        )


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic_for_role(
    result: Mapping[str, Any],
    *,
    phase: str,
    role: str,
) -> dict[str, Any]:
    if (
        result.get("phase") != phase
        or result.get("role") != role
        or not isinstance(result.get("worker_return"), dict)
        or not isinstance(result["worker_return"].get("result"), dict)
        or not isinstance(
            result["worker_return"]["result"].get("semantic"),
            dict,
        )
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare semantic is unavailable"
        )
    return dict(result["worker_return"]["result"]["semantic"])


def _equal_nonzero_hash(
    values: Mapping[str, Any],
    *,
    label: str,
) -> str:
    observed = {
        _nonzero_sha256(value, label=f"{label} {role}")
        for role, value in values.items()
    }
    if len(observed) != 1:
        raise FrozenPrepareOrchestratorError(
            f"{label} differs across prepare roles"
        )
    return next(iter(observed))


def _aggregate_hash(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _role_hashes(
    semantics: Mapping[str, Mapping[str, Any]],
    field: str,
) -> dict[str, str]:
    return {
        role: _nonzero_sha256(
            semantic.get(field),
            label=f"{role} prepare semantic {field}",
        )
        for role, semantic in semantics.items()
    }


def _semantic_nonnegative_integer(
    semantic: Mapping[str, Any],
    field: str,
    *,
    role: str,
) -> int:
    value = semantic.get(field)
    if (
        type(value) is not int
        or value < 0
        or value > 1_000_000_000
    ):
        raise FrozenPrepareOrchestratorError(
            f"{role} prepare semantic {field} is invalid"
        )
    return value


def _prior_claim_value(
    prior_records: Mapping[str, Mapping[str, Any]],
    *,
    phase: str,
    claim: str,
) -> Any:
    try:
        source = prior_records[phase]["document"]["claims"][claim]
    except (KeyError, TypeError) as exc:
        raise FrozenPrepareOrchestratorError(
            f"prior {phase} claim {claim} is unavailable"
        ) from exc
    if not isinstance(source, dict) or set(source) != VERIFY.CLAIM_FIELDS:
        raise FrozenPrepareOrchestratorError(
            f"prior {phase} claim {claim} fields differ"
        )
    return source["value"]


def _phase_claim_values(
    phase: str,
    results: Mapping[str, Mapping[str, Any]],
    *,
    prior_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    expected_roles = tuple(WORKER.PHASE_ROLES[phase])
    if set(results) != set(expected_roles):
        raise FrozenPrepareOrchestratorError(
            "prepare phase result roles are not exact"
        )
    semantics = {
        role: _semantic_for_role(results[role], phase=phase, role=role)
        for role in expected_roles
    }
    if phase == "shadow_roles_pre_migration":
        values = {
            "least_privilege_role_set_verified": all(
                semantic.get("least_privilege_role_set_verified") is True
                for semantic in semantics.values()
            ),
            "excessive_grant_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "excessive_grant_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
        }
    elif phase == "shadow_migrate":
        schema_fingerprint = _equal_nonzero_hash(
            {
                role: semantic.get("schema_fingerprint_sha256")
                for role, semantic in semantics.items()
            },
            label="migrated schema fingerprint",
        )
        if any(
            semantic.get("alembic_chain_state") != "target"
            or semantic.get("current_revision")
            != semantic.get("target_revision")
            for semantic in semantics.values()
        ):
            raise FrozenPrepareOrchestratorError(
                "prepare migrations did not all reach the target"
            )
        values = {
            "restore_result_set_sha256": _prior_claim_value(
                prior_records,
                phase="shadow_restore",
                claim="restore_result_set_sha256",
            ),
            "alembic_chain_state": "target",
            "off_chain_revision_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "off_chain_revision_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
            "invalid_unready_index_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "invalid_unready_index_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
            "schema_fingerprint_sha256": schema_fingerprint,
            "migration_journal_sha256": _aggregate_hash(
                {
                    role: {
                        "journal_tail_sha256": _nonzero_sha256(
                            results[role]["worker_return"]["result"][
                                "journal_tail_sha256"
                            ],
                            label=f"{role} migration journal tail",
                        ),
                        "worker_result_sha256": _nonzero_sha256(
                            results[role]["worker_result_sha256"],
                            label=f"{role} migration worker result",
                        ),
                    }
                    for role in expected_roles
                }
            ),
        }
    elif phase == "shadow_roles_post_migration":
        migrated_schema = _prior_claim_value(
            prior_records,
            phase="shadow_migrate",
            claim="schema_fingerprint_sha256",
        )
        if any(
            semantic.get("migrated_schema_fingerprint_sha256")
            != migrated_schema
            for semantic in semantics.values()
        ):
            raise FrozenPrepareOrchestratorError(
                "post-migration role bindings differ from migrated schema"
            )
        values = {
            "least_privilege_role_set_verified": all(
                semantic.get("least_privilege_role_set_verified") is True
                for semantic in semantics.values()
            ),
            "excessive_grant_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "excessive_grant_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
            "post_migration_grant_set_sha256": _aggregate_hash(
                _role_hashes(
                    semantics,
                    "post_migration_grant_set_sha256",
                )
            ),
            "migrated_schema_fingerprint_sha256": migrated_schema,
        }
    elif phase == "shadow_fence":
        migrated_schema = _prior_claim_value(
            prior_records,
            phase="shadow_migrate",
            claim="schema_fingerprint_sha256",
        )
        if any(
            semantic.get("migrated_schema_fingerprint_sha256")
            != migrated_schema
            for semantic in semantics.values()
        ):
            raise FrozenPrepareOrchestratorError(
                "database fence differs from the migrated schema"
            )
        if any(
            _semantic_nonnegative_integer(
                semantic,
                "fenced_database_count",
                role=role,
            )
            != 1
            for role, semantic in semantics.items()
        ):
            raise FrozenPrepareOrchestratorError(
                "database fence role count is not exact"
            )
        values = {
            "fenced_database_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "fenced_database_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
            "unfenced_writer_count": sum(
                _semantic_nonnegative_integer(
                    semantic,
                    "unfenced_writer_count",
                    role=role,
                )
                for role, semantic in semantics.items()
            ),
            "database_event_fence_verified": all(
                semantic.get("database_event_fence_verified") is True
                for semantic in semantics.values()
            ),
            "migrated_schema_fingerprint_sha256": migrated_schema,
            "fence_configuration_sha256": _aggregate_hash(
                _role_hashes(
                    semantics,
                    "fence_configuration_sha256",
                )
            ),
        }
    else:
        raise FrozenPrepareOrchestratorError(
            "prepare phase claim aggregation is invalid"
        )
    if set(values) != set(VERIFY.PHASE_CLAIM_RULES[phase]):
        raise FrozenPrepareOrchestratorError(
            "prepare phase claim aggregation fields differ"
        )
    try:
        VERIFY._validate_expected_dynamic_claims(  # noqa: SLF001
            {
                name: value
                for name, value in values.items()
                if VERIFY.PHASE_CLAIM_RULES[phase][name].kind != "exact"
            },
            phase=phase,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenPrepareOrchestratorError(
            "prepare phase claim values violate the release verifier"
        ) from exc
    for name, rule in VERIFY.PHASE_CLAIM_RULES[phase].items():
        try:
            VERIFY._validate_claim(  # noqa: SLF001
                name,
                {"value": values[name], "source_sha256": "1" * 64},
                rule,
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise FrozenPrepareOrchestratorError(
                f"prepare phase claim {name} is invalid"
            ) from exc
    return values


def _load_prior_records(
    context: LoadedOrchestration,
    *,
    phase: str,
    journal_state: Mapping[str, Any],
    evidence_paths: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    expected_phases = CONTROLLER.PHASES[
        : CONTROLLER.PHASES.index(phase)
    ]
    if set(evidence_paths) != set(expected_phases):
        raise FrozenPrepareOrchestratorError(
            "prior phase evidence path set is not exact"
        )
    records: dict[str, dict[str, Any]] = {}
    for prior_phase in expected_phases:
        path = evidence_paths[prior_phase]
        try:
            document, digest = VERIFY.read_root_only_evidence(path)
        except VERIFY.PhaseEvidenceError as exc:
            raise FrozenPrepareOrchestratorError(
                f"prior phase {prior_phase} evidence is unsafe"
            ) from exc
        if (
            digest
            != journal_state["phase_evidence_sha256"][prior_phase]
            or document.get("phase") != prior_phase
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise FrozenPrepareOrchestratorError(
                f"prior phase {prior_phase} evidence differs from journal"
            )
        records[prior_phase] = {
            "document": document,
            "file_sha256": digest,
        }
    return records


def _prepare_phase_evidence(
    context: LoadedOrchestration,
    *,
    phase: str,
    results: Mapping[str, Mapping[str, Any]],
    journal_state: Mapping[str, Any],
    evidence_paths: Mapping[str, Path],
) -> tuple[
    Path,
    dict[str, Path],
    dict[str, Path],
    dict[str, Any],
]:
    phase_root = context.output_root / "phases" / phase
    role_root = phase_root / "role-validation"
    claim_root = phase_root / "claim-sources"
    evidence_root = phase_root / "evidence"
    aggregate_root = phase_root / "aggregates"
    observed_at = _timestamp_now()
    role_paths: dict[str, Path] = {}
    role_documents: dict[str, dict[str, Any]] = {}
    role_source_sha256: dict[str, str] = {}
    for role in WORKER.PHASE_ROLES[phase]:
        result = results[role]
        validation = {
            "schema": ROLE_VALIDATION_SCHEMA,
            "status": "validated-request",
            "request_sha256": result["request_sha256"],
            "operation": WORKER.PHASE_OPERATIONS[phase],
            "role": role,
            "campaign_id": context.document["campaign_id"],
            "operation_id": context.document["operation_id"],
            "app_release_sha": context.document["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "approval_sha256": context.document["approval_sha256"],
            "expected_host": context.manifest["topology"][role]["host"],
            "observed_host": result["expected_host"],
            "required_journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
            "business_write_policy": "forbid",
            "agent_artifact_sha256": context.manifest["artifacts"][
                "host_agent_sha256"
            ],
            "host_agent_contract_sha256": context.manifest["artifacts"][
                "host_agent_contract_sha256"
            ],
            "transport": context.manifest["topology"][role]["transport"],
            "observed_at": observed_at,
            "host_identity_observed": result["host_identity_observed"],
            "execution_supported": False,
            "production_contacted": False,
        }
        if set(validation) != ROLE_VALIDATION_FIELDS:
            raise FrozenPrepareOrchestratorError(
                "internal role validation fields differ"
            )
        path, digest, _publication = _persist_document(
            role_root,
            prefix=f"role-validation-{role}",
            document=validation,
        )
        role_paths[role] = path
        role_documents[role] = validation
        role_source_sha256[role] = digest

    prior_records = _load_prior_records(
        context,
        phase=phase,
        journal_state=journal_state,
        evidence_paths=evidence_paths,
    )
    claim_values = _phase_claim_values(
        phase,
        results,
        prior_records=prior_records,
    )
    claim_paths: dict[str, Path] = {}
    claim_source_sha256: dict[str, str] = {}
    for claim in VERIFY.PHASE_CLAIM_RULES[phase]:
        source = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": context.document["campaign_id"],
            "operation_id": context.document["operation_id"],
            "release_sha": context.document["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": phase,
            "operation": WORKER.PHASE_OPERATIONS[phase],
            "claim": claim,
            "value": claim_values[claim],
            "observed_at": observed_at,
            "status": "observed",
        }
        if set(source) != CLAIM_SOURCE_FIELDS:
            raise FrozenPrepareOrchestratorError(
                "internal claim source fields differ"
            )
        path, digest, _publication = _persist_document(
            claim_root,
            prefix=f"claim-{claim}",
            document=source,
        )
        claim_paths[claim] = path
        claim_source_sha256[claim] = digest

    prior_rows = [
        {
            "phase": prior_phase,
            "evidence_sha256": journal_state[
                "phase_evidence_sha256"
            ][prior_phase],
        }
        for prior_phase in CONTROLLER.PHASES[
            : CONTROLLER.PHASES.index(phase)
        ]
    ]
    try:
        prior_claim_rows = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=phase,
            prior_digests={
                row["phase"]: row["evidence_sha256"]
                for row in prior_rows
            },
            prior_records=prior_records,
            campaign_id=context.document["campaign_id"],
            operation_id=context.document["operation_id"],
            release_sha=context.document["release_sha"],
            legacy_release_sha=context.manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan["plan_sha256"],
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenPrepareOrchestratorError(
            "prepare prior claim evidence binding is invalid"
        ) from exc
    dynamic_claims = {
        name: value
        for name, value in claim_values.items()
        if VERIFY.PHASE_CLAIM_RULES[phase][name].kind != "exact"
    }
    phase_input_closure = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _aggregate_hash(
            context.manifest["artifacts"]
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claim_rows,
        "dynamic_claim_values": dynamic_claims,
        "claim_source_sha256": {
            name: claim_source_sha256[name]
            for name in sorted(claim_source_sha256)
        },
        "role_request_sha256": {
            role: results[role]["request_sha256"]
            for role in WORKER.PHASE_ROLES[phase]
        },
        "role_source_artifact_sha256": {
            role: role_source_sha256[role]
            for role in WORKER.PHASE_ROLES[phase]
        },
        "role_observed_at": {
            role: observed_at for role in WORKER.PHASE_ROLES[phase]
        },
    }
    evidence = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": context.manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "release_sha": context.document["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "approval_sha256": context.document["approval_sha256"],
        "manifest_artifact_bindings": context.manifest["artifacts"],
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": observed_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _aggregate_hash(prior_rows),
        "prior_claim_bindings": prior_claim_rows,
        "phase_input_closure_sha256": _aggregate_hash(
            phase_input_closure
        ),
        "role_attestations": [
            {
                "role": role,
                "expected_host": context.manifest["topology"][role]["host"],
                "operation": WORKER.PHASE_OPERATIONS[phase],
                "request_sha256": results[role]["request_sha256"],
                "app_release_sha": context.document["release_sha"],
                "agent_artifact_sha256": context.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": observed_at,
                "status": "verified",
                "transport": context.manifest["topology"][role][
                    "transport"
                ],
                "source_artifact_sha256": role_source_sha256[role],
            }
            for role in WORKER.PHASE_ROLES[phase]
        ],
        "claims": {
            claim: {
                "value": claim_values[claim],
                "source_sha256": claim_source_sha256[claim],
            }
            for claim in VERIFY.PHASE_CLAIM_RULES[phase]
        },
    }
    if set(evidence) != VERIFY.EVIDENCE_FIELDS:
        raise FrozenPrepareOrchestratorError(
            "internal prepare phase evidence fields differ"
        )
    evidence_path, evidence_sha256, _publication = _persist_document(
        evidence_root,
        prefix=phase,
        document=evidence,
    )
    aggregate = {
        "schema": PHASE_AGGREGATE_SCHEMA,
        "status": "completed",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "phase": phase,
        "operation": WORKER.PHASE_OPERATIONS[phase],
        "roles": list(WORKER.PHASE_ROLES[phase]),
        "role_closure": {
            role: {
                "request_sha256": results[role]["request_sha256"],
                "worker_result_sha256": results[role][
                    "worker_result_sha256"
                ],
                "release_attestation_sha256": results[role][
                    "release_attestation_sha256"
                ],
                "transport_manifest_sha256": results[role][
                    "transport_manifest_sha256"
                ],
                "object_versions": results[role]["object_versions"],
                "host_identity_observed": True,
                "payload_bytes_over_ssh": 0,
                "business_write_observed": False,
            }
            for role in WORKER.PHASE_ROLES[phase]
        },
        "claims": claim_values,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "business_write_observed": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "object_storage_mutated": False,
    }
    _persist_document(
        aggregate_root,
        prefix=f"phase-aggregate-{phase}",
        document=aggregate,
    )
    return evidence_path, role_paths, claim_paths, aggregate


def _locate_completed_evidence(
    context: LoadedOrchestration,
    *,
    phase: str,
    digest: str,
) -> Path:
    directory = context.output_root / "phases" / phase / "evidence"
    path = directory / f"{phase}.{digest}.json"
    try:
        _document, _payload, observed = _secure_json(
            path,
            label=f"completed {phase} evidence",
        )
    except FrozenPrepareOrchestratorError:
        if phase in context.prior_paths:
            path = context.prior_paths[phase]
            try:
                _document, observed = VERIFY.read_root_only_evidence(path)
            except VERIFY.PhaseEvidenceError as exc:
                raise FrozenPrepareOrchestratorError(
                    f"completed {phase} evidence is unavailable"
                ) from exc
        else:
            raise
    if observed != digest:
        raise FrozenPrepareOrchestratorError(
            f"completed {phase} evidence digest differs"
        )
    return path


def execute_orchestration(
    request_path: Path,
    *,
    apply: bool = False,
    confirm: str | None = None,
    session_factory: SessionFactory = _default_session_factory,
) -> dict[str, Any]:
    """Execute or reconcile all four exact prepare phases."""

    if os.geteuid() != 0:
        raise FrozenPrepareOrchestratorError(
            "frozen prepare orchestration must run as root"
        )
    if threading.current_thread() is not threading.main_thread():
        raise FrozenPrepareOrchestratorError(
            "frozen prepare orchestration must run in the main thread"
        )
    context = load_orchestration_request(request_path)
    plan_result = {
        "schema": FINAL_AGGREGATE_SCHEMA,
        "status": "planned",
        "campaign_id": context.document["campaign_id"],
        "operation_id": context.document["operation_id"],
        "release_sha": context.document["release_sha"],
        "release_tree_sha": context.document["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "phases": list(PHASES),
        "phase_roles": {
            phase: list(WORKER.PHASE_ROLES[phase]) for phase in PHASES
        },
        "witness_prepare_role_present": False,
        "required_confirmation": orchestration_confirmation(context),
        "plan_only_default": True,
        "runtime_mutated": False,
        "journal_mutated": False,
        "business_write_observed": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "production_traffic_mutated": False,
        "object_storage_mutated": False,
    }
    if not apply:
        return plan_result
    if confirm != orchestration_confirmation(context):
        raise FrozenPrepareOrchestratorError(
            "frozen prepare orchestration confirmation differs"
        )
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            context.manifest,
            approval_path=Path(context.document["approval_path"]),
            approval_policy_path=Path(
                context.document["approval_policy_path"]
            ),
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenPrepareOrchestratorError(
            "production authorization is invalid or expired"
        ) from exc
    hashes = _release_artifact_hashes(context)
    journal = CONTROLLER.ProductionCutoverJournal(
        Path(context.manifest["deployment"]["controller_journal_path"])
    )
    try:
        initial_state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenPrepareOrchestratorError(
            "production cutover journal binding differs"
        ) from exc
    allowed_prefixes = {
        tuple(CONTROLLER.PHASES[: FIRST_PHASE_INDEX + count])
        for count in range(len(PHASES) + 1)
    }
    if tuple(initial_state["completed_phases"]) not in allowed_prefixes:
        raise FrozenPrepareOrchestratorError(
            "cutover journal is outside the exact prepare phase corridor"
        )
    evidence_paths: dict[str, Path] = dict(context.prior_paths)
    for completed_phase in PHASES:
        if completed_phase not in initial_state["completed_phases"]:
            break
        evidence_paths[completed_phase] = _locate_completed_evidence(
            context,
            phase=completed_phase,
            digest=initial_state["phase_evidence_sha256"][
                completed_phase
            ],
        )

    phase_aggregates: dict[str, Any] = {}
    with _signal_cancellation_guard():
        for phase in PHASES:
            try:
                CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
                    context.manifest,
                    approval_path=Path(context.document["approval_path"]),
                    approval_policy_path=Path(
                        context.document["approval_policy_path"]
                    ),
                )
                state = journal.assert_bindings(**_journal_bindings(context))
            except CONTROLLER.CutoverContractError as exc:
                raise FrozenPrepareOrchestratorError(
                    f"{phase} authorization or journal binding failed"
                ) from exc
            if phase in state["completed_phases"]:
                evidence_paths[phase] = _locate_completed_evidence(
                    context,
                    phase=phase,
                    digest=state["phase_evidence_sha256"][phase],
                )
                phase_aggregates[phase] = {
                    "status": "reused-completed",
                    "phase_evidence_sha256": state[
                        "phase_evidence_sha256"
                    ][phase],
                }
                continue
            try:
                state = journal.begin_phase(phase)
            except CONTROLLER.CutoverContractError as exc:
                raise FrozenPrepareOrchestratorError(
                    f"{phase} cannot be durably started"
                ) from exc
            state = _assert_phase_journal_state(
                state,
                context=context,
                phase=phase,
            )
            session_results: dict[str, HostSessionResult] = {}
            for role in WORKER.PHASE_ROLES[phase]:
                observed = _run_host_session(
                    context,
                    journal=journal,
                    phase=phase,
                    role=role,
                    orchestrator_sha256=hashes["orchestrator"],
                    prepare_worker_sha256=hashes["prepare_worker"],
                    session_factory=session_factory,
                )
                session_results[role] = observed
                _persist_document(
                    context.output_root
                    / "phases"
                    / phase
                    / "host-results",
                    prefix=f"host-result-{role}",
                    document=observed.document,
                )
            state = journal.assert_bindings(**_journal_bindings(context))
            state = _assert_phase_journal_state(
                state,
                context=context,
                phase=phase,
            )
            (
                evidence_path,
                role_paths,
                claim_paths,
                aggregate,
            ) = _prepare_phase_evidence(
                context,
                phase=phase,
                results={
                    role: session_results[role].document
                    for role in WORKER.PHASE_ROLES[phase]
                },
                journal_state=state,
                evidence_paths=evidence_paths,
            )
            try:
                CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
                    context.manifest,
                    approval_path=Path(context.document["approval_path"]),
                    approval_policy_path=Path(
                        context.document["approval_policy_path"]
                    ),
                )
                verification, receipt = (
                    CONTROLLER._run_release_phase_verifier(  # noqa: SLF001
                        phase=phase,
                        manifest=context.manifest,
                        manifest_sha256=context.manifest_sha256,
                        plan=context.plan,
                        manifest_path=Path(
                            context.document["controller_manifest_path"]
                        ),
                        approval_path=Path(
                            context.document["approval_path"]
                        ),
                        approval_policy_path=Path(
                            context.document["approval_policy_path"]
                        ),
                        evidence_path=evidence_path,
                        role_validation=[
                            f"{role}={role_paths[role]}"
                            for role in WORKER.PHASE_ROLES[phase]
                        ],
                        claim_source=[
                            f"{claim}={claim_paths[claim]}"
                            for claim in VERIFY.PHASE_CLAIM_RULES[phase]
                        ],
                        prior_phase_evidence=[
                            f"{prior_phase}={evidence_paths[prior_phase]}"
                            for prior_phase in CONTROLLER.PHASES[
                                : CONTROLLER.PHASES.index(phase)
                            ]
                        ],
                    )
                )
                CONTROLLER._persist_phase_verification_receipt(  # noqa: SLF001
                    token=verification,
                    receipt=receipt,
                    evidence_root=Path(
                        context.manifest["deployment"][
                            "controller_evidence_root"
                        ]
                    ),
                )
                completed = journal.complete_phase(
                    phase,
                    verification=verification,
                )
            except CONTROLLER.CutoverContractError as exc:
                raise FrozenPrepareOrchestratorError(
                    f"{phase} release-bound verification failed"
                ) from exc
            if (
                completed["phase_evidence_sha256"][phase]
                != verification.evidence_sha256
            ):
                raise FrozenPrepareOrchestratorError(
                    f"{phase} journal completion readback differs"
                )
            evidence_paths[phase] = evidence_path
            phase_aggregates[phase] = aggregate

    final_state = journal.assert_bindings(**_journal_bindings(context))
    expected_completed = list(
        CONTROLLER.PHASES[: FIRST_PHASE_INDEX + len(PHASES)]
    )
    if (
        final_state["completed_phases"] != expected_completed
        or final_state["status"] != "active"
        or final_state["started_phase"] is not None
        or final_state["first_business_write_allowed"] is not False
    ):
        raise FrozenPrepareOrchestratorError(
            "four-phase prepare journal closure differs"
        )
    final = {
        **plan_result,
        "status": "completed",
        "phase_evidence_sha256": {
            phase: final_state["phase_evidence_sha256"][phase]
            for phase in PHASES
        },
        "phase_aggregates": phase_aggregates,
        "journal_state_sha256": final_state["state_sha256"],
        "journal_event_tail_sha256": final_state["event_tail_sha256"],
        "next_phase": CONTROLLER.PHASES[
            FIRST_PHASE_INDEX + len(PHASES)
        ],
        "runtime_mutated": True,
        "journal_mutated": True,
    }
    final.pop("required_confirmation")
    final.pop("plan_only_default")
    final_path, final_sha256, publication = _persist_document(
        context.output_root / "aggregates",
        prefix="four-phase-aggregate",
        document=final,
    )
    return {
        **final,
        "aggregate_path": os.fspath(final_path),
        "aggregate_sha256": final_sha256,
        "aggregate_publication": publication,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    host = subparsers.add_parser("host", help=argparse.SUPPRESS)
    host.add_argument("--input-manifest", type=Path, required=True)
    host.add_argument("--input-sha256", required=True)
    host.add_argument("--role", choices=PREPARE_ROLES, required=True)
    host.add_argument("--phase", choices=PHASES, required=True)
    host.add_argument("--expected-orchestrator-sha256", required=True)
    controller = subparsers.add_parser("controller")
    controller.add_argument("--request", type=Path, required=True)
    controller.add_argument("--apply", action="store_true")
    controller.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.mode == "host":
            execute_host_session(
                input_manifest_path=args.input_manifest,
                input_sha256=args.input_sha256,
                role=args.role,
                phase=args.phase,
                expected_orchestrator_sha256=(
                    args.expected_orchestrator_sha256
                ),
                input_stream=sys.stdin.buffer,
                output_stream=sys.stdout.buffer,
            )
            return 0
        result = execute_orchestration(
            args.request,
            apply=args.apply,
            confirm=args.confirm,
        )
        print(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except FrozenPrepareOrchestratorError as exc:
        may_have_applied = (
            getattr(args, "mode", None) == "host"
            or bool(getattr(args, "apply", False))
        )
        output = {
            "status": "blocked",
            "error": str(exc),
            "error_class": type(exc).__name__,
            "runtime_mutated": None if may_have_applied else False,
            "journal_mutated": None if may_have_applied else False,
            "reconciliation_required": may_have_applied,
            "business_write_observed": False,
            "current_mutated": False,
            "legacy_mutated": False,
            "production_traffic_mutated": False,
            "object_storage_mutated": False,
        }
        target = (
            sys.stdout.buffer
            if getattr(args, "mode", None) == "host"
            else None
        )
        if target is not None:
            _emit_frame(target, output)
        else:
            print(
                json.dumps(
                    output,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
