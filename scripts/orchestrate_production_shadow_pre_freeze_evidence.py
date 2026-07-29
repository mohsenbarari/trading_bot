#!/usr/bin/env python3
"""Close the production-shadow pre-freeze evidence phase from local proofs.

This coordinator is deliberately not a collector.  It accepts only already
published, root-private evidence, verifies the complete four-role and
release-bound closure, derives the narrow records consumed by the immutable
phase verifier, and asks the public controller to begin and complete exactly
``pre_freeze_evidence``.  It never contacts a host, Docker, Object Storage, or
any production service.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import sys
import threading
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import assemble_production_shadow_stage_bindings as STAGE_BINDINGS  # noqa: E402
from scripts import attest_production_shadow_legacy_rollback as ROLLBACK  # noqa: E402
from scripts import build_production_shadow_cutover_manifest_template as TEMPLATE  # noqa: E402
from scripts import orchestrate_production_shadow_finland_artifacts as FINLAND  # noqa: E402
from scripts import orchestrate_production_shadow_nginx_generations as NGINX  # noqa: E402
from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED_INVENTORY  # noqa: E402
from scripts import orchestrate_wa_ir_production_artifacts as WA_ORCHESTRATOR  # noqa: E402
from scripts import produce_production_shadow_prepare_material as PREPARE  # noqa: E402
from scripts import production_shadow_convergence_runtime_targets as runtime_targets  # noqa: E402
from scripts import produce_production_shadow_witness_public_stage as WITNESS  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY  # noqa: E402
from scripts import production_shadow_host_agent as HOST_AGENT  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402
from scripts import wa_ir_production_operation as WA_OPERATION  # noqa: E402


PHASE = "pre_freeze_evidence"
OPERATION = "capture-pre-freeze-evidence"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
DOCKER_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
ROLLBACK_ROLES = ("bot_fi", "webapp_fi")
ZERO_SHA256 = "0" * 64
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024 * 1024
MAX_CONTROLLER_STDOUT_BYTES = 4 * 1024 * 1024
MAX_CONTROLLER_STDERR_BYTES = 128 * 1024
CONTROLLER_TIMEOUT_SECONDS = 180.0
SOURCE_MAX_AGE = VERIFY.MAX_EVIDENCE_AGE
ROLE_MAX_SKEW = VERIFY.MAX_ROLE_CAPTURE_SKEW
FUTURE_SKEW = VERIFY.MAX_FUTURE_SKEW

CLAIMS = tuple(VERIFY.PHASE_CLAIM_RULES[PHASE])
MANIFEST_BOUND_CLAIMS = dict(
    VERIFY.PHASE_MANIFEST_CLAIM_BINDINGS[PHASE]
)

INPUT_CLOSURE_SCHEMA = (
    "production-shadow-pre-freeze-input-closure-v1"
)
DERIVATION_SCHEMA = "production-shadow-pre-freeze-derivation-v1"
RESULT_SCHEMA = "production-shadow-pre-freeze-coordination-v1"
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"

COLLECTION_DEPENDENCIES = (
    "sealed release closure and its Git/image artifacts",
    "prepare-material metadata, four role archives, and canonical Compose",
    "Finland two-role stage evidence",
    "WA-IR operation manifest, stage attestation, and stage binding",
    "Witness health, public input, stage operation, and stage binding",
    "assembled exact four-role stage bindings",
    "fresh challenge-bound capture-after prepared-clone inventory closure for three Docker roles",
    "fresh challenge-bound legacy-normal two-host Nginx external-readback receipt",
    "Bot-FI and WebApp-FI sealed legacy rollback attestations",
)

INPUT_CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "captured_at",
        "role_observed_at",
        "source_files",
        "role_source_closure_sha256",
        "claim_provenance",
        "upstream_inventory_collection_performed",
        "upstream_inventory_production_contacted",
        "collection_performed",
        "production_contacted",
    }
)
SOURCE_FILE_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "bytes",
        "device",
        "inode",
        "mode",
        "uid",
        "gid",
        "nlink",
        "mtime_ns",
        "ctime_ns",
    }
)
DERIVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "plan_sha256",
        "input_closure_path",
        "input_closure_sha256",
        "role_validation",
        "claim_sources",
        "phase_evidence_path",
        "phase_evidence_sha256",
        "caller_truth_values_accepted",
        "upstream_inventory_collection_performed",
        "upstream_inventory_production_contacted",
        "collection_performed",
        "production_contacted",
    }
)
NORMALIZED_REFERENCE_FIELDS = frozenset({"path", "sha256"})
FINLAND_EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "closure_sha256",
        "agent_sha256",
        "roles",
        "binding_summaries",
        "stage_bindings",
        "pull_policy",
        "object_storage_used",
        "arvan_endpoint_contacted",
        "containers_created",
        "containers_started",
        "services_started",
        "networks_created",
        "volumes_created",
        "current_mutated",
        "data_mutated",
    }
)
FINLAND_ROLE_FIELDS = frozenset(
    {
        "host",
        "transport",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
        "stage_attestation_path",
        "runtime_image_ids",
    }
)
PREPARE_SET_FIELDS = frozenset(
    {
        "schema",
        "capabilities",
        "operation_id",
        "release_sha",
        "canonical_compose_sha256",
        "dr_ca_sha256",
        "dr_tls_attestation_sha256",
        "dr_tls_attested_at_epoch",
        "roles",
        "controller_bindings",
        "activation_secrets_included",
        "precommit_manifest_bound",
    }
)
PREPARE_ROLE_FIELDS = frozenset(
    {
        "filename",
        "sha256",
        "bytes",
        "format",
        "transport",
        "internal_manifest_sha256",
        "stage_operation_manifest_sha256",
        "stage_attestation_sha256",
    }
)
ROLLBACK_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "rollback_closure_sha256",
        "legacy_redis_rollback_sha256",
        "sha256sums_sha256",
        "backup_manifest_sha256",
        "backup_artifact_set_sha256",
        "backup_stamp",
        "database_restore_smoke_passed",
        "database_restore_smoke_table_count",
        "sealed_file_count",
        "backup_artifact_count",
        "source_mutated",
        "production_contacted",
    }
)
NGINX_AGGREGATE_FIELDS = TEMPLATE.NGINX_AGGREGATE_FIELDS
NGINX_AGGREGATE_ROLE_FIELDS = TEMPLATE.NGINX_AGGREGATE_ROLE_FIELDS
PREPARED_RECEIPT_LOAD_FIELDS = frozenset(
    {
        "schema",
        "status",
        "receipt",
        "requests",
        "responses",
        "artifacts",
        "aggregate",
        "artifact_count",
        "readback_verified",
    }
)
PREPARED_SOURCE_REFERENCE_FIELDS = frozenset(
    {"filename", "path", "sha256", "bytes"}
)
CANONICAL_DOCKER_TOPOLOGY = {
    "bot_fi": {
        "transport": "local-controller",
        "ssh_user": None,
        "ssh_port": None,
    },
    "webapp_fi": {
        "transport": "ssh-control",
        "ssh_user": "root",
        "ssh_port": 37067,
    },
    "webapp_ir": {
        "transport": "ssh-control-object-storage-payload-only",
        "ssh_user": "root",
        "ssh_port": 22,
    },
}


class PreFreezeEvidenceError(RuntimeError):
    """The pre-freeze phase cannot be closed from the supplied evidence."""


class LiveControllerAuthorityLost(PreFreezeEvidenceError):
    """The caller lost authority while a public journal transition ran."""


_SIGNAL_GUARD_ACTIVE = False
_SIGNAL_SEEN = False
_SIGNAL_DEFER_DEPTH = 0
_DEFERRED_SIGNAL: str | None = None


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SecureRecord:
    path: Path
    payload: bytes
    sha256: str
    identity: FileIdentity
    document: dict[str, Any] | None = None
    canonical_required: bool = True


@dataclass(frozen=True)
class EvidencePaths:
    release_closure: Path
    prepare_metadata: Path
    canonical_compose: Path
    finland_evidence: Path
    wa_ir_operation_manifest: Path
    wa_ir_stage_attestation: Path
    wa_ir_stage_binding: Path
    witness_health: Path
    witness_public_input: Path
    witness_stage_operation: Path
    witness_stage_binding: Path
    stage_bindings: Path
    nginx_aggregate: Path
    nginx_legacy_normal_receipt: Path
    inventory_receipt: Path
    inventory_output_root: Path
    rollback_attestations: Mapping[str, Path]


@dataclass(frozen=True)
class ControllerTransition:
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
class CoordinatorContext:
    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    plan_sha256: str
    output_root: Path
    journal: dict[str, Any]


@dataclass(frozen=True)
class ValidatedInputs:
    context: CoordinatorContext
    records: Mapping[str, SecureRecord]
    values: Mapping[str, Any]
    role_observed_at: Mapping[str, str]
    role_source_closure_sha256: Mapping[str, str]
    claim_provenance: Mapping[str, tuple[str, ...]]


class ControllerCallback(Protocol):
    def __call__(
        self,
        transition: ControllerTransition,
    ) -> Mapping[str, Any]:
        """Apply one exact local controller journal transition."""


AuthorityCheck = Callable[[str], None]


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
        raise PreFreezeEvidenceError(
            "value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreFreezeEvidenceError(
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
        raise PreFreezeEvidenceError(f"{label} is not a nonzero SHA-256")
    return value


def _image_id(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.IMAGE_ID_RE.fullmatch(value) is None
        or value == "sha256:" + ZERO_SHA256
    ):
        raise PreFreezeEvidenceError(f"{label} is not an immutable image ID")
    return value


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise PreFreezeEvidenceError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=stat.S_IMODE(metadata.st_mode),
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        nlink=metadata.st_nlink,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def _read_stable_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    allowed_modes: frozenset[int],
    private_parent: bool,
    parse_json: bool,
    canonical_required: bool = True,
) -> SecureRecord:
    path = _absolute_path(path, label=label)
    descriptor = -1
    try:
        if private_parent:
            parent = path.parent
            parent_metadata = parent.stat(follow_symlinks=False)
            if (
                parent.resolve(strict=True) != parent
                or not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != 0
                or parent_metadata.st_gid != 0
                or stat.S_IMODE(parent_metadata.st_mode) != 0o700
            ):
                raise PreFreezeEvidenceError(
                    f"{label} parent is not root-private mode 0700"
                )
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        before_identity = _identity(before)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or before_identity.mode not in allowed_modes
            or not 1 <= before.st_size <= maximum
        ):
            raise PreFreezeEvidenceError(
                f"{label} ownership, mode, link count, or size is unsafe"
            )
        chunks: list[bytes] = []
        size = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise PreFreezeEvidenceError(f"{label} is oversized")
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _identity(after) != before_identity:
            raise PreFreezeEvidenceError(f"{label} changed while being read")
        payload = b"".join(chunks)
        document: dict[str, Any] | None = None
        if parse_json:
            try:
                parsed = json.loads(
                    payload.decode("ascii"),
                    object_pairs_hook=_strict_object,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        ValueError(f"invalid constant {token}")
                    ),
                )
            except PreFreezeEvidenceError:
                raise
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise PreFreezeEvidenceError(
                    f"{label} is not strict JSON"
                ) from exc
            if not isinstance(parsed, dict):
                raise PreFreezeEvidenceError(
                    f"{label} JSON root is not an object"
                )
            canonical = _canonical_json(parsed)
            if (
                canonical_required
                and payload not in {canonical, canonical + b"\n"}
            ):
                raise PreFreezeEvidenceError(
                    f"{label} is not canonical JSON"
                )
            document = parsed
        return SecureRecord(
            path=path,
            payload=payload,
            sha256=digest.hexdigest(),
            identity=before_identity,
            document=document,
            canonical_required=canonical_required,
        )
    except PreFreezeEvidenceError:
        raise
    except OSError as exc:
        raise PreFreezeEvidenceError(
            f"{label} is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_json(path: Path, *, label: str) -> SecureRecord:
    return _read_stable_file(
        path,
        label=label,
        maximum=MAX_JSON_BYTES,
        allowed_modes=frozenset({0o600}),
        private_parent=True,
        parse_json=True,
    )


def _read_private_strict_json(
    path: Path,
    *,
    label: str,
) -> SecureRecord:
    return _read_stable_file(
        path,
        label=label,
        maximum=MAX_JSON_BYTES,
        allowed_modes=frozenset({0o600}),
        private_parent=True,
        parse_json=True,
        canonical_required=False,
    )


def _read_private_artifact(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_ARTIFACT_BYTES,
) -> SecureRecord:
    return _read_stable_file(
        path,
        label=label,
        maximum=maximum,
        allowed_modes=frozenset({0o600}),
        private_parent=True,
        parse_json=False,
    )


def _read_release_file(path: Path, *, label: str) -> SecureRecord:
    return _read_stable_file(
        path,
        label=label,
        maximum=16 * 1024 * 1024,
        allowed_modes=frozenset({0o644, 0o755}),
        private_parent=False,
        parse_json=False,
    )


def _record_document(record: SecureRecord, *, label: str) -> dict[str, Any]:
    if record.document is None:
        raise PreFreezeEvidenceError(f"{label} is not a JSON record")
    return record.document


def _record_reference(record: SecureRecord) -> dict[str, Any]:
    identity = record.identity
    return {
        "path": os.fspath(record.path),
        "sha256": record.sha256,
        "bytes": identity.size,
        "device": identity.device,
        "inode": identity.inode,
        "mode": f"{identity.mode:04o}",
        "uid": identity.uid,
        "gid": identity.gid,
        "nlink": identity.nlink,
        "mtime_ns": identity.mtime_ns,
        "ctime_ns": identity.ctime_ns,
    }


def _ensure_private_directory(path: Path) -> None:
    path = _absolute_path(path, label="coordinator output directory")
    if path == Path("/"):
        raise PreFreezeEvidenceError(
            "filesystem root cannot be a coordinator output directory"
        )
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PreFreezeEvidenceError(
            "coordinator output directory is unavailable"
        ) from exc
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PreFreezeEvidenceError(
            "coordinator output directory is not root-private mode 0700"
        )


def _persist_document(
    directory: Path,
    *,
    filename: str,
    document: Mapping[str, Any],
) -> tuple[Path, str, str]:
    _ensure_private_directory(directory)
    if (
        not filename
        or "/" in filename
        or filename in {".", ".."}
        or "\x00" in filename
    ):
        raise PreFreezeEvidenceError("output filename is unsafe")
    payload = _canonical_json(dict(document)) + b"\n"
    if len(payload) > MAX_JSON_BYTES:
        raise PreFreezeEvidenceError("coordinator output is oversized")
    path = directory / filename
    digest = _sha256(payload)
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="pre-freeze coordinator output",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
        status = "created"
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label="existing pre-freeze coordinator output",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PreFreezeEvidenceError(
                "coordinator output could not be persisted safely"
            ) from exc
        if existing != payload:
            raise PreFreezeEvidenceError(
                "create-only coordinator output already differs"
            )
        status = "reused"
    observed = _read_private_json(
        path,
        label="persisted pre-freeze coordinator output",
    )
    if observed.payload != payload or observed.sha256 != digest:
        raise PreFreezeEvidenceError(
            "coordinator output readback differs"
        )
    return path, digest, status


def _timestamp_from_epoch(value: Any, *, label: str) -> datetime:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 4_102_444_800
    ):
        raise PreFreezeEvidenceError(f"{label} epoch is invalid")
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _require_fresh(
    observed: datetime,
    *,
    now: datetime,
    maximum: timedelta,
    label: str,
) -> None:
    if observed > now + FUTURE_SKEW:
        raise PreFreezeEvidenceError(f"{label} is implausibly in the future")
    if now - observed > maximum:
        raise PreFreezeEvidenceError(f"{label} is stale")


def _require_fresh_nginx_interface() -> None:
    nginx_schema = getattr(
        NGINX,
        "PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA",
        None,
    )
    if not isinstance(nginx_schema, str) or not nginx_schema:
        raise PreFreezeEvidenceError(
            "fresh challenge-bound current Nginx readback contract is not "
            "installed; legacy file mtime evidence is rejected"
        )


def _assert_records_unchanged(records: Mapping[str, SecureRecord]) -> None:
    errors: list[str] = []
    for label, expected in records.items():
        try:
            observed = _read_stable_file(
                expected.path,
                label=label,
                maximum=max(expected.identity.size, 1),
                allowed_modes=frozenset({expected.identity.mode}),
                private_parent=expected.identity.mode == 0o600,
                parse_json=expected.document is not None,
                canonical_required=expected.canonical_required,
            )
        except PreFreezeEvidenceError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if (
            observed.sha256 != expected.sha256
            or observed.identity != expected.identity
            or observed.payload != expected.payload
        ):
            errors.append(f"{label}: file identity or bytes changed")
    if errors:
        raise PreFreezeEvidenceError(
            "source evidence changed after validation: " + "; ".join(errors)
        )


def _read_manifest_record(path: Path) -> SecureRecord:
    record = _read_private_strict_json(
        path,
        label="production cutover manifest",
    )
    try:
        manifest, digest = CONTROLLER.read_root_only_manifest(path)
    except CONTROLLER.CutoverContractError as exc:
        raise PreFreezeEvidenceError(
            "production cutover manifest is invalid"
        ) from exc
    if digest != record.sha256 or manifest != record.document:
        raise PreFreezeEvidenceError(
            "production cutover manifest readback differs"
        )
    return record


def _read_journal(context: CoordinatorContext) -> dict[str, Any]:
    journal = CONTROLLER.ProductionCutoverJournal(
        Path(context.manifest["deployment"]["controller_journal_path"])
    )
    try:
        document = journal.assert_bindings(
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
            campaign_id=context.manifest["campaign_id"],
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            legacy_release_sha=context.manifest["legacy_release_sha"],
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreFreezeEvidenceError(
            "production cutover journal binding is invalid"
        ) from exc
    completed = list(document["completed_phases"])
    if completed not in ([], [PHASE]):
        raise PreFreezeEvidenceError(
            "pre-freeze coordinator requires the chronological first phase"
        )
    if completed == [PHASE]:
        if (
            document["started_phase"] is not None
            or PHASE not in document["phase_evidence_sha256"]
            or PHASE not in document["phase_verification_sha256"]
        ):
            raise PreFreezeEvidenceError(
                "completed pre-freeze journal closure is incomplete"
            )
        return document
    if document["status"] == "active":
        if document["started_phase"] is not None:
            raise PreFreezeEvidenceError(
                "active journal has a stale started phase"
            )
    elif not (
        document["status"] == "phase_started"
        and document["started_phase"] == PHASE
    ):
        raise PreFreezeEvidenceError(
            "journal is not ready for pre_freeze_evidence"
        )
    return document


def _load_context(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
) -> tuple[CoordinatorContext, dict[str, SecureRecord]]:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise PreFreezeEvidenceError(
            "pre-freeze evidence coordinator requires root:root"
        )
    manifest_path = _absolute_path(
        manifest_path,
        label="production cutover manifest",
    )
    approval_path = _absolute_path(
        approval_path,
        label="production cutover approval",
    )
    approval_policy_path = _absolute_path(
        approval_policy_path,
        label="production human approval policy",
    )
    manifest_record = _read_manifest_record(manifest_path)
    manifest = _record_document(
        manifest_record,
        label="production cutover manifest",
    )
    try:
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_record.sha256,
            manifest_path=manifest_path,
        )
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            manifest,
            approval_path=approval_path,
            approval_policy_path=approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreFreezeEvidenceError(
            "production cutover authorization is invalid or expired"
        ) from exc
    approval = _read_private_strict_json(
        approval_path,
        label="production cutover approval",
    )
    policy = _read_private_strict_json(
        approval_policy_path,
        label="production human approval policy",
    )
    if (
        approval.sha256
        != manifest["artifacts"]["cutover_approval_sha256"]
        or policy.sha256
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise PreFreezeEvidenceError(
            "approval or policy bytes differ from the manifest"
        )
    output_root = (
        Path(manifest["deployment"]["controller_evidence_root"])
        / PHASE
    )
    context = CoordinatorContext(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        manifest=manifest,
        manifest_sha256=manifest_record.sha256,
        plan=plan,
        plan_sha256=plan["plan_sha256"],
        output_root=output_root,
        journal={},
    )
    journal = _read_journal(context)
    context = CoordinatorContext(
        **{
            **context.__dict__,
            "journal": journal,
        }
    )
    return context, {
        "cutover_manifest": manifest_record,
        "cutover_approval": approval,
        "human_approval_policy": policy,
    }


def _validate_release_closure(
    context: CoordinatorContext,
    path: Path,
    records: dict[str, SecureRecord],
) -> dict[str, Any]:
    record = _read_private_json(path, label="sealed release closure")
    try:
        closure, payload, digest = FINLAND.load_release_closure(
            path,
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            release_tree_sha=context.manifest["release_tree_sha"],
            required_uid=0,
        )
    except FINLAND.FinlandArtifactOrchestratorError as exc:
        raise PreFreezeEvidenceError(
            "sealed release closure is invalid"
        ) from exc
    if (
        payload != record.payload
        or digest != record.sha256
        or closure != record.document
    ):
        raise PreFreezeEvidenceError(
            "sealed release closure readback differs"
        )
    artifacts = context.manifest["artifacts"]
    if (
        closure["release"]["bundle"]["sha256"]
        != artifacts["release_bundle_sha256"]
        or closure["release"]["bundle"]["bytes"]
        != artifacts["release_bundle_bytes"]
        or closure["images"] != artifacts["image_artifacts"]
    ):
        raise PreFreezeEvidenceError(
            "sealed release closure differs from manifest artifacts"
        )
    records["release_closure"] = record
    artifact_rows = {
        "release_bundle": closure["release"]["bundle"],
        **{
            f"{kind}_image": {
                "filename": FINLAND.STAGE.ARTIFACT_FILENAMES[
                    f"{kind}-image-archive"
                ],
                "sha256": closure["images"][kind]["archive_sha256"],
                "bytes": closure["images"][kind]["archive_bytes"],
            }
            for kind in IMAGE_KINDS
        },
    }
    for label, row in artifact_rows.items():
        artifact = _read_private_artifact(
            path.parent / row["filename"],
            label=label.replace("_", " "),
        )
        if (
            artifact.sha256 != row["sha256"]
            or artifact.identity.size != row["bytes"]
        ):
            raise PreFreezeEvidenceError(
                f"{label} bytes differ from sealed release closure"
            )
        records[label] = artifact
    return closure


def _validate_stage_bindings(
    context: CoordinatorContext,
    path: Path,
    records: dict[str, SecureRecord],
) -> dict[str, Any]:
    record = _read_private_json(path, label="four-role stage bindings")
    document = _record_document(record, label="four-role stage bindings")
    if (
        set(document) != STAGE_BINDINGS.OUTPUT_FIELDS
        or document["schema"] != STAGE_BINDINGS.STAGE_BINDINGS_SCHEMA
        or document["operation_id"] != context.manifest["operation_id"]
        or document["release_sha"] != context.manifest["release_sha"]
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLES)
    ):
        raise PreFreezeEvidenceError(
            "four-role stage binding identity differs"
        )
    for role in ROLES:
        row = document["roles"][role]
        expected_runtime = (
            context.manifest["artifacts"]["role_runtime_image_ids"][role]
            if role in DOCKER_ROLES
            else {}
        )
        if (
            not isinstance(row, dict)
            or set(row) != STAGE_BINDINGS.OUTPUT_ROLE_FIELDS
            or row["runtime_image_ids"] != expected_runtime
        ):
            raise PreFreezeEvidenceError(
                f"{role} stage binding differs from the manifest"
            )
        for field in (
            "stage_operation_manifest_sha256",
            "stage_attestation_sha256",
        ):
            _nonzero_sha256(
                row[field],
                label=f"{role} stage binding {field}",
            )
        if role in DOCKER_ROLES:
            for kind in IMAGE_KINDS:
                _image_id(
                    row["runtime_image_ids"][kind],
                    label=f"{role} {kind} runtime image",
                )
    records["stage_bindings"] = record
    return document


def _validate_prepare_metadata(
    context: CoordinatorContext,
    path: Path,
    canonical_compose: Path,
    stage_bindings: Mapping[str, Any],
    records: dict[str, SecureRecord],
) -> dict[str, Any]:
    record = _read_private_json(path, label="prepare material metadata")
    document = _record_document(record, label="prepare material metadata")
    if runtime_targets.is_legacy_prepare_material_schema(document):
        raise PreFreezeEvidenceError(runtime_targets.PREPARE_V2_MIGRATION_MESSAGE)
    if runtime_targets.is_legacy_cutover_manifest_schema(context.manifest):
        raise PreFreezeEvidenceError(runtime_targets.CUTOVER_V2_MIGRATION_MESSAGE)
    if context.manifest.get("schema") != CONTROLLER.MANIFEST_SCHEMA:
        raise PreFreezeEvidenceError(
            "pre-freeze evidence requires a fresh v4 cutover manifest"
        )
    artifacts = context.manifest["artifacts"]
    try:
        runtime_targets.validate_runtime_target_capabilities(
            document.get("capabilities"),
            label="prepare material capabilities",
        )
        runtime_targets.validate_runtime_target_capabilities(
            context.manifest.get("capabilities"),
            label="cutover manifest capabilities",
        )
        metadata_runtime_targets = runtime_targets.validate_runtime_target_descriptor(
            document.get("controller_bindings", {}).get(
                "convergence_runtime_targets"
            ),
            label="prepare convergence runtime target descriptor",
        )
        manifest_runtime_targets = runtime_targets.validate_runtime_target_descriptor(
            artifacts["convergence_runtime_targets"],
            label="cutover convergence runtime target descriptor",
        )
    except (
        AttributeError,
        KeyError,
        runtime_targets.ConvergenceRuntimeTargetDescriptorError,
    ) as exc:
        raise PreFreezeEvidenceError(
            "prepare convergence runtime target descriptor or capability is invalid"
        ) from exc
    if metadata_runtime_targets != manifest_runtime_targets:
        raise PreFreezeEvidenceError(
            "prepare convergence runtime target descriptor differs from the "
            "cutover manifest"
        )
    if (
        set(document) != PREPARE_SET_FIELDS
        or document["schema"] != PREPARE.SET_SCHEMA
        or document["capabilities"]
        != list(runtime_targets.RUNTIME_TARGET_CAPABILITIES)
        or document["operation_id"] != context.manifest["operation_id"]
        or document["release_sha"] != context.manifest["release_sha"]
        or document["canonical_compose_sha256"]
        != artifacts["shadow_compose_sha256"]
        or set(document.get("roles", {})) != set(ROLES)
        or set(document.get("controller_bindings", {}))
        != {
            "role_materials",
            "role_runtime_image_ids",
            "convergence_runtime_targets",
        }
        or document["controller_bindings"]["role_materials"]
        != artifacts["role_materials"]
        or document["controller_bindings"]["role_runtime_image_ids"]
        != artifacts["role_runtime_image_ids"]
        or document["controller_bindings"]["convergence_runtime_targets"]
        != manifest_runtime_targets
        or document["activation_secrets_included"] is not False
        or document["precommit_manifest_bound"] is not False
    ):
        raise PreFreezeEvidenceError(
            "prepare material metadata differs from the manifest"
        )
    records["prepare_metadata"] = record
    for field in ("dr_ca_sha256", "dr_tls_attestation_sha256"):
        _nonzero_sha256(document[field], label=f"prepare metadata {field}")
    if (
        isinstance(document["dr_tls_attested_at_epoch"], bool)
        or not isinstance(document["dr_tls_attested_at_epoch"], int)
        or not 1
        <= document["dr_tls_attested_at_epoch"]
        <= 4_102_444_800
    ):
        raise PreFreezeEvidenceError(
            "prepare TLS attestation epoch is invalid"
        )
    for role in ROLES:
        row = document["roles"][role]
        binding = stage_bindings["roles"][role]
        material = artifacts["role_materials"][role]
        if (
            not isinstance(row, dict)
            or set(row) != PREPARE_ROLE_FIELDS
            or row["filename"] != PREPARE.ROLE_ARCHIVE_NAMES[role]
            or row["sha256"] != material["sha256"]
            or row["bytes"] != material["bytes"]
            or row["format"] != material["format"]
            or row["transport"] != material["transport"]
            or row["stage_operation_manifest_sha256"]
            != binding["stage_operation_manifest_sha256"]
            or row["stage_attestation_sha256"]
            != binding["stage_attestation_sha256"]
        ):
            raise PreFreezeEvidenceError(
                f"{role} prepare material binding differs"
            )
        _nonzero_sha256(
            row["internal_manifest_sha256"],
            label=f"{role} internal prepare manifest",
        )
        archive = _read_private_artifact(
            path.parent / row["filename"],
            label=f"{role} role material archive",
            maximum=PREPARE.MAX_ARCHIVE_BYTES,
        )
        if archive.sha256 != row["sha256"] or archive.identity.size != row["bytes"]:
            raise PreFreezeEvidenceError(
                f"{role} role material archive differs"
            )
        records[f"{role}_role_material"] = archive
    compose = _read_release_file(
        canonical_compose,
        label="canonical production-shadow Compose",
    )
    if compose.sha256 != artifacts["shadow_compose_sha256"]:
        raise PreFreezeEvidenceError(
            "canonical Compose differs from the manifest"
        )
    records["canonical_compose"] = compose
    return document


def _validate_finland_evidence(
    context: CoordinatorContext,
    path: Path,
    *,
    release_closure_sha256: str,
    stage_bindings: Mapping[str, Any],
    records: dict[str, SecureRecord],
) -> None:
    record = _read_private_json(path, label="Finland stage evidence")
    document = _record_document(record, label="Finland stage evidence")
    if (
        set(document) != FINLAND_EVIDENCE_FIELDS
        or document["schema"] != FINLAND.EVIDENCE_SCHEMA
        or document["status"] != "staged"
        or document["operation_id"] != context.manifest["operation_id"]
        or document["release_sha"] != context.manifest["release_sha"]
        or document["release_tree_sha"]
        != context.manifest["release_tree_sha"]
        or document["closure_sha256"] != release_closure_sha256
        or document["pull_policy"] != "never"
        or document["object_storage_used"] is not False
        or document["arvan_endpoint_contacted"] is not False
        or any(
            document[field] is not False
            for field in (
                "containers_created",
                "containers_started",
                "services_started",
                "networks_created",
                "volumes_created",
                "current_mutated",
                "data_mutated",
            )
        )
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != {"bot_fi", "webapp_fi"}
        or not isinstance(document["binding_summaries"], dict)
        or set(document["binding_summaries"])
        != {"bot_fi", "webapp_fi"}
        or not isinstance(document["stage_bindings"], dict)
        or document["stage_bindings"].get("schema")
        != STAGE_BINDINGS.STAGE_BINDINGS_SCHEMA
        or document["stage_bindings"].get("operation_id")
        != context.manifest["operation_id"]
        or document["stage_bindings"].get("release_sha")
        != context.manifest["release_sha"]
        or set(document["stage_bindings"].get("roles", {}))
        != {"bot_fi", "webapp_fi"}
    ):
        raise PreFreezeEvidenceError(
            "Finland stage evidence identity or safety boundary differs"
        )
    _nonzero_sha256(
        document["agent_sha256"],
        label="Finland stage agent",
    )
    for role in ("bot_fi", "webapp_fi"):
        role_row = document["roles"][role]
        summary = document["binding_summaries"][role]
        embedded = document["stage_bindings"]["roles"][role]
        expected = stage_bindings["roles"][role]
        if (
            not isinstance(role_row, dict)
            or set(role_row) != FINLAND_ROLE_FIELDS
            or role_row["host"] != context.manifest["topology"][role]["host"]
            or role_row["transport"]
            != context.manifest["topology"][role]["transport"]
            or not isinstance(
                role_row["stage_attestation_path"],
                str,
            )
            or not role_row["stage_attestation_path"].startswith("/")
            or not isinstance(summary, dict)
            or set(summary) != FINLAND.ROLE_BINDING_FIELDS
            or summary["schema"] != FINLAND.ROLE_BINDING_SCHEMA
            or summary["operation_id"] != context.manifest["operation_id"]
            or summary["release_sha"] != context.manifest["release_sha"]
            or summary["role"] != role
            or {
                "stage_operation_manifest_sha256": summary[
                    "stage_operation_manifest_sha256"
                ],
                "stage_attestation_sha256": summary[
                    "stage_attestation_sha256"
                ],
                "runtime_image_ids": summary["runtime_image_ids"],
            }
            != expected
            or embedded != expected
            or {
                "stage_operation_manifest_sha256": role_row[
                    "stage_operation_manifest_sha256"
                ],
                "stage_attestation_sha256": role_row[
                    "stage_attestation_sha256"
                ],
                "runtime_image_ids": role_row["runtime_image_ids"],
            }
            != expected
        ):
            raise PreFreezeEvidenceError(
                f"{role} Finland stage evidence differs or is substituted"
            )
    records["finland_stage_evidence"] = record


def _validate_wa_ir_evidence(
    context: CoordinatorContext,
    *,
    operation_manifest_path: Path,
    stage_attestation_path: Path,
    stage_binding_path: Path,
    stage_bindings: Mapping[str, Any],
    closure: Mapping[str, Any],
    records: dict[str, SecureRecord],
) -> None:
    operation_manifest_record = _read_private_json(
        operation_manifest_path,
        label="WA-IR operation manifest",
    )
    try:
        operation_manifest = WA_OPERATION.load_manifest(
            operation_manifest_path,
            required_uid=0,
        )
    except WA_OPERATION.ProductionOperationError as exc:
        raise PreFreezeEvidenceError(
            "WA-IR operation manifest is invalid"
        ) from exc
    if (
        operation_manifest.canonical_sha256
        != operation_manifest_record.sha256
        or operation_manifest.operation_id
        != context.manifest["operation_id"]
        or operation_manifest.release_sha
        != context.manifest["release_sha"]
        or operation_manifest.release_tree_sha
        != context.manifest["release_tree_sha"]
        or {
            kind: {
                "archive_sha256": operation_manifest.image_artifacts[
                    kind
                ].archive_sha256,
                "archive_bytes": operation_manifest.image_artifacts[
                    kind
                ].archive_bytes,
                "config_digest": operation_manifest.image_artifacts[
                    kind
                ].config_digest,
                "content_descriptor": operation_manifest.image_artifacts[
                    kind
                ].content_descriptor,
                "content_identity": operation_manifest.image_artifacts[
                    kind
                ].content_identity,
            }
            for kind in IMAGE_KINDS
        }
        != closure["images"]
    ):
        raise PreFreezeEvidenceError(
            "WA-IR operation manifest differs from the cutover release"
        )
    attestation_record = _read_private_json(
        stage_attestation_path,
        label="WA-IR stage operation attestation",
    )
    attestation = _record_document(
        attestation_record,
        label="WA-IR stage operation attestation",
    )
    try:
        runtime_ids, stage_attestation_sha256 = (
            WA_ORCHESTRATOR._validate_stage_operation_attestation(  # noqa: SLF001
                attestation,
                manifest=operation_manifest,
            )
        )
        expected_binding = WA_ORCHESTRATOR.build_stage_binding(
            attestation,
            manifest=operation_manifest,
        )
    except (
        WA_ORCHESTRATOR.ProductionOrchestratorError,
        WA_OPERATION.ProductionOperationError,
    ) as exc:
        raise PreFreezeEvidenceError(
            "WA-IR stage attestation is invalid"
        ) from exc
    binding_record = _read_private_json(
        stage_binding_path,
        label="WA-IR stage binding",
    )
    binding = _record_document(
        binding_record,
        label="WA-IR stage binding",
    )
    if (
        dict(runtime_ids)
        != context.manifest["artifacts"]["role_runtime_image_ids"][
            "webapp_ir"
        ]
        or stage_attestation_sha256
        != stage_bindings["roles"]["webapp_ir"][
            "stage_attestation_sha256"
        ]
        or binding != expected_binding
        or binding != {
            "schema": STAGE_BINDINGS.ROLE_SUMMARY_SCHEMA,
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "role": "webapp_ir",
            **stage_bindings["roles"]["webapp_ir"],
        }
        or binding["stage_operation_manifest_sha256"]
        != attestation_record.sha256
    ):
        raise PreFreezeEvidenceError(
            "WA-IR stage binding differs or is substituted"
        )
    records["wa_ir_operation_manifest"] = operation_manifest_record
    records["wa_ir_stage_attestation"] = attestation_record
    records["wa_ir_stage_binding"] = binding_record


def _validate_witness_evidence(
    context: CoordinatorContext,
    *,
    health_path: Path,
    public_path: Path,
    stage_path: Path,
    binding_path: Path,
    stage_bindings: Mapping[str, Any],
    now: datetime,
    records: dict[str, SecureRecord],
) -> datetime:
    health_record = _read_private_json(
        health_path,
        label="Witness health attestation",
    )
    public_record = _read_private_json(
        public_path,
        label="Witness public prepare input",
    )
    stage_record = _read_private_json(
        stage_path,
        label="Witness stage operation",
    )
    binding_record = _read_private_json(
        binding_path,
        label="Witness stage binding",
    )
    health = _record_document(
        health_record,
        label="Witness health attestation",
    )
    public = _record_document(
        public_record,
        label="Witness public prepare input",
    )
    stage = _record_document(
        stage_record,
        label="Witness stage operation",
    )
    binding = _record_document(
        binding_record,
        label="Witness stage binding",
    )
    identity = {
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
    }
    observed = _timestamp_from_epoch(
        health.get("observed_at_epoch"),
        label="Witness health observation",
    )
    _require_fresh(
        observed,
        now=now,
        maximum=timedelta(seconds=WITNESS.MAX_HEALTH_AGE_SECONDS),
        label="Witness health observation",
    )
    tls = health.get("loopback_tls")
    http = health.get("loopback_http")
    systemd = health.get("systemd")
    if (
        set(health) != WITNESS.HEALTH_ATTESTATION_FIELDS
        or health["schema"] != WITNESS.HEALTH_ATTESTATION_SCHEMA
        or any(health.get(field) != value for field, value in identity.items())
        or not isinstance(systemd, dict)
        or systemd.get("active_state") != "active"
        or systemd.get("returncode") != 0
        or not isinstance(http, dict)
        or set(http) != set(WITNESS.HEALTH_EXPECTATIONS)
        or any(
            not isinstance(http[name], dict)
            or http[name].get("status_code") != 200
            or http[name].get("content_type") != "application/json"
            for name in WITNESS.HEALTH_EXPECTATIONS
        )
        or not isinstance(tls, dict)
        or tls.get("host") != WITNESS.LOOPBACK_HOST
        or tls.get("port") != WITNESS.LOOPBACK_TLS_PORT
        or tls.get("server_name")
        != context.manifest["topology"]["witness"]["host"]
        or tls.get("certificate_encoding") != "canonical-pem"
    ):
        raise PreFreezeEvidenceError(
            "Witness health attestation identity or readback differs"
        )
    health_sha256 = _sha256(_canonical_json(health))
    if health_sha256 != health_record.sha256:
        raise PreFreezeEvidenceError(
            "Witness health attestation encoding differs"
        )
    if (
        set(public) != WITNESS.PUBLIC_INPUT_FIELDS
        or public["schema"] != WITNESS.PUBLIC_INPUT_SCHEMA
        or any(public.get(field) != value for field, value in identity.items())
        or public["health_attestation_sha256"] != health_sha256
        or public["health_attested_at_epoch"]
        != health["observed_at_epoch"]
        or public["release_manifest_sha256"]
        != health["release_manifest_sha256"]
        or public["ca_sha256"] != tls.get("ca_sha256")
        or public["server_cert_sha256"]
        != tls.get("server_cert_sha256")
        or public["native_release_reused"] is not True
        or public["current_mutated"] is not False
        or public["service_mutated"] is not False
        or public["legacy_secret_material_copied"] is not False
    ):
        raise PreFreezeEvidenceError(
            "Witness public prepare input differs"
        )
    public_sha256 = _sha256(_canonical_json(public))
    if public_sha256 != public_record.sha256:
        raise PreFreezeEvidenceError(
            "Witness public input encoding differs"
        )
    expected_release_root = (
        WITNESS.STAGED_RELEASE_PREFIX / context.manifest["release_sha"]
    )
    if (
        set(stage) != WITNESS.STAGE_OPERATION_FIELDS
        or stage["schema"] != WITNESS.STAGE_OPERATION_SCHEMA
        or any(stage.get(field) != value for field, value in identity.items())
        or stage["candidate_release_root"] != os.fspath(expected_release_root)
        or stage["release_manifest_sha256"]
        != public["release_manifest_sha256"]
        or stage["health_attestation_sha256"] != health_sha256
        or stage["stage_attestation_sha256"] != public_sha256
        or stage["health_attested_at_epoch"] != health["observed_at_epoch"]
        or stage["native_release_reused"] is not True
        or stage["current_mutated"] is not False
        or stage["service_mutated"] is not False
        or stage["legacy_secret_material_copied"] is not False
        or stage["runtime_image_ids"] != {}
    ):
        raise PreFreezeEvidenceError(
            "Witness stage operation differs"
        )
    stage_sha256 = _sha256(_canonical_json(stage))
    expected_binding = {
        "schema": WITNESS.CONTROLLER_BINDING_SCHEMA,
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "role": "witness",
        "stage_operation_manifest_sha256": stage_sha256,
        "stage_attestation_sha256": public_sha256,
        "runtime_image_ids": {},
    }
    if (
        set(binding) != WITNESS.CONTROLLER_BINDING_FIELDS
        or binding != expected_binding
        or binding_record.sha256 != _sha256(_canonical_json(binding))
        or stage_bindings["roles"]["witness"]
        != {
            "stage_operation_manifest_sha256": stage_sha256,
            "stage_attestation_sha256": public_sha256,
            "runtime_image_ids": {},
        }
    ):
        raise PreFreezeEvidenceError(
            "Witness stage binding differs or is substituted"
        )
    records["witness_health"] = health_record
    records["witness_public_input"] = public_record
    records["witness_stage_operation"] = stage_record
    records["witness_stage_binding"] = binding_record
    return observed


def _validate_inventory_evidence(
    context: CoordinatorContext,
    *,
    receipt_path: Path,
    output_root: Path,
    release_agent_sha256: str,
    release_contract_worker_sha256: Mapping[str, str],
    expected_role_manifest_sha256: Mapping[str, str],
    now: datetime,
    records: dict[str, SecureRecord],
) -> dict[str, datetime]:
    trusted_output_root = _absolute_path(
        Path(context.manifest["deployment"]["controller_evidence_root"]),
        label="manifest prepared inventory output root",
    )
    supplied_output_root = _absolute_path(
        output_root,
        label="prepared inventory output root",
    )
    if supplied_output_root != trusted_output_root:
        raise PreFreezeEvidenceError(
            "prepared inventory output root differs from the manifest"
        )
    if (
        set(release_contract_worker_sha256) != set(DOCKER_ROLES)
        or set(expected_role_manifest_sha256) != set(DOCKER_ROLES)
    ):
        raise PreFreezeEvidenceError(
            "prepared inventory release binding mapping is not exact"
        )
    try:
        loaded = (
            PREPARED_INVENTORY.load_pre_freeze_current_operation_receipt(
                receipt_path,
                output_root=trusted_output_root,
                now=now,
            )
        )
    except PREPARED_INVENTORY.PreparedCloneInventoryError as exc:
        raise PreFreezeEvidenceError(
            "fresh prepared-clone inventory receipt is invalid"
        ) from exc
    if (
        not isinstance(loaded, dict)
        or set(loaded) != PREPARED_RECEIPT_LOAD_FIELDS
        or loaded["schema"] != PREPARED_INVENTORY.LOADED_RECEIPT_SCHEMA
        or loaded["status"] != "loaded-readback-verified"
        or loaded["artifact_count"] != 7
        or loaded["readback_verified"] is not True
        or not isinstance(loaded["receipt"], dict)
        or not isinstance(loaded["requests"], dict)
        or not isinstance(loaded["responses"], dict)
        or not isinstance(loaded["artifacts"], dict)
        or set(loaded["requests"]) != set(DOCKER_ROLES)
        or set(loaded["responses"]) != set(DOCKER_ROLES)
        or set(loaded["artifacts"]) != set(DOCKER_ROLES)
        or not isinstance(loaded["aggregate"], dict)
        or set(loaded["aggregate"])
        != PREPARED_SOURCE_REFERENCE_FIELDS
    ):
        raise PreFreezeEvidenceError(
            "fresh prepared-clone receipt readback closure differs"
        )
    receipt = loaded["receipt"]
    canonical_receipt_path = _absolute_path(
        receipt_path,
        label="prepared inventory receipt",
    )
    expected_identity = {
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "expected_database_state": "running-healthy",
    }
    if (
        receipt.get("schema")
        != PREPARED_INVENTORY.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_SCHEMA
        or any(
            receipt.get(field) != value
            for field, value in expected_identity.items()
        )
        or receipt.get("collection_performed") is not True
        or receipt.get("production_contacted") is not True
        or receipt.get("docker_read_only") is not True
        or receipt.get("application_payload_bytes_over_ssh") != 0
    ):
        raise PreFreezeEvidenceError(
            "prepared-clone receipt identity or safety boundary differs"
        )

    aggregate_reference = loaded["aggregate"]
    if (
        not isinstance(aggregate_reference["filename"], str)
        or aggregate_reference["filename"]
        != PREPARED_INVENTORY.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME
        or not isinstance(aggregate_reference["path"], str)
        or _absolute_path(
            aggregate_reference["path"],
            label="prepared inventory aggregate reference",
        )
        != canonical_receipt_path
        or not isinstance(aggregate_reference["sha256"], str)
        or CONTROLLER.SHA256_RE.fullmatch(
            aggregate_reference["sha256"]
        )
        is None
        or aggregate_reference["sha256"] == ZERO_SHA256
        or isinstance(aggregate_reference["bytes"], bool)
        or not isinstance(aggregate_reference["bytes"], int)
        or aggregate_reference["bytes"] < 1
    ):
        raise PreFreezeEvidenceError(
            "prepared-clone aggregate reference differs"
        )
    aggregate_record = _read_private_json(
        canonical_receipt_path,
        label="prepared-clone inventory aggregate",
    )
    if (
        aggregate_record.path != canonical_receipt_path
        or aggregate_record.document != receipt
        or aggregate_record.sha256 != aggregate_reference["sha256"]
        or aggregate_record.identity.size != aggregate_reference["bytes"]
    ):
        raise PreFreezeEvidenceError(
            "prepared-clone aggregate provenance differs"
        )
    records["prepared_clone_inventory_receipt"] = aggregate_record

    observations: dict[str, datetime] = {}
    wa_operation_record = records.get("wa_ir_operation_manifest")
    if not isinstance(wa_operation_record, SecureRecord):
        raise PreFreezeEvidenceError(
            "WA-IR operation manifest provenance is absent"
        )
    for role in DOCKER_ROLES:
        references = loaded["artifacts"][role]
        if (
            not isinstance(references, dict)
            or set(references) != {"request", "response"}
            or any(
                not isinstance(references[kind], dict)
                or set(references[kind])
                != PREPARED_SOURCE_REFERENCE_FIELDS
                for kind in ("request", "response")
            )
        ):
            raise PreFreezeEvidenceError(
                f"{role} prepared inventory provenance differs"
            )
        request_reference = references["request"]
        response_reference = references["response"]
        expected_request_path = (
            canonical_receipt_path.parent
            / PREPARED_INVENTORY.REQUEST_FILENAMES[role]
        )
        expected_response_path = (
            canonical_receipt_path.parent
            / PREPARED_INVENTORY.RESPONSE_FILENAMES[role]
        )
        for kind, reference, expected_path, expected_filename in (
            (
                "request",
                request_reference,
                expected_request_path,
                PREPARED_INVENTORY.REQUEST_FILENAMES[role],
            ),
            (
                "response",
                response_reference,
                expected_response_path,
                PREPARED_INVENTORY.RESPONSE_FILENAMES[role],
            ),
        ):
            if (
                reference["filename"] != expected_filename
                or not isinstance(reference["path"], str)
                or _absolute_path(
                    reference["path"],
                    label=f"{role} prepared inventory {kind} reference",
                )
                != expected_path
                or not isinstance(reference["sha256"], str)
                or CONTROLLER.SHA256_RE.fullmatch(reference["sha256"])
                is None
                or reference["sha256"] == ZERO_SHA256
                or isinstance(reference["bytes"], bool)
                or not isinstance(reference["bytes"], int)
                or reference["bytes"] < 1
            ):
                raise PreFreezeEvidenceError(
                    f"{role} prepared inventory {kind} reference differs"
                )
        request_record = _read_private_json(
            expected_request_path,
            label=f"{role} prepared inventory request",
        )
        response_record = _read_private_json(
            expected_response_path,
            label=f"{role} prepared inventory response",
        )
        request = loaded["requests"][role]
        response = loaded["responses"][role]
        if (
            request_record.document != request
            or response_record.document != response
            or request_record.sha256 != request_reference["sha256"]
            or response_record.sha256 != response_reference["sha256"]
            or request_record.identity.size != request_reference["bytes"]
            or response_record.identity.size != response_reference["bytes"]
        ):
            raise PreFreezeEvidenceError(
                f"{role} prepared inventory source bytes differ"
            )
        topology = context.manifest["topology"][role]
        canonical_topology = CANONICAL_DOCKER_TOPOLOGY[role]
        expected_worker_sha256 = release_contract_worker_sha256[role]
        if (
            topology.get("host") != INVENTORY.ROLE_HOSTS[role]
            or any(
                topology.get(field) != expected
                for field, expected in canonical_topology.items()
            )
            or request.get("role") != role
            or request.get("expected_host") != topology["host"]
            or response.get("expected_host") != topology["host"]
            or request.get("agent_sha256") != release_agent_sha256
            or request.get("contract_worker_sha256")
            != expected_worker_sha256
            or request.get("role_manifest_sha256")
            != expected_role_manifest_sha256[role]
            or response.get("role_manifest_sha256")
            != request["role_manifest_sha256"]
        ):
            raise PreFreezeEvidenceError(
                f"{role} prepared inventory release or topology binding differs"
            )
        if (
            role == "webapp_ir"
            and request["role_manifest_sha256"]
            != wa_operation_record.sha256
        ):
            raise PreFreezeEvidenceError(
                "webapp_ir prepared role manifest differs from staged evidence"
            )
        try:
            observed = PREPARED_INVENTORY._parse_timestamp(  # noqa: SLF001
                response["captured_at"],
                label=f"{role} prepared inventory capture",
            )
        except PREPARED_INVENTORY.PreparedCloneInventoryError as exc:
            raise PreFreezeEvidenceError(
                f"{role} prepared inventory capture is invalid"
            ) from exc
        observations[role] = observed
        records[f"{role}_inventory_request"] = request_record
        records[f"{role}_inventory_response"] = response_record
    return observations


def _validate_rollback_evidence(
    context: CoordinatorContext,
    paths: Mapping[str, Path],
    records: dict[str, SecureRecord],
) -> dict[str, dict[str, Any]]:
    if set(paths) != set(ROLLBACK_ROLES):
        raise PreFreezeEvidenceError(
            "legacy rollback attestation role mapping is not exact"
        )
    documents: dict[str, dict[str, Any]] = {}
    manifest_artifacts = context.manifest["artifacts"]
    for role in ROLLBACK_ROLES:
        record = _read_private_json(
            paths[role],
            label=f"{role} legacy rollback attestation",
        )
        document = _record_document(
            record,
            label=f"{role} legacy rollback attestation",
        )
        expected_sealed_count = len(ROLLBACK.ROLE_SEALED_FILES[role]) + 1
        closure_field = (
            "legacy_bot_rollback_sha256"
            if role == "bot_fi"
            else "legacy_webapp_rollback_sha256"
        )
        redis_field = (
            "legacy_bot_redis_rollback_sha256"
            if role == "bot_fi"
            else "legacy_webapp_redis_rollback_sha256"
        )
        if (
            set(document) != ROLLBACK_FIELDS
            or document["schema"] != ROLLBACK.ATTESTATION_SCHEMA
            or document["status"] != "verified"
            or document["operation_id"] != context.manifest["operation_id"]
            or document["release_sha"] != context.manifest["release_sha"]
            or document["legacy_release_sha"]
            != context.manifest["legacy_release_sha"]
            or document["role"] != role
            or ROLLBACK.STAMP_RE.fullmatch(
                str(document["backup_stamp"])
            )
            is None
            or document["rollback_closure_sha256"]
            != manifest_artifacts[closure_field]
            or document["legacy_redis_rollback_sha256"]
            != manifest_artifacts[redis_field]
            or document["database_restore_smoke_passed"] is not True
            or isinstance(
                document["database_restore_smoke_table_count"],
                bool,
            )
            or not isinstance(
                document["database_restore_smoke_table_count"],
                int,
            )
            or not 1
            <= document["database_restore_smoke_table_count"]
            <= 100_000
            or document["sealed_file_count"] != expected_sealed_count
            or document["backup_artifact_count"] != len(ROLLBACK.BACKUP_KINDS)
            or document["source_mutated"] is not False
            or document["production_contacted"] is not True
        ):
            raise PreFreezeEvidenceError(
                f"{role} legacy rollback attestation differs"
            )
        for field in (
            "rollback_closure_sha256",
            "legacy_redis_rollback_sha256",
            "sha256sums_sha256",
            "backup_manifest_sha256",
            "backup_artifact_set_sha256",
        ):
            _nonzero_sha256(
                document[field],
                label=f"{role} rollback {field}",
            )
        documents[role] = document
        records[f"{role}_rollback_attestation"] = record
    return documents


def _validate_nginx_evidence(
    context: CoordinatorContext,
    *,
    aggregate_path: Path,
    receipt_path: Path,
    now: datetime,
    records: dict[str, SecureRecord],
) -> tuple[dict[str, Any], datetime]:
    _require_fresh_nginx_interface()
    aggregate_record = _read_private_json(
        aggregate_path,
        label="Nginx generation aggregate",
    )
    aggregate = _record_document(
        aggregate_record,
        label="Nginx generation aggregate",
    )
    release_root = (
        Path(context.manifest["deployment"]["shadow_root"])
        / "releases"
        / context.manifest["release_sha"]
    )
    try:
        nginx_hashes = TEMPLATE._verify_nginx_material(  # noqa: SLF001
            aggregate_path=aggregate_path,
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            release_tree_sha=context.manifest["release_tree_sha"],
            release_root=release_root,
            owner_uid=0,
        )
    except TEMPLATE.CutoverManifestTemplateError as exc:
        raise PreFreezeEvidenceError(
            "Nginx generation material is invalid"
        ) from exc
    artifacts = context.manifest["artifacts"]
    expected_hashes = {
        field: artifacts[field]
        for field in (
            "nginx_rollback_generation_sha256",
            "nginx_freeze_generation_sha256",
            "nginx_shadow_readonly_generation_sha256",
            "nginx_shadow_writable_generation_sha256",
        )
    }
    if (
        set(aggregate) != NGINX_AGGREGATE_FIELDS
        or aggregate["schema"] != NGINX.GENERATION.PRODUCER_SCHEMA
        or nginx_hashes != expected_hashes
        or aggregate["nginx_legacy_normal_generation_sha256"]
        != artifacts["nginx_rollback_generation_sha256"]
        or aggregate["generation_sha256"]["legacy-normal"]
        != artifacts["nginx_rollback_generation_sha256"]
    ):
        raise PreFreezeEvidenceError(
            "Nginx generation aggregate differs from the manifest"
        )
    records["nginx_aggregate"] = aggregate_record
    for role in NGINX.ROLE_ORDER:
        row = aggregate["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != NGINX_AGGREGATE_ROLE_FIELDS
        ):
            raise PreFreezeEvidenceError(
                f"{role} Nginx aggregate row differs"
            )
        manifest_record = _read_private_json(
            aggregate_path.parent
            / role
            / "nginx-generations-manifest.json",
            label=f"{role} Nginx generation manifest",
        )
        archive_record = _read_private_artifact(
            aggregate_path.parent / role / "nginx-generations.tar",
            label=f"{role} Nginx generation archive",
            maximum=NGINX.GENERATION.MAX_ARCHIVE_BYTES,
        )
        if (
            manifest_record.sha256 != row["manifest_sha256"]
            or manifest_record.identity.size != row["manifest_bytes"]
            or archive_record.sha256 != row["archive_sha256"]
            or archive_record.identity.size != row["archive_bytes"]
        ):
            raise PreFreezeEvidenceError(
                f"{role} Nginx material bytes differ"
            )
        records[f"{role}_nginx_manifest"] = manifest_record
        records[f"{role}_nginx_archive"] = archive_record
    receipt_record = _read_private_json(
        receipt_path,
        label="legacy-normal Nginx state receipt",
    )
    try:
        receipt, receipt_sha256 = NGINX.load_state_receipt(
            receipt_path,
            "legacy-normal",
            context.manifest["operation_id"],
            context.manifest["release_sha"],
            context.manifest["release_tree_sha"],
            aggregate_record.sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise PreFreezeEvidenceError(
            "legacy-normal Nginx readback receipt is invalid"
        ) from exc
    if receipt_sha256 != receipt_record.sha256 or receipt != receipt_record.document:
        raise PreFreezeEvidenceError(
            "legacy-normal Nginx receipt readback differs"
        )
    for role in NGINX.ROLE_ORDER:
        if (
            receipt["role_bindings"][role]["manifest_sha256"]
            != aggregate["roles"][role]["manifest_sha256"]
            or receipt["role_bindings"][role]["archive_sha256"]
            != aggregate["roles"][role]["archive_sha256"]
        ):
            raise PreFreezeEvidenceError(
                f"{role} Nginx receipt role binding is substituted"
            )
    if (
        receipt["global_generation_sha256"]
        != artifacts["nginx_rollback_generation_sha256"]
        or receipt["external_readback"]["states"] != ["legacy-normal"]
        or set(receipt["external_readback"]["vhosts"])
        != {
            vhost
            for vhosts in CONTROLLER.PRODUCTION_VHOSTS.values()
            for vhost in vhosts
        }
        or any(
            probes != {"get": probes["get"]}
            or not 200 <= probes["get"] <= 399
            for probes in receipt["external_readback"]["vhosts"].values()
        )
    ):
        raise PreFreezeEvidenceError(
            "active legacy-normal external route readback is absent"
        )
    receipt_time = _timestamp_from_epoch(
        receipt.get("captured_at_epoch"),
        label="legacy-normal Nginx state receipt",
    )
    _require_fresh(
        receipt_time,
        now=now,
        maximum=SOURCE_MAX_AGE,
        label="legacy-normal Nginx state receipt",
    )
    records["nginx_legacy_normal_receipt"] = receipt_record
    return receipt, receipt_time


def _validate_release_files(
    context: CoordinatorContext,
    records: dict[str, SecureRecord],
) -> dict[str, str]:
    release_root = (
        Path(context.manifest["deployment"]["shadow_root"])
        / "releases"
        / context.manifest["release_sha"]
    )
    relative_paths = {
        "phase_verifier": Path(
            CONTROLLER.PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH
        ),
        "host_agent": Path("scripts/production_shadow_host_agent.py"),
        "inventory_agent": Path(
            "scripts/production_shadow_global_docker_inventory_agent.py"
        ),
        "precommit_worker": Path(
            "scripts/production_shadow_precommit_worker.py"
        ),
        "wa_ir_operation": Path(
            "scripts/wa_ir_production_operation.py"
        ),
    }
    digests: dict[str, str] = {}
    for label, relative in relative_paths.items():
        record = _read_release_file(
            release_root / relative,
            label=f"release-bound {label.replace('_', ' ')}",
        )
        records[f"release_{label}"] = record
        digests[label] = record.sha256
    artifacts = context.manifest["artifacts"]
    if (
        digests["phase_verifier"]
        != artifacts["phase_evidence_verifier_sha256"]
        or digests["host_agent"] != artifacts["host_agent_sha256"]
    ):
        raise PreFreezeEvidenceError(
            "release verifier or host agent differs from the manifest"
        )
    try:
        verifier_sha256 = VERIFY.hash_release_verifier(
            release_root
            / CONTROLLER.PHASE_EVIDENCE_VERIFIER_RELATIVE_PATH,
            owner_uid=0,
        )
        agent_sha256 = HOST_AGENT.hash_agent_artifact(
            release_root / "scripts/production_shadow_host_agent.py",
        )
    except (VERIFY.PhaseEvidenceError, HOST_AGENT.HostAgentError) as exc:
        raise PreFreezeEvidenceError(
            "release-bound verifier or host agent is unsafe"
        ) from exc
    if (
        verifier_sha256 != digests["phase_verifier"]
        or agent_sha256 != digests["host_agent"]
    ):
        raise PreFreezeEvidenceError(
            "release-bound executable readback differs"
        )
    return digests


def _claim_values(
    context: CoordinatorContext,
    *,
    closure: Mapping[str, Any],
    rollback: Mapping[str, Mapping[str, Any]],
    nginx_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = context.manifest["artifacts"]
    expected_postgres_ref = (
        "trading_bot_postgres_boottime:15-"
        + context.manifest["release_sha"]
    )
    if artifacts["postgres_image_ref"] != expected_postgres_ref:
        raise PreFreezeEvidenceError(
            "PostgreSQL image reference is not release-derived"
        )
    values: dict[str, Any] = {
        "release_bundle_sha256": closure["release"]["bundle"]["sha256"],
        "shadow_compose_sha256": artifacts["shadow_compose_sha256"],
        "postgres_image_ref": expected_postgres_ref,
        "legacy_bot_rollback_sha256": rollback["bot_fi"][
            "rollback_closure_sha256"
        ],
        "legacy_webapp_rollback_sha256": rollback["webapp_fi"][
            "rollback_closure_sha256"
        ],
        "legacy_bot_redis_rollback_sha256": rollback["bot_fi"][
            "legacy_redis_rollback_sha256"
        ],
        "legacy_webapp_redis_rollback_sha256": rollback["webapp_fi"][
            "legacy_redis_rollback_sha256"
        ],
        "nginx_rollback_generation_sha256": nginx_receipt[
            "global_generation_sha256"
        ],
        "host_agent_sha256": artifacts["host_agent_sha256"],
        "host_agent_contract_sha256": artifacts[
            "host_agent_contract_sha256"
        ],
        "exact_release_image_compose_attested": True,
        "canonical_host_identity_attested": True,
        "legacy_rollback_artifact_set_attested": True,
        "active_route_generation_set_sha256": nginx_receipt[
            "global_generation_sha256"
        ],
    }
    for role in ROLES:
        values[f"{role}_role_material_sha256"] = artifacts[
            "role_materials"
        ][role]["sha256"]
    for kind in IMAGE_KINDS:
        values[f"{kind}_image_config_digest"] = closure["images"][kind][
            "config_digest"
        ]
        values[f"{kind}_image_content_identity"] = closure["images"][kind][
            "content_identity"
        ]
    for role in DOCKER_ROLES:
        for kind in IMAGE_KINDS:
            values[f"{role}_{kind}_runtime_image_id"] = artifacts[
                "role_runtime_image_ids"
            ][role][kind]
    if set(values) != set(CLAIMS):
        missing = sorted(set(CLAIMS) - set(values))
        extra = sorted(set(values) - set(CLAIMS))
        raise PreFreezeEvidenceError(
            f"derived pre-freeze claim set differs; missing={missing}, extra={extra}"
        )
    for name, rule in VERIFY.PHASE_CLAIM_RULES[PHASE].items():
        try:
            VERIFY._validate_claim(  # noqa: SLF001
                name,
                {"value": values[name], "source_sha256": "1" * 64},
                rule,
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise PreFreezeEvidenceError(
                f"derived claim {name} is invalid"
            ) from exc
    for claim, binding in MANIFEST_BOUND_CLAIMS.items():
        if (
            values[claim]
            != VERIFY._manifest_artifact_binding_value(  # noqa: SLF001
                artifacts,
                binding,
            )
        ):
            raise PreFreezeEvidenceError(
                f"derived claim {claim} differs from the manifest"
            )
    return values


def _claim_provenance() -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {
        "release_bundle_sha256": (
            "release_closure",
            "release_bundle",
        ),
        "shadow_compose_sha256": (
            "prepare_metadata",
            "canonical_compose",
        ),
        "postgres_image_ref": (
            "cutover_manifest",
            "release_closure",
        ),
        "legacy_bot_rollback_sha256": (
            "bot_fi_rollback_attestation",
        ),
        "legacy_webapp_rollback_sha256": (
            "webapp_fi_rollback_attestation",
        ),
        "legacy_bot_redis_rollback_sha256": (
            "bot_fi_rollback_attestation",
        ),
        "legacy_webapp_redis_rollback_sha256": (
            "webapp_fi_rollback_attestation",
        ),
        "nginx_rollback_generation_sha256": (
            "nginx_aggregate",
            "nginx_legacy_normal_receipt",
        ),
        "host_agent_sha256": (
            "release_host_agent",
            "cutover_manifest",
        ),
        "host_agent_contract_sha256": (
            "release_host_agent",
            "cutover_manifest",
        ),
        "exact_release_image_compose_attested": (
            "release_closure",
            "prepare_metadata",
            "canonical_compose",
            "stage_bindings",
            "finland_stage_evidence",
            "wa_ir_stage_attestation",
            "witness_stage_operation",
        ),
        "canonical_host_identity_attested": (
            "prepared_clone_inventory_receipt",
            "bot_fi_inventory_response",
            "webapp_fi_inventory_response",
            "webapp_ir_inventory_response",
            "witness_health",
            "nginx_legacy_normal_receipt",
        ),
        "legacy_rollback_artifact_set_attested": (
            "bot_fi_rollback_attestation",
            "webapp_fi_rollback_attestation",
        ),
        "active_route_generation_set_sha256": (
            "nginx_aggregate",
            "nginx_legacy_normal_receipt",
        ),
    }
    for role in ROLES:
        result[f"{role}_role_material_sha256"] = (
            "prepare_metadata",
            f"{role}_role_material",
            "stage_bindings",
        )
    for kind in IMAGE_KINDS:
        result[f"{kind}_image_config_digest"] = (
            "release_closure",
            f"{kind}_image",
        )
        result[f"{kind}_image_content_identity"] = (
            "release_closure",
            f"{kind}_image",
        )
    role_stage_labels = {
        "bot_fi": ("finland_stage_evidence",),
        "webapp_fi": ("finland_stage_evidence",),
        "webapp_ir": (
            "wa_ir_stage_attestation",
            "wa_ir_stage_binding",
        ),
    }
    for role in DOCKER_ROLES:
        for kind in IMAGE_KINDS:
            result[f"{role}_{kind}_runtime_image_id"] = (
                "stage_bindings",
                "prepared_clone_inventory_receipt",
                f"{role}_inventory_response",
                *role_stage_labels[role],
            )
    if set(result) != set(CLAIMS):
        raise PreFreezeEvidenceError(
            "internal claim provenance mapping is not exact"
        )
    return result


def _role_source_labels(role: str) -> tuple[str, ...]:
    if role == "bot_fi":
        return (
            "finland_stage_evidence",
            "prepared_clone_inventory_receipt",
            "bot_fi_inventory_request",
            "bot_fi_inventory_response",
            "bot_fi_nginx_manifest",
            "nginx_legacy_normal_receipt",
        )
    if role == "webapp_fi":
        return (
            "finland_stage_evidence",
            "prepared_clone_inventory_receipt",
            "webapp_fi_inventory_request",
            "webapp_fi_inventory_response",
            "webapp_fi_nginx_manifest",
            "nginx_legacy_normal_receipt",
        )
    if role == "webapp_ir":
        return (
            "wa_ir_operation_manifest",
            "wa_ir_stage_attestation",
            "wa_ir_stage_binding",
            "prepared_clone_inventory_receipt",
            "webapp_ir_inventory_request",
            "webapp_ir_inventory_response",
        )
    if role == "witness":
        return (
            "witness_health",
            "witness_public_input",
            "witness_stage_operation",
            "witness_stage_binding",
        )
    raise PreFreezeEvidenceError("unknown pre-freeze role")


def _derive_role_source_closures(
    records: Mapping[str, SecureRecord],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for role in ROLES:
        labels = _role_source_labels(role)
        if any(label not in records for label in labels):
            raise PreFreezeEvidenceError(
                f"{role} role source evidence is incomplete"
            )
        rows = [
            {"label": label, "sha256": records[label].sha256}
            for label in labels
        ]
        result[role] = _sha256(_canonical_json(rows))
    return result


def _validate_inputs(
    context: CoordinatorContext,
    paths: EvidencePaths,
    *,
    initial_records: Mapping[str, SecureRecord],
    now: datetime,
) -> ValidatedInputs:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PreFreezeEvidenceError(
            "pre-freeze validation time must be timezone-aware"
        )
    now = now.astimezone(timezone.utc)
    _require_fresh_nginx_interface()
    records = dict(initial_records)
    release_files = _validate_release_files(context, records)
    closure = _validate_release_closure(
        context,
        paths.release_closure,
        records,
    )
    stage_bindings = _validate_stage_bindings(
        context,
        paths.stage_bindings,
        records,
    )
    prepare_metadata = _validate_prepare_metadata(
        context,
        paths.prepare_metadata,
        paths.canonical_compose,
        stage_bindings,
        records,
    )
    _validate_finland_evidence(
        context,
        paths.finland_evidence,
        release_closure_sha256=records["release_closure"].sha256,
        stage_bindings=stage_bindings,
        records=records,
    )
    _validate_wa_ir_evidence(
        context,
        operation_manifest_path=paths.wa_ir_operation_manifest,
        stage_attestation_path=paths.wa_ir_stage_attestation,
        stage_binding_path=paths.wa_ir_stage_binding,
        stage_bindings=stage_bindings,
        closure=closure,
        records=records,
    )
    witness_observed = _validate_witness_evidence(
        context,
        health_path=paths.witness_health,
        public_path=paths.witness_public_input,
        stage_path=paths.witness_stage_operation,
        binding_path=paths.witness_stage_binding,
        stage_bindings=stage_bindings,
        now=now,
        records=records,
    )
    inventory_observed = _validate_inventory_evidence(
        context,
        receipt_path=paths.inventory_receipt,
        output_root=paths.inventory_output_root,
        release_agent_sha256=release_files["inventory_agent"],
        release_contract_worker_sha256={
            "bot_fi": release_files["precommit_worker"],
            "webapp_fi": release_files["precommit_worker"],
            "webapp_ir": release_files["wa_ir_operation"],
        },
        expected_role_manifest_sha256={
            role: prepare_metadata["roles"][role][
                "internal_manifest_sha256"
            ]
            for role in DOCKER_ROLES
        },
        now=now,
        records=records,
    )
    rollback = _validate_rollback_evidence(
        context,
        paths.rollback_attestations,
        records,
    )
    nginx_receipt, _nginx_observed = _validate_nginx_evidence(
        context,
        aggregate_path=paths.nginx_aggregate,
        receipt_path=paths.nginx_legacy_normal_receipt,
        now=now,
        records=records,
    )
    role_observed = {
        **inventory_observed,
        "witness": witness_observed,
    }
    captured_at = max(role_observed.values())
    if any(
        abs(captured_at - observed) > ROLE_MAX_SKEW
        for observed in role_observed.values()
    ):
        raise PreFreezeEvidenceError(
            "four-role observations are outside the allowed capture skew"
        )
    _require_fresh(
        captured_at,
        now=now,
        maximum=SOURCE_MAX_AGE,
        label="pre-freeze role closure",
    )
    identities = [
        (record.identity.device, record.identity.inode)
        for record in records.values()
    ]
    if len(identities) != len(set(identities)):
        raise PreFreezeEvidenceError(
            "two independent evidence inputs share one file identity"
        )
    values = _claim_values(
        context,
        closure=closure,
        rollback=rollback,
        nginx_receipt=nginx_receipt,
    )
    provenance = _claim_provenance()
    if any(
        label not in records
        for labels in provenance.values()
        for label in labels
    ):
        raise PreFreezeEvidenceError(
            "claim provenance references missing collection evidence"
        )
    role_source = _derive_role_source_closures(records)
    _assert_records_unchanged(records)
    return ValidatedInputs(
        context=context,
        records=records,
        values=values,
        role_observed_at={
            role: role_observed[role].isoformat() for role in ROLES
        },
        role_source_closure_sha256=role_source,
        claim_provenance=provenance,
    )


def _persist_digest_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str, str]:
    payload = _canonical_json(dict(document)) + b"\n"
    digest = _sha256(payload)
    return _persist_document(
        directory,
        filename=f"{prefix}-{digest}.json",
        document=document,
    )


def _input_closure_document(
    validated: ValidatedInputs,
) -> dict[str, Any]:
    context = validated.context
    captured_at = max(
        datetime.fromisoformat(value)
        for value in validated.role_observed_at.values()
    ).isoformat()
    source_files = {
        label: _record_reference(record)
        for label, record in sorted(validated.records.items())
    }
    if any(set(row) != SOURCE_FILE_FIELDS for row in source_files.values()):
        raise PreFreezeEvidenceError(
            "input closure source record fields are not exact"
        )
    document = {
        "schema": INPUT_CLOSURE_SCHEMA,
        "status": "validated-local-independent-evidence",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": context.manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "captured_at": captured_at,
        "role_observed_at": {
            role: validated.role_observed_at[role] for role in ROLES
        },
        "source_files": source_files,
        "role_source_closure_sha256": {
            role: validated.role_source_closure_sha256[role]
            for role in ROLES
        },
        "claim_provenance": {
            claim: list(validated.claim_provenance[claim])
            for claim in sorted(CLAIMS)
        },
        "upstream_inventory_collection_performed": True,
        "upstream_inventory_production_contacted": True,
        "collection_performed": False,
        "production_contacted": False,
    }
    if set(document) != INPUT_CLOSURE_FIELDS:
        raise PreFreezeEvidenceError(
            "input closure fields are not exact"
        )
    return document


def _write_input_closure(
    validated: ValidatedInputs,
) -> tuple[dict[str, Any], Path, str]:
    context = validated.context
    document = _input_closure_document(validated)
    path, digest, _publication = _persist_document(
        context.output_root / "inputs",
        filename="input-closure.json",
        document=document,
    )
    observed = _read_private_json(
        path,
        label="persisted pre-freeze input closure",
    )
    if observed.document != document or observed.sha256 != digest:
        raise PreFreezeEvidenceError(
            "pre-freeze input closure readback differs"
        )
    return document, path, digest


def _phase_plan_row(context: CoordinatorContext) -> dict[str, Any]:
    rows = [
        row
        for row in context.plan.get("phases", [])
        if isinstance(row, dict) and row.get("phase") == PHASE
    ]
    if len(rows) != 1:
        raise PreFreezeEvidenceError(
            "controller plan lacks the exact pre-freeze phase"
        )
    row = rows[0]
    if (
        row.get("operation") not in {None, OPERATION}
        or row.get("execution_supported") is not False
        or row.get("required_journal_status")
        != CONTROLLER.PRECOMMIT_JOURNAL_STATUS
        or row.get("business_write_allowed") is not False
        or not isinstance(row.get("commands"), list)
        or {
            command.get("role")
            for command in row["commands"]
            if isinstance(command, dict)
        }
        != set(ROLES)
    ):
        raise PreFreezeEvidenceError(
            "controller pre-freeze plan row differs"
        )
    return row


def _host_request_sha256(
    context: CoordinatorContext,
    *,
    role: str,
    release_host_agent_sha256: str,
) -> str:
    phase = _phase_plan_row(context)
    commands = [
        command
        for command in phase["commands"]
        if command.get("role") == role
    ]
    if len(commands) != 1:
        raise PreFreezeEvidenceError(
            f"controller plan lacks one {role} pre-freeze request"
        )
    command = commands[0]
    argv = command.get("argv")
    release_agent_path = os.fspath(
        Path(context.manifest["deployment"]["shadow_root"])
        / "releases"
        / context.manifest["release_sha"]
        / "scripts"
        / "production_shadow_host_agent.py"
    )
    if (
        not isinstance(argv, list)
        or "--execute" in argv
        or command.get("required") is not True
        or command.get("render_only") is not True
        or command.get("executor_available") is not False
        or command.get("business_write_allowed") is not False
        or command.get("approval_sha256")
        != context.manifest["artifacts"]["cutover_approval_sha256"]
        or argv.count(release_agent_path) != 1
    ):
        raise PreFreezeEvidenceError(
            f"{role} controller validation command differs"
        )
    index = argv.index(release_agent_path)
    try:
        request, execute = HOST_AGENT.parse_request_argv(
            argv[index + 1 :],
            contract=CONTROLLER.host_agent_contract_document(),
            observed_agent_sha256=release_host_agent_sha256,
        )
        request_sha256 = HOST_AGENT.request_sha256(
            request,
            contract=CONTROLLER.host_agent_contract_document(),
            observed_agent_sha256=release_host_agent_sha256,
        )
    except (HOST_AGENT.HostAgentError, SystemExit) as exc:
        raise PreFreezeEvidenceError(
            f"{role} controller host request is invalid"
        ) from exc
    if (
        execute
        or request["operation"] != OPERATION
        or request["role"] != role
        or request["expected_host"]
        != context.manifest["topology"][role]["host"]
        or request["manifest_sha256"] != context.manifest_sha256
    ):
        raise PreFreezeEvidenceError(
            f"{role} controller host request binding differs"
        )
    return request_sha256


def _write_role_validations(
    validated: ValidatedInputs,
) -> tuple[dict[str, Path], dict[str, str]]:
    context = validated.context
    host_agent_record = validated.records["release_host_agent"]
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for role in ROLES:
        request_sha256 = _host_request_sha256(
            context,
            role=role,
            release_host_agent_sha256=host_agent_record.sha256,
        )
        document = {
            "schema": ROLE_VALIDATION_SCHEMA,
            "status": "validated-request",
            "request_sha256": request_sha256,
            "operation": OPERATION,
            "role": role,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "app_release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "approval_sha256": context.manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "expected_host": context.manifest["topology"][role]["host"],
            "observed_host": context.manifest["topology"][role]["host"],
            "required_journal_status": (
                CONTROLLER.PRECOMMIT_JOURNAL_STATUS
            ),
            "business_write_policy": "forbid",
            "agent_artifact_sha256": host_agent_record.sha256,
            "host_agent_contract_sha256": context.manifest["artifacts"][
                "host_agent_contract_sha256"
            ],
            "transport": context.manifest["topology"][role]["transport"],
            "observed_at": validated.role_observed_at[role],
            "host_identity_observed": True,
            "execution_supported": False,
            "production_contacted": False,
        }
        if set(document) != VERIFY.HOST_AGENT_VALIDATION_FIELDS:
            raise PreFreezeEvidenceError(
                f"{role} normalized validation fields are not exact"
            )
        path, digest, _publication = _persist_digest_document(
            context.output_root / "role-validations",
            prefix=role,
            document=document,
        )
        paths[role] = path
        digests[role] = digest
    try:
        _requests, source_hashes, _observed = (
            VERIFY._read_role_validation_records(  # noqa: SLF001
                [f"{role}={paths[role]}" for role in ROLES],
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
            )
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PreFreezeEvidenceError(
            "normalized four-role validation set is invalid"
        ) from exc
    if source_hashes != digests:
        raise PreFreezeEvidenceError(
            "normalized role validation readback differs"
        )
    return paths, digests


def _write_claim_sources(
    validated: ValidatedInputs,
    *,
    observed_at: str,
) -> tuple[dict[str, Path], dict[str, str]]:
    context = validated.context
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
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
            "value": validated.values[claim],
            "observed_at": observed_at,
            "status": "observed",
        }
        if set(document) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise PreFreezeEvidenceError(
                f"{claim} normalized source fields are not exact"
            )
        path, digest, _publication = _persist_digest_document(
            context.output_root / "claim-sources",
            prefix=claim,
            document=document,
        )
        paths[claim] = path
        digests[claim] = digest
    try:
        dynamic, source_hashes = (
            VERIFY._read_claim_source_records(  # noqa: SLF001
                [f"{claim}={paths[claim]}" for claim in CLAIMS],
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                now=datetime.fromisoformat(observed_at),
            )
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PreFreezeEvidenceError(
            "normalized claim source set is invalid"
        ) from exc
    expected_dynamic = {
        claim: validated.values[claim]
        for claim, rule in VERIFY.PHASE_CLAIM_RULES[PHASE].items()
        if rule.kind != "exact"
    }
    if dynamic != expected_dynamic or source_hashes != digests:
        raise PreFreezeEvidenceError(
            "normalized claim source readback differs"
        )
    return paths, digests


def _build_phase_evidence(
    validated: ValidatedInputs,
    *,
    captured_at: str,
    role_paths: Mapping[str, Path],
    role_digests: Mapping[str, str],
    claim_paths: Mapping[str, Path],
    claim_digests: Mapping[str, str],
    now: datetime,
) -> tuple[dict[str, Any], Path, str]:
    context = validated.context
    try:
        request_hashes, observed_role_digests, observed_at = (
            VERIFY._read_role_validation_records(  # noqa: SLF001
                [f"{role}={role_paths[role]}" for role in ROLES],
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
            )
        )
        dynamic_values, observed_claim_digests = (
            VERIFY._read_claim_source_records(  # noqa: SLF001
                [
                    f"{claim}={claim_paths[claim]}"
                    for claim in CLAIMS
                ],
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                now=now,
            )
        )
        manifest_artifacts = VERIFY._validate_manifest_artifacts(  # noqa: SLF001
            context.manifest["artifacts"],
            release_sha=context.manifest["release_sha"],
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PreFreezeEvidenceError(
            "normalized verifier inputs failed readback"
        ) from exc
    if (
        observed_role_digests != dict(role_digests)
        or observed_claim_digests != dict(claim_digests)
        or observed_at != dict(validated.role_observed_at)
    ):
        raise PreFreezeEvidenceError(
            "normalized verifier input identity changed"
        )
    prior_rows: list[dict[str, str]] = []
    prior_claims: list[dict[str, Any]] = []
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _sha256(
            _canonical_json(manifest_artifacts)
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claims,
        "dynamic_claim_values": dynamic_values,
        "claim_source_sha256": {
            claim: claim_digests[claim] for claim in sorted(CLAIMS)
        },
        "role_request_sha256": {
            role: request_hashes[role] for role in ROLES
        },
        "role_source_artifact_sha256": {
            role: role_digests[role] for role in ROLES
        },
        "role_observed_at": {
            role: validated.role_observed_at[role] for role in ROLES
        },
    }
    document = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": (
            VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
        ),
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": context.manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "manifest_artifact_bindings": manifest_artifacts,
        "phase": PHASE,
        "operation": OPERATION,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": captured_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _sha256(
            _canonical_json(prior_rows)
        ),
        "prior_claim_bindings": prior_claims,
        "phase_input_closure_sha256": _sha256(
            _canonical_json(phase_input)
        ),
        "role_attestations": [
            {
                "role": role,
                "expected_host": context.manifest["topology"][role]["host"],
                "operation": OPERATION,
                "request_sha256": request_hashes[role],
                "app_release_sha": context.manifest["release_sha"],
                "agent_artifact_sha256": context.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": validated.role_observed_at[role],
                "status": "verified",
                "transport": context.manifest["topology"][role]["transport"],
                "source_artifact_sha256": role_digests[role],
            }
            for role in ROLES
        ],
        "claims": {
            claim: {
                "value": validated.values[claim],
                "source_sha256": claim_digests[claim],
            }
            for claim in CLAIMS
        },
    }
    if set(document) != VERIFY.EVIDENCE_FIELDS:
        raise PreFreezeEvidenceError(
            "pre-freeze phase evidence fields are not exact"
        )
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    path, observed_digest, _publication = _persist_document(
        context.output_root / "phase-evidence",
        filename=f"{PHASE}.{digest}.json",
        document=document,
    )
    if observed_digest != digest:
        raise PreFreezeEvidenceError(
            "pre-freeze phase evidence digest differs"
        )
    try:
        result = VERIFY.verify_phase_evidence(
            document,
            expected_phase=PHASE,
            expected_campaign_id=context.manifest["campaign_id"],
            expected_operation_id=context.manifest["operation_id"],
            expected_release_sha=context.manifest["release_sha"],
            expected_legacy_release_sha=context.manifest[
                "legacy_release_sha"
            ],
            expected_manifest_sha256=context.manifest_sha256,
            expected_plan_sha256=context.plan_sha256,
            expected_approval_sha256=context.manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            expected_phase_evidence_schema_sha256=context.manifest[
                "artifacts"
            ]["phase_evidence_schema_sha256"],
            expected_manifest_artifacts=context.manifest["artifacts"],
            expected_role_request_sha256=request_hashes,
            expected_role_source_artifact_sha256=dict(role_digests),
            expected_role_observed_at=dict(validated.role_observed_at),
            expected_dynamic_claim_values=dynamic_values,
            expected_claim_source_sha256=dict(claim_digests),
            expected_prior_phase_evidence_sha256={},
            prior_phase_evidence_records={},
            now=now,
            evidence_file_sha256=digest,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PreFreezeEvidenceError(
            "locally derived pre-freeze evidence failed semantic verification"
        ) from exc
    if (
        result["status"] != "verified"
        or result["evidence_sha256"] != digest
        or result["verified_roles"] != list(ROLES)
        or result["verified_claim_count"] != len(CLAIMS)
        or result["production_contacted"] is not False
    ):
        raise PreFreezeEvidenceError(
            "local pre-freeze verification result differs"
        )
    return document, path, digest


def _verify_runtime_authorization(context: CoordinatorContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            context.manifest,
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreFreezeEvidenceError(
            "production cutover authorization is invalid or expired"
        ) from exc


def _check_authority(
    authority_check: AuthorityCheck | None,
    checkpoint: str,
) -> None:
    if authority_check is None:
        return
    try:
        authority_check(checkpoint)
    except BaseException as exc:
        raise LiveControllerAuthorityLost(
            f"live controller authority was lost at {checkpoint}"
        ) from exc


@contextmanager
def _signal_reconciliation_scope() -> Iterator[None]:
    global _SIGNAL_DEFER_DEPTH, _DEFERRED_SIGNAL
    entry_exception = sys.exception()
    body_failed = False
    _SIGNAL_DEFER_DEPTH += 1
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        _SIGNAL_DEFER_DEPTH -= 1
        if (
            _SIGNAL_DEFER_DEPTH == 0
            and _DEFERRED_SIGNAL is not None
            and entry_exception is None
            and not body_failed
        ):
            reason = _DEFERRED_SIGNAL
            _DEFERRED_SIGNAL = None
            raise LiveControllerAuthorityLost(reason)


@contextmanager
def _signal_cancellation_guard() -> Iterator[None]:
    global _SIGNAL_GUARD_ACTIVE, _SIGNAL_SEEN, _DEFERRED_SIGNAL
    if threading.current_thread() is not threading.main_thread():
        raise PreFreezeEvidenceError(
            "pre-freeze apply must run in the main thread"
        )
    if _SIGNAL_GUARD_ACTIVE:
        raise PreFreezeEvidenceError(
            "pre-freeze signal guard cannot be nested"
        )
    handled = (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGUSR1,
    )
    previous = {signum: signal.getsignal(signum) for signum in handled}

    def cancel(signum: int, _frame: Any) -> None:
        global _SIGNAL_SEEN, _DEFERRED_SIGNAL
        if _SIGNAL_SEEN:
            return
        _SIGNAL_SEEN = True
        reason = (
            "pre-freeze controller received "
            f"{signal.Signals(signum).name}"
        )
        if _SIGNAL_DEFER_DEPTH:
            _DEFERRED_SIGNAL = reason
            return
        raise LiveControllerAuthorityLost(reason)

    _SIGNAL_GUARD_ACTIVE = True
    _SIGNAL_SEEN = False
    _DEFERRED_SIGNAL = None
    installed: list[signal.Signals] = []
    try:
        for signum in handled:
            signal.signal(signum, cancel)
            installed.append(signum)
        yield
        if _DEFERRED_SIGNAL is not None:
            raise LiveControllerAuthorityLost(_DEFERRED_SIGNAL)
    finally:
        original = sys.exception()
        restoration_errors: list[BaseException] = []
        try:
            for signum in reversed(installed):
                try:
                    signal.signal(signum, previous[signum])
                except BaseException as exc:
                    restoration_errors.append(exc)
        finally:
            _SIGNAL_GUARD_ACTIVE = False
            _SIGNAL_SEEN = False
            _DEFERRED_SIGNAL = None
        if restoration_errors:
            if original is not None:
                for error in restoration_errors:
                    try:
                        original.add_note(
                            "signal handler restoration also failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    except (AttributeError, TypeError):
                        pass
            else:
                raise restoration_errors[0]


def _assert_no_local_pipe_writer(
    control_fd: int,
    metadata: os.stat_result,
) -> None:
    try:
        descriptor_names = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise PreFreezeEvidenceError(
            "cannot prove controller liveness pipe ownership"
        ) from exc
    for name in descriptor_names:
        try:
            descriptor = int(name)
        except ValueError:
            continue
        if descriptor == control_fd:
            continue
        try:
            candidate = os.fstat(descriptor)
            if (
                candidate.st_dev != metadata.st_dev
                or candidate.st_ino != metadata.st_ino
            ):
                continue
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        except OSError as exc:
            if exc.errno in {errno.EBADF, errno.ENOENT}:
                continue
            raise PreFreezeEvidenceError(
                "cannot prove controller liveness pipe ownership"
            ) from exc
        if flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}:
            raise PreFreezeEvidenceError(
                "controller process retains a liveness pipe writer"
            )


class ControllerLiveness:
    """Treat anonymous read-pipe EOF or data as immediate authority loss."""

    def __init__(self, control_fd: int):
        if type(control_fd) is not int or control_fd < 0:
            raise PreFreezeEvidenceError(
                "apply requires a controller liveness descriptor"
            )
        try:
            metadata = os.fstat(control_fd)
            flags = fcntl.fcntl(control_fd, fcntl.F_GETFL)
            target = os.readlink(f"/proc/self/fd/{control_fd}")
        except OSError as exc:
            raise PreFreezeEvidenceError(
                "controller liveness pipe is unavailable"
            ) from exc
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or target != f"pipe:[{metadata.st_ino}]"
        ):
            raise PreFreezeEvidenceError(
                "controller liveness descriptor is not an anonymous read pipe"
            )
        _assert_no_local_pipe_writer(control_fd, metadata)
        try:
            self._fd = os.dup(control_fd)
            os.set_inheritable(self._fd, False)
            os.set_blocking(self._fd, False)
        except OSError as exc:
            raise PreFreezeEvidenceError(
                "controller liveness pipe cannot be duplicated"
            ) from exc
        self._stopping = threading.Event()
        self._cancelled = threading.Event()
        self._reason: str | None = None
        self._wake_sent = False
        self._thread = threading.Thread(
            target=self._watch,
            name="pre-freeze-controller-liveness",
            daemon=True,
        )

    def _cancel(self, reason: str) -> None:
        if self._wake_sent or self._stopping.is_set():
            return
        self._wake_sent = True
        self._reason = reason
        self._cancelled.set()
        os.kill(os.getpid(), signal.SIGUSR1)

    def _probe(self) -> None:
        try:
            readable, _, _ = select.select([self._fd], [], [], 0)
            if not readable:
                return
            payload = os.read(self._fd, 1)
        except BlockingIOError:
            return
        except (OSError, ValueError) as exc:
            raise LiveControllerAuthorityLost(
                "controller liveness pipe failed"
            ) from exc
        raise LiveControllerAuthorityLost(
            "controller liveness pipe carried forbidden data"
            if payload
            else "controller liveness pipe reached EOF"
        )

    def _watch(self) -> None:
        try:
            while not self._stopping.is_set():
                readable, _, _ = select.select([self._fd], [], [], 0.05)
                if not readable:
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                self._cancel(
                    "controller liveness pipe carried forbidden data"
                    if payload
                    else "controller liveness pipe reached EOF"
                )
                return
        except (OSError, ValueError):
            if not self._stopping.is_set():
                self._cancel("controller liveness pipe failed")

    def __enter__(self) -> "ControllerLiveness":
        try:
            self._probe()
            self._thread.start()
            self.check()
            return self
        except BaseException as original:
            self._stopping.set()
            cleanup_errors: list[BaseException] = []
            try:
                os.close(self._fd)
            except BaseException as exc:
                cleanup_errors.append(exc)
            if self._thread.ident is not None:
                self._thread.join(timeout=1.0)
                if self._thread.is_alive():
                    cleanup_errors.append(
                        PreFreezeEvidenceError(
                            "controller liveness watcher did not stop"
                        )
                    )
            for error in cleanup_errors:
                try:
                    original.add_note(
                        "controller liveness startup cleanup also failed: "
                        f"{type(error).__name__}: {error}"
                    )
                except (AttributeError, TypeError):
                    pass
            raise

    def check(self) -> None:
        if self._cancelled.is_set():
            raise LiveControllerAuthorityLost(
                self._reason or "controller liveness was lost"
            )

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        original = sys.exception()
        errors: list[BaseException] = []
        self._stopping.set()
        try:
            os.close(self._fd)
        except BaseException as exc:
            errors.append(exc)
        if self._thread.ident is not None:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                errors.append(
                    PreFreezeEvidenceError(
                        "controller liveness watcher did not stop"
                    )
                )
        if errors:
            if original is not None:
                for error in errors:
                    try:
                        original.add_note(
                            "controller liveness cleanup also failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    except (AttributeError, TypeError):
                        pass
            else:
                raise errors[0]


def _transition_arguments(
    transition: ControllerTransition,
) -> list[str]:
    if (
        transition.action not in {"begin-phase", "complete-phase"}
        or transition.phase != PHASE
    ):
        raise PreFreezeEvidenceError(
            "public controller transition is outside pre_freeze_evidence"
        )
    argv = [
        CONTROLLER.CONTROLLER_PATH,
        "--manifest",
        os.fspath(transition.manifest_path),
        "--action",
        transition.action,
        "--phase",
        PHASE,
        "--approval",
        os.fspath(transition.approval_path),
        "--approval-policy",
        os.fspath(transition.approval_policy_path),
    ]
    if transition.action == "complete-phase":
        if (
            transition.evidence_path is None
            or len(transition.role_validation) != len(ROLES)
            or len(transition.claim_source) != len(CLAIMS)
            or transition.prior_phase_evidence
        ):
            raise PreFreezeEvidenceError(
                "complete-phase controller inputs are not exact"
            )
        evidence_path = _absolute_path(
            transition.evidence_path,
            label="complete-phase evidence",
        )
        _parse_path_mapping(
            transition.role_validation,
            expected=ROLES,
            label="complete-phase role validation",
        )
        _parse_path_mapping(
            transition.claim_source,
            expected=CLAIMS,
            label="complete-phase claim source",
        )
        argv.extend(("--evidence", os.fspath(evidence_path)))
        for value in transition.role_validation:
            argv.extend(("--role-validation", value))
        for value in transition.claim_source:
            argv.extend(("--claim-source", value))
    elif (
        transition.evidence_path is not None
        or transition.role_validation
        or transition.claim_source
        or transition.prior_phase_evidence
    ):
        raise PreFreezeEvidenceError(
            "begin-phase unexpectedly carries evidence inputs"
        )
    argv.extend(
        (
            "--apply",
            "--confirm",
            CONTROLLER.APPLY_CONFIRMATION,
        )
    )
    return argv


def invoke_public_controller(
    transition: ControllerTransition,
) -> dict[str, Any]:
    """Run the installed public controller under the hardened local runner."""
    argv = _transition_arguments(transition)
    try:
        result = INVENTORY._bounded_command(  # noqa: SLF001
            argv,
            timeout=CONTROLLER_TIMEOUT_SECONDS,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdout_limit=MAX_CONTROLLER_STDOUT_BYTES,
            stderr_limit=MAX_CONTROLLER_STDERR_BYTES,
        )
    except BaseException as exc:
        raise PreFreezeEvidenceError(
            "public controller process was interrupted or failed closed"
        ) from exc
    if result.returncode != 0 or result.stderr or not result.stdout:
        raise PreFreezeEvidenceError(
            "public controller rejected the transition"
        )
    try:
        document = json.loads(
            result.stdout.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant {token}")
            ),
        )
    except PreFreezeEvidenceError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PreFreezeEvidenceError(
            "public controller returned invalid strict JSON"
        ) from exc
    if not isinstance(document, dict):
        raise PreFreezeEvidenceError(
            "public controller result is not an object"
        )
    return document


def _invoke_controller_transition(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    action: str,
    evidence_path: Path | None = None,
    role_validation: Mapping[str, Path] | None = None,
    claim_source: Mapping[str, Path] | None = None,
    authority_check: AuthorityCheck | None = None,
) -> dict[str, Any]:
    _verify_runtime_authorization(context)
    _check_authority(authority_check, f"before-{action}")
    transition = ControllerTransition(
        action=action,
        phase=PHASE,
        manifest_path=context.manifest_path,
        approval_path=context.approval_path,
        approval_policy_path=context.approval_policy_path,
        evidence_path=evidence_path,
        role_validation=(
            tuple(
                f"{role}={role_validation[role]}" for role in ROLES
            )
            if role_validation is not None
            else ()
        ),
        claim_source=(
            tuple(
                f"{claim}={claim_source[claim]}" for claim in CLAIMS
            )
            if claim_source is not None
            else ()
        ),
        prior_phase_evidence=(),
    )
    try:
        result = callback(transition)
    except BaseException as exc:
        raise PreFreezeEvidenceError(
            f"public controller {action} transition failed closed"
        ) from exc
    _check_authority(authority_check, f"after-{action}")
    _verify_runtime_authorization(context)
    if (
        not isinstance(result, Mapping)
        or set(result)
        != {"status", "action", "journal", "production_contacted"}
        or result["action"] != action
        or result["production_contacted"] is not False
        or not isinstance(result["journal"], Mapping)
    ):
        raise PreFreezeEvidenceError(
            f"public controller {action} result differs"
        )
    return dict(result)


def _begin_phase(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    authority_check: AuthorityCheck | None,
) -> dict[str, Any]:
    journal = _read_journal(context)
    if PHASE in journal["completed_phases"]:
        return journal
    if journal["status"] == "phase_started":
        return journal
    transition_error: BaseException | None = None
    with _signal_reconciliation_scope():
        try:
            _invoke_controller_transition(
                context,
                callback=callback,
                action="begin-phase",
                authority_check=authority_check,
            )
        except BaseException as exc:
            transition_error = exc
        after = _read_journal(context)
        if transition_error is not None:
            raise transition_error
    if (
        after["status"] != "phase_started"
        or after["started_phase"] != PHASE
        or after["completed_phases"] != []
    ):
        raise PreFreezeEvidenceError(
            "public controller did not durably start pre_freeze_evidence"
        )
    return after


def _verification_receipt_path(
    context: CoordinatorContext,
    receipt_sha256: str,
) -> Path:
    return (
        Path(context.manifest["deployment"]["controller_evidence_root"])
        / "verification"
        / f"{PHASE}.{receipt_sha256}.json"
    )


def _validate_verification_receipt(
    context: CoordinatorContext,
    *,
    journal: Mapping[str, Any],
    evidence_sha256: str,
) -> tuple[Path, str]:
    receipt_sha256 = _nonzero_sha256(
        journal.get("phase_verification_sha256", {}).get(PHASE),
        label="pre-freeze phase verification receipt",
    )
    path = _verification_receipt_path(context, receipt_sha256)
    record = _read_private_strict_json(
        path,
        label="pre-freeze phase verification receipt",
    )
    if record.sha256 != receipt_sha256:
        raise PreFreezeEvidenceError(
            "phase verification receipt digest differs"
        )
    try:
        token, expected_payload = (
            CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
                _record_document(
                    record,
                    label="pre-freeze phase verification receipt",
                ),
                phase=PHASE,
                manifest=context.manifest,
                manifest_sha256=context.manifest_sha256,
                plan_sha256=context.plan_sha256,
            )
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PreFreezeEvidenceError(
            "phase verification receipt is invalid"
        ) from exc
    if (
        record.payload != expected_payload
        or token.evidence_sha256 != evidence_sha256
        or token.receipt_sha256 != receipt_sha256
    ):
        raise PreFreezeEvidenceError(
            "phase verification receipt differs from journal evidence"
        )
    return path, receipt_sha256


def _complete_phase(
    context: CoordinatorContext,
    *,
    callback: ControllerCallback,
    evidence_path: Path,
    evidence_sha256: str,
    role_validation: Mapping[str, Path],
    claim_source: Mapping[str, Path],
    authority_check: AuthorityCheck | None,
) -> tuple[dict[str, Any], Path, str]:
    before = _read_journal(context)
    if PHASE in before["completed_phases"]:
        if before["phase_evidence_sha256"][PHASE] != evidence_sha256:
            raise PreFreezeEvidenceError(
                "completed pre-freeze evidence differs"
            )
        receipt_path, receipt_sha256 = _validate_verification_receipt(
            context,
            journal=before,
            evidence_sha256=evidence_sha256,
        )
        return before, receipt_path, receipt_sha256
    if (
        before["status"] != "phase_started"
        or before["started_phase"] != PHASE
    ):
        raise PreFreezeEvidenceError(
            "pre-freeze completion has no durable matching start"
        )
    transition_error: BaseException | None = None
    with _signal_reconciliation_scope():
        try:
            _invoke_controller_transition(
                context,
                callback=callback,
                action="complete-phase",
                evidence_path=evidence_path,
                role_validation=role_validation,
                claim_source=claim_source,
                authority_check=authority_check,
            )
        except BaseException as exc:
            transition_error = exc
        after = _read_journal(context)
        if (
            after["completed_phases"] != [PHASE]
            or after["phase_evidence_sha256"].get(PHASE) != evidence_sha256
            or PHASE not in after["phase_verification_sha256"]
            or after["started_phase"] is not None
        ):
            if transition_error is not None:
                raise transition_error
            raise PreFreezeEvidenceError(
                "public controller did not persist exact pre-freeze verification"
            )
        receipt_path, receipt_sha256 = _validate_verification_receipt(
            context,
            journal=after,
            evidence_sha256=evidence_sha256,
        )
        if transition_error is not None:
            raise transition_error
    return after, receipt_path, receipt_sha256


def _write_derivation(
    validated: ValidatedInputs,
    *,
    input_closure_path: Path,
    input_closure_sha256: str,
    role_paths: Mapping[str, Path],
    role_digests: Mapping[str, str],
    claim_paths: Mapping[str, Path],
    claim_digests: Mapping[str, str],
    evidence_path: Path,
    evidence_sha256: str,
) -> tuple[Path, str]:
    context = validated.context
    document = {
        "schema": DERIVATION_SCHEMA,
        "status": "derived-without-caller-truth",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "input_closure_path": os.fspath(input_closure_path),
        "input_closure_sha256": input_closure_sha256,
        "role_validation": {
            role: {
                "path": os.fspath(role_paths[role]),
                "sha256": role_digests[role],
            }
            for role in ROLES
        },
        "claim_sources": {
            claim: {
                "path": os.fspath(claim_paths[claim]),
                "sha256": claim_digests[claim],
            }
            for claim in sorted(CLAIMS)
        },
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "caller_truth_values_accepted": False,
        "upstream_inventory_collection_performed": True,
        "upstream_inventory_production_contacted": True,
        "collection_performed": False,
        "production_contacted": False,
    }
    if (
        set(document) != DERIVATION_FIELDS
        or any(
            set(row) != NORMALIZED_REFERENCE_FIELDS
            for row in document["role_validation"].values()
        )
        or any(
            set(row) != NORMALIZED_REFERENCE_FIELDS
            for row in document["claim_sources"].values()
        )
    ):
        raise PreFreezeEvidenceError(
            "pre-freeze derivation fields are not exact"
        )
    path, digest, _publication = _persist_document(
        context.output_root / "derivations",
        filename="derivation.json",
        document=document,
    )
    return path, digest


def _coordinator_plan(
    validated: ValidatedInputs,
) -> dict[str, Any]:
    context = validated.context
    input_document = _input_closure_document(validated)
    input_sha256 = _sha256(_canonical_json(input_document) + b"\n")
    basis = {
        "schema": "production-shadow-pre-freeze-coordinator-plan-v1",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "phase": PHASE,
        "operation": OPERATION,
        "roles": list(ROLES),
        "claim_count": len(CLAIMS),
        "input_closure_sha256": input_sha256,
        "collection_dependencies": list(COLLECTION_DEPENDENCIES),
        "fresh_prepared_inventory_receipt_required": True,
        "upstream_inventory_collection_performed": True,
        "upstream_inventory_production_contacted": True,
        "collection_performed": False,
        "host_contact_performed": False,
        "docker_contact_performed": False,
        "object_storage_contact_performed": False,
        "caller_truth_values_accepted": False,
        "public_controller_transition_required": True,
        "controller_liveness_pipe_required": True,
    }
    plan_sha256 = _sha256(_canonical_json(basis))
    confirmation = (
        "execute-production-shadow-pre-freeze-evidence:"
        f"{context.manifest['operation_id']}:{plan_sha256}:{input_sha256}"
    )
    return {
        **basis,
        "coordinator_plan_sha256": plan_sha256,
        "required_confirmation": confirmation,
        "status": "planned",
        "journal_mutated": False,
        "production_contacted": False,
    }


def plan_pre_freeze_evidence(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    evidence_paths: EvidencePaths,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate every local input and render a mutation-free exact plan."""
    context, initial_records = _load_context(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
    )
    validated = _validate_inputs(
        context,
        evidence_paths,
        initial_records=initial_records,
        now=now or datetime.now(timezone.utc),
    )
    return _coordinator_plan(validated)


def _execute_pre_freeze_evidence_with_authority(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    evidence_paths: EvidencePaths,
    confirm: str,
    controller_callback: ControllerCallback | None = None,
    authority_check: AuthorityCheck,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Close exactly the first public journal phase from local evidence."""
    callback = controller_callback or invoke_public_controller
    if not callable(callback):
        raise PreFreezeEvidenceError(
            "public controller callback is not callable"
        )
    if not callable(authority_check):
        raise PreFreezeEvidenceError(
            "live controller authority check is not callable"
        )
    observed_now = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    context, initial_records = _load_context(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
    )
    validated = _validate_inputs(
        context,
        evidence_paths,
        initial_records=initial_records,
        now=observed_now,
    )
    plan = _coordinator_plan(validated)
    if confirm != plan["required_confirmation"]:
        raise PreFreezeEvidenceError(
            "pre-freeze execution requires the exact digest-bound confirmation"
        )
    _check_authority(authority_check, "before-output-publication")
    _verify_runtime_authorization(context)
    _ensure_private_directory(context.output_root)
    verification_root = (
        Path(context.manifest["deployment"]["controller_evidence_root"])
        / "verification"
    )
    _ensure_private_directory(verification_root)
    input_document, input_path, input_sha256 = _write_input_closure(
        validated
    )
    if input_sha256 != plan["input_closure_sha256"]:
        raise PreFreezeEvidenceError(
            "persisted input closure differs from the confirmed plan"
        )
    _assert_records_unchanged(validated.records)
    _begin_phase(
        context,
        callback=callback,
        authority_check=authority_check,
    )
    role_paths, role_digests = _write_role_validations(validated)
    captured_at = input_document["captured_at"]
    claim_paths, claim_digests = _write_claim_sources(
        validated,
        observed_at=captured_at,
    )
    _evidence, evidence_path, evidence_sha256 = _build_phase_evidence(
        validated,
        captured_at=captured_at,
        role_paths=role_paths,
        role_digests=role_digests,
        claim_paths=claim_paths,
        claim_digests=claim_digests,
        now=observed_now,
    )
    derivation_path, derivation_sha256 = _write_derivation(
        validated,
        input_closure_path=input_path,
        input_closure_sha256=input_sha256,
        role_paths=role_paths,
        role_digests=role_digests,
        claim_paths=claim_paths,
        claim_digests=claim_digests,
        evidence_path=evidence_path,
        evidence_sha256=evidence_sha256,
    )
    _assert_records_unchanged(validated.records)
    _verify_runtime_authorization(context)
    _check_authority(authority_check, "before-public-completion")
    final_journal, receipt_path, receipt_sha256 = _complete_phase(
        context,
        callback=callback,
        evidence_path=evidence_path,
        evidence_sha256=evidence_sha256,
        role_validation=role_paths,
        claim_source=claim_paths,
        authority_check=authority_check,
    )
    _assert_records_unchanged(validated.records)
    final_derivation = _read_private_json(
        derivation_path,
        label="final pre-freeze derivation readback",
    )
    final_evidence = _read_private_json(
        evidence_path,
        label="final pre-freeze phase evidence readback",
    )
    if (
        final_derivation.sha256 != derivation_sha256
        or final_evidence.sha256 != evidence_sha256
        or final_journal["phase_evidence_sha256"][PHASE]
        != evidence_sha256
        or final_journal["phase_verification_sha256"][PHASE]
        != receipt_sha256
    ):
        raise PreFreezeEvidenceError(
            "final pre-freeze journal or evidence readback differs"
        )
    return {
        "schema": RESULT_SCHEMA,
        "status": (
            "already-complete"
            if context.journal["completed_phases"] == [PHASE]
            else "complete"
        ),
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "input_closure_path": os.fspath(input_path),
        "input_closure_sha256": input_sha256,
        "derivation_path": os.fspath(derivation_path),
        "derivation_sha256": derivation_sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "phase_verification_path": os.fspath(receipt_path),
        "phase_verification_sha256": receipt_sha256,
        "verified_roles": list(ROLES),
        "verified_claim_count": len(CLAIMS),
        "caller_truth_values_accepted": False,
        "upstream_inventory_collection_performed": True,
        "upstream_inventory_production_contacted": True,
        "collection_performed": False,
        "journal_mutated": (
            context.journal["completed_phases"] != [PHASE]
        ),
        "production_contacted": False,
    }


def execute_pre_freeze_evidence(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    evidence_paths: EvidencePaths,
    confirm: str,
    controller_liveness_fd: int | None = None,
    controller_callback: ControllerCallback | None = None,
    authority_check: AuthorityCheck | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Close the phase while an anonymous controller pipe stays live."""
    if controller_liveness_fd is None:
        raise PreFreezeEvidenceError(
            "apply requires an anonymous controller-liveness pipe"
        )
    if threading.current_thread() is not threading.main_thread():
        raise PreFreezeEvidenceError(
            "pre-freeze apply must run in the main thread"
        )
    if authority_check is not None and not callable(authority_check):
        raise PreFreezeEvidenceError(
            "supplemental authority check is not callable"
        )
    with _signal_cancellation_guard():
        with ControllerLiveness(controller_liveness_fd) as liveness:

            def combined_authority_check(checkpoint: str) -> None:
                liveness.check()
                if authority_check is not None:
                    authority_check(checkpoint)
                liveness.check()

            return _execute_pre_freeze_evidence_with_authority(
                manifest_path=manifest_path,
                approval_path=approval_path,
                approval_policy_path=approval_policy_path,
                evidence_paths=evidence_paths,
                confirm=confirm,
                controller_callback=controller_callback,
                authority_check=combined_authority_check,
                now=now,
            )


def _parse_path_mapping(
    values: Sequence[str],
    *,
    expected: Sequence[str],
    label: str,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for raw in values:
        key, separator, path = str(raw).partition("=")
        if (
            not separator
            or not key
            or not path
            or key in result
        ):
            raise PreFreezeEvidenceError(f"{label} mapping is invalid")
        result[key] = _absolute_path(path, label=f"{label} {key}")
    if set(result) != set(expected):
        raise PreFreezeEvidenceError(f"{label} mapping is not exact")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--release-closure", type=Path, required=True)
    parser.add_argument("--prepare-metadata", type=Path, required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--finland-evidence", type=Path, required=True)
    parser.add_argument(
        "--wa-ir-operation-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--wa-ir-stage-attestation",
        type=Path,
        required=True,
    )
    parser.add_argument("--wa-ir-stage-binding", type=Path, required=True)
    parser.add_argument("--witness-health", type=Path, required=True)
    parser.add_argument("--witness-public-input", type=Path, required=True)
    parser.add_argument("--witness-stage-operation", type=Path, required=True)
    parser.add_argument("--witness-stage-binding", type=Path, required=True)
    parser.add_argument("--stage-bindings", type=Path, required=True)
    parser.add_argument("--nginx-aggregate", type=Path, required=True)
    parser.add_argument(
        "--nginx-legacy-normal-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument("--inventory-receipt", type=Path, required=True)
    parser.add_argument("--inventory-output-root", type=Path, required=True)
    parser.add_argument(
        "--rollback-attestation",
        action="append",
        default=[],
        metavar="ROLE=/ABS/PATH",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--controller-liveness-fd", type=int)
    return parser


def _evidence_paths(args: argparse.Namespace) -> EvidencePaths:
    return EvidencePaths(
        release_closure=args.release_closure,
        prepare_metadata=args.prepare_metadata,
        canonical_compose=args.canonical_compose,
        finland_evidence=args.finland_evidence,
        wa_ir_operation_manifest=args.wa_ir_operation_manifest,
        wa_ir_stage_attestation=args.wa_ir_stage_attestation,
        wa_ir_stage_binding=args.wa_ir_stage_binding,
        witness_health=args.witness_health,
        witness_public_input=args.witness_public_input,
        witness_stage_operation=args.witness_stage_operation,
        witness_stage_binding=args.witness_stage_binding,
        stage_bindings=args.stage_bindings,
        nginx_aggregate=args.nginx_aggregate,
        nginx_legacy_normal_receipt=args.nginx_legacy_normal_receipt,
        inventory_receipt=_absolute_path(
            args.inventory_receipt,
            label="inventory receipt",
        ),
        inventory_output_root=_absolute_path(
            args.inventory_output_root,
            label="inventory output root",
        ),
        rollback_attestations=_parse_path_mapping(
            args.rollback_attestation,
            expected=ROLLBACK_ROLES,
            label="rollback attestation",
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        paths = _evidence_paths(args)
        if args.apply:
            if not isinstance(args.confirm, str):
                raise PreFreezeEvidenceError(
                    "--apply requires --confirm with the planned value"
                )
            result = execute_pre_freeze_evidence(
                manifest_path=args.manifest,
                approval_path=args.approval,
                approval_policy_path=args.approval_policy,
                evidence_paths=paths,
                confirm=args.confirm,
                controller_liveness_fd=args.controller_liveness_fd,
            )
        else:
            if (
                args.confirm is not None
                or args.controller_liveness_fd is not None
            ):
                raise PreFreezeEvidenceError(
                    "plan-only mode does not accept apply authority"
                )
            result = plan_pre_freeze_evidence(
                manifest_path=args.manifest,
                approval_path=args.approval,
                approval_policy_path=args.approval_policy,
                evidence_paths=paths,
            )
        print(_canonical_json(result).decode("ascii"))
        return 0
    except (
        PreFreezeEvidenceError,
        CONTROLLER.CutoverContractError,
        VERIFY.PhaseEvidenceError,
        INVENTORY.GlobalDockerInventoryError,
        NGINX.NginxCoordinatorError,
        FINLAND.FinlandArtifactOrchestratorError,
        WA_ORCHESTRATOR.ProductionOrchestratorError,
        WA_OPERATION.ProductionOperationError,
        SecureFileError,
    ) as exc:
        print(
            _canonical_json(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "collection_performed": False,
                    "journal_mutated": None if args.apply else False,
                    "reconciliation_required": bool(args.apply),
                    "production_contacted": False,
                }
            ).decode("ascii")
        )
        return 2
    except Exception:
        print(
            _canonical_json(
                {
                    "status": "blocked",
                    "error": "pre-freeze evidence coordinator failed closed",
                    "error_class": "PreFreezeEvidenceError",
                    "collection_performed": False,
                    "journal_mutated": None if args.apply else False,
                    "reconciliation_required": bool(args.apply),
                    "production_contacted": False,
                }
            ).decode("ascii")
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
