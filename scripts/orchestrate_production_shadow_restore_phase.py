#!/usr/bin/env python3
"""Coordinate the frozen restore and publish its exact cutover phase closure.

The command-line interface is intentionally plan-only.  The live Python API
requires explicit authority, transport, and controller-transition callbacks;
inventory and validation use this module's bounded runner by default.  This
module never synthesizes a Docker command, transfers a payload, or completes
the production cutover journal directly.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_final_restore as RESTORE,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as WORKER,
)
from scripts import (  # noqa: E402
    production_shadow_global_docker_inventory_agent as INVENTORY,
)
from scripts import (  # noqa: E402
    produce_production_shadow_frozen_final_restore_phase_evidence as PRODUCER,
)
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASE = "shadow_restore"
OPERATION = "restore-shadow-postgres-and-files-without-redis"
PLAN_SCHEMA = "production-shadow-restore-phase-coordinator-plan-v1"
BASELINE_SCHEMA = (
    "production-shadow-global-docker-inventory-three-role-baseline-v2"
)
INVENTORY_CLOSURE_SCHEMA = (
    "production-shadow-global-docker-inventory-zero-delta-closure-v2"
)
INVENTORY_CLOSURE_REFERENCE_SCHEMA = (
    "production-shadow-global-docker-inventory-zero-delta-reference-v2"
)
DERIVATION_SCHEMA = (
    "production-shadow-frozen-final-restore-claim-derivation-v1"
)
PUBLICATION_SCHEMA = (
    "production-shadow-frozen-final-restore-phase-coordination-v1"
)
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
ROLES = tuple(RESTORE.ROLES)
CLAIMS = tuple(VERIFY.PHASE_CLAIM_RULES[PHASE])
DERIVED_CLAIM_FIELDS = frozenset(
    {"value", "source_path", "source_sha256"}
)
DERIVED_ROLE_VALIDATION_FIELDS = frozenset({"path", "sha256"})
INVENTORY_CLOSURE_REFERENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "completion_path",
        "completion_sha256",
        "closure_path",
        "closure_sha256",
        "operation_host_config_sha256_by_role",
        "captured_before_lease_consumption",
    }
)
INVENTORY_ROLE_FIELDS = frozenset(
    {
        "before",
        "after",
        "comparison",
        "expected_operation_container_id",
        "expected_operation_host_config_sha256",
        "observed_operation_host_config_sha256",
    }
)
INVENTORY_OBSERVATION_FIELDS = frozenset({"request", "response"})
DERIVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "restore_set_path",
        "restore_set_sha256",
        "completion_path",
        "completion_sha256",
        "post_consumption_receipt_path",
        "post_consumption_receipt_sha256",
        "inventory_closure_path",
        "inventory_closure_sha256",
        "prior_final_snapshot_evidence_path",
        "prior_final_snapshot_evidence_sha256",
        "manifest_path",
        "evidence_output_directory",
        "role_validation",
        "prior_phase_evidence",
        "claims",
        "caller_claim_sources_accepted",
        "observed_at",
    }
)
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_VALIDATION_BYTES = 256 * 1024
MAX_CONTROL_STDERR_BYTES = 64 * 1024
CONTROL_TIMEOUT_SECONDS = 120.0
PROCESS_GROUP_TERM_GRACE_SECONDS = 2.0
PROCESS_POLL_SECONDS = 0.01
PROCESS_TREE_QUIESCENCE_SECONDS = 0.05
MAX_PROCESS_SNAPSHOT_MEMBERS = 65536
MAX_PROCESS_TREE_MEMBERS = 8192
PR_SET_CHILD_SUBREAPER = 36
_BOUNDED_PROCESS_LOCK = threading.Lock()
ZERO_SHA256 = "0" * 64


class RestorePhaseCoordinatorError(RuntimeError):
    """The restore phase cannot advance without violating its closure."""


@dataclass(frozen=True)
class CoordinatorContext:
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    restore_set_path: Path
    restore_set: dict[str, Any]
    restore_set_sha256: str
    requests: dict[str, dict[str, Any]]
    restore_output_directory: Path
    coordinator_output_directory: Path
    prior_paths: dict[str, Path]
    prior_records: dict[str, dict[str, Any]]
    journal: dict[str, Any]


@dataclass(frozen=True)
class ControllerTransition:
    """One exact request to the public production cutover controller."""

    action: str
    phase: str
    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    evidence_path: Path | None = None
    role_validation: tuple[str, ...] = ()
    claim_source: tuple[str, ...] = ()
    prior_phase_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventoryControl:
    """One bounded host-stdio control call with no application payload."""

    role: str
    argv: tuple[str, ...]
    stdin: bytes
    max_stdout_bytes: int
    max_stderr_bytes: int
    timeout_seconds: float
    start_new_session: bool
    terminate_process_group_on_exit: bool
    kill_process_group_after_seconds: float
    application_payload_bytes_over_ssh: int


@dataclass(frozen=True)
class ValidationControl:
    """One bounded validation-only host-agent call."""

    role: str
    argv: tuple[str, ...]
    stdin: bytes
    max_stdout_bytes: int
    max_stderr_bytes: int
    timeout_seconds: float
    start_new_session: bool
    terminate_process_group_on_exit: bool
    kill_process_group_after_seconds: float


@dataclass(frozen=True)
class BoundedProcessResult:
    """Typed proof that a process ran under the complete bounded contract."""

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
class InventorySshTrust:
    """SSH paths pinned by the already-validated Nginx coordinator inputs."""

    known_hosts: Path
    ssh_identity: Path
    ssh_identity_sha256: str


@dataclass(frozen=True)
class EvidencePublication:
    """One coordinator-derived receipt; no loose claim input is accepted."""

    derivation_path: Path
    derivation_sha256: str


class InventoryInvoker(Protocol):
    def __call__(self, control: InventoryControl) -> BoundedProcessResult:
        """Run one exact inventory request on its bound host."""


class ValidationRunner(Protocol):
    def __call__(self, control: ValidationControl) -> BoundedProcessResult:
        """Run one rendered validation-only host-agent command."""


class ControllerCallback(Protocol):
    def __call__(self, transition: ControllerTransition) -> Mapping[str, Any]:
        """Invoke the public controller with runtime authorization intact."""


class EvidencePublisher(Protocol):
    def __call__(self, publication: EvidencePublication) -> Mapping[str, Any]:
        """Publish only coordinator-derived evidence inputs."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RestorePhaseCoordinatorError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise RestorePhaseCoordinatorError(f"{label} is not a SHA-256")
    return value


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise RestorePhaseCoordinatorError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_path(path, label="coordinator directory")
    descriptor = -1
    parent_descriptor = -1
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            if path == path.parent:
                raise RestorePhaseCoordinatorError(
                    "filesystem root cannot be a coordinator directory"
                )
            _ensure_private_directory(path.parent)
            parent_descriptor = os.open(path.parent, flags)
            try:
                os.mkdir(
                    path.name,
                    mode=0o700,
                    dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
            except FileExistsError:
                pass
            descriptor = os.open(
                path.name,
                flags,
                dir_fd=parent_descriptor,
            )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise RestorePhaseCoordinatorError(
                "coordinator directory must be root-owned mode 0700"
            )
    except RestorePhaseCoordinatorError:
        raise
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "coordinator directory is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _secure_json(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    path = _absolute_path(path, label=label)
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        SecureFileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            f"{label} is not secure strict JSON"
        ) from exc
    if not isinstance(document, dict):
        raise RestorePhaseCoordinatorError(f"{label} root is not an object")
    return document, _sha256(payload)


def _persist_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str, str]:
    _ensure_private_directory(directory)
    payload = _canonical_json(document) + b"\n"
    if len(payload) > MAX_JSON_BYTES:
        raise RestorePhaseCoordinatorError(
            f"{prefix} document exceeds its bound"
        )
    digest = _sha256(payload)
    path = directory / f"{prefix}-{digest}.json"
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
        try:
            observed = read_secure_bytes(
                path,
                label=f"existing {prefix}",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise RestorePhaseCoordinatorError(
                f"{prefix} could not be persisted safely"
            ) from exc
        if observed != payload:
            raise RestorePhaseCoordinatorError(
                f"existing digest-derived {prefix} differs"
            )
        publication = "reused"
    observed = read_secure_bytes(
        path,
        label=f"persisted {prefix}",
        owner_uid=0,
        max_size=MAX_JSON_BYTES,
    )
    if observed != payload or _sha256(observed) != digest:
        raise RestorePhaseCoordinatorError(
            f"{prefix} readback differs"
        )
    return path, digest, publication


def _parse_path_mapping(
    values: Sequence[str],
    *,
    expected: Sequence[str],
    label: str,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for raw in values:
        key, separator, value = str(raw).partition("=")
        if not separator or not key or not value or key in result:
            raise RestorePhaseCoordinatorError(f"{label} mapping is invalid")
        result[key] = _absolute_path(value, label=f"{label} {key}")
    if set(result) != set(expected):
        raise RestorePhaseCoordinatorError(f"{label} mapping is not exact")
    return result


def _read_cutover_journal(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    plan_sha256: str,
    allow_started: bool,
    allow_completed: bool = False,
) -> dict[str, Any]:
    path = Path(manifest["deployment"]["controller_journal_path"])
    document, _digest = _secure_json(path, label="production cutover journal")
    try:
        journal = CONTROLLER._validate_journal(document)  # noqa: SLF001
    except CONTROLLER.CutoverContractError as exc:
        raise RestorePhaseCoordinatorError(
            "production cutover journal is invalid"
        ) from exc
    expected_bindings = {
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
    }
    if any(
        journal[field] != expected
        for field, expected in expected_bindings.items()
    ):
        raise RestorePhaseCoordinatorError(
            "production cutover journal binding differs"
        )
    index = CONTROLLER.PHASES.index(PHASE)
    prefix = list(CONTROLLER.PHASES[:index])
    completed = list(journal["completed_phases"])
    if allow_completed and completed == [*prefix, PHASE]:
        if (
            PHASE not in journal["phase_evidence_sha256"]
            or PHASE not in journal["phase_verification_sha256"]
            or journal["started_phase"] is not None
        ):
            raise RestorePhaseCoordinatorError(
                "completed restore phase journal closure differs"
            )
        return journal
    if (
        completed != prefix
        or set(journal["phase_evidence_sha256"]) != set(prefix)
        or set(journal["phase_verification_sha256"]) != set(prefix)
    ):
        raise RestorePhaseCoordinatorError(
            "cutover journal lacks the exact restore predecessor prefix"
        )
    if journal["status"] == "active":
        if journal["started_phase"] is not None:
            raise RestorePhaseCoordinatorError(
                "active cutover journal has a stale started phase"
            )
    elif not (
        allow_started
        and journal["status"] == "phase_started"
        and journal["started_phase"] == PHASE
    ):
        raise RestorePhaseCoordinatorError(
            "cutover journal is not ready for shadow_restore"
        )
    return journal


def _load_prior_evidence(
    paths: Mapping[str, Path],
    *,
    journal: Mapping[str, Any],
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
) -> dict[str, dict[str, Any]]:
    expected = CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]
    if set(paths) != set(expected):
        raise RestorePhaseCoordinatorError(
            "prior phase evidence mapping is not exact"
        )
    result: dict[str, dict[str, Any]] = {}
    for phase in expected:
        try:
            document, digest = VERIFY.read_root_only_evidence(paths[phase])
        except VERIFY.PhaseEvidenceError as exc:
            raise RestorePhaseCoordinatorError(
                f"prior phase evidence {phase} is unsafe"
            ) from exc
        if (
            digest != journal["phase_evidence_sha256"][phase]
            or document.get("phase") != phase
            or document.get("campaign_id") != manifest["campaign_id"]
            or document.get("operation_id") != manifest["operation_id"]
            or document.get("release_sha") != manifest["release_sha"]
            or document.get("manifest_sha256") != manifest_sha256
            or document.get("plan_sha256") != plan_sha256
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise RestorePhaseCoordinatorError(
                f"prior phase evidence {phase} binding differs"
            )
        result[phase] = {
            "document": document,
            "file_sha256": digest,
            "path": os.fspath(paths[phase]),
        }
    return result


def _validate_restore_requests(
    requests: Mapping[str, Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    restore_set: Mapping[str, Any],
    restore_set_sha256: str,
) -> dict[str, dict[str, Any]]:
    if set(requests) != set(ROLES):
        raise RestorePhaseCoordinatorError(
            "restore request roles are not exact"
        )
    validated = {
        role: RESTORE.validate_host_request(requests[role])
        for role in ROLES
    }
    try:
        RESTORE.controller_plan(validated)
    except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
        raise RestorePhaseCoordinatorError(
            "frozen restore request plan is invalid"
        ) from exc
    expected = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "controller_manifest_sha256": manifest_sha256,
        "restore_set_sha256": restore_set_sha256,
        "restore_generation_sha256": restore_set[
            "restore_generation_sha256"
        ],
    }
    for role, request in validated.items():
        if (
            request["action"] != "plan"
            or any(request[field] != value for field, value in expected.items())
            or request["expected_host"] != manifest["topology"][role]["host"]
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} restore request differs from phase identity"
            )
    sealed_transport = restore_set["webapp_ir_transport"]
    wa_transport = validated["webapp_ir"]["wa_exact_version"]
    exact_transport_fields = (
        "provider",
        "private",
        "versioned",
        "encryption",
        "bucket",
        "recipient",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "readback_receipt_sha256",
        "exact_version_readback_verified",
    )
    if (
        not isinstance(wa_transport, Mapping)
        or not isinstance(sealed_transport, Mapping)
        or any(
            wa_transport.get(field) != sealed_transport.get(field)
            for field in exact_transport_fields
        )
        or wa_transport.get("payload_bytes_over_ssh") is not False
        or wa_transport.get("presigned_url_persisted") is not False
        or not isinstance(sealed_transport.get("bucket"), str)
        or not sealed_transport["bucket"]
        or not isinstance(sealed_transport.get("recipient"), str)
        or not sealed_transport["recipient"]
    ):
        raise RestorePhaseCoordinatorError(
            "WebApp-IR exact-VersionId differs from the sealed restore set"
        )
    return validated


def _load_context(
    *,
    manifest_path: Path,
    restore_set_path: Path,
    requests: Mapping[str, Mapping[str, Any]],
    prior_phase_evidence: Mapping[str, Path],
    allow_started: bool,
    allow_completed: bool = False,
) -> CoordinatorContext:
    if os.geteuid() != 0:
        raise RestorePhaseCoordinatorError(
            "restore phase coordinator must run as root"
        )
    manifest_path = _absolute_path(manifest_path, label="cutover manifest")
    restore_set_path = _absolute_path(
        restore_set_path,
        label="frozen restore set",
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
        restore_set, restore_set_sha256 = WORKER.load_restore_set(
            restore_set_path
        )
    except (
        CONTROLLER.CutoverContractError,
        WORKER.FrozenFinalRestoreWorkerError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            "manifest, plan, or frozen restore set is invalid"
        ) from exc
    plan_sha256 = plan["plan_sha256"]
    identity = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "controller_manifest_sha256": manifest_sha256,
    }
    if any(restore_set[field] != value for field, value in identity.items()):
        raise RestorePhaseCoordinatorError(
            "frozen restore set differs from cutover identity"
        )
    validated_requests = _validate_restore_requests(
        requests,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        restore_set=restore_set,
        restore_set_sha256=restore_set_sha256,
    )
    journal = _read_cutover_journal(
        manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        allow_started=allow_started,
        allow_completed=allow_completed,
    )
    prior_records = _load_prior_evidence(
        prior_phase_evidence,
        journal=journal,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
    )
    try:
        restore_output = RESTORE.canonical_controller_output_directory(
            validated_requests
        )
    except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
        raise RestorePhaseCoordinatorError(
            "frozen restore output contract is invalid"
        ) from exc
    coordinator_output = (
        Path(manifest["deployment"]["controller_evidence_root"])
        / "shadow-restore-coordinator"
    )
    return CoordinatorContext(
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        plan_sha256=plan_sha256,
        restore_set_path=restore_set_path,
        restore_set=restore_set,
        restore_set_sha256=restore_set_sha256,
        requests=validated_requests,
        restore_output_directory=restore_output,
        coordinator_output_directory=coordinator_output,
        prior_paths=dict(prior_phase_evidence),
        prior_records=prior_records,
        journal=journal,
    )


def _phase_plan_row(plan: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        row for row in plan["phases"] if row.get("phase") == PHASE
    ]
    if len(matches) != 1:
        raise RestorePhaseCoordinatorError(
            "rendered controller plan lacks exact shadow_restore phase"
        )
    row = matches[0]
    operations = []
    for command in row["commands"]:
        argv = command["argv"]
        try:
            operations.append(argv[argv.index("--operation") + 1])
        except (ValueError, IndexError) as exc:
            raise RestorePhaseCoordinatorError(
                "rendered shadow_restore command lacks its operation"
            ) from exc
    if (
        operations != [OPERATION] * len(ROLES)
        or row["execution_supported"] is not False
        or row["journal_begin_required_before_commands"] is not True
        or row["journal_completion_requires_release_verifier_receipt"]
        is not True
        or tuple(command["role"] for command in row["commands"]) != ROLES
        or any(
            command["render_only"] is not True
            or command["executor_available"] is not False
            or "--execute" in command["argv"]
            for command in row["commands"]
        )
    ):
        raise RestorePhaseCoordinatorError(
            "rendered shadow_restore validation plan differs"
        )
    return row


def _derive_inventory_agent_release_sha256(
    context: CoordinatorContext,
) -> str:
    expected_path = (
        INVENTORY.PROJECT_ROOT_PREFIX
        / context.manifest["operation_id"]
        / "releases"
        / context.manifest["release_sha"]
        / INVENTORY.AGENT_RELATIVE
    )
    try:
        paths = {
            _absolute_path(
                Path(context.requests[role]["release_root"])
                / INVENTORY.AGENT_RELATIVE,
                label=f"{role} global Docker inventory agent",
            )
            for role in ROLES
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise RestorePhaseCoordinatorError(
            "global Docker inventory agent release path is unavailable"
        ) from exc
    if paths != {expected_path}:
        raise RestorePhaseCoordinatorError(
            "global Docker inventory agent is not release-derived"
        )
    descriptor = -1
    try:
        descriptor = os.open(
            expected_path,
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
            raise RestorePhaseCoordinatorError(
                "global Docker inventory agent is not an immutable "
                "release file"
            )
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable_fields = (
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
        if any(
            getattr(before, field) != getattr(after, field)
            for field in stable_fields
        ):
            raise RestorePhaseCoordinatorError(
                "global Docker inventory agent changed while hashing"
            )
        return digest.hexdigest()
    except RestorePhaseCoordinatorError:
        raise
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "global Docker inventory agent is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _plan_document(context: CoordinatorContext) -> dict[str, Any]:
    phase = _phase_plan_row(context.plan)
    inventory_agent_sha256 = (
        _derive_inventory_agent_release_sha256(context)
    )
    basis = {
        "schema": PLAN_SCHEMA,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "restore_generation_sha256": context.restore_set[
            "restore_generation_sha256"
        ],
        "phase": PHASE,
        "operation": OPERATION,
        "roles": list(ROLES),
        "restore_request_sha256": {
            role: _sha256(
                RESTORE.canonical_json(context.requests[role])
            )
            for role in ROLES
        },
        "inventory_agent_sha256": inventory_agent_sha256,
        "phase_validation_command_sha256": {
            command["role"]: _sha256(_canonical_json(command["argv"]))
            for command in phase["commands"]
        },
        "baseline_before_restore_required": True,
        "post_inventory_before_lease_consume_required": True,
        "non_operation_resource_delta_required": 0,
        "claim_sources_caller_supplied": False,
        "controller_public_transition_required": True,
        "webapp_ir_payload_bytes_over_ssh": 0,
        "cli_apply_supported": False,
    }
    digest = _sha256(_canonical_json(basis))
    confirmation = (
        "execute-production-shadow-restore-phase:"
        f"{context.manifest['operation_id']}:{digest}:"
        f"{context.restore_set_sha256}"
    )
    return {
        **basis,
        "coordinator_plan_sha256": digest,
        "required_confirmation": confirmation,
        "status": "planned",
        "production_contacted": False,
        "journal_mutated": False,
    }


def plan_restore_phase(
    *,
    manifest_path: Path,
    restore_set_path: Path,
    requests: Mapping[str, Mapping[str, Any]],
    prior_phase_evidence: Mapping[str, Path],
) -> dict[str, Any]:
    context = _load_context(
        manifest_path=manifest_path,
        restore_set_path=restore_set_path,
        requests=requests,
        prior_phase_evidence=prior_phase_evidence,
        allow_started=True,
        allow_completed=True,
    )
    return _plan_document(context)


def _persist_payload(
    directory: Path,
    *,
    prefix: str,
    payload: bytes,
    maximum: int,
) -> tuple[Path, str]:
    _ensure_private_directory(directory)
    if not payload or len(payload) > maximum:
        raise RestorePhaseCoordinatorError(
            f"{prefix} payload is empty or oversized"
        )
    digest = _sha256(payload)
    path = directory / f"{prefix}-{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=prefix,
            mode=0o600,
            max_size=maximum,
        )
    except SecureFileError:
        try:
            observed = read_secure_bytes(
                path,
                label=f"existing {prefix}",
                owner_uid=0,
                max_size=maximum,
            )
        except SecureFileError as exc:
            raise RestorePhaseCoordinatorError(
                f"{prefix} could not be persisted"
            ) from exc
        if observed != payload:
            raise RestorePhaseCoordinatorError(
                f"existing digest-derived {prefix} differs"
            )
    observed = read_secure_bytes(
        path,
        label=f"persisted {prefix}",
        owner_uid=0,
        max_size=maximum,
    )
    if observed != payload or _sha256(observed) != digest:
        raise RestorePhaseCoordinatorError(f"{prefix} readback differs")
    return path, digest


def _load_validated_restore_closure(
    context: CoordinatorContext,
) -> tuple[
    dict[str, Any],
    Path,
    str,
    dict[str, dict[str, Any]],
    RESTORE.ControllerJournalStore,
]:
    """Reconstruct completion only from persisted, validated restore bytes."""
    journal_path = (
        context.restore_output_directory / "controller-journal.json"
    )
    preliminary, _journal_sha256 = _secure_json(
        journal_path,
        label="frozen restore controller journal",
    )
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
        raise RestorePhaseCoordinatorError(
            "persisted frozen restore claim identity differs"
        )
    persisted_lease = RESTORE._PersistedLeaseIdentity(claim)  # noqa: SLF001
    prepared: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        path = RESTORE._prepared_request_path(  # noqa: SLF001
            context.restore_output_directory,
            role=role,
            claim_sha256=claim["sha256"],
        )
        readback = RESTORE._read_document_file(  # noqa: SLF001
            path,
            label=f"{role} persisted prepared restore request",
            newline=False,
            allowed_modes=frozenset({0o600}),
        )
        prepared[role] = RESTORE._prepared_apply_request(  # noqa: SLF001
            context.requests[role],
            readback.document,
            lease=persisted_lease,
        )
    store = RESTORE.ControllerJournalStore(
        context.restore_output_directory,
        prepared,
    )
    reference = store.document["completion"]
    if reference is None:
        raise RestorePhaseCoordinatorError(
            "frozen restore completion is not persisted"
        )
    completion_path = Path(reference["path"])
    completion_readback = RESTORE._read_document_file(  # noqa: SLF001
        completion_path,
        label="frozen restore completion",
        maximum=RESTORE.MAX_COMPLETION_BYTES,
        newline=False,
        allowed_modes=frozenset({0o600}),
    )
    if completion_readback.content_sha256 != reference["sha256"]:
        raise RestorePhaseCoordinatorError(
            "frozen restore completion digest differs"
        )
    results = store.load_latest_results()
    expected, expected_sha256 = RESTORE.build_completion(prepared, results)
    if (
        completion_readback.document != expected
        or reference["sha256"] != expected_sha256
    ):
        raise RestorePhaseCoordinatorError(
            "frozen restore completion differs from persisted role closure"
        )
    return (
        expected,
        completion_path,
        expected_sha256,
        prepared,
        store,
    )


def _database_container_ids(
    completion: Mapping[str, Any],
) -> dict[str, str]:
    identifiers: dict[str, str] = {}
    for role in ROLES:
        try:
            semantic = completion["roles"][role]["host_result"][
                "action_evidence"
            ]["verify-final"]["document"]["semantic"]
            identifier = semantic["database_container_id"]
        except (KeyError, TypeError) as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} completion lacks its verified database container"
            ) from exc
        if (
            not isinstance(identifier, str)
            or len(identifier) != 64
            or CONTROLLER.SHA256_RE.fullmatch(identifier) is None
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} database container identity is invalid"
            )
        identifiers[role] = identifier
    if len(set(identifiers.values())) != len(ROLES):
        raise RestorePhaseCoordinatorError(
            "database container identities are not role-distinct"
        )
    return identifiers


def _database_host_config_sha256s(
    completion: Mapping[str, Any],
) -> dict[str, str]:
    digests: dict[str, str] = {}
    for role in ROLES:
        try:
            value = completion["roles"][role]["host_result"][
                "action_evidence"
            ]["verify-final"]["document"]["semantic"][
                "database_host_config_sha256"
            ]
        except (KeyError, TypeError) as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} completion lacks its verified database HostConfig"
            ) from exc
        digests[role] = _nonzero_sha256(
            value,
            label=f"{role} database HostConfig",
        )
    return digests


def _installed_role_manifests(
    completion: Mapping[str, Any],
) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    for role in ROLES:
        readback = completion["roles"][role]["host_result"]["role_manifest"]
        if (
            not isinstance(readback, dict)
            or not isinstance(readback.get("path"), str)
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} completion role manifest reference differs"
            )
        result[role] = (
            _absolute_path(
                readback["path"],
                label=f"{role} installed role manifest",
            ),
            _nonzero_sha256(
                readback.get("canonical_document_sha256"),
                label=f"{role} role manifest",
            ),
        )
    return result


def _inventory_request(
    context: CoordinatorContext,
    *,
    role: str,
    action: str,
    inventory_agent_sha256: str,
    expected_operation_container_id: str | None,
    expected_operation_host_config_sha256: str | None,
    role_manifest_path: Path | None,
    role_manifest_sha256: str | None,
) -> dict[str, Any]:
    try:
        return INVENTORY.build_request(
            action=action,
            campaign_id=context.manifest["campaign_id"],
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            release_tree_sha=context.manifest["release_tree_sha"],
            restore_generation_sha256=context.restore_set[
                "restore_generation_sha256"
            ],
            role=role,
            agent_sha256=inventory_agent_sha256,
            worker_sha256=context.requests[role]["worker_sha256"],
            expected_operation_container_id=(
                expected_operation_container_id
            ),
            expected_operation_host_config_sha256=(
                expected_operation_host_config_sha256
            ),
            role_manifest_path=role_manifest_path,
            role_manifest_sha256=role_manifest_sha256,
        )
    except (
        INVENTORY.GlobalDockerInventoryError,
        TypeError,
        ValueError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            f"{role} inventory request could not be built"
        ) from exc


def inventory_session_arguments(
    request_value: Mapping[str, Any],
    *,
    ssh_trust: InventorySshTrust,
) -> tuple[str, ...]:
    try:
        request = INVENTORY.validate_request(request_value)
    except INVENTORY.GlobalDockerInventoryError as exc:
        raise RestorePhaseCoordinatorError(
            "inventory control request is invalid"
        ) from exc
    host_arguments = [
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "HOME=/root",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        "/usr/bin/python3",
        "-I",
        "-B",
        request["agent_path"],
        "--host-stdio",
    ]
    if request["role"] == "bot_fi":
        return tuple(host_arguments)
    if not isinstance(ssh_trust, InventorySshTrust):
        raise RestorePhaseCoordinatorError(
            "inventory SSH trust anchor is invalid"
        )
    ssh_identity = _absolute_path(
        ssh_trust.ssh_identity,
        label="SSH identity",
    )
    known_hosts = _absolute_path(
        ssh_trust.known_hosts,
        label="SSH known-hosts",
    )
    _nonzero_sha256(
        ssh_trust.ssh_identity_sha256,
        label="SSH identity trust anchor",
    )
    port = RESTORE.ROLE_PORTS[request["role"]]
    if type(port) is not int or not 1 <= port <= 65535:
        raise RestorePhaseCoordinatorError(
            "inventory SSH port binding is invalid"
        )
    try:
        remote = RESTORE._safe_remote_command(host_arguments)  # noqa: SLF001
    except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
        raise RestorePhaseCoordinatorError(
            "inventory remote command is unsafe"
        ) from exc
    return (
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
        "-T",
        "-p",
        str(port),
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
        remote,
    )


def _bind_inventory_ssh_trust(
    context: CoordinatorContext,
    *,
    nginx_inputs: NGINX.CoordinatorInputs,
    ssh_identity: Path,
    known_hosts: Path,
) -> InventorySshTrust:
    try:
        bound_identity = _absolute_path(
            nginx_inputs.ssh_identity,
            label="Nginx SSH identity",
        )
        bound_known_hosts = _absolute_path(
            nginx_inputs.known_hosts,
            label="Nginx SSH known-hosts",
        )
        bound_identity_sha256 = _nonzero_sha256(
            nginx_inputs.ssh_identity_sha256,
            label="Nginx SSH identity trust anchor",
        )
        operation_id = nginx_inputs.operation_id
        release_sha = nginx_inputs.release_sha
        release_tree_sha = nginx_inputs.release_tree_sha
        coordinator_root = nginx_inputs.coordinator_root
    except (AttributeError, TypeError) as exc:
        raise RestorePhaseCoordinatorError(
            "validated Nginx SSH trust inputs are unavailable"
        ) from exc
    supplied_identity = _absolute_path(
        ssh_identity,
        label="supplied SSH identity",
    )
    supplied_known_hosts = _absolute_path(
        known_hosts,
        label="supplied SSH known-hosts",
    )
    if (
        supplied_identity != bound_identity
        or supplied_known_hosts != bound_known_hosts
        or operation_id != context.manifest["operation_id"]
        or release_sha != context.manifest["release_sha"]
        or release_tree_sha != context.manifest["release_tree_sha"]
        or coordinator_root != context.restore_output_directory.parent
    ):
        raise RestorePhaseCoordinatorError(
            "inventory SSH paths or release identity differ from "
            "validated Nginx trust inputs"
        )
    try:
        identity_payload = read_secure_bytes(
            bound_identity,
            label="validated Nginx SSH identity",
            owner_uid=0,
            max_size=NGINX.MAX_KEY_BYTES,
        )
        known_hosts_payload = read_secure_bytes(
            bound_known_hosts,
            label="validated Nginx SSH known-hosts",
            owner_uid=0,
            max_size=NGINX.MAX_KEY_BYTES,
        )
    except SecureFileError as exc:
        raise RestorePhaseCoordinatorError(
            "validated Nginx SSH trust files are unavailable or unsafe"
        ) from exc
    if (
        not identity_payload
        or _sha256(identity_payload) != bound_identity_sha256
        or not known_hosts_payload
    ):
        raise RestorePhaseCoordinatorError(
            "validated Nginx SSH trust file identity differs"
        )
    return InventorySshTrust(
        known_hosts=bound_known_hosts,
        ssh_identity=bound_identity,
        ssh_identity_sha256=bound_identity_sha256,
    )


def _capture_inventory_set(
    context: CoordinatorContext,
    *,
    action: str,
    invoke: InventoryInvoker,
    inventory_agent_sha256: str,
    ssh_trust: InventorySshTrust,
    baseline: Mapping[str, Mapping[str, Any]] | None = None,
    completion: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    if action not in {"capture-before", "capture-after"}:
        raise RestorePhaseCoordinatorError("inventory action is invalid")
    if (action == "capture-before") != (
        baseline is None and completion is None
    ):
        raise RestorePhaseCoordinatorError(
            "inventory before/after inputs are inconsistent"
        )
    container_ids = (
        _database_container_ids(completion)
        if completion is not None
        else {role: None for role in ROLES}
    )
    host_config_sha256s = (
        _database_host_config_sha256s(completion)
        if completion is not None
        else {role: None for role in ROLES}
    )
    role_manifests = (
        _installed_role_manifests(completion)
        if completion is not None
        else {role: (None, None) for role in ROLES}
    )
    result: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        role_manifest_path, role_manifest_sha256 = role_manifests[role]
        request = _inventory_request(
            context,
            role=role,
            action=action,
            inventory_agent_sha256=inventory_agent_sha256,
            expected_operation_container_id=container_ids[role],
            expected_operation_host_config_sha256=(
                host_config_sha256s[role]
            ),
            role_manifest_path=role_manifest_path,
            role_manifest_sha256=role_manifest_sha256,
        )
        try:
            control = InventoryControl(
                role=role,
                argv=inventory_session_arguments(
                    request,
                    ssh_trust=ssh_trust,
                ),
                stdin=_canonical_json(request) + b"\n",
                max_stdout_bytes=INVENTORY.MAX_RESPONSE_BYTES + 1,
                max_stderr_bytes=MAX_CONTROL_STDERR_BYTES,
                timeout_seconds=CONTROL_TIMEOUT_SECONDS,
                start_new_session=True,
                terminate_process_group_on_exit=True,
                kill_process_group_after_seconds=(
                    PROCESS_GROUP_TERM_GRACE_SECONDS
                ),
                application_payload_bytes_over_ssh=0,
            )
            execution = _invoke_bounded_process(
                invoke,
                control,
                label=f"{role} inventory control",
            )
            if (
                execution.returncode != 0
                or execution.timed_out
                or execution.stdout_limit_exceeded
                or execution.stderr_limit_exceeded
                or execution.stderr
                or not execution.stdout
                or not execution.stdout.endswith(b"\n")
                or execution.stdout.count(b"\n") != 1
            ):
                raise RestorePhaseCoordinatorError(
                    f"{role} inventory control result is invalid"
                )
            observed = json.loads(
                execution.stdout[:-1].decode("ascii"),
                object_pairs_hook=_strict_object,
            )
            validated = INVENTORY.validate_response(
                observed,
                request=request,
            )
        except (
            INVENTORY.GlobalDockerInventoryError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} inventory capture failed closed"
            ) from exc
        if validated["role"] != role:
            raise RestorePhaseCoordinatorError(
                f"{role} inventory response role differs"
            )
        result[role] = {
            "request": request,
            "response": validated,
        }
    return result


def _baseline_document(
    context: CoordinatorContext,
    responses: Mapping[str, Mapping[str, Any]],
    *,
    inventory_agent_sha256: str,
) -> dict[str, Any]:
    if set(responses) != set(ROLES):
        raise RestorePhaseCoordinatorError(
            "inventory baseline roles are not exact"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        row = responses[role]
        if not isinstance(row, Mapping) or set(row) != {
            "request",
            "response",
        }:
            raise RestorePhaseCoordinatorError(
                f"{role} inventory baseline row fields differ"
            )
        expected_request = _inventory_request(
            context,
            role=role,
            action="capture-before",
            inventory_agent_sha256=inventory_agent_sha256,
            expected_operation_container_id=None,
            expected_operation_host_config_sha256=None,
            role_manifest_path=None,
            role_manifest_sha256=None,
        )
        try:
            request = INVENTORY.validate_request(row["request"])
            response = INVENTORY.validate_response(
                row["response"],
                request=request,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} inventory baseline validation failed"
            ) from exc
        if (
            request != expected_request
            or response["action"] != "capture-before"
            or sum(response["operation_resource_counts"].values()) != 0
        ):
            raise RestorePhaseCoordinatorError(
                "pre-restore inventory contains operation resources "
                "or different bindings"
            )
        normalized[role] = {
            "request": request,
            "response": response,
        }
    return {
        "schema": BASELINE_SCHEMA,
        "status": "captured-before-any-restore",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "restore_generation_sha256": context.restore_set[
            "restore_generation_sha256"
        ],
        "roles": {
            role: normalized[role]
            for role in ROLES
        },
        "role_order": list(ROLES),
        "complete_before_restore": True,
        "operation_resource_count": 0,
        "operation_host_config_sha256_by_role": {
            role: None for role in ROLES
        },
    }


def _load_existing_baseline(
    context: CoordinatorContext,
    *,
    inventory_agent_sha256: str,
) -> tuple[dict[str, Any], Path, str] | None:
    baseline_directory = context.coordinator_output_directory / "inventory"
    fixed_path = baseline_directory / "baseline-reference.json"
    try:
        os.lstat(fixed_path)
    except FileNotFoundError:
        reference = None
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference cannot be inspected"
        ) from exc
    else:
        reference, _reference_digest = _secure_json(
            fixed_path,
            label="inventory baseline reference",
        )
    if reference is None:
        return None
    if set(reference) != {"path", "sha256"}:
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference fields differ"
        )
    path = _absolute_path(reference["path"], label="inventory baseline")
    expected_digest = _nonzero_sha256(
        reference["sha256"],
        label="inventory baseline reference",
    )
    if (
        path.parent != baseline_directory
        or path.name != f"baseline-{expected_digest}.json"
    ):
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference path differs"
        )
    document, digest = _secure_json(path, label="inventory baseline")
    if digest != expected_digest:
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference digest differs"
        )
    if not isinstance(document.get("roles"), Mapping):
        raise RestorePhaseCoordinatorError(
            "persisted inventory baseline roles differ"
        )
    expected = _baseline_document(
        context,
        document["roles"],
        inventory_agent_sha256=inventory_agent_sha256,
    )
    if document != expected:
        raise RestorePhaseCoordinatorError(
            "persisted inventory baseline differs"
        )
    return document, path, digest


def _persist_or_load_baseline(
    context: CoordinatorContext,
    *,
    inventory_invoke: InventoryInvoker,
    inventory_agent_sha256: str,
    ssh_trust: InventorySshTrust,
) -> tuple[dict[str, Any], Path, str]:
    existing = _load_existing_baseline(
        context,
        inventory_agent_sha256=inventory_agent_sha256,
    )
    if existing is not None:
        return existing

    restore_journal = (
        context.restore_output_directory / "controller-journal.json"
    )
    try:
        os.lstat(restore_journal)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "restore journal state cannot be inspected"
        ) from exc
    else:
        raise RestorePhaseCoordinatorError(
            "restore journal exists without a complete pre-restore baseline"
        )
    responses = _capture_inventory_set(
        context,
        action="capture-before",
        invoke=inventory_invoke,
        inventory_agent_sha256=inventory_agent_sha256,
        ssh_trust=ssh_trust,
    )
    document = _baseline_document(
        context,
        responses,
        inventory_agent_sha256=inventory_agent_sha256,
    )
    path, digest, _publication = _persist_document(
        baseline_directory,
        prefix="baseline",
        document=document,
    )
    reference_document = {"path": os.fspath(path), "sha256": digest}
    _ensure_private_directory(baseline_directory)
    try:
        write_secure_new_bytes(
            fixed_path,
            _canonical_json(reference_document) + b"\n",
            label="inventory baseline reference",
            mode=0o600,
            max_size=4096,
        )
    except SecureFileError as exc:
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference could not be created"
        ) from exc
    observed_reference, _observed_digest = _secure_json(
        fixed_path,
        label="inventory baseline reference",
    )
    if observed_reference != reference_document:
        raise RestorePhaseCoordinatorError(
            "inventory baseline reference readback differs"
        )
    return document, path, digest


def _inventory_closure(
    context: CoordinatorContext,
    *,
    baseline: Mapping[str, Any],
    baseline_path: Path,
    baseline_sha256: str,
    completion: Mapping[str, Any],
    completion_path: Path,
    completion_sha256: str,
    inventory_invoke: InventoryInvoker,
    inventory_agent_sha256: str,
    ssh_trust: InventorySshTrust,
) -> tuple[dict[str, Any], Path, str]:
    before = baseline["roles"]
    after = _capture_inventory_set(
        context,
        action="capture-after",
        invoke=inventory_invoke,
        inventory_agent_sha256=inventory_agent_sha256,
        ssh_trust=ssh_trust,
        baseline=before,
        completion=completion,
    )
    comparisons: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        try:
            comparison = INVENTORY.compare_non_operation_inventories(
                before[role]["response"],
                after[role]["response"],
                before_request=before[role]["request"],
                after_request=after[role]["request"],
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} non-operation Docker inventory changed"
            ) from exc
        if comparison.get("non_operation_resource_delta_count") != 0:
            raise RestorePhaseCoordinatorError(
                f"{role} non-operation resource delta is nonzero"
            )
        comparisons[role] = dict(comparison)
    container_ids = _database_container_ids(completion)
    host_config_sha256s = _database_host_config_sha256s(completion)
    document = {
        "schema": INVENTORY_CLOSURE_SCHEMA,
        "status": "zero-non-operation-resource-delta",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "restore_generation_sha256": context.restore_set[
            "restore_generation_sha256"
        ],
        "baseline_path": os.fspath(baseline_path),
        "baseline_sha256": baseline_sha256,
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "role_order": list(ROLES),
        "roles": {
            role: {
                "before": dict(before[role]),
                "after": dict(after[role]),
                "comparison": comparisons[role],
                "expected_operation_container_id": container_ids[role],
                "expected_operation_host_config_sha256": (
                    host_config_sha256s[role]
                ),
                "observed_operation_host_config_sha256": after[role][
                    "response"
                ]["observed_operation_host_config_sha256"],
            }
            for role in ROLES
        },
        "non_operation_resource_delta_count": 0,
        "operation_host_config_sha256_by_role": host_config_sha256s,
        "captured_before_lease_consumption": True,
    }
    path, digest, _publication = _persist_document(
        context.coordinator_output_directory / "inventory",
        prefix="zero-delta",
        document=document,
    )
    _persist_inventory_closure_reference(
        context,
        completion=completion,
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        closure_path=path,
        closure_sha256=digest,
    )
    return document, path, digest


def _inventory_closure_reference_document(
    context: CoordinatorContext,
    *,
    completion: Mapping[str, Any],
    completion_path: Path,
    completion_sha256: str,
    closure_path: Path,
    closure_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": INVENTORY_CLOSURE_REFERENCE_SCHEMA,
        "status": "persisted-before-lease-consumption",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "restore_generation_sha256": context.restore_set[
            "restore_generation_sha256"
        ],
        "completion_path": os.fspath(
            _absolute_path(completion_path, label="restore completion")
        ),
        "completion_sha256": _nonzero_sha256(
            completion_sha256,
            label="restore completion",
        ),
        "closure_path": os.fspath(
            _absolute_path(closure_path, label="inventory closure")
        ),
        "closure_sha256": _nonzero_sha256(
            closure_sha256,
            label="inventory closure",
        ),
        "operation_host_config_sha256_by_role": (
            _database_host_config_sha256s(completion)
        ),
        "captured_before_lease_consumption": True,
    }


def _persist_inventory_closure_reference(
    context: CoordinatorContext,
    *,
    completion: Mapping[str, Any],
    completion_path: Path,
    completion_sha256: str,
    closure_path: Path,
    closure_sha256: str,
) -> Path:
    document = _inventory_closure_reference_document(
        context,
        completion=completion,
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        closure_path=closure_path,
        closure_sha256=closure_sha256,
    )
    if set(document) != INVENTORY_CLOSURE_REFERENCE_FIELDS:
        raise RestorePhaseCoordinatorError(
            "inventory closure reference fields differ"
        )
    directory = context.coordinator_output_directory / "inventory"
    _ensure_private_directory(directory)
    path = directory / "zero-delta-reference.json"
    payload = _canonical_json(document) + b"\n"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="inventory closure reference",
            mode=0o600,
            max_size=64 * 1024,
        )
    except SecureFileError:
        try:
            observed = read_secure_bytes(
                path,
                label="existing inventory closure reference",
                owner_uid=0,
                max_size=64 * 1024,
            )
        except SecureFileError as exc:
            raise RestorePhaseCoordinatorError(
                "inventory closure reference cannot be persisted safely"
            ) from exc
        if observed != payload:
            raise RestorePhaseCoordinatorError(
                "existing inventory closure reference differs"
            )
    observed_document, _observed_sha256 = _secure_json(
        path,
        label="inventory closure reference",
    )
    if observed_document != document:
        raise RestorePhaseCoordinatorError(
            "inventory closure reference readback differs"
        )
    return path


def _load_inventory_closure_reference(
    context: CoordinatorContext,
    *,
    completion: Mapping[str, Any],
    completion_path: Path,
    completion_sha256: str,
) -> tuple[dict[str, Any], Path, str]:
    reference_path = (
        context.coordinator_output_directory
        / "inventory"
        / "zero-delta-reference.json"
    )
    reference, _reference_sha256 = _secure_json(
        reference_path,
        label="pre-consume inventory closure reference",
    )
    if set(reference) != INVENTORY_CLOSURE_REFERENCE_FIELDS:
        raise RestorePhaseCoordinatorError(
            "pre-consume inventory closure reference fields differ"
        )
    closure_path = _absolute_path(
        reference["closure_path"],
        label="referenced pre-consume inventory closure",
    )
    closure_sha256 = _nonzero_sha256(
        reference["closure_sha256"],
        label="referenced pre-consume inventory closure",
    )
    expected_reference = _inventory_closure_reference_document(
        context,
        completion=completion,
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        closure_path=closure_path,
        closure_sha256=closure_sha256,
    )
    expected_directory = context.coordinator_output_directory / "inventory"
    if (
        reference != expected_reference
        or closure_path.parent != expected_directory
        or closure_path.name != f"zero-delta-{closure_sha256}.json"
    ):
        raise RestorePhaseCoordinatorError(
            "pre-consume inventory closure reference binding differs"
        )
    closure, observed_sha256 = _secure_json(
        closure_path,
        label="referenced pre-consume inventory closure",
    )
    expected_identity = {
        "schema": INVENTORY_CLOSURE_SCHEMA,
        "status": "zero-non-operation-resource-delta",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "restore_generation_sha256": context.restore_set[
            "restore_generation_sha256"
        ],
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "role_order": list(ROLES),
        "non_operation_resource_delta_count": 0,
        "operation_host_config_sha256_by_role": (
            _database_host_config_sha256s(completion)
        ),
        "captured_before_lease_consumption": True,
    }
    if (
        observed_sha256 != closure_sha256
        or any(
            closure.get(field) != value
            for field, value in expected_identity.items()
        )
        or not isinstance(closure.get("roles"), Mapping)
        or set(closure["roles"]) != set(ROLES)
        or completion.get("operation_id")
        != context.manifest["operation_id"]
    ):
        raise RestorePhaseCoordinatorError(
            "referenced pre-consume inventory closure differs"
        )
    expected_container_ids = _database_container_ids(completion)
    expected_host_config_sha256s = _database_host_config_sha256s(completion)
    for role in ROLES:
        row = closure["roles"][role]
        if not isinstance(row, Mapping) or set(row) != INVENTORY_ROLE_FIELDS:
            raise RestorePhaseCoordinatorError(
                f"{role} pre-consume inventory role closure differs"
            )
        observations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for name in ("before", "after"):
            observation = row[name]
            if (
                not isinstance(observation, Mapping)
                or set(observation) != INVENTORY_OBSERVATION_FIELDS
            ):
                raise RestorePhaseCoordinatorError(
                    f"{role} pre-consume inventory observation differs"
                )
            try:
                request = INVENTORY.validate_request(
                    observation["request"]
                )
                response = INVENTORY.validate_response(
                    observation["response"],
                    request=request,
                )
            except INVENTORY.GlobalDockerInventoryError as exc:
                raise RestorePhaseCoordinatorError(
                    f"{role} pre-consume inventory observation is invalid"
                ) from exc
            observations[name] = (request, response)
        before_request, before_response = observations["before"]
        after_request, after_response = observations["after"]
        try:
            comparison = INVENTORY.compare_non_operation_inventories(
                before_response,
                after_response,
                before_request=before_request,
                after_request=after_request,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} pre-consume inventory comparison is invalid"
            ) from exc
        expected_digest = expected_host_config_sha256s[role]
        if (
            row["comparison"] != comparison
            or row["expected_operation_container_id"]
            != expected_container_ids[role]
            or row["expected_operation_host_config_sha256"]
            != expected_digest
            or row["observed_operation_host_config_sha256"]
            != expected_digest
            or after_request["expected_operation_container_id"]
            != expected_container_ids[role]
            or after_request["expected_operation_host_config_sha256"]
            != expected_digest
            or after_response["expected_operation_host_config_sha256"]
            != expected_digest
            or after_response["observed_operation_host_config_sha256"]
            != expected_digest
            or before_request["expected_operation_host_config_sha256"]
            is not None
            or before_response["observed_operation_host_config_sha256"]
            is not None
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} pre-consume HostConfig closure differs"
            )
    return closure, closure_path, closure_sha256


def _post_consumption_receipt(
    *,
    completion: Mapping[str, Any],
    completion_path: Path,
    completion_sha256: str,
    prepared_requests: Mapping[str, Mapping[str, Any]],
    nginx_inputs: NGINX.CoordinatorInputs,
    store: RESTORE.ControllerJournalStore,
) -> tuple[dict[str, Any], Path, str]:
    post_reference = store.document["post_consumption"]
    consumption_reference = store.document["consumption"]
    if post_reference is None or consumption_reference is None:
        raise RestorePhaseCoordinatorError(
            "frozen restore lacks its post-consumption closure"
        )
    post_path = Path(post_reference["path"])
    post, post_sha256 = _secure_json(
        post_path,
        label="frozen restore post-consumption receipt",
    )
    if post_sha256 != post_reference["sha256"]:
        raise RestorePhaseCoordinatorError(
            "post-consumption receipt digest differs"
        )
    if set(prepared_requests) != set(ROLES):
        raise RestorePhaseCoordinatorError(
            "prepared restore requests are not exact"
        )
    authorities = []
    for role in ROLES:
        try:
            request = RESTORE.validate_host_request(
                prepared_requests[role]
            )
        except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} prepared restore request is invalid"
            ) from exc
        authority = request.get("authority")
        if not isinstance(authority, Mapping):
            raise RestorePhaseCoordinatorError(
                f"{role} prepared restore request lacks live authority"
            )
        authorities.append(dict(authority))
    if any(authority != authorities[0] for authority in authorities[1:]):
        raise RestorePhaseCoordinatorError(
            "prepared restore requests do not share one live authority"
        )
    authority = authorities[0]
    try:
        (
            actual_consumption_path,
            actual_consumption_sha256,
            actual_consumption,
        ) = RESTORE.coordinator_consumption_readback(
            nginx_inputs,
            claim_path=Path(authority["claim_path"]),
            claim_sha256=authority["claim_sha256"],
            claimed_path=Path(consumption_reference["path"]),
            claimed_sha256=consumption_reference["sha256"],
            completion=completion,
            completion_sha256=completion_sha256,
        )
    except (
        KeyError,
        TypeError,
        RESTORE.FrozenFinalRestoreOrchestratorError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            "actual coordinator consumption audit is invalid"
        ) from exc
    if (
        actual_consumption_path
        != _absolute_path(
            consumption_reference["path"],
            label="actual consumption audit",
        )
        or actual_consumption_sha256
        != _nonzero_sha256(
            consumption_reference["sha256"],
            label="actual consumption audit",
        )
        or actual_consumption.get("schema")
        != NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA
        or actual_consumption.get("status") != "consumed"
        or actual_consumption.get("claim_sha256")
        != completion["live_lease_claim_sha256"]
        or actual_consumption.get("claim_epoch")
        != completion["live_lease_claim_epoch"]
        or actual_consumption.get("claim_nonce")
        != completion["live_lease_claim_nonce"]
        or actual_consumption.get("outcome")
        != WORKER.LIVE_LEASE_SUCCESS_OUTCOME
        or actual_consumption.get("outcome_sha256")
        != completion_sha256
    ):
        raise RestorePhaseCoordinatorError(
            "actual coordinator consumption audit binding differs"
        )
    expected, expected_sha256 = RESTORE.build_post_consumption_receipt(
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        completion=completion,
        consumption_path=actual_consumption_path,
        consumption_sha256=actual_consumption_sha256,
    )
    if post != expected or post_sha256 != expected_sha256:
        raise RestorePhaseCoordinatorError(
            "post-consumption receipt differs from exact completion"
        )
    return post, post_path, post_sha256


ProcessControl = InventoryControl | ValidationControl


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


def _validate_process_control(control: ProcessControl) -> None:
    if not isinstance(control, (InventoryControl, ValidationControl)):
        raise RestorePhaseCoordinatorError(
            "bounded process control type is invalid"
        )
    if (
        not isinstance(control.role, str)
        or not control.role
        or not isinstance(control.argv, tuple)
        or not control.argv
        or any(
            not isinstance(token, str)
            or not token
            or "\x00" in token
            for token in control.argv
        )
        or not os.path.isabs(control.argv[0])
        or not isinstance(control.stdin, bytes)
        or len(control.stdin) > MAX_JSON_BYTES
        or type(control.max_stdout_bytes) is not int
        or not 0 <= control.max_stdout_bytes <= MAX_JSON_BYTES
        or type(control.max_stderr_bytes) is not int
        or not 0 <= control.max_stderr_bytes <= MAX_JSON_BYTES
        or type(control.timeout_seconds) not in {int, float}
        or not math.isfinite(control.timeout_seconds)
        or not 0 < control.timeout_seconds <= 3600
        or control.start_new_session is not True
        or control.terminate_process_group_on_exit is not True
        or type(control.kill_process_group_after_seconds)
        not in {int, float}
        or not math.isfinite(control.kill_process_group_after_seconds)
        or not 0 < control.kill_process_group_after_seconds <= 10
    ):
        raise RestorePhaseCoordinatorError(
            "bounded process control contract is invalid"
        )
    if isinstance(control, InventoryControl) and (
        type(control.application_payload_bytes_over_ssh) is not int
        or control.application_payload_bytes_over_ssh != 0
    ):
        raise RestorePhaseCoordinatorError(
            "inventory control application payload contract is invalid"
        )


def _process_control_sha256(control: ProcessControl) -> str:
    _validate_process_control(control)
    document: dict[str, Any] = {
        "schema": "production-shadow-bounded-process-control-v1",
        "kind": (
            "inventory"
            if isinstance(control, InventoryControl)
            else "validation"
        ),
        "role": control.role,
        "argv": list(control.argv),
        "stdin_bytes": len(control.stdin),
        "stdin_sha256": _sha256(control.stdin),
        "max_stdout_bytes": control.max_stdout_bytes,
        "max_stderr_bytes": control.max_stderr_bytes,
        "timeout_seconds": control.timeout_seconds,
        "start_new_session": control.start_new_session,
        "terminate_process_group_on_exit": (
            control.terminate_process_group_on_exit
        ),
        "kill_process_group_after_seconds": (
            control.kill_process_group_after_seconds
        ),
    }
    if isinstance(control, InventoryControl):
        document["application_payload_bytes_over_ssh"] = (
            control.application_payload_bytes_over_ssh
        )
    return _sha256(_canonical_json(document))


def _process_identity(pid: int) -> ProcessIdentity | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            return None
        return ProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _process_snapshot() -> dict[int, ProcessIdentity]:
    observed: dict[int, ProcessIdentity] = {}
    scanned = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_PROCESS_SNAPSHOT_MEMBERS:
                raise RestorePhaseCoordinatorError(
                    "subprocess closure exceeds its process bound"
                )
            identity = _process_identity(int(entry.name, 10))
            if identity is not None:
                observed[identity.pid] = identity
    except RestorePhaseCoordinatorError:
        raise
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "subprocess ownership inventory is unavailable"
        ) from exc
    return observed


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise RestorePhaseCoordinatorError(
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
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity] | None = None,
    include_zombies: bool = False,
) -> tuple[ProcessIdentity, ...]:
    snapshot = _process_snapshot()
    observed_root = snapshot.get(root_identity.pid)
    owned_ids: set[int] = set()
    if (
        observed_root is not None
        and observed_root.start_time == root_identity.start_time
    ):
        owned_ids.add(root_identity.pid)
    if tracked is not None:
        for identity in tracked:
            current = snapshot.get(identity.pid)
            if (
                current is not None
                and current.start_time == identity.start_time
            ):
                owned_ids.add(identity.pid)
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.pid != root_identity.pid
            and identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.pid)
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
    owned = tuple(
        identity
        for pid, identity in snapshot.items()
        if pid in owned_ids
    )
    if tracked is not None:
        discovered = set(owned)
        if len(tracked | discovered) > MAX_PROCESS_TREE_MEMBERS:
            raise RestorePhaseCoordinatorError(
                "subprocess tree exceeds its process bound"
            )
        tracked.update(discovered)
    return tuple(
        identity
        for identity in owned
        if include_zombies or identity.state != "Z"
    )


def _reap_owned_zombies(
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=True,
        ):
            if (
                identity.key == root_identity.key
                or identity.parent_pid != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(identity.pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise RestorePhaseCoordinatorError(
                    "owned subprocess zombie could not be reaped"
                ) from exc
            if waited not in {0, identity.pid}:
                raise RestorePhaseCoordinatorError(
                    "owned subprocess reap returned an unexpected PID"
                )
            reaped |= waited == identity.pid
        if not reaped:
            return


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> bool:
    current = _process_identity(identity.pid)
    if current is None or current.start_time != identity.start_time:
        return False
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "identity-bound subprocess handle cannot be opened"
        ) from exc
    try:
        refreshed = _process_identity(identity.pid)
        if refreshed is None or refreshed.start_time != identity.start_time:
            return False
        return _signal_process_handle(descriptor, signum)
    except ProcessLookupError:
        return False
    finally:
        original_error = sys.exception()
        try:
            os.close(descriptor)
        except BaseException as close_error:
            if original_error is not None:
                raise original_error from close_error
            raise


def _signal_process_handle(descriptor: int, signum: int) -> bool:
    try:
        signal.pidfd_send_signal(descriptor, signum)
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "identity-bound subprocess signal failed"
        ) from exc


def _signal_owned_process(
    identity: ProcessIdentity,
    signum: int,
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
) -> bool:
    if identity.key == root_identity.key and root_descriptor is not None:
        return _signal_process_handle(root_descriptor, signum)
    return _signal_process_identity(identity, signum)


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity],
    grace_seconds: float,
) -> bool:
    signalled = False
    for identity in reversed(
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    ):
        signalled = (
            _signal_owned_process(
                identity,
                signal.SIGTERM,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )
            or signalled
        )
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    ):
        process.poll()
        _reap_owned_zombies(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    for identity in reversed(
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
    ):
        signalled = (
            _signal_owned_process(
                identity,
                signal.SIGKILL,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )
            or signalled
        )
    try:
        process.wait(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        signalled = (
            _signal_owned_process(
                root_identity,
                signal.SIGKILL,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )
            or signalled
        )
        try:
            process.wait(timeout=max(0.1, grace_seconds))
        except subprocess.TimeoutExpired as final_exc:
            raise RestorePhaseCoordinatorError(
                "bounded subprocess root survived identity-bound cleanup"
            ) from final_exc

    absence_deadline = (
        time.monotonic()
        + grace_seconds
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_owned_zombies(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        owned = _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
            include_zombies=True,
        )
        if owned:
            stable_since = None
            for identity in reversed(owned):
                if identity.state != "Z":
                    signalled = (
                        _signal_owned_process(
                            identity,
                            signal.SIGKILL,
                            root_identity=root_identity,
                            root_descriptor=root_descriptor,
                        )
                        or signalled
                    )
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= PROCESS_TREE_QUIESCENCE_SECONDS
        ):
            return signalled
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, absence_deadline - time.monotonic()),
            )
        )
    _reap_owned_zombies(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    if _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
        include_zombies=True,
    ):
        raise RestorePhaseCoordinatorError(
            "bounded subprocess tree survived forced cleanup"
        )
    return signalled


def _close_selector_stream(
    selector: selectors.BaseSelector,
    stream: Any,
) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass
    stream.close()


def _run_bounded_process_locked(
    control: ProcessControl,
) -> BoundedProcessResult:
    """Execute one exact argv with bounded pipes and whole-group cleanup."""

    _validate_process_control(control)
    process: subprocess.Popen[bytes] | None = None
    root_identity: ProcessIdentity | None = None
    root_descriptor: int | None = None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    stdin_bytes_sent = 0
    timed_out = False
    stdout_limit_exceeded = False
    stderr_limit_exceeded = False
    cleanup_performed = False
    process_group_terminated = False
    tracked: set[ProcessIdentity] = set()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    selector = selectors.DefaultSelector()
    try:
        process = subprocess.Popen(  # noqa: S603
            list(control.argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            close_fds=True,
            shell=False,
            start_new_session=control.start_new_session,
            bufsize=0,
        )
        root_descriptor = os.pidfd_open(process.pid, 0)
        root_identity = _process_identity(process.pid)
        if root_identity is None:
            raise RestorePhaseCoordinatorError(
                "bounded subprocess identity is unavailable"
            )
        tracked.add(root_identity)
        if (
            process.stdin is None
            or process.stdout is None
            or process.stderr is None
        ):
            raise RestorePhaseCoordinatorError(
                "bounded process pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        if control.stdin:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(
                process.stdin,
                selectors.EVENT_WRITE,
                "stdin",
            )
        else:
            process.stdin.close()
        deadline = time.monotonic() + control.timeout_seconds
        drain_deadline: float | None = None
        while True:
            _reap_owned_zombies(
                root_identity,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            now = time.monotonic()
            if (
                not cleanup_performed
                and not stdout_limit_exceeded
                and not stderr_limit_exceeded
                and now >= deadline
            ):
                timed_out = True
            cancellation_required = (
                timed_out
                or stdout_limit_exceeded
                or stderr_limit_exceeded
                or process.poll() is not None
            )
            if cancellation_required and not cleanup_performed:
                process_group_terminated = _terminate_process_tree(
                    process,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                    baseline_children=baseline_children,
                    tracked=tracked,
                    grace_seconds=control.kill_process_group_after_seconds,
                )
                cleanup_performed = True
                drain_deadline = (
                    time.monotonic()
                    + max(
                        0.1,
                        control.kill_process_group_after_seconds,
                    )
                )
                if not process.stdin.closed:
                    _close_selector_stream(selector, process.stdin)
            read_streams_open = any(
                key.data in {"stdout", "stderr"}
                for key in selector.get_map().values()
            )
            if cleanup_performed and not read_streams_open:
                break
            if (
                cleanup_performed
                and drain_deadline is not None
                and time.monotonic() >= drain_deadline
            ):
                break
            remaining = (
                drain_deadline - time.monotonic()
                if cleanup_performed and drain_deadline is not None
                else deadline - time.monotonic()
            )
            events = selector.select(max(0.0, min(0.05, remaining)))
            for key, _mask in events:
                stream = key.fileobj
                label = key.data
                if label == "stdin":
                    try:
                        written = os.write(
                            stream.fileno(),
                            control.stdin[
                                stdin_bytes_sent : stdin_bytes_sent + 65536
                            ],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        _close_selector_stream(selector, stream)
                        continue
                    stdin_bytes_sent += written
                    if stdin_bytes_sent == len(control.stdin):
                        _close_selector_stream(selector, stream)
                    continue
                buffer = buffers[label]
                limit = (
                    control.max_stdout_bytes
                    if label == "stdout"
                    else control.max_stderr_bytes
                )
                read_size = min(65536, max(1, limit - len(buffer) + 1))
                try:
                    chunk = os.read(stream.fileno(), read_size)
                except BlockingIOError:
                    continue
                if not chunk:
                    _close_selector_stream(selector, stream)
                    continue
                available = max(0, limit - len(buffer))
                buffer.extend(chunk[:available])
                if len(chunk) > available:
                    if label == "stdout":
                        stdout_limit_exceeded = True
                    else:
                        stderr_limit_exceeded = True
            if (
                process.poll() is not None
                and not selector.get_map()
                and not cleanup_performed
            ):
                continue
        if not cleanup_performed:
            process_group_terminated = _terminate_process_tree(
                process,
                root_identity=root_identity,
                root_descriptor=root_descriptor,
                baseline_children=baseline_children,
                tracked=tracked,
                grace_seconds=control.kill_process_group_after_seconds,
            )
            cleanup_performed = True
        returncode = process.poll()
        if returncode is None:
            raise RestorePhaseCoordinatorError(
                "bounded process did not terminate after group cleanup"
            )
        return BoundedProcessResult(
            control_sha256=_process_control_sha256(control),
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
            stdin_bytes_sent=stdin_bytes_sent,
            deadline_enforced=True,
            stdout_limit_enforced=True,
            stderr_limit_enforced=True,
            timed_out=timed_out,
            stdout_limit_exceeded=stdout_limit_exceeded,
            stderr_limit_exceeded=stderr_limit_exceeded,
            process_group_cleanup_performed=cleanup_performed,
            process_group_terminated=process_group_terminated,
        )
    except RestorePhaseCoordinatorError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestorePhaseCoordinatorError(
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
                                raise RestorePhaseCoordinatorError(
                                    "unidentified bounded subprocess cannot "
                                    "be cleaned safely"
                                )
                        else:
                            _signal_process_handle(
                                root_descriptor,
                                signal.SIGKILL,
                            )
                            try:
                                process.wait(
                                    timeout=max(
                                        0.1,
                                        control.kill_process_group_after_seconds,
                                    )
                                )
                            except subprocess.TimeoutExpired as exc:
                                raise RestorePhaseCoordinatorError(
                                    "unidentified bounded subprocess root "
                                    "survived forced cleanup"
                                ) from exc
                            root_identity = ProcessIdentity(
                                pid=process.pid,
                                parent_pid=os.getpid(),
                                start_time=-1,
                                state="?",
                            )
                    _terminate_process_tree(
                        process,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline_children,
                        tracked=tracked,
                        grace_seconds=(
                            control.kill_process_group_after_seconds
                        ),
                    )
            except BaseException as exc:
                cleanup_errors.append(exc)
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
                if stream is None or stream.closed:
                    continue
                try:
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
    """Execute one exact argv with bounded pipes and whole-tree cleanup."""

    with _BOUNDED_PROCESS_LOCK:
        return _run_bounded_process_locked(control)


def _validated_bounded_result(
    value: Any,
    control: ProcessControl,
    *,
    label: str,
) -> BoundedProcessResult:
    if type(value) is not BoundedProcessResult:
        raise RestorePhaseCoordinatorError(
            f"{label} did not return a typed bounded result"
        )
    if (
        value.control_sha256 != _process_control_sha256(control)
        or type(value.returncode) is not int
        or not isinstance(value.stdout, bytes)
        or not isinstance(value.stderr, bytes)
        or type(value.stdin_bytes_sent) is not int
        or value.stdin_bytes_sent != len(control.stdin)
        or value.deadline_enforced is not True
        or value.stdout_limit_enforced is not True
        or value.stderr_limit_enforced is not True
        or type(value.timed_out) is not bool
        or type(value.stdout_limit_exceeded) is not bool
        or type(value.stderr_limit_exceeded) is not bool
        or type(value.process_group_cleanup_performed) is not bool
        or value.process_group_cleanup_performed is not True
        or type(value.process_group_terminated) is not bool
        or len(value.stdout) > control.max_stdout_bytes
        or len(value.stderr) > control.max_stderr_bytes
        or (
            value.stdout_limit_exceeded
            and len(value.stdout) != control.max_stdout_bytes
        )
        or (
            value.stderr_limit_exceeded
            and len(value.stderr) != control.max_stderr_bytes
        )
    ):
        raise RestorePhaseCoordinatorError(
            f"{label} bounded result contract is invalid"
        )
    return value


def _invoke_bounded_process(
    runner: Callable[[ProcessControl], Any],
    control: ProcessControl,
    *,
    label: str,
) -> BoundedProcessResult:
    _validate_process_control(control)
    if not callable(runner):
        raise RestorePhaseCoordinatorError(
            f"{label} runner is not callable"
        )
    try:
        observed = runner(control)
    except Exception as exc:
        raise RestorePhaseCoordinatorError(
            f"{label} runner failed"
        ) from exc
    return _validated_bounded_result(
        observed,
        control,
        label=label,
    )


def _run_role_validations(
    context: CoordinatorContext,
    *,
    runner: ValidationRunner,
) -> tuple[dict[str, Path], dict[str, str]]:
    phase = _phase_plan_row(context.plan)
    commands = {command["role"]: command for command in phase["commands"]}
    output_directory = (
        context.coordinator_output_directory / "role-validations"
    )
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for role in ROLES:
        command = commands[role]
        argv = tuple(command["argv"])
        if (
            "--execute" in argv
            or command["payload_transfer"]
            != (
                "object-storage-private-versioned-age"
                if role == "webapp_ir"
                else "none"
            )
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} validation command violates transport policy"
            )
        if role == "webapp_ir":
            if argv[0] != "/usr/bin/ssh":
                raise RestorePhaseCoordinatorError(
                    "WebApp-IR validation lacks hardened SSH control"
                )
            required = {
                "BatchMode=yes",
                "IdentitiesOnly=yes",
                "StrictHostKeyChecking=yes",
                "UserKnownHostsFile=/root/.ssh/known_hosts",
                "ConnectTimeout=10",
            }
            if (
                not required.issubset(set(argv))
                or "-F" not in argv
                or argv[argv.index("-F") + 1 : argv.index("-F") + 2]
                != ("/dev/null",)
            ):
                raise RestorePhaseCoordinatorError(
                    "WebApp-IR validation SSH options differ"
                )
        control = ValidationControl(
            role=role,
            argv=argv,
            stdin=b"",
            max_stdout_bytes=MAX_VALIDATION_BYTES,
            max_stderr_bytes=MAX_CONTROL_STDERR_BYTES,
            timeout_seconds=CONTROL_TIMEOUT_SECONDS,
            start_new_session=True,
            terminate_process_group_on_exit=True,
            kill_process_group_after_seconds=(
                PROCESS_GROUP_TERM_GRACE_SECONDS
            ),
        )
        observed = _invoke_bounded_process(
            runner,
            control,
            label=f"{role} validation command",
        )
        if (
            observed.returncode != 0
            or observed.timed_out
            or observed.stdout_limit_exceeded
            or observed.stderr_limit_exceeded
            or observed.stderr
            or not observed.stdout
            or not observed.stdout.endswith(b"\n")
            or observed.stdout.count(b"\n") != 1
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} validation command output is invalid"
            )
        try:
            document = json.loads(
                observed.stdout[:-1].decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} validation did not return strict JSON"
            ) from exc
        if not isinstance(document, dict):
            raise RestorePhaseCoordinatorError(
                f"{role} validation result is not an object"
            )
        payload = observed.stdout
        path, digest = _persist_payload(
            output_directory,
            prefix=role,
            payload=payload,
            maximum=MAX_VALIDATION_BYTES,
        )
        paths[role] = path
        digests[role] = digest
    try:
        _request_hashes, source_hashes, _observed = (
            VERIFY._read_role_validation_records(  # noqa: SLF001
                [f"{role}={paths[role]}" for role in ROLES],
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
            )
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise RestorePhaseCoordinatorError(
            "host-agent role validation set is invalid"
        ) from exc
    if source_hashes != digests:
        raise RestorePhaseCoordinatorError(
            "host-agent validation readback digest differs"
        )
    return paths, digests


def _snapshot_claim(
    context: CoordinatorContext,
    name: str,
) -> str:
    final = context.prior_records["final_snapshot_hashes"]["document"]
    try:
        value = final["claims"][name]["value"]
    except (KeyError, TypeError) as exc:
        raise RestorePhaseCoordinatorError(
            f"final snapshot evidence lacks {name}"
        ) from exc
    return _nonzero_sha256(value, label=f"prior {name}")


def _derive_claim_values(
    context: CoordinatorContext,
    *,
    completion: Mapping[str, Any],
    completion_sha256: str,
    inventory_closure: Mapping[str, Any],
    inventory_closure_sha256: str,
) -> dict[str, Any]:
    postgres_snapshot = context.restore_set[
        "postgres_snapshot_set_sha256"
    ]
    file_snapshot = context.restore_set[
        "reviewed_file_snapshot_set_sha256"
    ]
    if (
        postgres_snapshot
        != _snapshot_claim(context, "postgres_snapshot_set_sha256")
        or file_snapshot
        != _snapshot_claim(
            context,
            "reviewed_file_snapshot_set_sha256",
        )
    ):
        raise RestorePhaseCoordinatorError(
            "restore set differs from final snapshot evidence"
        )
    inventory_closure_sha256 = _nonzero_sha256(
        inventory_closure_sha256,
        label="inventory closure",
    )
    if (
        completion.get("schema") != RESTORE.COMPLETION_SCHEMA
        or completion.get("redis_restored") is not False
        or context.restore_set["constraints"][
            "legacy_redis_restore_included"
        ]
        is not False
        or inventory_closure.get(
            "non_operation_resource_delta_count"
        )
        != 0
        or _sha256(_canonical_json(inventory_closure) + b"\n")
        != inventory_closure_sha256
    ):
        raise RestorePhaseCoordinatorError(
            "restore completion or inventory closure differs"
        )
    for role in ROLES:
        row = completion["roles"][role]["host_result"]
        restore = row["restore_result"]["document"]
        source_role = context.restore_set["target_map"][role]["source_role"]
        source = context.restore_set["sources"][source_role]
        expected_database = source["source_database"]
        expected_trees = {
            "uploads": source["artifacts"][
                "uploads-archive"
            ]["restored_tree_sha256"],
            "audit": source["artifacts"][
                "audit-archive"
            ]["restored_tree_sha256"],
        }
        if (
            restore["database"]["alembic_revision"]
            != expected_database["alembic_revision"]
            or restore["database"]["database_fingerprint_sha256"]
            != expected_database["database_fingerprint_sha256"]
            or restore["database"]["row_count"]
            != expected_database["row_count"]
            or restore["database"]["table_count"]
            != expected_database["table_count"]
            or restore["file_trees"] != expected_trees
            or restore["redis_restore_bytes"] != 0
            or restore["redis_pristine"] is not True
            or row["worker_return"]["result"] != restore
        ):
            raise RestorePhaseCoordinatorError(
                f"{role} restore result differs from the frozen source"
            )
    return {
        "postgres_restore_verified": True,
        "reviewed_file_restore_verified": True,
        "legacy_redis_restore_byte_count": 0,
        "non_operation_resource_delta_count": 0,
        "inventory_closure_sha256": inventory_closure_sha256,
        "restored_postgres_snapshot_set_sha256": postgres_snapshot,
        "restored_reviewed_file_snapshot_set_sha256": file_snapshot,
        "restore_result_set_sha256": completion_sha256,
    }


def _write_claim_derivation(
    context: CoordinatorContext,
    *,
    completion_path: Path,
    completion_sha256: str,
    post_consumption_path: Path,
    post_consumption_sha256: str,
    inventory_closure_path: Path,
    inventory_closure_sha256: str,
    role_validation_paths: Mapping[str, Path],
    role_validation_sha256: Mapping[str, str],
    values: Mapping[str, Any],
    now: datetime,
) -> tuple[dict[str, Path], Path, str]:
    if (
        set(values) != set(CLAIMS)
        or set(role_validation_paths) != set(ROLES)
        or set(role_validation_sha256) != set(ROLES)
    ):
        raise RestorePhaseCoordinatorError(
            "derived restore claim or role-validation set is not exact"
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise RestorePhaseCoordinatorError(
            "claim observation time must include a timezone"
        )
    observed_at = now.astimezone(timezone.utc).isoformat()
    role_validation: dict[str, dict[str, str]] = {}
    for role in ROLES:
        path = _absolute_path(
            role_validation_paths[role],
            label=f"{role} role validation",
        )
        digest = _nonzero_sha256(
            role_validation_sha256[role],
            label=f"{role} role validation",
        )
        try:
            payload = read_secure_bytes(
                path,
                label=f"{role} role validation",
                owner_uid=0,
                max_size=MAX_VALIDATION_BYTES,
            )
        except SecureFileError as exc:
            raise RestorePhaseCoordinatorError(
                f"{role} role validation is unsafe"
            ) from exc
        if _sha256(payload) != digest:
            raise RestorePhaseCoordinatorError(
                f"{role} role validation digest differs"
            )
        role_validation[role] = {
            "path": os.fspath(path),
            "sha256": digest,
        }
    claim_directory = context.coordinator_output_directory / "claims"
    claim_paths: dict[str, Path] = {}
    claim_rows: dict[str, dict[str, Any]] = {}
    for claim in CLAIMS:
        document = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": PHASE,
            "operation": OPERATION,
            "claim": claim,
            "value": values[claim],
            "observed_at": observed_at,
            "status": "observed",
        }
        path, digest, _publication = _persist_document(
            claim_directory,
            prefix=claim,
            document=document,
        )
        claim_paths[claim] = path
        claim_rows[claim] = {
            "value": values[claim],
            "source_path": os.fspath(path),
            "source_sha256": digest,
        }
    final_snapshot = context.prior_records["final_snapshot_hashes"]
    derivation = {
        "schema": DERIVATION_SCHEMA,
        "status": "derived-from-validated-frozen-restore",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_path": os.fspath(context.restore_set_path),
        "restore_set_sha256": context.restore_set_sha256,
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "post_consumption_receipt_path": os.fspath(
            post_consumption_path
        ),
        "post_consumption_receipt_sha256": (
            post_consumption_sha256
        ),
        "inventory_closure_path": os.fspath(inventory_closure_path),
        "inventory_closure_sha256": inventory_closure_sha256,
        "prior_final_snapshot_evidence_path": final_snapshot["path"],
        "prior_final_snapshot_evidence_sha256": final_snapshot[
            "file_sha256"
        ],
        "manifest_path": os.fspath(context.manifest_path),
        "evidence_output_directory": os.fspath(
            context.coordinator_output_directory / "phase-evidence"
        ),
        "role_validation": role_validation,
        "prior_phase_evidence": {
            phase: os.fspath(context.prior_paths[phase])
            for phase in CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(PHASE)
            ]
        },
        "claims": {
            claim: claim_rows[claim] for claim in sorted(claim_rows)
        },
        "caller_claim_sources_accepted": False,
        "observed_at": observed_at,
    }
    if (
        set(derivation) != DERIVATION_FIELDS
        or any(
            set(row) != DERIVED_CLAIM_FIELDS
            for row in derivation["claims"].values()
        )
        or any(
            set(row) != DERIVED_ROLE_VALIDATION_FIELDS
            for row in derivation["role_validation"].values()
        )
    ):
        raise RestorePhaseCoordinatorError(
            "restore claim derivation fields are not exact"
        )
    path, digest, _publication = _persist_document(
        context.coordinator_output_directory / "derivations",
        prefix="claim-derivation",
        document=derivation,
    )
    observed, observed_sha256 = _secure_json(
        path,
        label="restore claim derivation",
    )
    if observed != derivation or observed_sha256 != digest:
        raise RestorePhaseCoordinatorError(
            "restore claim derivation readback differs"
        )
    return claim_paths, path, digest


def publish_derived_evidence(
    publication: EvidencePublication,
) -> dict[str, Any]:
    """Trusted adapter from one derivation receipt to the narrow producer."""
    try:
        planned = PRODUCER.execute_derived(
            derivation_path=publication.derivation_path,
            derivation_sha256=publication.derivation_sha256,
        )
        if (
            planned.get("status") != "planned"
            or planned.get("derivation_path")
            != os.fspath(publication.derivation_path)
            or planned.get("derivation_sha256")
            != publication.derivation_sha256
            or planned.get("journal_mutated") is not False
            or planned.get("production_contacted") is not False
        ):
            raise RestorePhaseCoordinatorError(
                "phase evidence producer plan differs"
            )
        result = PRODUCER.execute_derived(
            derivation_path=publication.derivation_path,
            derivation_sha256=publication.derivation_sha256,
            apply=True,
            confirm=planned["required_confirmation"],
        )
    except PRODUCER.FrozenFinalRestorePhaseEvidenceError as exc:
        raise RestorePhaseCoordinatorError(
            "derived restore evidence publication failed"
        ) from exc
    if (
        result.get("status") != "published"
        or result.get("self_verification_status") != "verified"
        or result.get("journal_mutated") is not False
        or result.get("production_contacted") is not False
    ):
        raise RestorePhaseCoordinatorError(
            "derived restore evidence publication result differs"
        )
    return dict(result)


def _canonical_published_evidence_path(
    context: CoordinatorContext,
    *,
    path: Path,
    evidence_sha256: str,
) -> Path:
    path = _absolute_path(path, label="published shadow_restore evidence")
    evidence_sha256 = _nonzero_sha256(
        evidence_sha256,
        label="published shadow_restore evidence",
    )
    expected = (
        context.coordinator_output_directory
        / "phase-evidence"
        / f"{PHASE}.{evidence_sha256}.json"
    )
    if path != expected:
        raise RestorePhaseCoordinatorError(
            "published shadow_restore evidence path is not canonical"
        )
    return path


def _verify_runtime_authorization(
    context: CoordinatorContext,
    *,
    approval_path: Path,
    approval_policy_path: Path,
) -> tuple[Path, Path]:
    approval_path = _absolute_path(
        approval_path,
        label="production cutover approval",
    )
    approval_policy_path = _absolute_path(
        approval_policy_path,
        label="production cutover approval policy",
    )
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            context.manifest,
            approval_path=approval_path,
            approval_policy_path=approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise RestorePhaseCoordinatorError(
            "production cutover authorization is invalid or expired"
        ) from exc
    return approval_path, approval_policy_path


def _invoke_controller_transition(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    approval_path: Path,
    approval_policy_path: Path,
    action: str,
    evidence_path: Path | None = None,
    role_validation: Mapping[str, Path] | None = None,
    claim_source: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    if action not in {"begin-phase", "complete-phase"}:
        raise RestorePhaseCoordinatorError(
            "controller transition action is invalid"
        )
    approval_path, approval_policy_path = _verify_runtime_authorization(
        context,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
    )
    transition = ControllerTransition(
        action=action,
        phase=PHASE,
        manifest_path=context.manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        evidence_path=evidence_path,
        role_validation=tuple(
            f"{role}={role_validation[role]}"
            for role in ROLES
        )
        if role_validation is not None
        else (),
        claim_source=tuple(
            f"{claim}={claim_source[claim]}"
            for claim in CLAIMS
        )
        if claim_source is not None
        else (),
        prior_phase_evidence=tuple(
            f"{phase}={context.prior_paths[phase]}"
            for phase in CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(PHASE)
            ]
        )
        if action == "complete-phase"
        else (),
    )
    if action == "complete-phase" and (
        evidence_path is None
        or role_validation is None
        or claim_source is None
    ):
        raise RestorePhaseCoordinatorError(
            "complete-phase transition inputs are incomplete"
        )
    try:
        result = callback(transition)
    except Exception as exc:
        raise RestorePhaseCoordinatorError(
            f"public controller {action} transition failed"
        ) from exc
    if (
        not isinstance(result, Mapping)
        or result.get("action") != action
        or result.get("production_contacted") is not False
        or not isinstance(result.get("journal"), Mapping)
    ):
        raise RestorePhaseCoordinatorError(
            f"public controller {action} result differs"
        )
    return dict(result)


def _begin_phase(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    approval_path: Path,
    approval_policy_path: Path,
) -> None:
    _verify_runtime_authorization(
        context,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
    )
    journal = _read_cutover_journal(
        context.manifest,
        manifest_sha256=context.manifest_sha256,
        plan_sha256=context.plan_sha256,
        allow_started=True,
    )
    if journal["status"] == "phase_started":
        return
    _invoke_controller_transition(
        context,
        callback=callback,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        action="begin-phase",
    )
    after = _read_cutover_journal(
        context.manifest,
        manifest_sha256=context.manifest_sha256,
        plan_sha256=context.plan_sha256,
        allow_started=True,
    )
    if (
        after["status"] != "phase_started"
        or after["started_phase"] != PHASE
    ):
        raise RestorePhaseCoordinatorError(
            "public controller did not durably start shadow_restore"
        )


def _validate_phase_verification_receipt(
    context: CoordinatorContext,
    *,
    journal: Mapping[str, Any],
    evidence_sha256: str,
) -> Path:
    receipt_sha256 = _nonzero_sha256(
        journal["phase_verification_sha256"].get(PHASE),
        label="shadow_restore phase verification receipt",
    )
    path = (
        Path(context.manifest["deployment"]["controller_evidence_root"])
        / "verification"
        / f"{PHASE}.{receipt_sha256}.json"
    )
    try:
        payload = read_secure_bytes(
            path,
            label="shadow_restore phase verification receipt",
            owner_uid=0,
            max_size=64 * 1024,
        )
        if _sha256(payload) != receipt_sha256:
            raise RestorePhaseCoordinatorError(
                "phase verification receipt digest differs"
            )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        token, expected_payload = (
            CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
                document,
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                plan_sha256=context.plan_sha256,
            )
        )
    except RestorePhaseCoordinatorError:
        raise
    except (
        SecureFileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        CONTROLLER.CutoverContractError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            "phase verification receipt is invalid or unsafe"
        ) from exc
    if (
        payload != expected_payload
        or token.phase != PHASE
        or token.evidence_sha256 != evidence_sha256
        or token.receipt_sha256 != receipt_sha256
    ):
        raise RestorePhaseCoordinatorError(
            "phase verification receipt differs from journal evidence"
        )
    return path


def _validate_completed_phase_readback(
    context: CoordinatorContext,
    *,
    journal: Mapping[str, Any],
) -> tuple[Path, str]:
    try:
        evidence_sha256 = _nonzero_sha256(
            journal["phase_evidence_sha256"][PHASE],
            label="completed shadow_restore evidence",
        )
    except KeyError as exc:
        raise RestorePhaseCoordinatorError(
            "completed shadow_restore evidence is absent"
        ) from exc
    evidence_path = (
        context.coordinator_output_directory
        / "phase-evidence"
        / f"{PHASE}.{evidence_sha256}.json"
    )
    try:
        evidence, observed_sha256 = VERIFY.read_root_only_evidence(
            evidence_path
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise RestorePhaseCoordinatorError(
            "completed shadow_restore evidence is invalid or unsafe"
        ) from exc
    expected = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase": PHASE,
        "operation": OPERATION,
        "status": "passed",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
    }
    if (
        observed_sha256 != evidence_sha256
        or set(evidence) != VERIFY.EVIDENCE_FIELDS
        or any(evidence.get(field) != value for field, value in expected.items())
    ):
        raise RestorePhaseCoordinatorError(
            "completed shadow_restore evidence readback differs"
        )
    _validate_phase_verification_receipt(
        context,
        journal=journal,
        evidence_sha256=evidence_sha256,
    )
    return evidence_path, evidence_sha256


def _complete_phase(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    approval_path: Path,
    approval_policy_path: Path,
    evidence_path: Path,
    evidence_sha256: str,
    role_validation: Mapping[str, Path],
    claim_source: Mapping[str, Path],
) -> dict[str, Any]:
    before = _read_cutover_journal(
        context.manifest,
        manifest_sha256=context.manifest_sha256,
        plan_sha256=context.plan_sha256,
        allow_started=True,
        allow_completed=True,
    )
    if PHASE in before["completed_phases"]:
        if before["phase_evidence_sha256"][PHASE] != evidence_sha256:
            raise RestorePhaseCoordinatorError(
                "completed shadow_restore evidence differs"
            )
        _validate_phase_verification_receipt(
            context,
            journal=before,
            evidence_sha256=evidence_sha256,
        )
        return before
    _invoke_controller_transition(
        context,
        callback=callback,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        action="complete-phase",
        evidence_path=evidence_path,
        role_validation=role_validation,
        claim_source=claim_source,
    )
    after = _read_cutover_journal(
        context.manifest,
        manifest_sha256=context.manifest_sha256,
        plan_sha256=context.plan_sha256,
        allow_started=True,
        allow_completed=True,
    )
    if (
        PHASE not in after["completed_phases"]
        or after["phase_evidence_sha256"][PHASE] != evidence_sha256
        or PHASE not in after["phase_verification_sha256"]
    ):
        raise RestorePhaseCoordinatorError(
            "public controller did not persist exact phase verification"
        )
    _validate_phase_verification_receipt(
        context,
        journal=after,
        evidence_sha256=evidence_sha256,
    )
    return after


def _validate_inventory_agent_release_binding(
    context: CoordinatorContext,
    inventory_agent_sha256: str,
) -> str:
    digest = _nonzero_sha256(
        inventory_agent_sha256,
        label="global Docker inventory agent",
    )
    observed = _derive_inventory_agent_release_sha256(context)
    if observed != digest:
        raise RestorePhaseCoordinatorError(
            "global Docker inventory agent differs from exact release"
        )
    return digest


def _validate_live_lease_nginx_binding(
    nginx_inputs: NGINX.CoordinatorInputs,
    lease: Any,
) -> None:
    try:
        claim_path = _absolute_path(
            lease.claim_path,
            label="Nginx live lease claim",
        )
        claim_sha256 = _nonzero_sha256(
            lease.claim_sha256,
            label="Nginx live lease claim",
        )
        expected_claim = lease.claim
        loaded_claim, _receipt = NGINX._load_claim_from_controller(  # noqa: SLF001
            nginx_inputs,
            claim_path,
            claim_sha256,
        )
    except (
        AttributeError,
        TypeError,
        ValueError,
        NGINX.NginxCoordinatorError,
    ) as exc:
        raise RestorePhaseCoordinatorError(
            "Nginx live lease differs from validated coordinator inputs"
        ) from exc
    if (
        not isinstance(expected_claim, Mapping)
        or loaded_claim != expected_claim
    ):
        raise RestorePhaseCoordinatorError(
            "Nginx live lease differs from validated coordinator inputs"
        )


def apply_restore_phase(
    *,
    manifest_path: Path,
    restore_set_path: Path,
    requests: Mapping[str, Mapping[str, Any]],
    prior_phase_evidence: Mapping[str, Path],
    approval_path: Path,
    approval_policy_path: Path,
    nginx_inputs: NGINX.CoordinatorInputs,
    lease: Any | None,
    prepare_restore_request: Callable[
        [str, Mapping[str, Any], Any],
        Mapping[str, Any],
    ],
    invoke_restore_host: Callable[
        [
            Mapping[str, Any],
            Callable[[Mapping[str, Any]], Mapping[str, Any]],
        ],
        Mapping[str, Any],
    ],
    inventory_invoke: InventoryInvoker | None = None,
    inventory_agent_sha256: str,
    ssh_identity: Path,
    known_hosts: Path,
    validation_runner: ValidationRunner | None = None,
    controller_callback: ControllerCallback,
    evidence_publisher: EvidencePublisher,
    confirm: str,
    now: datetime | None = None,
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute the phase through bounded defaults or typed injected runners."""
    required_callbacks = (
        prepare_restore_request,
        invoke_restore_host,
        controller_callback,
        evidence_publisher,
    )
    if any(not callable(callback) for callback in required_callbacks):
        raise RestorePhaseCoordinatorError(
            "live restore phase requires every exact callback"
        )
    if inventory_invoke is None:
        inventory_invoke = run_bounded_process
    if validation_runner is None:
        validation_runner = run_bounded_process
    if not callable(inventory_invoke) or not callable(validation_runner):
        raise RestorePhaseCoordinatorError(
            "live restore phase bounded runner is invalid"
        )
    context = _load_context(
        manifest_path=manifest_path,
        restore_set_path=restore_set_path,
        requests=requests,
        prior_phase_evidence=prior_phase_evidence,
        allow_started=True,
        allow_completed=True,
    )
    plan = _plan_document(context)
    supplied_inventory_agent_sha256 = _nonzero_sha256(
        inventory_agent_sha256,
        label="global Docker inventory agent",
    )
    if (
        plan.get("inventory_agent_sha256")
        != supplied_inventory_agent_sha256
    ):
        raise RestorePhaseCoordinatorError(
            "global Docker inventory agent differs from the confirmed plan"
        )
    if confirm != plan["required_confirmation"]:
        raise RestorePhaseCoordinatorError(
            "live restore phase requires exact digest-bound confirmation"
        )
    if PHASE in context.journal["completed_phases"]:
        _validate_completed_phase_readback(
            context,
            journal=context.journal,
        )
        return {
            "schema": PUBLICATION_SCHEMA,
            "status": "already-complete",
            "phase": PHASE,
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "phase_evidence_sha256": context.journal[
                "phase_evidence_sha256"
            ][PHASE],
            "phase_verification_sha256": context.journal[
                "phase_verification_sha256"
            ][PHASE],
            "second_restore_performed": False,
            "second_lease_consume_performed": False,
        }
    ssh_trust = _bind_inventory_ssh_trust(
        context,
        nginx_inputs=nginx_inputs,
        ssh_identity=ssh_identity,
        known_hosts=known_hosts,
    )
    inventory_agent_sha256 = _validate_inventory_agent_release_binding(
        context,
        supplied_inventory_agent_sha256,
    )
    preflight_baseline = _load_existing_baseline(
        context,
        inventory_agent_sha256=inventory_agent_sha256,
    )
    restore_journal_path = (
        context.restore_output_directory / "controller-journal.json"
    )
    try:
        os.lstat(restore_journal_path)
    except FileNotFoundError:
        preflight_restore_journal_exists = False
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "restore journal state cannot be inspected"
        ) from exc
    else:
        preflight_restore_journal_exists = True
    if (
        preflight_baseline is None
        and preflight_restore_journal_exists
    ):
        raise RestorePhaseCoordinatorError(
            "restore journal exists without a complete pre-restore baseline"
        )
    baseline_exists = preflight_baseline is not None
    if not baseline_exists and lease is None:
        raise RestorePhaseCoordinatorError(
            "first inventory baseline requires the exact held live lease"
        )
    if lease is not None:
        try:
            RESTORE._validate_exact_controller_live_lease(  # noqa: SLF001
                lease,
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                release_tree_sha=context.manifest["release_tree_sha"],
            )
            lease.verify()
        except Exception as exc:
            raise RestorePhaseCoordinatorError(
                "exact Nginx live lease is not held for inventory baseline"
            ) from exc
        _validate_live_lease_nginx_binding(nginx_inputs, lease)
    _begin_phase(
        context,
        callback=controller_callback,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
    )
    _ensure_private_directory(context.coordinator_output_directory)
    if preflight_baseline is None:
        baseline, baseline_path, baseline_sha256 = (
            _persist_or_load_baseline(
                context,
                inventory_invoke=inventory_invoke,
                inventory_agent_sha256=inventory_agent_sha256,
                ssh_trust=ssh_trust,
            )
        )
    else:
        baseline, baseline_path, baseline_sha256 = preflight_baseline
    if lease is not None:
        lease.verify()
    callback = checkpoint or (lambda _name: None)
    inventory_result: tuple[dict[str, Any], Path, str] | None = None

    def restore_checkpoint(name: str) -> None:
        nonlocal inventory_result
        if name == "after-completion-before-consume":
            (
                completion_at_boundary,
                completion_path_at_boundary,
                completion_sha256_at_boundary,
                _prepared,
                _store,
            ) = _load_validated_restore_closure(context)
            # An unconsumed resume must observe Docker again. The immutable
            # reference may only prove consumed recovery; it cannot authorize
            # a later lease consumption after the host state has changed.
            inventory_result = _inventory_closure(
                context,
                baseline=baseline,
                baseline_path=baseline_path,
                baseline_sha256=baseline_sha256,
                completion=completion_at_boundary,
                completion_path=completion_path_at_boundary,
                completion_sha256=completion_sha256_at_boundary,
                inventory_invoke=inventory_invoke,
                inventory_agent_sha256=inventory_agent_sha256,
                ssh_trust=ssh_trust,
            )
            if (
                inventory_result[0][
                    "non_operation_resource_delta_count"
                ]
                != 0
            ):
                raise RestorePhaseCoordinatorError(
                    "post-restore inventory delta is nonzero"
                )
        callback(name)

    restore_result: dict[str, Any] | None = None
    try:
        os.lstat(restore_journal_path)
    except FileNotFoundError:
        restore_journal_exists = False
    except OSError as exc:
        raise RestorePhaseCoordinatorError(
            "frozen restore journal cannot be inspected"
        ) from exc
    else:
        restore_journal_exists = True
    if restore_journal_exists:
        def recovery_checkpoint(name: str) -> None:
            nonlocal inventory_result
            if name == "after-recovered-post-consumption-receipt":
                (
                    recovered_completion,
                    recovered_completion_path,
                    recovered_completion_sha256,
                    _recovered_prepared,
                    _recovered_store,
                ) = _load_validated_restore_closure(context)
                inventory_result = _load_inventory_closure_reference(
                    context,
                    completion=recovered_completion,
                    completion_path=recovered_completion_path,
                    completion_sha256=recovered_completion_sha256,
                )
            callback(name)

        try:
            recovered = RESTORE.recover_consumed_controller_operation(
                inputs=nginx_inputs,
                output_directory=context.restore_output_directory,
                requests=context.requests,
                checkpoint=recovery_checkpoint,
            )
        except RESTORE.ConsumptionAuditAbsent:
            recovered = None
        except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
            raise RestorePhaseCoordinatorError(
                "consumed frozen restore recovery failed"
            ) from exc
        if recovered is not None:
            restore_result = dict(recovered)
            if inventory_result is None:
                raise RestorePhaseCoordinatorError(
                    "consumed recovery did not verify its locked "
                    "pre-consume inventory closure"
                )
    if restore_result is None:
        if lease is None:
            raise RestorePhaseCoordinatorError(
                "unconsumed frozen restore requires its exact live lease"
            )

        def exact_consumption_readback(
            claimed_path: Path | None,
            claimed_sha256: str | None,
            completion_value: Mapping[str, Any],
            completion_digest: str,
        ) -> tuple[Path, str, Mapping[str, Any]]:
            try:
                return RESTORE.coordinator_consumption_readback(
                    nginx_inputs,
                    claim_path=Path(lease.claim_path),
                    claim_sha256=lease.claim_sha256,
                    claimed_path=claimed_path,
                    claimed_sha256=claimed_sha256,
                    completion=completion_value,
                    completion_sha256=completion_digest,
                )
            except (
                AttributeError,
                RESTORE.FrozenFinalRestoreOrchestratorError,
            ) as exc:
                raise RestorePhaseCoordinatorError(
                    "exact coordinator consumption readback failed"
                ) from exc
        try:
            restore_result = RESTORE.run_three_roles_under_lease(
                lease=lease,
                requests=context.requests,
                prepare_request=prepare_restore_request,
                invoke=invoke_restore_host,
                output_directory=context.restore_output_directory,
                consumption_readback=exact_consumption_readback,
                checkpoint=restore_checkpoint,
            )
        except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
            raise RestorePhaseCoordinatorError(
                "three-role frozen restore failed closed"
            ) from exc
    (
        completion,
        completion_path,
        completion_sha256,
        prepared,
        store,
    ) = _load_validated_restore_closure(context)
    if inventory_result is None:
        raise RestorePhaseCoordinatorError(
            "restore did not persist or verify its pre-consume "
            "inventory closure"
        )
    inventory_closure, inventory_path, inventory_sha256 = inventory_result
    _post, post_path, post_sha256 = _post_consumption_receipt(
        completion=completion,
        completion_path=completion_path,
        completion_sha256=completion_sha256,
        prepared_requests=prepared,
        nginx_inputs=nginx_inputs,
        store=store,
    )
    role_paths, role_digests = _run_role_validations(
        context,
        runner=validation_runner,
    )
    values = _derive_claim_values(
        context,
        completion=completion,
        completion_sha256=completion_sha256,
        inventory_closure=inventory_closure,
        inventory_closure_sha256=inventory_sha256,
    )
    observed_now = now or datetime.now(timezone.utc)
    claim_paths, derivation_path, derivation_sha256 = (
        _write_claim_derivation(
            context,
            completion_path=completion_path,
            completion_sha256=completion_sha256,
            post_consumption_path=post_path,
            post_consumption_sha256=post_sha256,
            inventory_closure_path=inventory_path,
            inventory_closure_sha256=inventory_sha256,
            role_validation_paths=role_paths,
            role_validation_sha256=role_digests,
            values=values,
            now=observed_now,
        )
    )
    try:
        publication = evidence_publisher(
            EvidencePublication(
                derivation_path=derivation_path,
                derivation_sha256=derivation_sha256,
            )
        )
    except Exception as exc:
        raise RestorePhaseCoordinatorError(
            "coordinator-derived evidence publisher failed"
        ) from exc
    if (
        not isinstance(publication, Mapping)
        or publication.get("status") != "published"
        or publication.get("self_verification_status") != "verified"
        or publication.get("journal_mutated") is not False
        or publication.get("production_contacted") is not False
    ):
        raise RestorePhaseCoordinatorError(
            "coordinator-derived evidence publication differs"
        )
    evidence_sha256 = _nonzero_sha256(
        publication.get("evidence_sha256"),
        label="published shadow_restore evidence",
    )
    evidence_path = _canonical_published_evidence_path(
        context,
        path=publication.get("output"),
        evidence_sha256=evidence_sha256,
    )
    try:
        evidence, observed_evidence_sha256 = (
            VERIFY.read_root_only_evidence(evidence_path)
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise RestorePhaseCoordinatorError(
            "published shadow_restore evidence is unsafe"
        ) from exc
    derivation, observed_derivation_sha256 = _secure_json(
        derivation_path,
        label="restore claim derivation final readback",
    )
    if (
        observed_evidence_sha256 != evidence_sha256
        or observed_derivation_sha256 != derivation_sha256
        or evidence.get("phase") != PHASE
        or evidence.get("operation_id")
        != context.manifest["operation_id"]
        or evidence["claims"]["restore_result_set_sha256"]["value"]
        != completion_sha256
        or any(
            evidence["claims"][claim]["source_sha256"]
            != derivation["claims"][claim]["source_sha256"]
            for claim in CLAIMS
        )
    ):
        raise RestorePhaseCoordinatorError(
            "published shadow_restore evidence differs from derivation"
        )
    final_journal = _complete_phase(
        context,
        callback=controller_callback,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        evidence_path=evidence_path,
        evidence_sha256=evidence_sha256,
        role_validation=role_paths,
        claim_source=claim_paths,
    )
    return {
        "schema": PUBLICATION_SCHEMA,
        "status": "complete",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "restore_set_sha256": context.restore_set_sha256,
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "post_consumption_receipt_path": os.fspath(post_path),
        "post_consumption_receipt_sha256": post_sha256,
        "inventory_closure_path": os.fspath(inventory_path),
        "inventory_closure_sha256": inventory_sha256,
        "claim_derivation_path": os.fspath(derivation_path),
        "claim_derivation_sha256": derivation_sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "phase_verification_sha256": final_journal[
            "phase_verification_sha256"
        ][PHASE],
        "non_operation_resource_delta_count": 0,
        "caller_claim_sources_accepted": False,
        "webapp_ir_payload_bytes_over_ssh": 0,
        "second_lease_consume_performed": restore_result.get(
            "second_consume_performed",
            False,
        ),
    }


def _load_request_mapping(values: Sequence[str]) -> dict[str, dict[str, Any]]:
    paths = _parse_path_mapping(
        values,
        expected=ROLES,
        label="restore request",
    )
    return {
        role: _secure_json(
            paths[role],
            label=f"{role} restore request",
        )[0]
        for role in ROLES
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--restore-set", type=Path, required=True)
    parser.add_argument(
        "--restore-request",
        action="append",
        default=[],
        metavar="ROLE=/ABS/PATH",
    )
    parser.add_argument(
        "--prior-phase-evidence",
        action="append",
        default=[],
        metavar="PHASE=/ABS/PATH",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        requests = _load_request_mapping(args.restore_request)
        prior = _parse_path_mapping(
            args.prior_phase_evidence,
            expected=CONTROLLER.PHASES[
                : CONTROLLER.PHASES.index(PHASE)
            ],
            label="prior phase evidence",
        )
        result = plan_restore_phase(
            manifest_path=args.manifest,
            restore_set_path=args.restore_set,
            requests=requests,
            prior_phase_evidence=prior,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        RestorePhaseCoordinatorError,
        CONTROLLER.CutoverContractError,
        RESTORE.FrozenFinalRestoreOrchestratorError,
        WORKER.FrozenFinalRestoreWorkerError,
        INVENTORY.GlobalDockerInventoryError,
        PRODUCER.FrozenFinalRestorePhaseEvidenceError,
        VERIFY.PhaseEvidenceError,
        SecureFileError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "plan_only": True,
                    "journal_mutated": False,
                    "production_contacted": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
