#!/usr/bin/env python3
"""Close the pristine-shadow-Redis phase from one fresh stopped inventory.

This bridge is intentionally local.  Its context-only begin mode durably starts
the phase before any receipt is accepted.  A separate, already-authorized
collector then publishes the exact seven-file prepared-clone inventory package.
The completion mode reloads that package through its no-follow, root-only
loader, derives all four public claims from the immutable source bytes and the
prior frozen snapshot evidence, and advances the public cutover journal only
after the release-bound verifier accepts the persisted evidence.

No function in this module contacts a production host or mutates Redis.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_new_bytes,
)
from scripts import orchestrate_production_shadow_prepared_clone_inventory as PREPARED  # noqa: E402
from scripts import orchestrate_production_shadow_freeze_snapshot_phases as FROZEN_PHASE  # noqa: E402
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import production_shadow_global_docker_inventory_agent as INVENTORY  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASE = "pristine_shadow_redis"
OPERATION = "verify-pristine-empty-shadow-redis-targets"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
CLAIMS = (
    "redis_target_count",
    "unsafe_redis_path_count",
    "nonempty_redis_target_count",
    "legacy_redis_restore_byte_count",
)
PRIOR_PHASE = "final_snapshot_hashes"
OUTPUT_SUBDIRECTORY = PHASE

PLAN_SCHEMA = "production-shadow-pristine-redis-phase-plan-v1"
REQUEST_SCHEMA = "production-shadow-pristine-redis-phase-request-v1"
BEGIN_PLAN_SCHEMA = (
    "production-shadow-pristine-redis-capture-begin-plan-v1"
)
BEGIN_REQUEST_SCHEMA = (
    "production-shadow-pristine-redis-capture-begin-request-v1"
)
CLOSURE_SCHEMA = "production-shadow-pristine-redis-closure-v1"
ROLE_SOURCE_SCHEMA = "production-shadow-pristine-redis-role-source-v1"
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"
PUBLICATION_SCHEMA = "production-shadow-pristine-redis-publication-v1"
PUBLICATION_INDEX_SCHEMA = (
    "production-shadow-pristine-redis-publication-index-v1"
)
RESULT_SCHEMA = "production-shadow-pristine-redis-result-v1"

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_path",
        "manifest_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "prior_phase_evidence",
        "prepared_inventory_receipt",
        "final_snapshot_request",
        "final_snapshot_aggregate",
        "constraints",
    }
)
BEGIN_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_path",
        "manifest_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "prior_phase_evidence",
        "constraints",
    }
)
REFERENCE_FIELDS = frozenset({"path", "sha256"})
RECEIPT_REFERENCE_FIELDS = frozenset(
    {"path", "sha256", "controller_challenge_sha256"}
)
EXPECTED_REQUEST_CONSTRAINTS = {
    "local_bridge_only": True,
    "fresh_stopped_receipt_required": True,
    "exact_seven_receipt_artifacts_required": True,
    "final_snapshot_source_closure_required": True,
    "legacy_redis_restore_forbidden": True,
    "redis_mutation_forbidden": True,
    "caller_truth_values_forbidden": True,
    "create_only_evidence_required": True,
    "runtime_authorization_required": True,
    "controller_liveness_required": True,
}
EXPECTED_BEGIN_REQUEST_CONSTRAINTS = {
    "local_bridge_only": True,
    "journal_begin_only": True,
    "receipt_must_be_captured_after_begin": True,
    "receipt_reference_accepted": False,
    "claim_values_accepted": False,
    "redis_mutation_forbidden": True,
    "runtime_authorization_required": True,
    "controller_liveness_required": True,
}

MAX_JSON_BYTES = 16 * 1024 * 1024
OUTPUT_DIRECTORY_MODE = 0o700
OUTPUT_FILE_MODE = 0o600
ZERO_SHA256 = "0" * 64

CANONICAL_DOCKER_TOPOLOGY = {
    "bot_fi": {
        "host": INVENTORY.ROLE_HOSTS["bot_fi"],
        "transport": "local-controller",
        "ssh_user": None,
        "ssh_port": None,
    },
    "webapp_fi": {
        "host": INVENTORY.ROLE_HOSTS["webapp_fi"],
        "transport": "ssh-control",
        "ssh_user": "root",
        "ssh_port": 37067,
    },
    "webapp_ir": {
        "host": INVENTORY.ROLE_HOSTS["webapp_ir"],
        "transport": "ssh-control-object-storage-payload-only",
        "ssh_user": "root",
        "ssh_port": 22,
    },
}

ROLE_SOURCE_FIELDS = frozenset(
    {
        "schema",
        "role",
        "expected_host",
        "request_artifact_sha256",
        "response_artifact_sha256",
        "request_binding_sha256",
        "agent_sha256",
        "contract_worker_sha256",
        "role_manifest_sha256",
        "prepared_container_id",
        "prepared_network_id",
        "prepared_redis_identity_sha256",
        "prepared_redis_chain_metadata_sha256",
        "prepared_redis_metadata_sha256",
        "prepared_redis_target_count",
        "prepared_redis_unsafe_path_count",
        "prepared_redis_entry_count",
        "prepared_redis_pristine",
        "prepared_database_running",
        "prepared_database_healthy",
        "captured_at",
        "source_binding_sha256",
    }
)
CLOSURE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_challenge_sha256",
        "aggregate_artifact_sha256",
        "aggregate_sha256",
        "expected_database_state",
        "source_artifact_inventory",
        "roles",
        "claims",
        "claim_derivation",
        "prior_final_snapshot_evidence_sha256",
        "final_snapshot_source_closure_sha256",
        "final_snapshot_aggregate_artifact_sha256",
        "legacy_redis_exclusion_sha256",
        "receipt_freshly_validated",
        "source_artifact_count",
        "source_artifacts_readback_verified",
        "source_artifacts_stable_readback_verified",
        "caller_truth_values_accepted",
        "redis_mutated",
        "legacy_redis_restored",
        "production_contacted_by_bridge",
        "captured_at",
        "closure_sha256",
    }
)
PUBLICATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "phase",
        "operation",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "plan_sha256",
        "source_binding_sha256",
        "closure_path",
        "closure_file_sha256",
        "role_source_paths",
        "role_source_sha256",
        "role_validation_paths",
        "role_validation_sha256",
        "claim_source_paths",
        "claim_source_sha256",
        "phase_evidence_path",
        "phase_evidence_sha256",
        "local_verification_path",
        "local_verification_sha256",
        "journal_status",
        "journal_mutated",
        "production_contacted",
        "redis_mutated",
        "caller_truth_values_accepted",
        "create_only",
        "readback_verified",
    }
)


class PristineRedisPhaseError(RuntimeError):
    """The phase could not prove three exact pristine Redis targets."""


@dataclass(frozen=True)
class PersistedReceiptSpec:
    """Expected immutable identity of the fresh stopped source package."""

    receipt_path: Path
    controller_challenge_sha256: str
    aggregate_artifact_sha256: str
    final_snapshot_request_path: Path
    final_snapshot_request_sha256: str
    final_snapshot_aggregate_path: Path
    final_snapshot_aggregate_sha256: str


@dataclass(frozen=True)
class EvidenceContext:
    """Trusted controller records needed to publish and advance this phase."""

    manifest_path: Path
    approval_path: Path
    approval_policy_path: Path
    journal_path: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    plan: Mapping[str, Any]
    plan_sha256: str
    journal: Mapping[str, Any]
    prior_records: Mapping[str, Mapping[str, Any]]
    prior_digests: Mapping[str, str]
    prior_paths: Mapping[str, Path]
    output_root: Path


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
        raise PristineRedisPhaseError(
            "phase value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _document_sha256(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(value) + b"\n")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PristineRedisPhaseError(
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
        raise PristineRedisPhaseError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(os.fspath(path)))
    ):
        raise PristineRedisPhaseError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    try:
        return PREPARED._parse_timestamp(value, label=label)  # noqa: SLF001
    except PREPARED.PreparedCloneInventoryError as exc:
        raise PristineRedisPhaseError(
            f"{label} is not canonical UTC"
        ) from exc


def _timestamp(value: datetime) -> str:
    try:
        return INVENTORY.canonical_utc_timestamp(
            value.astimezone(timezone.utc)
        )
    except (
        AttributeError,
        INVENTORY.GlobalDockerInventoryError,
    ) as exc:
        raise PristineRedisPhaseError(
            "phase timestamp is invalid"
        ) from exc


def _parse_journal_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise PristineRedisPhaseError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PristineRedisPhaseError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PristineRedisPhaseError(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _manifest_output_root(manifest: Mapping[str, Any]) -> Path:
    return _absolute_path(
        manifest["deployment"]["controller_evidence_root"],
        label="manifest controller evidence root",
    )


def _phase_root(context: EvidenceContext) -> Path:
    return context.output_root / OUTPUT_SUBDIRECTORY


def _require_private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PristineRedisPhaseError(
            f"{label} directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIRECTORY_MODE
    ):
        raise PristineRedisPhaseError(
            f"{label} directory is not root-only"
        )


def _ensure_private_child(path: Path, *, root: Path) -> None:
    root = _absolute_path(root, label="phase output root")
    path = _absolute_path(path, label="phase output directory")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PristineRedisPhaseError(
            "phase output directory escapes the manifest root"
        ) from exc
    _require_private_directory(root, label="manifest evidence root")
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        try:
            os.mkdir(current, OUTPUT_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as exc:
            raise PristineRedisPhaseError(
                "phase output directory cannot be created"
            ) from exc
        _require_private_directory(current, label="phase output")


def _persist_document(
    directory: Path,
    *,
    root: Path,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str]:
    _ensure_private_child(directory, root=root)
    payload = _canonical_json(document) + b"\n"
    if not 1 <= len(payload) <= MAX_JSON_BYTES:
        raise PristineRedisPhaseError(
            f"{prefix} document exceeds its bound"
        )
    digest = _sha256(payload)
    path = directory / f"{prefix}.{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=prefix,
            mode=OUTPUT_FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label=f"existing {prefix}",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise PristineRedisPhaseError(
                f"{prefix} could not be persisted safely"
            ) from exc
        if existing != payload:
            raise PristineRedisPhaseError(
                f"existing digest-addressed {prefix} differs"
            )
    try:
        observed = read_secure_bytes(
            path,
            label=f"persisted {prefix}",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PristineRedisPhaseError(
            f"{prefix} readback failed"
        ) from exc
    if observed != payload or _sha256(observed) != digest:
        raise PristineRedisPhaseError(f"{prefix} readback differs")
    return path, digest


def _persist_fixed_bytes(
    path: Path,
    *,
    root: Path,
    payload: bytes,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> None:
    _ensure_private_child(path.parent, root=root)
    if not 1 <= len(payload) <= maximum:
        raise PristineRedisPhaseError(f"{label} exceeds its bound")
    try:
        write_secure_new_bytes(
            path,
            payload,
            label=label,
            mode=OUTPUT_FILE_MODE,
            max_size=maximum,
        )
    except SecureFileError:
        try:
            existing = read_secure_bytes(
                path,
                label=f"existing {label}",
                owner_uid=0,
                max_size=maximum,
            )
        except SecureFileError as exc:
            raise PristineRedisPhaseError(
                f"{label} could not be reconciled"
            ) from exc
        if existing != payload:
            raise PristineRedisPhaseError(
                f"existing create-only {label} differs"
            )
    try:
        observed = read_secure_bytes(
            path,
            label=f"persisted {label}",
            owner_uid=0,
            max_size=maximum,
        )
    except SecureFileError as exc:
        raise PristineRedisPhaseError(
            f"{label} readback failed"
        ) from exc
    if observed != payload:
        raise PristineRedisPhaseError(f"{label} readback differs")


def _read_strict_document(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], bytes]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=maximum,
        )
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PristineRedisPhaseError(
            f"{label} is unavailable or invalid"
        ) from exc
    if (
        not isinstance(document, dict)
        or payload != _canonical_json(document) + b"\n"
    ):
        raise PristineRedisPhaseError(
            f"{label} is not canonical newline JSON"
        )
    return document, payload


def _source_spec_binding(
    spec: PersistedReceiptSpec,
) -> tuple[dict[str, Any], str]:
    if not isinstance(spec, PersistedReceiptSpec):
        raise PristineRedisPhaseError(
            "persisted receipt specification is invalid"
        )
    document = {
        "receipt_path": os.fspath(
            _absolute_path(
                spec.receipt_path,
                label="prepared inventory receipt",
            )
        ),
        "controller_challenge_sha256": _nonzero_sha256(
            spec.controller_challenge_sha256,
            label="prepared inventory challenge",
        ),
        "aggregate_artifact_sha256": _nonzero_sha256(
            spec.aggregate_artifact_sha256,
            label="prepared inventory aggregate artifact",
        ),
        "final_snapshot_request_path": os.fspath(
            _absolute_path(
                spec.final_snapshot_request_path,
                label="final snapshot bridge request",
            )
        ),
        "final_snapshot_request_sha256": _nonzero_sha256(
            spec.final_snapshot_request_sha256,
            label="final snapshot bridge request",
        ),
        "final_snapshot_aggregate_path": os.fspath(
            _absolute_path(
                spec.final_snapshot_aggregate_path,
                label="final snapshot aggregate",
            )
        ),
        "final_snapshot_aggregate_sha256": _nonzero_sha256(
            spec.final_snapshot_aggregate_sha256,
            label="final snapshot aggregate",
        ),
    }
    return document, _sha256(_canonical_json(document))


def _journal_bindings(context: EvidenceContext) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _prior_phase_names() -> tuple[str, ...]:
    return CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]


def _validated_context(
    context: EvidenceContext,
    *,
    required_position: str = "any",
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    if (
        not isinstance(context, EvidenceContext)
        or os.geteuid() != 0
        or os.getegid() != 0
    ):
        raise PristineRedisPhaseError(
            "phase controller context requires root:root"
        )
    try:
        manifest = CONTROLLER.validate_manifest(
            json.loads(_canonical_json(dict(context.manifest)))
        )
        journal = CONTROLLER._validate_journal(  # noqa: SLF001
            json.loads(_canonical_json(dict(context.journal)))
        )
    except (CONTROLLER.CutoverContractError, TypeError) as exc:
        raise PristineRedisPhaseError(
            "phase controller context is invalid"
        ) from exc
    manifest_sha256 = _nonzero_sha256(
        context.manifest_sha256,
        label="context manifest",
    )
    plan_sha256 = _nonzero_sha256(
        context.plan_sha256,
        label="context controller plan",
    )
    if (
        required_position not in {"ready", "started", "completed", "any"}
        or not isinstance(context.plan, Mapping)
        or context.plan.get("plan_sha256") != plan_sha256
    ):
        raise PristineRedisPhaseError(
            "phase controller plan binding differs"
        )
    for value, label in (
        (context.manifest_path, "cutover manifest"),
        (context.approval_path, "cutover approval"),
        (context.approval_policy_path, "approval policy"),
        (context.journal_path, "cutover journal"),
    ):
        _absolute_path(value, label=label)
    if context.journal_path != Path(
        manifest["deployment"]["controller_journal_path"]
    ):
        raise PristineRedisPhaseError(
            "phase journal path differs from the manifest"
        )
    expected_bindings = {
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
    }
    if any(
        journal.get(field) != expected
        for field, expected in expected_bindings.items()
    ):
        raise PristineRedisPhaseError(
            "phase journal bindings differ"
        )
    expected_prior = list(_prior_phase_names())
    if (
        journal["completed_phases"] == expected_prior
        and journal["status"] == "active"
        and journal["started_phase"] is None
    ):
        position = "ready"
    elif (
        journal["completed_phases"] == expected_prior
        and journal["status"] == "phase_started"
        and journal["started_phase"] == PHASE
        and journal["started_at"] is not None
    ):
        position = "started"
    elif (
        journal["completed_phases"] == [*expected_prior, PHASE]
        and journal["status"] == "active"
        and journal["started_phase"] is None
    ):
        position = "completed"
    else:
        position = "invalid"
    if position == "invalid" or (
        required_position != "any" and position != required_position
    ):
        raise PristineRedisPhaseError(
            "pristine Redis phase is not the exact journal successor"
        )
    if (
        not isinstance(context.prior_records, Mapping)
        or set(context.prior_records) != set(expected_prior)
        or not isinstance(context.prior_digests, Mapping)
        or set(context.prior_digests) != set(expected_prior)
        or not isinstance(context.prior_paths, Mapping)
        or set(context.prior_paths) != set(expected_prior)
    ):
        raise PristineRedisPhaseError(
            "prior phase evidence closure is not exact"
        )
    if dict(context.prior_digests) != {
        phase: journal["phase_evidence_sha256"][phase]
        for phase in expected_prior
    }:
        raise PristineRedisPhaseError(
            "prior phase evidence differs from the journal"
        )
    prior_records: dict[str, dict[str, Any]] = {}
    for prior_phase in expected_prior:
        raw = context.prior_records[prior_phase]
        if not isinstance(raw, Mapping):
            raise PristineRedisPhaseError(
                f"{prior_phase} prior evidence is invalid"
            )
        document = json.loads(_canonical_json(dict(raw)))
        digest = context.prior_digests[prior_phase]
        expected = {
            "phase": prior_phase,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "legacy_release_sha": manifest["legacy_release_sha"],
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan_sha256,
            "approval_sha256": manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            "status": "passed",
            "business_write_observed": False,
        }
        if (
            set(document) != VERIFY.EVIDENCE_FIELDS
            or _document_sha256(document) != digest
            or any(
                document.get(field) != value
                for field, value in expected.items()
            )
        ):
            raise PristineRedisPhaseError(
                f"{prior_phase} prior evidence differs"
            )
        prior_records[prior_phase] = {
            "document": document,
            "file_sha256": digest,
        }
    final_snapshot = prior_records[PRIOR_PHASE]["document"]
    legacy_member_claim = final_snapshot.get("claims", {}).get(
        "legacy_redis_restore_member_count"
    )
    if (
        not isinstance(legacy_member_claim, dict)
        or legacy_member_claim.get("value") != 0
        or set(legacy_member_claim) != VERIFY.CLAIM_FIELDS
    ):
        raise PristineRedisPhaseError(
            "final snapshot does not exclude legacy Redis restore members"
        )
    if (
        manifest["policy"].get("legacy_redis_restore_forbidden")
        is not True
        or manifest["policy"].get("pristine_shadow_redis_required")
        is not True
        or manifest["artifacts"]["phase_evidence_schema_sha256"]
        != VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
    ):
        raise PristineRedisPhaseError(
            "manifest pristine Redis policy differs"
        )
    output_root = _absolute_path(
        context.output_root,
        label="phase output root",
    )
    if output_root != _manifest_output_root(manifest):
        raise PristineRedisPhaseError(
            "phase output root is not manifest-derived"
        )
    _require_private_directory(
        output_root,
        label="manifest evidence root",
    )
    return manifest, journal, prior_records


def load_evidence_context(
    *,
    manifest_path: Path,
    approval_path: Path,
    approval_policy_path: Path,
    prior_evidence_paths: Mapping[str, Path],
) -> EvidenceContext:
    """Load the exact root-owned local controller context."""

    if os.geteuid() != 0 or os.getegid() != 0:
        raise PristineRedisPhaseError(
            "phase context loading requires root:root"
        )
    manifest_path = _absolute_path(
        manifest_path,
        label="cutover manifest",
    )
    approval_path = _absolute_path(
        approval_path,
        label="cutover approval",
    )
    approval_policy_path = _absolute_path(
        approval_policy_path,
        label="approval policy",
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
        approval = read_secure_bytes(
            approval_path,
            label="production cutover approval",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        policy = read_secure_bytes(
            approval_policy_path,
            label="production approval policy",
            owner_uid=0,
            max_size=4 * 1024 * 1024,
        )
    except (CONTROLLER.CutoverContractError, SecureFileError) as exc:
        raise PristineRedisPhaseError(
            "trusted cutover context is unavailable"
        ) from exc
    if (
        _sha256(approval)
        != manifest["artifacts"]["cutover_approval_sha256"]
        or _sha256(policy)
        != manifest["artifacts"]["human_approval_policy_sha256"]
    ):
        raise PristineRedisPhaseError(
            "approval artifacts differ from the manifest"
        )
    expected_prior = _prior_phase_names()
    if (
        not isinstance(prior_evidence_paths, Mapping)
        or set(prior_evidence_paths) != set(expected_prior)
    ):
        raise PristineRedisPhaseError(
            "prior evidence path mapping is not exact"
        )
    prior_records: dict[str, dict[str, Any]] = {}
    prior_digests: dict[str, str] = {}
    prior_paths: dict[str, Path] = {}
    for phase in expected_prior:
        path = _absolute_path(
            prior_evidence_paths[phase],
            label=f"{phase} prior evidence",
        )
        try:
            document, digest = VERIFY.read_root_only_evidence(path)
        except VERIFY.PhaseEvidenceError as exc:
            raise PristineRedisPhaseError(
                f"{phase} prior evidence is unavailable"
            ) from exc
        prior_records[phase] = document
        prior_digests[phase] = digest
        prior_paths[phase] = path
    journal_path = Path(
        manifest["deployment"]["controller_journal_path"]
    )
    try:
        journal = CONTROLLER.ProductionCutoverJournal(
            journal_path
        ).load()
    except CONTROLLER.CutoverContractError as exc:
        raise PristineRedisPhaseError(
            "cutover journal is unavailable"
        ) from exc
    context = EvidenceContext(
        manifest_path=manifest_path,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        journal_path=journal_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        plan_sha256=plan["plan_sha256"],
        journal=journal,
        prior_records=prior_records,
        prior_digests=prior_digests,
        prior_paths=prior_paths,
        output_root=_manifest_output_root(manifest),
    )
    _validated_context(context)
    return context


def _request_reference(
    value: Any,
    *,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(value, dict) or set(value) != REFERENCE_FIELDS:
        raise PristineRedisPhaseError(
            f"{label} reference fields differ"
        )
    return (
        _absolute_path(value["path"], label=label),
        _nonzero_sha256(value["sha256"], label=label),
    )


def _validate_request_context_binding(
    document: Mapping[str, Any],
    context: EvidenceContext,
    *,
    expected_prior_digests: Mapping[str, str],
) -> None:
    expected_identity = {
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "approval_sha256": context.manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "approval_policy_sha256": context.manifest["artifacts"][
            "human_approval_policy_sha256"
        ],
    }
    if (
        any(
            document.get(field) != value
            for field, value in expected_identity.items()
        )
        or document["manifest_path"]
        != os.fspath(context.manifest_path)
        or document["approval_path"]
        != os.fspath(context.approval_path)
        or document["approval_policy_path"]
        != os.fspath(context.approval_policy_path)
        or dict(expected_prior_digests)
        != dict(context.prior_digests)
    ):
        raise PristineRedisPhaseError(
            "pristine Redis phase request differs from trusted context"
        )


def _request_prior_paths(
    document: Mapping[str, Any],
) -> tuple[dict[str, Path], dict[str, str]]:
    prior = document.get("prior_phase_evidence")
    if (
        not isinstance(prior, dict)
        or set(prior) != set(_prior_phase_names())
    ):
        raise PristineRedisPhaseError(
            "phase request prior evidence mapping differs"
        )
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    for phase in _prior_phase_names():
        path, digest = _request_reference(
            prior[phase],
            label=f"{phase} prior phase evidence",
        )
        paths[phase] = path
        digests[phase] = digest
    return paths, digests


def load_begin_request(
    request_path: Path,
) -> tuple[EvidenceContext, str]:
    """Load a context-only request that cannot contain receipt or claims."""

    request_path = _absolute_path(
        request_path,
        label="pristine Redis capture-begin request",
    )
    document, payload = _read_strict_document(
        request_path,
        label="pristine Redis capture-begin request",
    )
    if (
        set(document) != BEGIN_REQUEST_FIELDS
        or document["schema"] != BEGIN_REQUEST_SCHEMA
        or document["status"] != "ready"
        or document.get("constraints")
        != EXPECTED_BEGIN_REQUEST_CONSTRAINTS
    ):
        raise PristineRedisPhaseError(
            "pristine Redis capture-begin request fields differ"
        )
    prior_paths, prior_digests = _request_prior_paths(document)
    context = load_evidence_context(
        manifest_path=_absolute_path(
            document["manifest_path"],
            label="cutover manifest",
        ),
        approval_path=_absolute_path(
            document["approval_path"],
            label="cutover approval",
        ),
        approval_policy_path=_absolute_path(
            document["approval_policy_path"],
            label="approval policy",
        ),
        prior_evidence_paths=prior_paths,
    )
    _validate_request_context_binding(
        document,
        context,
        expected_prior_digests=prior_digests,
    )
    return context, _sha256(payload)


def load_phase_request(
    request_path: Path,
) -> tuple[EvidenceContext, PersistedReceiptSpec, str]:
    """Load one root-private request containing paths, never claim values."""

    request_path = _absolute_path(
        request_path,
        label="pristine Redis phase request",
    )
    document, payload = _read_strict_document(
        request_path,
        label="pristine Redis phase request",
    )
    if (
        set(document) != REQUEST_FIELDS
        or document["schema"] != REQUEST_SCHEMA
        or document["status"] != "ready"
        or document.get("constraints") != EXPECTED_REQUEST_CONSTRAINTS
        or not isinstance(document.get("prior_phase_evidence"), dict)
        or set(document["prior_phase_evidence"])
        != set(_prior_phase_names())
        or not isinstance(
            document.get("prepared_inventory_receipt"),
            dict,
        )
        or set(document["prepared_inventory_receipt"])
        != RECEIPT_REFERENCE_FIELDS
    ):
        raise PristineRedisPhaseError(
            "pristine Redis phase request fields differ"
        )
    prior_paths, expected_prior_digests = _request_prior_paths(
        document
    )
    final_request_path, final_request_sha256 = _request_reference(
        document.get("final_snapshot_request"),
        label="final snapshot bridge request",
    )
    final_aggregate_path, final_aggregate_sha256 = _request_reference(
        document.get("final_snapshot_aggregate"),
        label="final snapshot aggregate",
    )
    receipt_reference = document["prepared_inventory_receipt"]
    context = load_evidence_context(
        manifest_path=_absolute_path(
            document["manifest_path"],
            label="cutover manifest",
        ),
        approval_path=_absolute_path(
            document["approval_path"],
            label="cutover approval",
        ),
        approval_policy_path=_absolute_path(
            document["approval_policy_path"],
            label="approval policy",
        ),
        prior_evidence_paths=prior_paths,
    )
    _validate_request_context_binding(
        document,
        context,
        expected_prior_digests=expected_prior_digests,
    )
    spec = PersistedReceiptSpec(
        receipt_path=_absolute_path(
            receipt_reference["path"],
            label="prepared inventory receipt",
        ),
        controller_challenge_sha256=_nonzero_sha256(
            receipt_reference["controller_challenge_sha256"],
            label="prepared inventory challenge",
        ),
        aggregate_artifact_sha256=_nonzero_sha256(
            receipt_reference["sha256"],
            label="prepared inventory receipt",
        ),
        final_snapshot_request_path=final_request_path,
        final_snapshot_request_sha256=final_request_sha256,
        final_snapshot_aggregate_path=final_aggregate_path,
        final_snapshot_aggregate_sha256=final_aggregate_sha256,
    )
    _source_spec_binding(spec)
    return context, spec, _sha256(payload)


def _validate_source_artifact_metadata(
    loaded: Mapping[str, Any],
) -> dict[str, tuple[int, int, int, int, int, int, int]]:
    identities: set[tuple[int, int]] = set()
    result: dict[
        str,
        tuple[int, int, int, int, int, int, int],
    ] = {}
    references = [loaded["aggregate"]]
    for role in ROLES:
        references.extend(
            (
                loaded["artifacts"][role]["request"],
                loaded["artifacts"][role]["response"],
            )
        )
    for reference in references:
        path = _absolute_path(
            reference["path"],
            label="prepared source artifact",
        )
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise PristineRedisPhaseError(
                "prepared source artifact metadata is unavailable"
            ) from exc
        identity = (metadata.st_dev, metadata.st_ino)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != OUTPUT_FILE_MODE
            or metadata.st_nlink != 1
            or metadata.st_size != reference["bytes"]
            or identity in identities
        ):
            raise PristineRedisPhaseError(
                "prepared source artifact metadata or identity differs"
            )
        identities.add(identity)
        result[os.fspath(path)] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_size,
        )
    if len(result) != 7:
        raise PristineRedisPhaseError(
            "prepared source artifact identity closure differs"
        )
    return result


def _source_artifact_inventory(
    loaded: Mapping[str, Any],
    *,
    receipt_path: Path,
) -> dict[str, Any]:
    receipt_path = _absolute_path(
        receipt_path,
        label="prepared receipt",
    )
    result: dict[str, Any] = {}
    aggregate = loaded["aggregate"]
    expected_aggregate = {
        "filename": PREPARED.PRE_FREEZE_CURRENT_OPERATION_RECEIPT_FILENAME,
        "path": os.fspath(receipt_path),
    }
    if any(
        aggregate.get(field) != value
        for field, value in expected_aggregate.items()
    ):
        raise PristineRedisPhaseError(
            "prepared aggregate reference path differs"
        )
    result["aggregate"] = {
        "path": aggregate["path"],
        "sha256": _nonzero_sha256(
            aggregate.get("sha256"),
            label="prepared aggregate artifact",
        ),
        "bytes": aggregate.get("bytes"),
    }
    for role in ROLES:
        references = loaded["artifacts"][role]
        if not isinstance(references, Mapping) or set(references) != {
            "request",
            "response",
        }:
            raise PristineRedisPhaseError(
                f"{role} prepared source references differ"
            )
        result[role] = {}
        for kind, filename in (
            ("request", PREPARED.REQUEST_FILENAMES[role]),
            ("response", PREPARED.RESPONSE_FILENAMES[role]),
        ):
            reference = references[kind]
            expected_path = receipt_path.parent / filename
            if (
                not isinstance(reference, Mapping)
                or set(reference) != {"filename", "path", "sha256", "bytes"}
                or reference.get("filename") != filename
                or reference.get("path") != os.fspath(expected_path)
            ):
                raise PristineRedisPhaseError(
                    f"{role} prepared {kind} reference differs"
                )
            result[role][kind] = {
                "path": reference["path"],
                "sha256": _nonzero_sha256(
                    reference.get("sha256"),
                    label=f"{role} prepared {kind}",
                ),
                "bytes": reference.get("bytes"),
            }
    rows = [result["aggregate"]]
    rows.extend(
        result[role][kind]
        for role in ROLES
        for kind in ("request", "response")
    )
    if any(
        isinstance(row["bytes"], bool)
        or not isinstance(row["bytes"], int)
        or row["bytes"] < 1
        for row in rows
    ):
        raise PristineRedisPhaseError(
            "prepared source artifact size differs"
        )
    return result


def _private_file_identity(
    path: Path,
    *,
    label: str,
) -> tuple[int, int, int, int, int, int, int]:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PristineRedisPhaseError(
            f"{label} identity is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != OUTPUT_FILE_MODE
        or metadata.st_nlink != 1
        or metadata.st_size < 1
    ):
        raise PristineRedisPhaseError(
            f"{label} identity is unsafe"
        )
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
    )


def _frozen_record_identity(
    record: Any,
    *,
    label: str,
) -> tuple[int, int, int, int, int, int, int]:
    identity = getattr(record, "identity", None)
    try:
        result = (
            identity.device,
            identity.inode,
            identity.mode,
            identity.uid,
            identity.gid,
            identity.nlink,
            identity.size,
        )
    except AttributeError as exc:
        raise PristineRedisPhaseError(
            f"{label} identity is invalid"
        ) from exc
    if (
        not stat.S_ISREG(result[2])
        or stat.S_ISLNK(result[2])
        or result[3] != 0
        or result[4] != 0
        or stat.S_IMODE(result[2]) != OUTPUT_FILE_MODE
        or result[5] != 1
        or result[6] < 1
    ):
        raise PristineRedisPhaseError(
            f"{label} identity is unsafe"
        )
    return result


def _validate_final_snapshot_source_closure(
    context: EvidenceContext,
    spec: PersistedReceiptSpec,
    *,
    now: datetime,
) -> dict[str, str]:
    """Re-validate the exact source package that produced final snapshots."""

    try:
        frozen_context = FROZEN_PHASE._load_request(  # noqa: SLF001
            spec.final_snapshot_request_path
        )
        sources = FROZEN_PHASE._validate_sources(  # noqa: SLF001
            frozen_context,
            now=now,
        )
        derived_claims = FROZEN_PHASE._phase_claims(  # noqa: SLF001
            PRIOR_PHASE,
            sources,
        )
    except FROZEN_PHASE.FreezeSnapshotPhaseBridgeError as exc:
        raise PristineRedisPhaseError(
            "final snapshot source closure is invalid"
        ) from exc
    manifest = context.manifest
    expected_frozen_root = (
        context.output_root / "freeze-snapshot-phase-bridge"
    )
    if (
        frozen_context.request.sha256
        != spec.final_snapshot_request_sha256
        or frozen_context.request.path
        != spec.final_snapshot_request_path
        or frozen_context.manifest_path != context.manifest_path
        or frozen_context.manifest != manifest
        or frozen_context.manifest_sha256 != context.manifest_sha256
        or frozen_context.plan_sha256 != context.plan_sha256
        or frozen_context.output_root != expected_frozen_root
    ):
        raise PristineRedisPhaseError(
            "final snapshot request differs from controller context"
        )
    request_identity = _frozen_record_identity(
        frozen_context.request,
        label="final snapshot request",
    )
    prior_evidence = context.prior_records[PRIOR_PHASE]
    expected_claims = {
        name: row["value"]
        for name, row in prior_evidence["claims"].items()
    }
    if (
        derived_claims != expected_claims
        or derived_claims.get("legacy_redis_restore_member_count") != 0
    ):
        raise PristineRedisPhaseError(
            "final snapshot source claims differ from verified evidence"
        )

    aggregate_path = _absolute_path(
        spec.final_snapshot_aggregate_path,
        label="final snapshot aggregate",
    )
    aggregate, aggregate_payload = _read_strict_document(
        aggregate_path,
        label="final snapshot aggregate",
    )
    aggregate_digest = _sha256(aggregate_payload)
    aggregate_identity = _private_file_identity(
        aggregate_path,
        label="final snapshot aggregate",
    )
    expected_aggregate_path = (
        frozen_context.output_root
        / PRIOR_PHASE
        / "aggregates"
        / (
            f"phase-aggregate-{PRIOR_PHASE}."
            f"{aggregate_digest}.json"
        )
    )
    aggregate_fields = {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "phase",
        "operation",
        "roles",
        "source_closure_sha256",
        "claims",
        "phase_evidence_path",
        "phase_evidence_sha256",
        "caller_truth_values_accepted",
        "legacy_writers_frozen",
        "restore_performed",
        "writer_restart_performed",
        "business_write_observed",
    }
    expected_aggregate = {
        "schema": FROZEN_PHASE.PHASE_AGGREGATE_SCHEMA,
        "status": "completed",
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "phase": PRIOR_PHASE,
        "operation": next(
            item.operation
            for item in CONTROLLER.PHASE_SPECS
            if item.phase == PRIOR_PHASE
        ),
        "roles": list(FROZEN_PHASE.ROLE_ORDER),
        "source_closure_sha256": sources.source_closure_sha256,
        "claims": derived_claims,
        "phase_evidence_path": os.fspath(
            context.prior_paths[PRIOR_PHASE]
        ),
        "phase_evidence_sha256": context.prior_digests[PRIOR_PHASE],
        "caller_truth_values_accepted": False,
        "legacy_writers_frozen": True,
        "restore_performed": False,
        "writer_restart_performed": False,
        "business_write_observed": False,
    }
    if (
        aggregate_path != expected_aggregate_path
        or aggregate_digest != spec.final_snapshot_aggregate_sha256
        or set(aggregate) != aggregate_fields
        or aggregate != expected_aggregate
    ):
        raise PristineRedisPhaseError(
            "final snapshot aggregate or source closure differs"
        )

    redis_exclusion_rows: list[dict[str, Any]] = []
    for role in FROZEN_PHASE.ROLE_ORDER:
        snapshot = sources.snapshots[role]
        redis = snapshot.get("redis_rollback_only")
        artifacts = snapshot.get("artifacts")
        if (
            not isinstance(redis, dict)
            or not isinstance(artifacts, dict)
            or set(artifacts)
            != {
                "database-backup",
                "uploads-archive",
                "audit-archive",
            }
            or redis.get("policy")
            != "sealed-rollback-evidence-only"
            or redis.get("archive_created") is not False
            or redis.get("restore") is not False
            or snapshot.get("redis_restored") is not False
            or isinstance(redis.get("bytes"), bool)
            or not isinstance(redis.get("bytes"), int)
            or redis["bytes"] < 0
            or isinstance(redis.get("member_count"), bool)
            or not isinstance(redis.get("member_count"), int)
            or redis["member_count"] < 0
        ):
            raise PristineRedisPhaseError(
                f"{role} legacy Redis is not excluded rollback evidence"
            )
        redis_exclusion_rows.append(
            {
                "role": role,
                "policy": redis["policy"],
                "source_volume": redis["source_volume"],
                "tree_sha256": redis["tree_sha256"],
                "metadata_sha256": redis["metadata_sha256"],
                "member_count": redis["member_count"],
                "bytes": redis["bytes"],
                "archive_created": False,
                "restore": False,
                "redis_restored": False,
                "restore_artifact_member": False,
            }
        )
    legacy_exclusion_sha256 = _sha256(
        _canonical_json(redis_exclusion_rows)
    )

    role_validation_paths: dict[str, Path] = {}
    expected_role_requests: dict[str, str] = {}
    for role in FROZEN_PHASE.ROLE_ORDER:
        source = sources.records[f"{role}_snapshot_manifest"]
        expected_role_requests[role] = FROZEN_PHASE._aggregate_hash(  # noqa: SLF001
            {
                "phase": PRIOR_PHASE,
                "operation": expected_aggregate["operation"],
                "role": role,
                "source_path": os.fspath(source.path),
                "source_sha256": source.sha256,
                "source_closure_sha256": (
                    sources.source_closure_sha256
                ),
            }
        )
    attestation_by_role = {
        row["role"]: row
        for row in prior_evidence["role_attestations"]
    }
    if set(attestation_by_role) != set(FROZEN_PHASE.ROLE_ORDER):
        raise PristineRedisPhaseError(
            "final snapshot role attestation set differs"
        )
    for role in FROZEN_PHASE.ROLE_ORDER:
        digest = attestation_by_role[role]["source_artifact_sha256"]
        role_validation_paths[role] = (
            frozen_context.output_root
            / PRIOR_PHASE
            / "role-validation"
            / f"role-validation-{role}.{digest}.json"
        )
    try:
        (
            role_requests,
            role_sources,
            role_observed_at,
        ) = VERIFY._read_role_validation_records(  # noqa: SLF001
            [
                f"{role}={role_validation_paths[role]}"
                for role in FROZEN_PHASE.ROLE_ORDER
            ],
            phase=PRIOR_PHASE,
            manifest=dict(manifest),
            manifest_sha256=context.manifest_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "final snapshot role validation source is invalid"
        ) from exc
    if (
        role_requests != expected_role_requests
        or any(
            role_sources[role]
            != attestation_by_role[role]["source_artifact_sha256"]
            for role in FROZEN_PHASE.ROLE_ORDER
        )
    ):
        raise PristineRedisPhaseError(
            "final snapshot role source closure differs"
        )

    claim_source_paths = {
        claim: (
            frozen_context.output_root
            / PRIOR_PHASE
            / "claim-sources"
            / (
                f"claim-{claim}."
                f"{prior_evidence['claims'][claim]['source_sha256']}.json"
            )
        )
        for claim in derived_claims
    }
    evidence_time = _parse_journal_timestamp(
        prior_evidence["captured_at"],
        label="final snapshot evidence captured_at",
    )
    try:
        dynamic_values, claim_sources = (
            VERIFY._read_claim_source_records(  # noqa: SLF001
                [
                    f"{claim}={claim_source_paths[claim]}"
                    for claim in derived_claims
                ],
                phase=PRIOR_PHASE,
                manifest=dict(manifest),
                manifest_sha256=context.manifest_sha256,
                now=evidence_time,
            )
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "final snapshot claim source inventory is invalid"
        ) from exc
    expected_dynamic = {
        name: value
        for name, value in derived_claims.items()
        if VERIFY.PHASE_CLAIM_RULES[PRIOR_PHASE][name].kind
        != "exact"
    }
    if (
        dynamic_values != expected_dynamic
        or any(
            claim_sources[claim]
            != prior_evidence["claims"][claim]["source_sha256"]
            for claim in derived_claims
        )
    ):
        raise PristineRedisPhaseError(
            "final snapshot claim artifact inventory differs"
        )
    prior_names = CONTROLLER.PHASES[
        : CONTROLLER.PHASES.index(PRIOR_PHASE)
    ]
    verifier_arguments = {
        "expected_phase": PRIOR_PHASE,
        "expected_campaign_id": manifest["campaign_id"],
        "expected_operation_id": manifest["operation_id"],
        "expected_release_sha": manifest["release_sha"],
        "expected_legacy_release_sha": manifest["legacy_release_sha"],
        "expected_manifest_sha256": context.manifest_sha256,
        "expected_plan_sha256": context.plan_sha256,
        "expected_approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "expected_phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "expected_manifest_artifacts": dict(manifest["artifacts"]),
        "expected_role_request_sha256": role_requests,
        "expected_role_source_artifact_sha256": role_sources,
        "expected_role_observed_at": role_observed_at,
        "expected_dynamic_claim_values": expected_dynamic,
        "expected_claim_source_sha256": claim_sources,
        "expected_prior_phase_evidence_sha256": {
            phase: context.prior_digests[phase]
            for phase in prior_names
        },
        "prior_phase_evidence_records": {
            phase: {
                "document": context.prior_records[phase],
                "file_sha256": context.prior_digests[phase],
            }
            for phase in prior_names
        },
        "now": evidence_time,
    }
    try:
        VERIFY.verify_phase_evidence(
            prior_evidence,
            evidence_file_sha256=context.prior_digests[PRIOR_PHASE],
            **verifier_arguments,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "final snapshot evidence is not verifier-valid"
        ) from exc
    try:
        FROZEN_PHASE._assert_records_unchanged(  # noqa: SLF001
            sources.records
        )
        stable_frozen_context = FROZEN_PHASE._load_request(  # noqa: SLF001
            spec.final_snapshot_request_path
        )
    except FROZEN_PHASE.FreezeSnapshotPhaseBridgeError as exc:
        raise PristineRedisPhaseError(
            "final snapshot source changed during validation"
        ) from exc
    stable_aggregate, stable_aggregate_payload = _read_strict_document(
        aggregate_path,
        label="stable final snapshot aggregate",
    )
    if (
        stable_frozen_context.request.path
        != frozen_context.request.path
        or stable_frozen_context.request.sha256
        != frozen_context.request.sha256
        or stable_frozen_context.request.payload
        != frozen_context.request.payload
        or _frozen_record_identity(
            stable_frozen_context.request,
            label="stable final snapshot request",
        )
        != request_identity
        or stable_aggregate != aggregate
        or stable_aggregate_payload != aggregate_payload
        or _private_file_identity(
            aggregate_path,
            label="stable final snapshot aggregate",
        )
        != aggregate_identity
    ):
        raise PristineRedisPhaseError(
            "final snapshot request or aggregate changed during validation"
        )
    return {
        "source_closure_sha256": sources.source_closure_sha256,
        "aggregate_artifact_sha256": aggregate_digest,
        "legacy_redis_exclusion_sha256": legacy_exclusion_sha256,
    }


def _validate_loaded_receipt(
    context: EvidenceContext,
    spec: PersistedReceiptSpec,
    loaded: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    manifest, journal, _prior = _validated_context(
        context,
        required_position="started",
    )
    spec_document, _source_binding = _source_spec_binding(spec)
    expected_loaded_fields = {
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
    if (
        not isinstance(loaded, Mapping)
        or set(loaded) != expected_loaded_fields
        or loaded["schema"] != PREPARED.LOADED_RECEIPT_SCHEMA
        or loaded["status"] != "loaded-readback-verified"
        or loaded["artifact_count"] != 7
        or loaded["readback_verified"] is not True
        or not isinstance(loaded["receipt"], Mapping)
        or not isinstance(loaded["requests"], Mapping)
        or not isinstance(loaded["responses"], Mapping)
        or not isinstance(loaded["artifacts"], Mapping)
        or not isinstance(loaded["aggregate"], Mapping)
        or set(loaded["requests"]) != set(ROLES)
        or set(loaded["responses"]) != set(ROLES)
        or set(loaded["artifacts"]) != set(ROLES)
    ):
        raise PristineRedisPhaseError(
            "prepared source package closure differs"
        )
    receipt = dict(loaded["receipt"])
    expected_identity = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "controller_challenge_sha256": spec_document[
            "controller_challenge_sha256"
        ],
        "expected_database_state": "stopped",
    }
    if (
        any(
            receipt.get(field) != value
            for field, value in expected_identity.items()
        )
        or receipt.get("collection_performed") is not True
        or receipt.get("production_contacted") is not True
        or receipt.get("docker_read_only") is not True
        or receipt.get("application_payload_bytes_over_ssh") != 0
        or loaded["aggregate"].get("sha256")
        != spec_document["aggregate_artifact_sha256"]
        or _absolute_path(
            loaded["aggregate"].get("path", ""),
            label="aggregate source path",
        )
        != _absolute_path(
            spec.receipt_path,
            label="receipt source path",
        )
    ):
        raise PristineRedisPhaseError(
            "prepared source identity or safety boundary differs"
        )
    started_at = _parse_journal_timestamp(
        journal["started_at"],
        label="phase journal started_at",
    )
    issued_at = _parse_timestamp(
        receipt["issued_at"],
        label="receipt issued_at",
    )
    controller_observed_at = _parse_timestamp(
        receipt["controller_observed_at"],
        label="receipt controller_observed_at",
    )
    if (
        issued_at
        < started_at
        - timedelta(seconds=PREPARED.COMMAND_CLOCK_SKEW_SECONDS)
        or controller_observed_at < started_at
        or controller_observed_at > now
    ):
        raise PristineRedisPhaseError(
            "prepared source predates durable phase start"
        )
    source_inventory = _source_artifact_inventory(
        loaded,
        receipt_path=spec.receipt_path,
    )
    source_identities = _validate_source_artifact_metadata(loaded)
    final_snapshot = _validate_final_snapshot_source_closure(
        context,
        spec,
        now=now,
    )

    role_sources: dict[str, dict[str, Any]] = {}
    redis_identities: set[str] = set()
    agent_digests: set[str] = set()
    worker_digests: dict[str, str] = {}
    role_manifest_digests: set[str] = set()
    for role in ROLES:
        request = loaded["requests"][role]
        response = loaded["responses"][role]
        references = loaded["artifacts"][role]
        topology = manifest["topology"][role]
        canonical_topology = CANONICAL_DOCKER_TOPOLOGY[role]
        if (
            not isinstance(request, Mapping)
            or not isinstance(response, Mapping)
            or not isinstance(references, Mapping)
            or set(references) != {"request", "response"}
            or any(
                topology.get(field) != expected
                for field, expected in canonical_topology.items()
            )
            or request.get("role") != role
            or response.get("role") != role
            or request.get("expected_host") != topology["host"]
            or response.get("expected_host") != topology["host"]
            or response.get("role_manifest_sha256")
            != request.get("role_manifest_sha256")
            or response.get("prepared_redis_target_count") != 1
            or response.get("prepared_redis_unsafe_path_count") != 0
            or response.get("prepared_redis_entry_count") != 0
            or response.get("prepared_redis_pristine") is not True
            or response.get("prepared_database_running") is not False
            or response.get("prepared_database_healthy") is not False
            or response.get("filesystem_mutated") is not False
            or response.get("network_io_performed") is not False
            or response.get("docker_read_only") is not True
        ):
            raise PristineRedisPhaseError(
                f"{role} pristine Redis source differs"
            )
        redis_identity = _nonzero_sha256(
            response.get("prepared_redis_identity_sha256"),
            label=f"{role} prepared Redis identity",
        )
        if redis_identity in redis_identities:
            raise PristineRedisPhaseError(
                "cross-role Redis target identity was substituted"
            )
        redis_identities.add(redis_identity)
        agent_digests.add(
            _nonzero_sha256(
                request.get("agent_sha256"),
                label=f"{role} inventory agent",
            )
        )
        worker_digests[role] = _nonzero_sha256(
            request.get("contract_worker_sha256"),
            label=f"{role} contract worker",
        )
        role_manifest = _nonzero_sha256(
            request.get("role_manifest_sha256"),
            label=f"{role} role manifest",
        )
        if role_manifest in role_manifest_digests:
            raise PristineRedisPhaseError(
                "cross-role manifest digest was substituted"
            )
        role_manifest_digests.add(role_manifest)
        for field in (
            "prepared_redis_chain_metadata_sha256",
            "prepared_redis_metadata_sha256",
        ):
            _nonzero_sha256(
                response.get(field),
                label=f"{role} {field}",
            )
        request_reference = references["request"]
        response_reference = references["response"]
        request_digest = _nonzero_sha256(
            request_reference.get("sha256"),
            label=f"{role} request artifact",
        )
        response_digest = _nonzero_sha256(
            response_reference.get("sha256"),
            label=f"{role} response artifact",
        )
        source = {
            "schema": ROLE_SOURCE_SCHEMA,
            "role": role,
            "expected_host": topology["host"],
            "request_artifact_sha256": request_digest,
            "response_artifact_sha256": response_digest,
            "request_binding_sha256": _nonzero_sha256(
                request.get("request_binding_sha256"),
                label=f"{role} request binding",
            ),
            "agent_sha256": request["agent_sha256"],
            "contract_worker_sha256": request[
                "contract_worker_sha256"
            ],
            "role_manifest_sha256": request["role_manifest_sha256"],
            "prepared_container_id": response[
                "prepared_container_id"
            ],
            "prepared_network_id": response["prepared_network_id"],
            "prepared_redis_identity_sha256": redis_identity,
            "prepared_redis_chain_metadata_sha256": response[
                "prepared_redis_chain_metadata_sha256"
            ],
            "prepared_redis_metadata_sha256": response[
                "prepared_redis_metadata_sha256"
            ],
            "prepared_redis_target_count": 1,
            "prepared_redis_unsafe_path_count": 0,
            "prepared_redis_entry_count": 0,
            "prepared_redis_pristine": True,
            "prepared_database_running": False,
            "prepared_database_healthy": False,
            "captured_at": response["captured_at"],
        }
        source["source_binding_sha256"] = _sha256(
            _canonical_json(source)
        )
        if set(source) != ROLE_SOURCE_FIELDS:
            raise PristineRedisPhaseError(
                f"{role} role source fields differ"
            )
        role_sources[role] = source
    if (
        len(agent_digests) != 1
        or worker_digests["bot_fi"] != worker_digests["webapp_fi"]
        or worker_digests["webapp_ir"] == worker_digests["bot_fi"]
    ):
        raise PristineRedisPhaseError(
            "prepared inventory release-agent binding differs"
        )
    try:
        stable_loaded = (
            PREPARED.load_pre_freeze_current_operation_receipt(
                spec.receipt_path,
                output_root=context.output_root,
                now=now,
            )
        )
    except PREPARED.PreparedCloneInventoryError as exc:
        raise PristineRedisPhaseError(
            "prepared source failed stable readback"
        ) from exc
    stable_inventory = _source_artifact_inventory(
        stable_loaded,
        receipt_path=spec.receipt_path,
    )
    stable_identities = _validate_source_artifact_metadata(
        stable_loaded
    )
    if (
        stable_loaded != loaded
        or stable_inventory != source_inventory
        or stable_identities != source_identities
    ):
        raise PristineRedisPhaseError(
            "prepared source changed during validation"
        )
    claims = {
        "redis_target_count": sum(
            row["prepared_redis_target_count"]
            for row in role_sources.values()
        ),
        "unsafe_redis_path_count": sum(
            row["prepared_redis_unsafe_path_count"]
            for row in role_sources.values()
        ),
        "nonempty_redis_target_count": sum(
            0 if row["prepared_redis_pristine"] else 1
            for row in role_sources.values()
        ),
        "legacy_redis_restore_byte_count": 0,
    }
    if claims != {
        "redis_target_count": 3,
        "unsafe_redis_path_count": 0,
        "nonempty_redis_target_count": 0,
        "legacy_redis_restore_byte_count": 0,
    }:
        raise PristineRedisPhaseError(
            "derived pristine Redis claim set differs"
        )
    closure = {
        "schema": CLOSURE_SCHEMA,
        "status": "validated-fresh-stopped-three-role",
        "campaign_id": receipt["campaign_id"],
        "operation_id": receipt["operation_id"],
        "release_sha": receipt["release_sha"],
        "release_tree_sha": receipt["release_tree_sha"],
        "controller_challenge_sha256": receipt[
            "controller_challenge_sha256"
        ],
        "aggregate_artifact_sha256": loaded["aggregate"]["sha256"],
        "aggregate_sha256": receipt["aggregate_sha256"],
        "expected_database_state": "stopped",
        "source_artifact_inventory": source_inventory,
        "roles": role_sources,
        "claims": claims,
        "claim_derivation": {
            "redis_target_count": (
                "sum(exact role prepared_redis_target_count)"
            ),
            "unsafe_redis_path_count": (
                "sum(exact role prepared_redis_unsafe_path_count)"
            ),
            "nonempty_redis_target_count": (
                "count(role prepared_redis_pristine is not true)"
            ),
            "legacy_redis_restore_byte_count": (
                "manifest restore prohibition + final snapshot zero "
                "restore members + fresh empty targets"
            ),
        },
        "prior_final_snapshot_evidence_sha256": (
            context.prior_digests[PRIOR_PHASE]
        ),
        "final_snapshot_source_closure_sha256": final_snapshot[
            "source_closure_sha256"
        ],
        "final_snapshot_aggregate_artifact_sha256": final_snapshot[
            "aggregate_artifact_sha256"
        ],
        "legacy_redis_exclusion_sha256": final_snapshot[
            "legacy_redis_exclusion_sha256"
        ],
        "receipt_freshly_validated": True,
        "source_artifact_count": 7,
        "source_artifacts_readback_verified": True,
        "source_artifacts_stable_readback_verified": True,
        "caller_truth_values_accepted": False,
        "redis_mutated": False,
        "legacy_redis_restored": False,
        "production_contacted_by_bridge": False,
        "captured_at": receipt["captured_at"],
    }
    closure["closure_sha256"] = _sha256(
        _canonical_json(closure)
    )
    return validate_closure(context, closure)


def load_pristine_closure(
    context: EvidenceContext,
    spec: PersistedReceiptSpec,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load one still-fresh stopped source package and derive the closure."""

    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    _validated_context(context, required_position="started")
    try:
        loaded = PREPARED.load_pre_freeze_current_operation_receipt(
            spec.receipt_path,
            output_root=context.output_root,
            now=observed_now,
        )
    except PREPARED.PreparedCloneInventoryError as exc:
        raise PristineRedisPhaseError(
            "fresh stopped prepared inventory receipt is invalid"
        ) from exc
    return _validate_loaded_receipt(
        context,
        spec,
        loaded,
        now=observed_now,
    )


def validate_closure(
    context: EvidenceContext,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    manifest, _journal, _prior = _validated_context(
        context,
        required_position="started",
    )
    if not isinstance(value, Mapping) or set(value) != CLOSURE_FIELDS:
        raise PristineRedisPhaseError(
            "pristine Redis closure fields are not exact"
        )
    document = json.loads(_canonical_json(dict(value)))
    expected_identity = {
        "schema": CLOSURE_SCHEMA,
        "status": "validated-fresh-stopped-three-role",
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "expected_database_state": "stopped",
        "prior_final_snapshot_evidence_sha256": (
            context.prior_digests[PRIOR_PHASE]
        ),
        "receipt_freshly_validated": True,
        "source_artifact_count": 7,
        "source_artifacts_readback_verified": True,
        "source_artifacts_stable_readback_verified": True,
        "caller_truth_values_accepted": False,
        "redis_mutated": False,
        "legacy_redis_restored": False,
        "production_contacted_by_bridge": False,
    }
    if (
        any(
            document.get(field) != expected
            for field, expected in expected_identity.items()
        )
        or set(document.get("roles", {})) != set(ROLES)
        or document.get("claims")
        != {
            "redis_target_count": 3,
            "unsafe_redis_path_count": 0,
            "nonempty_redis_target_count": 0,
            "legacy_redis_restore_byte_count": 0,
        }
        or set(document.get("claim_derivation", {})) != set(CLAIMS)
    ):
        raise PristineRedisPhaseError(
            "pristine Redis closure identity or claims differ"
        )
    inventory = document.get("source_artifact_inventory")
    if (
        not isinstance(inventory, dict)
        or set(inventory) != {"aggregate", *ROLES}
        or not isinstance(inventory["aggregate"], dict)
        or set(inventory["aggregate"]) != {"path", "sha256", "bytes"}
        or any(
            not isinstance(inventory[role], dict)
            or set(inventory[role]) != {"request", "response"}
            or any(
                not isinstance(inventory[role][kind], dict)
                or set(inventory[role][kind])
                != {"path", "sha256", "bytes"}
                for kind in ("request", "response")
            )
            for role in ROLES
        )
    ):
        raise PristineRedisPhaseError(
            "pristine Redis source artifact inventory differs"
        )
    expected_receipt_path = PREPARED.canonical_receipt_path(
        context.output_root,
        operation_id=manifest["operation_id"],
        controller_challenge_sha256=document[
            "controller_challenge_sha256"
        ],
    )
    expected_paths = {
        "aggregate": expected_receipt_path,
        **{
            f"{role}:{kind}": (
                expected_receipt_path.parent
                / (
                    PREPARED.REQUEST_FILENAMES[role]
                    if kind == "request"
                    else PREPARED.RESPONSE_FILENAMES[role]
                )
            )
            for role in ROLES
            for kind in ("request", "response")
        },
    }
    inventory_rows = {"aggregate": inventory["aggregate"]}
    inventory_rows.update(
        {
            f"{role}:{kind}": inventory[role][kind]
            for role in ROLES
            for kind in ("request", "response")
        }
    )
    if (
        set(inventory_rows) != set(expected_paths)
        or any(
            row["path"] != os.fspath(expected_paths[label])
            or _nonzero_sha256(
                row["sha256"],
                label=f"{label} source artifact",
            )
            != row["sha256"]
            or isinstance(row["bytes"], bool)
            or not isinstance(row["bytes"], int)
            or row["bytes"] < 1
            for label, row in inventory_rows.items()
        )
        or len({row["path"] for row in inventory_rows.values()}) != 7
        or inventory["aggregate"]["sha256"]
        != document["aggregate_artifact_sha256"]
    ):
        raise PristineRedisPhaseError(
            "pristine Redis seven-artifact binding differs"
        )
    redis_identities: set[str] = set()
    captured: list[datetime] = []
    for role in ROLES:
        row = document["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != ROLE_SOURCE_FIELDS
            or row["schema"] != ROLE_SOURCE_SCHEMA
            or row["role"] != role
            or row["expected_host"] != manifest["topology"][role]["host"]
            or row["prepared_redis_target_count"] != 1
            or row["prepared_redis_unsafe_path_count"] != 0
            or row["prepared_redis_entry_count"] != 0
            or row["prepared_redis_pristine"] is not True
            or row["prepared_database_running"] is not False
            or row["prepared_database_healthy"] is not False
        ):
            raise PristineRedisPhaseError(
                f"{role} pristine Redis closure row differs"
            )
        if (
            row["request_artifact_sha256"]
            != inventory[role]["request"]["sha256"]
            or row["response_artifact_sha256"]
            != inventory[role]["response"]["sha256"]
        ):
            raise PristineRedisPhaseError(
                f"{role} source inventory digest differs"
            )
        unsigned = {
            key: item
            for key, item in row.items()
            if key != "source_binding_sha256"
        }
        if row["source_binding_sha256"] != _sha256(
            _canonical_json(unsigned)
        ):
            raise PristineRedisPhaseError(
                f"{role} source binding digest differs"
            )
        for field in (
            "request_artifact_sha256",
            "response_artifact_sha256",
            "request_binding_sha256",
            "agent_sha256",
            "contract_worker_sha256",
            "role_manifest_sha256",
            "prepared_redis_identity_sha256",
            "prepared_redis_chain_metadata_sha256",
            "prepared_redis_metadata_sha256",
        ):
            _nonzero_sha256(row[field], label=f"{role} {field}")
        if row["prepared_redis_identity_sha256"] in redis_identities:
            raise PristineRedisPhaseError(
                "pristine Redis role targets are not distinct"
            )
        redis_identities.add(row["prepared_redis_identity_sha256"])
        captured.append(
            _parse_timestamp(
                row["captured_at"],
                label=f"{role} captured_at",
            )
        )
    if (
        document["captured_at"] != _timestamp(max(captured))
        or max(captured) - min(captured)
        > timedelta(seconds=PREPARED.MAX_ROLE_CAPTURE_SKEW_SECONDS)
    ):
        raise PristineRedisPhaseError(
            "pristine Redis capture chronology differs"
        )
    for field in (
        "controller_challenge_sha256",
        "aggregate_artifact_sha256",
        "aggregate_sha256",
        "final_snapshot_source_closure_sha256",
        "final_snapshot_aggregate_artifact_sha256",
        "legacy_redis_exclusion_sha256",
    ):
        _nonzero_sha256(document[field], label=f"closure {field}")
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "closure_sha256"
    }
    if document["closure_sha256"] != _sha256(
        _canonical_json(unsigned)
    ):
        raise PristineRedisPhaseError(
            "pristine Redis closure digest differs"
        )
    return document


def _build_role_validation(
    *,
    context: EvidenceContext,
    role: str,
    request_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    manifest = context.manifest
    document = {
        "schema": ROLE_VALIDATION_SCHEMA,
        "status": "validated-request",
        "request_sha256": request_sha256,
        "operation": OPERATION,
        "role": role,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "app_release_sha": manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "expected_host": manifest["topology"][role]["host"],
        "observed_host": manifest["topology"][role]["host"],
        "required_journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "business_write_policy": "forbid",
        "agent_artifact_sha256": manifest["artifacts"][
            "host_agent_sha256"
        ],
        "host_agent_contract_sha256": manifest["artifacts"][
            "host_agent_contract_sha256"
        ],
        "transport": manifest["topology"][role]["transport"],
        "observed_at": observed_at,
        "host_identity_observed": True,
        "execution_supported": False,
        "production_contacted": False,
    }
    if set(document) != VERIFY.HOST_AGENT_VALIDATION_FIELDS:
        raise PristineRedisPhaseError(
            f"{role} role validation fields differ"
        )
    return document


def publish_phase_evidence(
    context: EvidenceContext,
    *,
    closure: Mapping[str, Any],
    source_binding_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist create-only evidence and verify it locally."""

    manifest, journal, prior_records = _validated_context(
        context,
        required_position="started",
    )
    closure = validate_closure(context, closure)
    source_binding_sha256 = _nonzero_sha256(
        source_binding_sha256,
        label="source specification binding",
    )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    captured_at = _parse_timestamp(
        closure["captured_at"],
        label="closure captured_at",
    )
    if (
        captured_at > observed_now + VERIFY.MAX_FUTURE_SKEW
        or observed_now - captured_at > VERIFY.MAX_EVIDENCE_AGE
    ):
        raise PristineRedisPhaseError(
            "pristine Redis closure is stale for evidence"
        )
    phase_root = _phase_root(context)
    closure_path, closure_file_sha256 = _persist_document(
        phase_root / "closures",
        root=context.output_root,
        prefix="pristine-redis-closure",
        document=closure,
    )
    role_source_paths: dict[str, str] = {}
    role_source_sha256: dict[str, str] = {}
    role_validation_paths: dict[str, str] = {}
    role_validation_sha256: dict[str, str] = {}
    role_request_sha256: dict[str, str] = {}
    role_observed_at: dict[str, str] = {}
    for role in ROLES:
        source = closure["roles"][role]
        source_path, source_digest = _persist_document(
            phase_root / "role-sources",
            root=context.output_root,
            prefix=f"role-source-{role}",
            document=source,
        )
        role_source_paths[role] = os.fspath(source_path)
        role_source_sha256[role] = source_digest
        request_binding = {
            "phase": PHASE,
            "operation": OPERATION,
            "role": role,
            "source_binding_sha256": source_binding_sha256,
            "closure_file_sha256": closure_file_sha256,
            "role_source_sha256": source_digest,
            "inventory_request_sha256": source[
                "request_artifact_sha256"
            ],
            "inventory_response_sha256": source[
                "response_artifact_sha256"
            ],
        }
        request_digest = _sha256(_canonical_json(request_binding))
        role_request_sha256[role] = request_digest
        role_observed_at[role] = source["captured_at"]
        validation = _build_role_validation(
            context=context,
            role=role,
            request_sha256=request_digest,
            observed_at=source["captured_at"],
        )
        validation_path, validation_digest = _persist_document(
            phase_root / "role-validation",
            root=context.output_root,
            prefix=f"role-validation-{role}",
            document=validation,
        )
        role_validation_paths[role] = os.fspath(validation_path)
        role_validation_sha256[role] = validation_digest
    claim_source_paths: dict[str, str] = {}
    claim_source_sha256: dict[str, str] = {}
    for claim in CLAIMS:
        source = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": PHASE,
            "operation": OPERATION,
            "claim": claim,
            "value": closure["claims"][claim],
            "observed_at": closure["captured_at"],
            "status": "observed",
        }
        if set(source) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise PristineRedisPhaseError(
                f"{claim} claim source fields differ"
            )
        path, digest = _persist_document(
            phase_root / "claim-sources",
            root=context.output_root,
            prefix=f"claim-{claim}",
            document=source,
        )
        claim_source_paths[claim] = os.fspath(path)
        claim_source_sha256[claim] = digest
    prior_rows = [
        {
            "phase": phase,
            "evidence_sha256": context.prior_digests[phase],
        }
        for phase in _prior_phase_names()
    ]
    try:
        prior_claims = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=PHASE,
            prior_digests=dict(context.prior_digests),
            prior_records=prior_records,
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            legacy_release_sha=manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "prior claim bindings are invalid"
        ) from exc
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _sha256(
            _canonical_json(manifest["artifacts"])
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claims,
        "dynamic_claim_values": {
            "redis_target_count": closure["claims"][
                "redis_target_count"
            ]
        },
        "claim_source_sha256": {
            claim: claim_source_sha256[claim]
            for claim in sorted(CLAIMS)
        },
        "role_request_sha256": {
            role: role_request_sha256[role] for role in ROLES
        },
        "role_source_artifact_sha256": {
            role: role_validation_sha256[role] for role in ROLES
        },
        "role_observed_at": {
            role: role_observed_at[role] for role in ROLES
        },
    }
    evidence = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "manifest_artifact_bindings": manifest["artifacts"],
        "phase": PHASE,
        "operation": OPERATION,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": closure["captured_at"],
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
                "expected_host": manifest["topology"][role]["host"],
                "operation": OPERATION,
                "request_sha256": role_request_sha256[role],
                "app_release_sha": manifest["release_sha"],
                "agent_artifact_sha256": manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": role_observed_at[role],
                "status": "verified",
                "transport": manifest["topology"][role]["transport"],
                "source_artifact_sha256": (
                    role_validation_sha256[role]
                ),
            }
            for role in ROLES
        ],
        "claims": {
            claim: {
                "value": closure["claims"][claim],
                "source_sha256": claim_source_sha256[claim],
            }
            for claim in CLAIMS
        },
    }
    if set(evidence) != VERIFY.EVIDENCE_FIELDS:
        raise PristineRedisPhaseError(
            "pristine Redis evidence fields differ"
        )
    verification_arguments = {
        "expected_phase": PHASE,
        "expected_campaign_id": manifest["campaign_id"],
        "expected_operation_id": manifest["operation_id"],
        "expected_release_sha": manifest["release_sha"],
        "expected_legacy_release_sha": manifest["legacy_release_sha"],
        "expected_manifest_sha256": context.manifest_sha256,
        "expected_plan_sha256": context.plan_sha256,
        "expected_approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "expected_phase_evidence_schema_sha256": manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "expected_manifest_artifacts": dict(manifest["artifacts"]),
        "expected_role_request_sha256": role_request_sha256,
        "expected_role_source_artifact_sha256": (
            role_validation_sha256
        ),
        "expected_role_observed_at": role_observed_at,
        "expected_dynamic_claim_values": {
            "redis_target_count": 3
        },
        "expected_claim_source_sha256": claim_source_sha256,
        "expected_prior_phase_evidence_sha256": dict(
            context.prior_digests
        ),
        "prior_phase_evidence_records": prior_records,
        "now": observed_now,
    }
    try:
        VERIFY.verify_phase_evidence(evidence, **verification_arguments)
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "pristine Redis evidence failed local verification"
        ) from exc
    evidence_path, evidence_sha256 = _persist_document(
        phase_root / "evidence",
        root=context.output_root,
        prefix=PHASE,
        document=evidence,
    )
    try:
        readback, readback_sha256 = VERIFY.read_root_only_evidence(
            evidence_path
        )
        if readback != evidence or readback_sha256 != evidence_sha256:
            raise VERIFY.PhaseEvidenceError(
                "pristine Redis evidence readback differs"
            )
        local_verification = VERIFY.verify_phase_evidence(
            readback,
            evidence_file_sha256=evidence_sha256,
            **verification_arguments,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise PristineRedisPhaseError(
            "persisted pristine Redis evidence failed verification"
        ) from exc
    local_path, local_sha256 = _persist_document(
        phase_root / "local-verification",
        root=context.output_root,
        prefix="local-verification",
        document=local_verification,
    )
    publication = {
        "schema": PUBLICATION_SCHEMA,
        "status": "published-create-only-readback-verified",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "source_binding_sha256": source_binding_sha256,
        "closure_path": os.fspath(closure_path),
        "closure_file_sha256": closure_file_sha256,
        "role_source_paths": role_source_paths,
        "role_source_sha256": role_source_sha256,
        "role_validation_paths": role_validation_paths,
        "role_validation_sha256": role_validation_sha256,
        "claim_source_paths": claim_source_paths,
        "claim_source_sha256": claim_source_sha256,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "local_verification_path": os.fspath(local_path),
        "local_verification_sha256": local_sha256,
        "journal_status": journal["status"],
        "journal_mutated": False,
        "production_contacted": False,
        "redis_mutated": False,
        "caller_truth_values_accepted": False,
        "create_only": True,
        "readback_verified": True,
    }
    if set(publication) != PUBLICATION_FIELDS:
        raise PristineRedisPhaseError(
            "pristine Redis publication fields differ"
        )
    return publication


def _publication_index_path(
    context: EvidenceContext,
    *,
    source_binding_sha256: str,
) -> Path:
    return (
        _phase_root(context)
        / "resume"
        / f"publication.{source_binding_sha256}.json"
    )


def _verification_candidate_path(
    context: EvidenceContext,
    *,
    source_binding_sha256: str,
) -> Path:
    return (
        _phase_root(context)
        / "resume"
        / f"verification.{source_binding_sha256}.json"
    )


def _persist_publication_index(
    context: EvidenceContext,
    *,
    source_binding_sha256: str,
    publication: Mapping[str, Any],
) -> Path:
    index = {
        "schema": PUBLICATION_INDEX_SCHEMA,
        "phase": PHASE,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "source_binding_sha256": source_binding_sha256,
        "publication": dict(publication),
    }
    path = _publication_index_path(
        context,
        source_binding_sha256=source_binding_sha256,
    )
    _persist_fixed_bytes(
        path,
        root=context.output_root,
        payload=_canonical_json(index) + b"\n",
        label="pristine Redis publication index",
    )
    return path


def _load_publication_index(
    context: EvidenceContext,
    *,
    source_binding_sha256: str,
) -> dict[str, Any] | None:
    path = _publication_index_path(
        context,
        source_binding_sha256=source_binding_sha256,
    )
    if not path.exists():
        return None
    document, _payload = _read_strict_document(
        path,
        label="pristine Redis publication index",
    )
    expected_fields = {
        "schema",
        "phase",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "plan_sha256",
        "source_binding_sha256",
        "publication",
    }
    expected = {
        "schema": PUBLICATION_INDEX_SCHEMA,
        "phase": PHASE,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "source_binding_sha256": source_binding_sha256,
    }
    publication = document.get("publication")
    if (
        set(document) != expected_fields
        or any(
            document.get(field) != value
            for field, value in expected.items()
        )
        or not isinstance(publication, dict)
        or set(publication) != PUBLICATION_FIELDS
        or publication.get("source_binding_sha256")
        != source_binding_sha256
        or publication.get("phase") != PHASE
        or publication.get("manifest_sha256")
        != context.manifest_sha256
        or publication.get("plan_sha256") != context.plan_sha256
        or publication.get("create_only") is not True
        or publication.get("readback_verified") is not True
        or publication.get("production_contacted") is not False
        or publication.get("redis_mutated") is not False
    ):
        raise PristineRedisPhaseError(
            "persisted publication index differs"
        )
    path_fields = {
        "closure_path": publication["closure_file_sha256"],
        "phase_evidence_path": publication["phase_evidence_sha256"],
        "local_verification_path": publication[
            "local_verification_sha256"
        ],
    }
    for mapping_name, digest_name in (
        ("role_source_paths", "role_source_sha256"),
        ("role_validation_paths", "role_validation_sha256"),
        ("claim_source_paths", "claim_source_sha256"),
    ):
        paths = publication[mapping_name]
        digests = publication[digest_name]
        expected_keys = set(ROLES) if mapping_name.startswith("role_") else set(CLAIMS)
        if (
            not isinstance(paths, dict)
            or set(paths) != expected_keys
            or not isinstance(digests, dict)
            or set(digests) != expected_keys
        ):
            raise PristineRedisPhaseError(
                "persisted publication source mapping differs"
            )
        path_fields.update(
            {paths[key]: digests[key] for key in expected_keys}
        )
    for raw_path, expected_digest in path_fields.items():
        path = _absolute_path(raw_path, label="publication artifact")
        try:
            path.relative_to(_phase_root(context))
        except ValueError as exc:
            raise PristineRedisPhaseError(
                "publication artifact escapes phase root"
            ) from exc
        _nonzero_sha256(
            expected_digest,
            label="publication artifact digest",
        )
        _document, payload = _read_strict_document(
            path,
            label="publication artifact",
        )
        if _sha256(payload) != expected_digest:
            raise PristineRedisPhaseError(
                "publication artifact readback differs"
            )
    evidence, evidence_digest = VERIFY.read_root_only_evidence(
        Path(publication["phase_evidence_path"])
    )
    if (
        evidence_digest != publication["phase_evidence_sha256"]
        or evidence.get("phase") != PHASE
        or evidence.get("manifest_sha256") != context.manifest_sha256
        or evidence.get("plan_sha256") != context.plan_sha256
        or evidence.get("status") != "passed"
        or evidence.get("business_write_observed") is not False
    ):
        raise PristineRedisPhaseError(
            "publication evidence differs"
        )
    return publication


def _verify_runtime_authorization(context: EvidenceContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            dict(context.manifest),
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise PristineRedisPhaseError(
            "production approval is invalid or expired"
        ) from exc


def _load_verification_candidate(
    context: EvidenceContext,
    *,
    source_binding_sha256: str,
    evidence_sha256: str,
) -> tuple[CONTROLLER.VerifiedPhaseCompletion, bytes] | None:
    path = _verification_candidate_path(
        context,
        source_binding_sha256=source_binding_sha256,
    )
    if not path.exists():
        return None
    try:
        receipt = read_secure_bytes(
            path,
            label="pristine Redis verification candidate",
            owner_uid=0,
            max_size=64 * 1024,
        )
        result = json.loads(
            receipt.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        verification, canonical = (
            CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
                result,
                phase=PHASE,
                manifest=dict(context.manifest),
                manifest_sha256=context.manifest_sha256,
                plan_sha256=context.plan_sha256,
            )
        )
    except (
        CONTROLLER.CutoverContractError,
        SecureFileError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PristineRedisPhaseError(
            "verification candidate is invalid"
        ) from exc
    if (
        canonical != receipt
        or verification.evidence_sha256 != evidence_sha256
        or verification.phase != PHASE
    ):
        raise PristineRedisPhaseError(
            "verification candidate differs from publication"
        )
    return verification, receipt


def _load_completed_phase(
    context: EvidenceContext,
    *,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    evidence_sha256 = state["phase_evidence_sha256"][PHASE]
    receipt_sha256 = state["phase_verification_sha256"][PHASE]
    evidence_path = (
        _phase_root(context)
        / "evidence"
        / f"{PHASE}.{evidence_sha256}.json"
    )
    receipt_path = (
        context.output_root
        / "verification"
        / f"{PHASE}.{receipt_sha256}.json"
    )
    try:
        evidence, observed_evidence_sha256 = (
            VERIFY.read_root_only_evidence(evidence_path)
        )
        receipt = read_secure_bytes(
            receipt_path,
            label="pristine Redis release verification receipt",
            owner_uid=0,
            max_size=64 * 1024,
        )
        result = json.loads(
            receipt.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        verification, canonical = (
            CONTROLLER._validate_phase_verification_result(  # noqa: SLF001
                result,
                phase=PHASE,
                manifest=dict(context.manifest),
                manifest_sha256=context.manifest_sha256,
                plan_sha256=context.plan_sha256,
            )
        )
    except (
        CONTROLLER.CutoverContractError,
        SecureFileError,
        VERIFY.PhaseEvidenceError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise PristineRedisPhaseError(
            "completed pristine Redis phase is unavailable"
        ) from exc
    if (
        observed_evidence_sha256 != evidence_sha256
        or evidence.get("phase") != PHASE
        or evidence.get("status") != "passed"
        or evidence.get("business_write_observed") is not False
        or canonical != receipt
        or _sha256(receipt) != receipt_sha256
        or verification.evidence_sha256 != evidence_sha256
        or verification.receipt_sha256 != receipt_sha256
    ):
        raise PristineRedisPhaseError(
            "completed pristine Redis phase differs from journal"
        )
    return {
        "status": "completed-reused",
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": receipt_sha256,
    }


def build_begin_capture_plan(
    context: EvidenceContext,
    *,
    request_sha256: str,
) -> dict[str, Any]:
    """Plan the explicit durable checkpoint preceding fresh collection."""

    manifest, _journal, _prior = _validated_context(context)
    request_sha256 = _nonzero_sha256(
        request_sha256,
        label="capture-begin request",
    )
    body = {
        "schema": BEGIN_PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "controller_plan_sha256": context.plan_sha256,
        "request_sha256": request_sha256,
        "journal_position_accepted": "ready-or-started",
        "journal_begin_only": True,
        "receipt_reference_accepted": False,
        "claim_values_accepted": False,
        "fresh_stopped_capture_required_after_begin": True,
        "runtime_authorization_required": True,
        "external_controller_liveness_required": True,
        "production_contacted": False,
        "redis_mutated": False,
        "journal_mutated": False,
    }
    plan_sha256 = _sha256(_canonical_json(body))
    return {
        **body,
        "plan_sha256": plan_sha256,
        "required_confirmation": (
            f"begin-{PHASE}:{manifest['operation_id']}:"
            f"{manifest['release_sha']}:{request_sha256}:"
            f"{plan_sha256}"
        ),
    }


def begin_capture_phase(
    context: EvidenceContext,
    *,
    request_sha256: str,
    confirm: str,
    control_fd: int,
    journal_factory: Any = CONTROLLER.ProductionCutoverJournal,
    liveness_factory: Any = PREPARED.ControllerLiveness,
    signal_authority_factory: Any = PREPARED._signal_authority,  # noqa: SLF001
    authorization_verifier: Any = _verify_runtime_authorization,
) -> dict[str, Any]:
    """Durably begin phase 9, without accepting a pre-existing receipt."""

    plan = build_begin_capture_plan(
        context,
        request_sha256=request_sha256,
    )
    if confirm != plan["required_confirmation"]:
        raise PristineRedisPhaseError(
            "capture-begin requires exact digest-bound confirmation"
        )
    if type(control_fd) is not int or control_fd < 0:
        raise PristineRedisPhaseError(
            "capture-begin requires controller liveness"
        )
    if not all(
        callable(item)
        for item in (
            journal_factory,
            liveness_factory,
            signal_authority_factory,
            authorization_verifier,
        )
    ):
        raise PristineRedisPhaseError(
            "capture-begin dependency is unavailable"
        )
    try:
        journal = journal_factory(context.journal_path)
        state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise PristineRedisPhaseError(
            "capture-begin journal binding differs"
        ) from exc
    live_context = replace(context, journal=state)
    _validated_context(live_context)
    mutated = False
    try:
        with (
            signal_authority_factory(),
            liveness_factory(control_fd) as liveness,
        ):
            liveness.check()
            authorization_verifier(live_context)
            if (
                state["status"] == "active"
                and PHASE not in state["completed_phases"]
            ):
                state = journal.begin_phase(PHASE)
                mutated = True
            state = journal.assert_bindings(
                **_journal_bindings(context)
            )
            live_context = replace(context, journal=state)
            if PHASE in state["completed_phases"]:
                raise PristineRedisPhaseError(
                    "capture-begin cannot report a completed phase; "
                    "use phase apply for verified completion readback"
                )
            _validated_context(
                live_context,
                required_position="started",
            )
            liveness.check()
            authorization_verifier(live_context)
    except (
        CONTROLLER.CutoverContractError,
        PREPARED.PreparedCloneInventoryError,
    ) as exc:
        raise PristineRedisPhaseError(
            "capture-begin failed closed"
        ) from exc
    capture_binding = {
        "phase": PHASE,
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "controller_plan_sha256": context.plan_sha256,
        "journal_started_at": state["started_at"],
        "expected_database_state": "stopped",
        "prepared_receipt_output_root": os.fspath(
            context.output_root
        ),
        "receipt_challenge_generated_by_collector": True,
        "claim_values_included": False,
        "production_contacted": False,
        "redis_mutated": False,
    }
    return {
        **plan,
        "status": (
            "capture-required"
            if mutated
            else "capture-required-reused"
        ),
        "capture_binding": capture_binding,
        "capture_binding_sha256": _sha256(
            _canonical_json(capture_binding)
        ),
        "journal_status": state["status"],
        "journal_mutated": mutated,
        "production_contacted": False,
        "redis_mutated": False,
    }


def execute_begin_capture(
    context: EvidenceContext,
    *,
    request_sha256: str,
    apply: bool = False,
    confirm: str | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    plan = build_begin_capture_plan(
        context,
        request_sha256=request_sha256,
    )
    if not apply:
        if confirm is not None or control_fd is not None:
            raise PristineRedisPhaseError(
                "capture-begin plan rejects confirmation or liveness"
            )
        return plan
    if control_fd is None:
        raise PristineRedisPhaseError(
            "capture-begin apply requires liveness"
        )
    return begin_capture_phase(
        context,
        request_sha256=request_sha256,
        confirm=confirm or "",
        control_fd=control_fd,
    )


def build_plan(
    *,
    operation_id: str,
    release_sha: str,
    source_available: bool,
    manifest_sha256: str | None = None,
    controller_plan_sha256: str | None = None,
    source_binding_sha256: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(operation_id, str)
        or not isinstance(release_sha, str)
        or CONTROLLER.SHA_RE.fullmatch(release_sha) is None
        or type(source_available) is not bool
    ):
        raise PristineRedisPhaseError("phase plan identity is invalid")
    bindings = (
        manifest_sha256,
        controller_plan_sha256,
        source_binding_sha256,
    )
    if source_available:
        for value, label in zip(
            bindings,
            ("manifest", "controller plan", "source"),
            strict=True,
        ):
            _nonzero_sha256(value, label=f"phase plan {label}")
    elif any(value is not None for value in bindings):
        raise PristineRedisPhaseError(
            "unavailable source plan contains apply bindings"
        )
    body = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "roles": list(ROLES),
        "claims": list(CLAIMS),
        "fresh_stopped_prepared_receipt_required": True,
        "source_artifact_count": 7,
        "journal_begin_required_before_source_capture": True,
        "manifest_derived_output_root_required": True,
        "release_verifier_required": True,
        "runtime_authorization_required": True,
        "external_controller_liveness_required": True,
        "redis_mutation_allowed": False,
        "legacy_redis_restore_allowed": False,
        "caller_truth_values_accepted": False,
        "source_available": source_available,
        "manifest_sha256": manifest_sha256,
        "controller_plan_sha256": controller_plan_sha256,
        "source_binding_sha256": source_binding_sha256,
        "apply_supported": source_available,
        "production_contacted": False,
        "journal_mutated": False,
    }
    digest = _sha256(_canonical_json(body))
    return {
        **body,
        "plan_sha256": digest,
        "required_confirmation": (
            f"run-{PHASE}:{operation_id}:{release_sha}:{digest}"
        ),
    }


def apply_phase(
    context: EvidenceContext,
    *,
    source_spec: PersistedReceiptSpec,
    confirm: str,
    control_fd: int,
    now: datetime | None = None,
    journal_factory: Any = CONTROLLER.ProductionCutoverJournal,
    liveness_factory: Any = PREPARED.ControllerLiveness,
    signal_authority_factory: Any = PREPARED._signal_authority,  # noqa: SLF001
    authorization_verifier: Any = _verify_runtime_authorization,
    release_verifier: Any = CONTROLLER._run_release_phase_verifier,  # noqa: SLF001
    receipt_persister: Any = CONTROLLER._persist_phase_verification_receipt,  # noqa: SLF001
    completed_reader: Any = _load_completed_phase,
) -> dict[str, Any]:
    """Apply or resume the phase without contacting or mutating Redis."""

    manifest, _state, _prior = _validated_context(context)
    _source_document, source_binding_sha256 = _source_spec_binding(
        source_spec
    )
    plan = build_plan(
        operation_id=manifest["operation_id"],
        release_sha=manifest["release_sha"],
        source_available=True,
        manifest_sha256=context.manifest_sha256,
        controller_plan_sha256=context.plan_sha256,
        source_binding_sha256=source_binding_sha256,
    )
    if confirm != plan["required_confirmation"]:
        raise PristineRedisPhaseError(
            "phase apply requires exact digest-bound confirmation"
        )
    if type(control_fd) is not int or control_fd < 0:
        raise PristineRedisPhaseError(
            "phase apply requires controller liveness"
        )
    dependencies = (
        journal_factory,
        liveness_factory,
        signal_authority_factory,
        authorization_verifier,
        release_verifier,
        receipt_persister,
        completed_reader,
    )
    if not all(callable(item) for item in dependencies):
        raise PristineRedisPhaseError(
            "phase apply dependency is unavailable"
        )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    try:
        journal = journal_factory(context.journal_path)
        state = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise PristineRedisPhaseError(
            "pristine Redis journal binding differs"
        ) from exc
    live_context = replace(context, journal=state)
    _validated_context(live_context)
    if PHASE in state["completed_phases"]:
        reused = completed_reader(live_context, state=state)
        return {
            **plan,
            **reused,
            "journal_mutated": False,
            "production_contacted": False,
            "redis_mutated": False,
        }
    _validated_context(
        live_context,
        required_position="started",
    )
    try:
        with (
            signal_authority_factory(),
            liveness_factory(control_fd) as liveness,
        ):
            liveness.check()
            authorization_verifier(live_context)
            state = journal.assert_bindings(**_journal_bindings(context))
            live_context = replace(context, journal=state)
            _validated_context(
                live_context,
                required_position="started",
            )
            liveness.check()
            authorization_verifier(live_context)
            publication = _load_publication_index(
                live_context,
                source_binding_sha256=source_binding_sha256,
            )
            if publication is None:
                closure = load_pristine_closure(
                    live_context,
                    source_spec,
                    now=observed_now,
                )
                liveness.check()
                authorization_verifier(live_context)
                publication = publish_phase_evidence(
                    live_context,
                    closure=closure,
                    source_binding_sha256=source_binding_sha256,
                    now=observed_now,
                )
                _persist_publication_index(
                    live_context,
                    source_binding_sha256=source_binding_sha256,
                    publication=publication,
                )
            liveness.check()
            authorization_verifier(live_context)
            candidate = _load_verification_candidate(
                live_context,
                source_binding_sha256=source_binding_sha256,
                evidence_sha256=publication[
                    "phase_evidence_sha256"
                ],
            )
            if candidate is None:
                verification, receipt = release_verifier(
                    phase=PHASE,
                    manifest=dict(manifest),
                    manifest_sha256=context.manifest_sha256,
                    plan=dict(context.plan),
                    manifest_path=context.manifest_path,
                    approval_path=context.approval_path,
                    approval_policy_path=context.approval_policy_path,
                    evidence_path=Path(
                        publication["phase_evidence_path"]
                    ),
                    role_validation=[
                        (
                            f"{role}="
                            f"{publication['role_validation_paths'][role]}"
                        )
                        for role in ROLES
                    ],
                    claim_source=[
                        (
                            f"{claim}="
                            f"{publication['claim_source_paths'][claim]}"
                        )
                        for claim in CLAIMS
                    ],
                    prior_phase_evidence=[
                        f"{phase}={context.prior_paths[phase]}"
                        for phase in _prior_phase_names()
                    ],
                )
                if (
                    not isinstance(
                        verification,
                        CONTROLLER.VerifiedPhaseCompletion,
                    )
                    or verification.phase != PHASE
                    or verification.evidence_sha256
                    != publication["phase_evidence_sha256"]
                    or _sha256(receipt)
                    != verification.receipt_sha256
                ):
                    raise PristineRedisPhaseError(
                        "release verifier completion differs"
                    )
                _persist_fixed_bytes(
                    _verification_candidate_path(
                        live_context,
                        source_binding_sha256=source_binding_sha256,
                    ),
                    root=live_context.output_root,
                    payload=receipt,
                    label="pristine Redis verification candidate",
                    maximum=64 * 1024,
                )
                candidate = _load_verification_candidate(
                    live_context,
                    source_binding_sha256=source_binding_sha256,
                    evidence_sha256=publication[
                        "phase_evidence_sha256"
                    ],
                )
                if candidate is None:
                    raise PristineRedisPhaseError(
                        "verification candidate was not persisted"
                    )
            verification, receipt = candidate
            liveness.check()
            authorization_verifier(live_context)
            receipt_path = receipt_persister(
                token=verification,
                receipt=receipt,
                evidence_root=context.output_root,
            )
            liveness.check()
            authorization_verifier(live_context)
            completed = journal.complete_phase(
                PHASE,
                verification=verification,
            )
            liveness.check()
    except (
        CONTROLLER.CutoverContractError,
        PREPARED.PreparedCloneInventoryError,
        VERIFY.PhaseEvidenceError,
        SecureFileError,
    ) as exc:
        raise PristineRedisPhaseError(
            "pristine Redis phase apply failed closed"
        ) from exc
    final_context = replace(context, journal=completed)
    _validated_context(
        final_context,
        required_position="completed",
    )
    if (
        completed["phase_evidence_sha256"][PHASE]
        != verification.evidence_sha256
        or completed["phase_verification_sha256"][PHASE]
        != verification.receipt_sha256
    ):
        raise PristineRedisPhaseError(
            "pristine Redis journal completion differs"
        )
    return {
        **plan,
        "status": "completed",
        "publication": publication,
        "phase_evidence_path": publication["phase_evidence_path"],
        "phase_evidence_sha256": verification.evidence_sha256,
        "verification_receipt_path": os.fspath(receipt_path),
        "verification_receipt_sha256": verification.receipt_sha256,
        "journal_status": completed["status"],
        "journal_mutated": True,
        "production_contacted": False,
        "redis_mutated": False,
    }


def execute(
    *,
    operation_id: str,
    release_sha: str,
    apply: bool = False,
    confirm: str | None = None,
    context: EvidenceContext | None = None,
    source_spec: PersistedReceiptSpec | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    """Plan or apply the local public phase bridge."""

    available = (
        isinstance(context, EvidenceContext)
        and isinstance(source_spec, PersistedReceiptSpec)
    )
    if available:
        _validated_context(context)
        _spec, source_binding_sha256 = _source_spec_binding(
            source_spec
        )
        manifest_sha256 = context.manifest_sha256
        controller_plan_sha256 = context.plan_sha256
    else:
        source_binding_sha256 = None
        manifest_sha256 = None
        controller_plan_sha256 = None
    plan = build_plan(
        operation_id=operation_id,
        release_sha=release_sha,
        source_available=available,
        manifest_sha256=manifest_sha256,
        controller_plan_sha256=controller_plan_sha256,
        source_binding_sha256=source_binding_sha256,
    )
    if not apply:
        if confirm is not None or control_fd is not None:
            raise PristineRedisPhaseError(
                "phase plan does not accept confirmation or liveness"
            )
        return plan
    if (
        not available
        or context is None
        or source_spec is None
        or control_fd is None
    ):
        raise PristineRedisPhaseError(
            "phase apply requires trusted persisted sources and liveness"
        )
    if (
        operation_id != context.manifest["operation_id"]
        or release_sha != context.manifest["release_sha"]
    ):
        raise PristineRedisPhaseError(
            "phase apply identity differs from trusted context"
        )
    return apply_phase(
        context,
        source_spec=source_spec,
        confirm=confirm or "",
        control_fd=control_fd,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--begin-capture", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--controller-liveness-fd", type=int)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.begin_capture and args.apply:
            raise PristineRedisPhaseError(
                "capture-begin and phase apply are mutually exclusive"
            )
        request_header, _payload = _read_strict_document(
            args.request,
            label="pristine Redis routed request",
        )
        request_schema = request_header.get("schema")
        if request_schema == BEGIN_REQUEST_SCHEMA:
            if args.apply:
                raise PristineRedisPhaseError(
                    "capture-begin request rejects phase apply"
                )
            context, request_sha256 = load_begin_request(
                args.request
            )
            result = execute_begin_capture(
                context,
                request_sha256=request_sha256,
                apply=args.begin_capture,
                confirm=args.confirm,
                control_fd=args.controller_liveness_fd,
            )
        elif request_schema == REQUEST_SCHEMA:
            if args.begin_capture:
                raise PristineRedisPhaseError(
                    "phase request rejects capture-begin"
                )
            context, source_spec, _request_sha256 = (
                load_phase_request(args.request)
            )
            result = execute(
                operation_id=context.manifest["operation_id"],
                release_sha=context.manifest["release_sha"],
                apply=args.apply,
                confirm=args.confirm,
                context=context,
                source_spec=source_spec,
                control_fd=args.controller_liveness_fd,
            )
        else:
            raise PristineRedisPhaseError(
                "pristine Redis request schema is unsupported"
            )
        status = 0
    except PristineRedisPhaseError:
        result = {
            "schema": RESULT_SCHEMA,
            "status": "blocked",
            "error": "pristine-shadow-Redis phase failed closed",
            "production_contacted": False,
            "redis_mutated": False,
            "journal_mutated": False,
        }
        status = 1
    sys.stdout.buffer.write(_canonical_json(result) + b"\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
