#!/usr/bin/env python3
"""Produce exact cutover evidence for the completed frozen-final restore.

The producer is deliberately local and read-only by default.  It normalizes
already-collected, root-only role and claim records into the exact
``shadow_restore`` phase evidence contract.  It never runs the restore,
contacts a host, or advances the cutover journal.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_final_restore as RESTORE,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import (  # noqa: E402
    production_shadow_frozen_final_restore_worker as WORKER,
)
from scripts import (  # noqa: E402
    production_shadow_global_docker_inventory_agent as INVENTORY,
)
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


PHASE = "shadow_restore"
PUBLICATION_SCHEMA = (
    "production-shadow-frozen-final-restore-phase-evidence-publication-v1"
)
DERIVATION_SCHEMA = (
    "production-shadow-frozen-final-restore-claim-derivation-v1"
)
INVENTORY_BASELINE_SCHEMA = (
    "production-shadow-global-docker-inventory-three-role-baseline-v2"
)
INVENTORY_CLOSURE_SCHEMA = (
    "production-shadow-global-docker-inventory-zero-delta-closure-v2"
)
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DERIVATION_BYTES = 64 * 1024 * 1024
OUTPUT_MODE = 0o600
OUTPUT_DIRECTORY_MODE = 0o700
ZERO_SHA256 = "0" * 64

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
DERIVED_CLAIM_FIELDS = frozenset(
    {"value", "source_path", "source_sha256"}
)
COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "restore_set_sha256",
        "restore_generation_sha256",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "live_lease_claim_nonce",
        "legacy_frozen_receipt_sha256",
        "roles",
        "role_order",
        "claim_consume_outcome",
        "claim_consumed",
        "consumption_receipt_included",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated_by_restore",
        "app_services_started",
        "redis_restored",
    }
)
COMPLETION_ROLE_FIELDS = frozenset(
    {
        "source_role",
        "transport",
        "host_result",
        "host_result_sha256",
        "role_manifest_sha256",
        "installer_receipt_sha256",
        "restore_result_sha256",
        "final_evidence_sha256",
    }
)
INVENTORY_BASELINE_FIELDS = frozenset(
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
        "roles",
        "role_order",
        "complete_before_restore",
        "operation_resource_count",
        "operation_host_config_sha256_by_role",
    }
)
INVENTORY_CLOSURE_FIELDS = frozenset(
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
        "baseline_path",
        "baseline_sha256",
        "completion_path",
        "completion_sha256",
        "role_order",
        "roles",
        "non_operation_resource_delta_count",
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
ROLE_DERIVATION_FIELDS = frozenset({"path", "sha256"})
CONSUMPTION_AUDIT_FIELDS = frozenset(
    {
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
)


class FrozenFinalRestorePhaseEvidenceError(RuntimeError):
    """The normalized restore closure could not produce exact phase evidence."""


@dataclass(frozen=True)
class PreparedEvidence:
    document: dict[str, Any]
    payload: bytes
    evidence_sha256: str
    output: Path
    required_confirmation: str
    verification: dict[str, Any]
    manifest_sha256: str
    plan_sha256: str


@dataclass(frozen=True)
class DerivedEvidenceInputs:
    derivation_path: Path
    derivation_sha256: str
    manifest_path: Path
    output_directory: Path
    role_validation: tuple[str, ...]
    claim_source: tuple[str, ...]
    prior_phase_evidence: tuple[str, ...]
    role_source_sha256: Mapping[str, str]
    claim_source_sha256: Mapping[str, str]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _absolute_path(value: Path | str, *, label: str) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(path))
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _parse_path_mapping(
    values: Sequence[str],
    *,
    expected: Sequence[str],
    label: str,
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, separator, raw_path = str(value).partition("=")
        if not separator or not key or not raw_path or key in result:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{label} mapping is invalid"
            )
        result[key] = _absolute_path(
            raw_path,
            label=f"{label} {key}",
        )
    if set(result) != set(expected):
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} mapping is not exact"
        )
    return result


def _secure_json(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[dict[str, Any], str]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=maximum,
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
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} is not secure strict JSON"
        ) from exc
    if not isinstance(document, dict):
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} root must be an object"
        )
    return document, hashlib.sha256(payload).hexdigest()


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} timestamp is invalid"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} timestamp lacks a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_journal(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    plan_sha256: str,
) -> dict[str, Any]:
    path = _absolute_path(
        manifest["deployment"]["controller_journal_path"],
        label="cutover journal",
    )
    document, _digest = _secure_json(
        path,
        label="production cutover journal",
    )
    try:
        journal = CONTROLLER._validate_journal(document)  # noqa: SLF001
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
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
        raise FrozenFinalRestorePhaseEvidenceError(
            "cutover journal differs from the manifest or plan"
        )
    phase_index = CONTROLLER.PHASES.index(PHASE)
    expected_prefix = list(CONTROLLER.PHASES[:phase_index])
    if (
        journal["status"] != "phase_started"
        or journal["started_phase"] != PHASE
        or journal["completed_phases"] != expected_prefix
        or set(journal["phase_evidence_sha256"])
        != set(expected_prefix)
        or set(journal["phase_verification_sha256"])
        != set(expected_prefix)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "cutover journal is not durably started at shadow_restore "
            "with the exact prior phase prefix"
        )
    return journal


def _read_prior_records(
    paths: Mapping[str, Path],
    *,
    journal: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for phase in CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]:
        try:
            document, digest = VERIFY.read_root_only_evidence(paths[phase])
        except VERIFY.PhaseEvidenceError as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"prior phase evidence {phase} is unavailable or unsafe"
            ) from exc
        if digest != journal["phase_evidence_sha256"][phase]:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"prior phase evidence {phase} differs from the journal"
            )
        records[phase] = {
            "document": document,
            "file_sha256": digest,
        }
        digests[phase] = digest
    return records, digests


def _claim_source_details(
    paths: Mapping[str, Path],
    *,
    expected_hashes: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    values: dict[str, Any] = {}
    observed: dict[str, str] = {}
    for claim in VERIFY.PHASE_CLAIM_RULES[PHASE]:
        document, digest = _secure_json(
            paths[claim],
            label=f"{PHASE} claim source {claim}",
        )
        if (
            digest != expected_hashes[claim]
            or document.get("claim") != claim
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"claim source {claim} changed after validation"
        )
        observed_at = document.get("observed_at")
        _timestamp(observed_at, label=f"claim source {claim}")
        values[claim] = document.get("value")
        observed[claim] = str(observed_at)
    return values, observed


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or CONTROLLER.SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _identity_values(
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    plan_sha256: str,
) -> dict[str, Any]:
    return {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
    }


def _validate_restore_set(
    path: Path,
    expected_sha256: str,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    try:
        document, digest = WORKER.load_restore_set(
            path,
            require_publication_namespace=False,
        )
    except WORKER.FrozenFinalRestoreWorkerError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derivation restore set is invalid"
        ) from exc
    expected = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "controller_manifest_sha256": manifest_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
    }
    if (
        digest != expected_sha256
        or any(document.get(field) != value for field, value in expected.items())
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derivation restore set differs from the cutover manifest"
        )
    return document


def _validate_completion(
    path: Path,
    expected_sha256: str,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    restore_set: Mapping[str, Any],
    restore_set_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    completion, digest = _secure_json(
        path,
        label="derived frozen restore completion",
        maximum=RESTORE.MAX_COMPLETION_BYTES,
    )
    expected = {
        "schema": RESTORE.COMPLETION_SCHEMA,
        "status": "three-role-frozen-final-restored",
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "controller_manifest_sha256": manifest_sha256,
        "restore_set_sha256": restore_set_sha256,
        "restore_generation_sha256": restore_set[
            "restore_generation_sha256"
        ],
        "role_order": list(RESTORE.ROLES),
        "claim_consume_outcome": WORKER.LIVE_LEASE_SUCCESS_OUTCOME,
        "claim_consumed": False,
        "consumption_receipt_included": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated_by_restore": False,
        "app_services_started": False,
        "redis_restored": False,
    }
    if (
        digest != expected_sha256
        or set(completion) != COMPLETION_FIELDS
        or any(completion.get(field) != value for field, value in expected.items())
        or not isinstance(completion.get("roles"), dict)
        or set(completion["roles"]) != set(RESTORE.ROLES)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived frozen restore completion fields differ"
        )
    requests: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    claim_sha256 = _nonzero_sha256(
        completion["live_lease_claim_sha256"],
        label="restore completion live claim",
    )
    for role in RESTORE.ROLES:
        row = completion["roles"][role]
        if not isinstance(row, dict) or set(row) != COMPLETION_ROLE_FIELDS:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived completion {role} fields differ"
            )
        request_path = RESTORE._prepared_request_path(  # noqa: SLF001
            path.parent,
            role=role,
            claim_sha256=claim_sha256,
        )
        request, _request_digest = _secure_json(
            request_path,
            label=f"{role} persisted restore request",
        )
        try:
            requests[role] = RESTORE.validate_host_request(request)
        except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} persisted restore request is invalid"
            ) from exc
        host_result = row["host_result"]
        if not isinstance(host_result, dict):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} completion host result is invalid"
            )
        results[role] = host_result
    try:
        wa_version = RESTORE.validate_wa_exact_version(
            requests["webapp_ir"]["wa_exact_version"]
        )
        fresh_wa_version = RESTORE.validate_wa_fresh_control_version(
            requests["webapp_ir"]["wa_fresh_control_exact_version"],
            request=requests["webapp_ir"],
        )
    except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "WebApp-IR completion transport is invalid"
        ) from exc
    sealed_wa = restore_set["webapp_ir_transport"]
    wa_bindings = {
        "provider": "provider",
        "private": "private",
        "versioned": "versioned",
        "encryption": "encryption",
        "bucket": "bucket",
        "recipient": "recipient",
        "object_key": "object_key",
        "version_id": "version_id",
        "ciphertext_sha256": "ciphertext_sha256",
        "readback_receipt_sha256": "readback_receipt_sha256",
        "exact_version_readback_verified": (
            "exact_version_readback_verified"
        ),
    }
    if any(
        wa_version[request_field] != sealed_wa[restore_field]
        for request_field, restore_field in wa_bindings.items()
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "WebApp-IR completion VersionId differs from the restore set"
        )
    if (
        fresh_wa_version["version_id"] == wa_version["version_id"]
        or fresh_wa_version["object_key"] == wa_version["object_key"]
        or fresh_wa_version["bucket"] != wa_version["bucket"]
        or fresh_wa_version["recipient"] != wa_version["recipient"]
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "WebApp-IR fresh-control object is not new and distinct"
        )
    try:
        rebuilt, rebuilt_sha256 = RESTORE.build_completion(
            requests,
            results,
        )
    except RESTORE.FrozenFinalRestoreOrchestratorError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived completion cannot be rebuilt from persisted requests"
        ) from exc
    canonical_output = RESTORE.canonical_controller_output_directory(
        requests
    )
    if (
        path
        != canonical_output / f"completion-{expected_sha256}.json"
        or rebuilt != completion
        or rebuilt_sha256 != expected_sha256
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived completion differs from its persisted role closure"
        )
    return completion, requests


def _validate_post_consumption(
    path: Path,
    expected_sha256: str,
    *,
    completion_path: Path,
    completion: Mapping[str, Any],
    completion_sha256: str,
) -> None:
    document, digest = _secure_json(
        path,
        label="derived restore post-consumption receipt",
    )
    try:
        expected, expected_digest = RESTORE.build_post_consumption_receipt(
            completion_path=completion_path,
            completion_sha256=completion_sha256,
            completion=completion,
            consumption_path=_absolute_path(
                document.get("consumption_path", ""),
                label="restore consumption receipt",
            ),
            consumption_sha256=_nonzero_sha256(
                document.get("consumption_sha256"),
                label="restore consumption receipt",
            ),
        )
    except (
        RESTORE.FrozenFinalRestoreOrchestratorError,
        KeyError,
    ) as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived restore post-consumption receipt is invalid"
        ) from exc
    if (
        digest != expected_sha256
        or expected_digest != expected_sha256
        or document != expected
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived restore post-consumption receipt differs"
        )
    consumption_path = _absolute_path(
        document["consumption_path"],
        label="restore consumption audit",
    )
    coordinator_root = completion_path.parent.parent
    expected_consumption_path = (
        coordinator_root
        / "live-leases"
        / "consumptions"
        / f"{completion['live_lease_claim_sha256']}.json"
    )
    if consumption_path != expected_consumption_path:
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore consumption audit path is not canonical"
        )
    try:
        payload = read_secure_bytes(
            consumption_path,
            label="restore consumption audit",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
        consumption = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (
        SecureFileError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore consumption audit is not secure canonical JSON"
        ) from exc
    if (
        not isinstance(consumption, dict)
        or payload != _canonical_json(consumption)
        or hashlib.sha256(payload).hexdigest()
        != document["consumption_sha256"]
        or set(consumption) != CONSUMPTION_AUDIT_FIELDS
        or consumption.get("schema")
        != NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA
        or consumption.get("status") != "consumed"
        or consumption.get("owner_action")
        != WORKER.LIVE_LEASE_OWNER_ACTION
        or consumption.get("operation_id") != completion["operation_id"]
        or consumption.get("release_sha") != completion["release_sha"]
        or consumption.get("release_tree_sha")
        != completion["release_tree_sha"]
        or consumption.get("claim_sha256")
        != completion["live_lease_claim_sha256"]
        or consumption.get("claim_epoch")
        != completion["live_lease_claim_epoch"]
        or consumption.get("claim_nonce")
        != completion["live_lease_claim_nonce"]
        or consumption.get("outcome")
        != WORKER.LIVE_LEASE_SUCCESS_OUTCOME
        or consumption.get("outcome_sha256") != completion_sha256
        or consumption.get("readiness_audit_sha256") is not None
        or consumption.get("final_state") != "legacy-frozen"
        or consumption.get("final_state_receipt_sha256")
        != completion["legacy_frozen_receipt_sha256"]
        or consumption.get("controller_authoritative") is not True
        or consumption.get("automatic") is not False
        or type(consumption.get("adopted_after_crash")) is not bool
        or type(consumption.get("consumer_pid")) is not int
        or consumption["consumer_pid"] < 1
        or type(consumption.get("controller_journal_event_count")) is not int
        or consumption["controller_journal_event_count"] < 0
        or type(consumption.get("controller_evidence_count")) is not int
        or consumption["controller_evidence_count"] < 1
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore consumption audit binding differs"
        )
    for field in (
        "aggregate_sha256",
        "controller_journal_sha256",
        "controller_evidence_tail_sha256",
        "consumption_nonce",
    ):
        _nonzero_sha256(
            consumption.get(field),
            label=f"restore consumption audit {field}",
        )
    controller_lock_path = _absolute_path(
        consumption.get("controller_lock_path", ""),
        label="restore consumption controller lock",
    )
    if controller_lock_path != coordinator_root / "coordinator.lock":
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore consumption controller lock path is not canonical"
        )


def _completion_database_container_id(
    completion: Mapping[str, Any],
    role: str,
) -> str:
    try:
        value = completion["roles"][role]["host_result"][
            "action_evidence"
        ]["verify-final"]["document"]["semantic"][
            "database_container_id"
        ]
    except (KeyError, TypeError) as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{role} completion lacks the final database container"
        ) from exc
    if (
        not isinstance(value, str)
        or INVENTORY.CONTAINER_ID_RE.fullmatch(value) is None
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{role} completion database container is invalid"
        )
    return value


def _completion_database_host_config_sha256(
    completion: Mapping[str, Any],
    role: str,
) -> str:
    try:
        value = completion["roles"][role]["host_result"][
            "action_evidence"
        ]["verify-final"]["document"]["semantic"][
            "database_host_config_sha256"
        ]
    except (KeyError, TypeError) as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            f"{role} completion lacks the final database HostConfig"
        ) from exc
    return _nonzero_sha256(
        value,
        label=f"{role} completion database HostConfig",
    )


def _validate_inventory_closure(
    path: Path,
    expected_sha256: str,
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
    restore_set: Mapping[str, Any],
    restore_set_sha256: str,
    completion: Mapping[str, Any],
    restore_requests: Mapping[str, Mapping[str, Any]],
    completion_path: Path,
    completion_sha256: str,
) -> dict[str, Any]:
    closure, digest = _secure_json(
        path,
        label="derived global Docker inventory closure",
        maximum=MAX_DERIVATION_BYTES,
    )
    identity = _identity_values(
        manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
    )
    expected = {
        **identity,
        "schema": INVENTORY_CLOSURE_SCHEMA,
        "status": "zero-non-operation-resource-delta",
        "restore_set_sha256": restore_set_sha256,
        "restore_generation_sha256": restore_set[
            "restore_generation_sha256"
        ],
        "completion_path": os.fspath(completion_path),
        "completion_sha256": completion_sha256,
        "role_order": list(RESTORE.ROLES),
        "non_operation_resource_delta_count": 0,
        "operation_host_config_sha256_by_role": {
            role: _completion_database_host_config_sha256(
                completion,
                role,
            )
            for role in RESTORE.ROLES
        },
        "captured_before_lease_consumption": True,
    }
    if (
        digest != expected_sha256
        or set(closure) != INVENTORY_CLOSURE_FIELDS
        or any(closure.get(field) != value for field, value in expected.items())
        or not isinstance(closure.get("roles"), dict)
        or set(closure["roles"]) != set(RESTORE.ROLES)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived global Docker inventory closure differs"
        )
    baseline_path = _absolute_path(
        closure["baseline_path"],
        label="global Docker inventory baseline",
    )
    baseline_sha256 = _nonzero_sha256(
        closure["baseline_sha256"],
        label="global Docker inventory baseline",
    )
    if baseline_path != path.parent / f"baseline-{baseline_sha256}.json":
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived global Docker inventory baseline path is not canonical"
        )
    baseline, observed_baseline_sha256 = _secure_json(
        baseline_path,
        label="global Docker inventory baseline",
        maximum=MAX_DERIVATION_BYTES,
    )
    baseline_expected = {
        **identity,
        "schema": INVENTORY_BASELINE_SCHEMA,
        "status": "captured-before-any-restore",
        "restore_set_sha256": restore_set_sha256,
        "restore_generation_sha256": restore_set[
            "restore_generation_sha256"
        ],
        "role_order": list(RESTORE.ROLES),
        "complete_before_restore": True,
        "operation_resource_count": 0,
        "operation_host_config_sha256_by_role": {
            role: None for role in RESTORE.ROLES
        },
    }
    if (
        observed_baseline_sha256 != baseline_sha256
        or set(baseline) != INVENTORY_BASELINE_FIELDS
        or any(
            baseline.get(field) != value
            for field, value in baseline_expected.items()
        )
        or not isinstance(baseline.get("roles"), dict)
        or set(baseline["roles"]) != set(RESTORE.ROLES)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived global Docker inventory baseline differs"
        )
    for role in RESTORE.ROLES:
        row = closure["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row) != INVENTORY_ROLE_FIELDS
            or baseline["roles"][role] != row["before"]
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} Docker inventory role closure differs"
            )
        observations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for name in ("before", "after"):
            observed = row[name]
            if (
                not isinstance(observed, dict)
                or set(observed) != INVENTORY_OBSERVATION_FIELDS
            ):
                raise FrozenFinalRestorePhaseEvidenceError(
                    f"{role} {name} inventory observation differs"
                )
            try:
                request = INVENTORY.validate_request(observed["request"])
                response = INVENTORY.validate_response(
                    observed["response"],
                    request=request,
                )
            except INVENTORY.GlobalDockerInventoryError as exc:
                raise FrozenFinalRestorePhaseEvidenceError(
                    f"{role} {name} inventory observation is invalid"
                ) from exc
            observations[name] = (request, response)
        expected_container_id = _completion_database_container_id(
            completion,
            role,
        )
        expected_host_config_sha256 = (
            _completion_database_host_config_sha256(completion, role)
        )
        before_request, before_response = observations["before"]
        after_request, after_response = observations["after"]
        try:
            INVENTORY._secure_file_sha256(  # noqa: SLF001
                Path(before_request["agent_path"]),
                expected_sha256=before_request["agent_sha256"],
                label="immutable release inventory agent",
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                "inventory request agent differs from immutable release"
            ) from exc
        expected_inventory_identity = {
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "release_tree_sha": manifest["release_tree_sha"],
            "restore_generation_sha256": restore_set[
                "restore_generation_sha256"
            ],
            "role": role,
        }
        role_manifest = completion["roles"][role]["host_result"][
            "role_manifest"
        ]
        try:
            comparison = INVENTORY.compare_non_operation_inventories(
                before_response,
                after_response,
                before_request=before_request,
                after_request=after_request,
            )
        except INVENTORY.GlobalDockerInventoryError as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} non-operation Docker inventory changed"
            ) from exc
        if (
            any(
                before_request[field] != value
                or after_request[field] != value
                for field, value in expected_inventory_identity.items()
            )
            or before_request["agent_sha256"]
            != after_request["agent_sha256"]
            or before_request["worker_sha256"]
            != restore_requests[role]["worker_sha256"]
            or after_request["worker_sha256"]
            != restore_requests[role]["worker_sha256"]
            or row["expected_operation_container_id"]
            != expected_container_id
            or row["expected_operation_host_config_sha256"]
            != expected_host_config_sha256
            or row["observed_operation_host_config_sha256"]
            != expected_host_config_sha256
            or after_request["expected_operation_container_id"]
            != expected_container_id
            or after_request["expected_operation_host_config_sha256"]
            != expected_host_config_sha256
            or after_response["expected_operation_host_config_sha256"]
            != expected_host_config_sha256
            or after_response["observed_operation_host_config_sha256"]
            != expected_host_config_sha256
            or after_request["role_manifest_path"]
            != role_manifest["path"]
            or after_request["role_manifest_sha256"]
            != role_manifest["canonical_document_sha256"]
            or row["comparison"] != comparison
            or comparison["non_operation_resource_delta_count"] != 0
            or sum(before_response["operation_resource_counts"].values()) != 0
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} Docker inventory exclusion closure differs"
            )
    return closure


def _validate_restored_sources(
    completion: Mapping[str, Any],
    restore_set: Mapping[str, Any],
) -> None:
    for role in RESTORE.ROLES:
        try:
            restore = completion["roles"][role]["host_result"][
                "restore_result"
            ]["document"]
            worker_result = completion["roles"][role]["host_result"][
                "worker_return"
            ]["result"]
            source_role = restore_set["target_map"][role]["source_role"]
            source = restore_set["sources"][source_role]
            expected_database = source["source_database"]
            expected_trees = {
                "uploads": source["artifacts"]["uploads-archive"][
                    "restored_tree_sha256"
                ],
                "audit": source["artifacts"]["audit-archive"][
                    "restored_tree_sha256"
                ],
            }
        except (KeyError, TypeError) as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} restored source closure is unavailable"
            ) from exc
        expected_restore = {
            "alembic_revision": expected_database["alembic_revision"],
            "database_fingerprint_sha256": expected_database[
                "database_fingerprint_sha256"
            ],
            "row_count": expected_database["row_count"],
            "table_count": expected_database["table_count"],
        }
        if (
            not isinstance(restore, dict)
            or any(
                restore.get("database", {}).get(field) != value
                for field, value in expected_restore.items()
            )
            or restore.get("file_trees") != expected_trees
            or restore.get("redis_restore_bytes") != 0
            or restore.get("redis_pristine") is not True
            or worker_result != restore
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} restore differs from its frozen source"
            )


def _validated_derivation_inputs(
    derivation_path: Path,
    derivation_sha256: str,
) -> DerivedEvidenceInputs:
    derivation_path = _absolute_path(
        derivation_path,
        label="restore claim derivation",
    )
    derivation_sha256 = _nonzero_sha256(
        derivation_sha256,
        label="restore claim derivation",
    )
    derivation, observed_derivation_sha256 = _secure_json(
        derivation_path,
        label="restore claim derivation",
        maximum=MAX_DERIVATION_BYTES,
    )
    if (
        observed_derivation_sha256 != derivation_sha256
        or set(derivation) != DERIVATION_FIELDS
        or derivation.get("schema") != DERIVATION_SCHEMA
        or derivation.get("status")
        != "derived-from-validated-frozen-restore"
        or derivation.get("caller_claim_sources_accepted") is not False
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore claim derivation fields differ"
        )
    _timestamp(
        derivation["observed_at"],
        label="restore claim derivation",
    )
    manifest_path = _absolute_path(
        derivation["manifest_path"],
        label="derived cutover manifest",
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
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived cutover manifest or plan is invalid"
        ) from exc
    plan_sha256 = plan["plan_sha256"]
    identity = _identity_values(
        manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
    )
    if any(
        derivation.get(field) != value
        for field, value in identity.items()
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore claim derivation identity differs"
        )
    output_directory = _absolute_path(
        derivation["evidence_output_directory"],
        label="derived phase evidence output directory",
    )
    controller_evidence_root = _absolute_path(
        manifest["deployment"]["controller_evidence_root"],
        label="controller evidence root",
    )
    if output_directory != (
        controller_evidence_root
        / "shadow-restore-coordinator"
        / "phase-evidence"
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived phase evidence output directory is not canonical"
        )
    expected_derivation_path = (
        output_directory.parent
        / "derivations"
        / f"claim-derivation-{derivation_sha256}.json"
    )
    if derivation_path != expected_derivation_path:
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore claim derivation path is not canonical"
        )
    restore_set_path = _absolute_path(
        derivation["restore_set_path"],
        label="derived restore set",
    )
    restore_set_sha256 = _nonzero_sha256(
        derivation["restore_set_sha256"],
        label="derived restore set",
    )
    restore_set = _validate_restore_set(
        restore_set_path,
        restore_set_sha256,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
    )
    completion_path = _absolute_path(
        derivation["completion_path"],
        label="derived restore completion",
    )
    completion_sha256 = _nonzero_sha256(
        derivation["completion_sha256"],
        label="derived restore completion",
    )
    completion, restore_requests = _validate_completion(
        completion_path,
        completion_sha256,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        restore_set=restore_set,
        restore_set_sha256=restore_set_sha256,
    )
    _validate_restored_sources(completion, restore_set)
    post_consumption_path = _absolute_path(
        derivation["post_consumption_receipt_path"],
        label="derived restore post-consumption receipt",
    )
    post_consumption_sha256 = _nonzero_sha256(
        derivation["post_consumption_receipt_sha256"],
        label="derived restore post-consumption receipt",
    )
    if post_consumption_path != (
        completion_path.parent
        / f"consumption-{post_consumption_sha256}.json"
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived post-consumption receipt path is not canonical"
        )
    _validate_post_consumption(
        post_consumption_path,
        post_consumption_sha256,
        completion_path=completion_path,
        completion=completion,
        completion_sha256=completion_sha256,
    )
    inventory_path = _absolute_path(
        derivation["inventory_closure_path"],
        label="derived global Docker inventory closure",
    )
    inventory_sha256 = _nonzero_sha256(
        derivation["inventory_closure_sha256"],
        label="derived global Docker inventory closure",
    )
    if inventory_path != (
        output_directory.parent
        / "inventory"
        / f"zero-delta-{inventory_sha256}.json"
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived global Docker inventory path is not canonical"
        )
    inventory = _validate_inventory_closure(
        inventory_path,
        inventory_sha256,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        restore_set=restore_set,
        restore_set_sha256=restore_set_sha256,
        completion=completion,
        restore_requests=restore_requests,
        completion_path=completion_path,
        completion_sha256=completion_sha256,
    )
    prior = derivation["prior_phase_evidence"]
    expected_prior = CONTROLLER.PHASES[
        : CONTROLLER.PHASES.index(PHASE)
    ]
    if not isinstance(prior, dict) or set(prior) != set(expected_prior):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived prior phase evidence mapping differs"
        )
    prior_paths = {
        phase: _absolute_path(
            prior[phase],
            label=f"derived prior phase evidence {phase}",
        )
        for phase in expected_prior
    }
    final_snapshot_path = _absolute_path(
        derivation["prior_final_snapshot_evidence_path"],
        label="derived final snapshot evidence",
    )
    if final_snapshot_path != prior_paths["final_snapshot_hashes"]:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived final snapshot evidence path differs"
        )
    try:
        final_snapshot, final_snapshot_sha256 = (
            VERIFY.read_root_only_evidence(final_snapshot_path)
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived final snapshot evidence is unsafe"
        ) from exc
    if (
        final_snapshot_sha256
        != _nonzero_sha256(
            derivation["prior_final_snapshot_evidence_sha256"],
            label="derived final snapshot evidence",
        )
        or final_snapshot.get("phase") != "final_snapshot_hashes"
        or final_snapshot.get("campaign_id") != manifest["campaign_id"]
        or final_snapshot.get("operation_id") != manifest["operation_id"]
        or final_snapshot.get("release_sha") != manifest["release_sha"]
        or final_snapshot.get("manifest_sha256") != manifest_sha256
        or final_snapshot.get("plan_sha256") != plan_sha256
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived final snapshot evidence binding differs"
        )
    try:
        postgres_snapshot_sha256 = final_snapshot["claims"][
            "postgres_snapshot_set_sha256"
        ]["value"]
        file_snapshot_sha256 = final_snapshot["claims"][
            "reviewed_file_snapshot_set_sha256"
        ]["value"]
    except (KeyError, TypeError) as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived final snapshot evidence lacks restore bindings"
        ) from exc
    expected_claim_values = {
        "postgres_restore_verified": True,
        "reviewed_file_restore_verified": True,
        "legacy_redis_restore_byte_count": 0,
        "non_operation_resource_delta_count": inventory[
            "non_operation_resource_delta_count"
        ],
        "inventory_closure_sha256": inventory_sha256,
        "restored_postgres_snapshot_set_sha256": (
            restore_set["postgres_snapshot_set_sha256"]
        ),
        "restored_reviewed_file_snapshot_set_sha256": (
            restore_set["reviewed_file_snapshot_set_sha256"]
        ),
        "restore_result_set_sha256": completion_sha256,
    }
    if (
        expected_claim_values["restored_postgres_snapshot_set_sha256"]
        != postgres_snapshot_sha256
        or expected_claim_values[
            "restored_reviewed_file_snapshot_set_sha256"
        ]
        != file_snapshot_sha256
        or set(expected_claim_values)
        != set(VERIFY.PHASE_CLAIM_RULES[PHASE])
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "restore set differs from final snapshot evidence"
        )
    claims = derivation["claims"]
    if not isinstance(claims, dict) or set(claims) != set(
        expected_claim_values
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived restore claim set differs"
        )
    claim_arguments: list[str] = []
    for claim, expected_value in expected_claim_values.items():
        row = claims[claim]
        if (
            not isinstance(row, dict)
            or set(row) != DERIVED_CLAIM_FIELDS
            or row.get("value") != expected_value
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived restore claim {claim} differs"
            )
        expected_source_sha256 = _nonzero_sha256(
            row["source_sha256"],
            label=f"derived restore claim {claim}",
        )
        source_path = _absolute_path(
            row["source_path"],
            label=f"derived restore claim {claim}",
        )
        if source_path != (
            output_directory.parent
            / "claims"
            / f"{claim}-{expected_source_sha256}.json"
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived restore claim {claim} path is not canonical"
            )
        source, source_sha256 = _secure_json(
            source_path,
            label=f"derived restore claim {claim}",
        )
        if (
            source_sha256 != expected_source_sha256
            or set(source) != VERIFY.CLAIM_SOURCE_FIELDS
            or source.get("claim") != claim
            or source.get("value") != expected_value
            or source.get("observed_at") != derivation["observed_at"]
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived restore claim {claim} readback differs"
            )
        claim_arguments.append(f"{claim}={source_path}")
    role_validation = derivation["role_validation"]
    if (
        not isinstance(role_validation, dict)
        or set(role_validation) != set(RESTORE.ROLES)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived role validation mapping differs"
        )
    role_arguments: list[str] = []
    role_source_sha256: dict[str, str] = {}
    for role in RESTORE.ROLES:
        row = role_validation[role]
        if not isinstance(row, dict) or set(row) != ROLE_DERIVATION_FIELDS:
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived {role} role validation fields differ"
            )
        role_path = _absolute_path(
            row["path"],
            label=f"{role} role validation",
        )
        _document, observed_sha256 = _secure_json(
            role_path,
            label=f"{role} role validation",
        )
        expected_sha256 = _nonzero_sha256(
            row["sha256"],
            label=f"{role} role validation",
        )
        if (
            role_path
            != (
                output_directory.parent
                / "role-validations"
                / f"{role}-{expected_sha256}.json"
            )
            or observed_sha256 != expected_sha256
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"derived {role} role validation readback differs"
            )
        role_source_sha256[role] = expected_sha256
        role_arguments.append(f"{role}={role_path}")
    return DerivedEvidenceInputs(
        derivation_path=derivation_path,
        derivation_sha256=derivation_sha256,
        manifest_path=manifest_path,
        output_directory=output_directory,
        role_validation=tuple(role_arguments),
        claim_source=tuple(claim_arguments),
        prior_phase_evidence=tuple(
            f"{phase}={prior_paths[phase]}" for phase in expected_prior
        ),
        role_source_sha256=role_source_sha256,
        claim_source_sha256={
            claim: claims[claim]["source_sha256"]
            for claim in expected_claim_values
        },
    )


def _validate_output_directory(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != OUTPUT_DIRECTORY_MODE
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                "output directory must be a real root-owned mode-0700 directory"
            )
    except FrozenFinalRestorePhaseEvidenceError:
        raise
    except OSError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "output directory is unavailable or unsafe"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _build_document(
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan_sha256: str,
    journal: Mapping[str, Any],
    prior_records: Mapping[str, Mapping[str, Any]],
    prior_digests: Mapping[str, str],
    role_request_sha256: Mapping[str, str],
    role_source_sha256: Mapping[str, str],
    role_observed_at: Mapping[str, str],
    dynamic_claim_values: Mapping[str, Any],
    normalized_claim_values: Mapping[str, Any],
    claim_source_sha256: Mapping[str, str],
    claim_observed_at: Mapping[str, str],
) -> dict[str, Any]:
    spec = VERIFY.PHASE_SPEC_BY_NAME[PHASE]
    prior_rows = [
        {
            "phase": phase,
            "evidence_sha256": prior_digests[phase],
        }
        for phase in CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PHASE)]
    ]
    try:
        prior_claim_rows = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=PHASE,
            prior_digests=dict(prior_digests),
            prior_records=dict(prior_records),
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            legacy_release_sha=manifest["legacy_release_sha"],
            manifest_sha256=manifest_sha256,
            plan_sha256=plan_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "prior phase evidence records are not an exact verified prefix"
        ) from exc

    observed_times = [
        _timestamp(value, label=f"{role} role observation")
        for role, value in role_observed_at.items()
    ]
    observed_times.extend(
        _timestamp(value, label=f"{claim} claim observation")
        for claim, value in claim_observed_at.items()
    )
    if not observed_times:
        raise FrozenFinalRestorePhaseEvidenceError(
            "phase observation timestamps are unavailable"
        )
    captured_at = max(observed_times).isoformat()
    phase_input = {
        "manifest_sha256": manifest_sha256,
        "manifest_artifacts_sha256": hashlib.sha256(
            _canonical_json(manifest["artifacts"])
        ).hexdigest(),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claim_rows,
        "dynamic_claim_values": dict(dynamic_claim_values),
        "claim_source_sha256": {
            name: claim_source_sha256[name]
            for name in sorted(claim_source_sha256)
        },
        "role_request_sha256": {
            role: role_request_sha256[role] for role in spec.roles
        },
        "role_source_artifact_sha256": {
            role: role_source_sha256[role] for role in spec.roles
        },
        "role_observed_at": {
            role: role_observed_at[role] for role in spec.roles
        },
    }
    claims = {
        name: {
            "value": normalized_claim_values[name],
            "source_sha256": claim_source_sha256[name],
        }
        for name in VERIFY.PHASE_CLAIM_RULES[PHASE]
    }
    document = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": (
            VERIFY.PHASE_EVIDENCE_CONTRACT_SHA256
        ),
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "legacy_release_sha": manifest["legacy_release_sha"],
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "approval_sha256": manifest["artifacts"][
            "cutover_approval_sha256"
        ],
        "manifest_artifact_bindings": dict(manifest["artifacts"]),
        "phase": PHASE,
        "operation": spec.operation,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": captured_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": hashlib.sha256(
            _canonical_json(prior_rows)
        ).hexdigest(),
        "prior_claim_bindings": prior_claim_rows,
        "phase_input_closure_sha256": hashlib.sha256(
            _canonical_json(phase_input)
        ).hexdigest(),
        "role_attestations": [
            {
                "role": role,
                "expected_host": CONTROLLER.EXPECTED_TOPOLOGY[role]["host"],
                "operation": spec.operation,
                "request_sha256": role_request_sha256[role],
                "app_release_sha": manifest["release_sha"],
                "agent_artifact_sha256": manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": role_observed_at[role],
                "status": "verified",
                "transport": CONTROLLER.EXPECTED_TOPOLOGY[role][
                    "transport"
                ],
                "source_artifact_sha256": role_source_sha256[role],
            }
            for role in spec.roles
        ],
        "claims": claims,
    }
    if set(document) != VERIFY.EVIDENCE_FIELDS:
        raise FrozenFinalRestorePhaseEvidenceError(
            "constructed phase evidence fields are not exact"
        )
    if journal["started_phase"] != document["phase"]:
        raise FrozenFinalRestorePhaseEvidenceError(
            "constructed phase differs from the durable journal start"
        )
    return document


def prepare_evidence(
    *,
    manifest_path: Path,
    output_directory: Path,
    role_validation: Sequence[str],
    claim_source: Sequence[str],
    prior_phase_evidence: Sequence[str],
    now: datetime | None = None,
) -> PreparedEvidence:
    if os.geteuid() != 0:
        raise FrozenFinalRestorePhaseEvidenceError(
            "phase evidence producer must run as root"
        )
    manifest_path = _absolute_path(
        manifest_path,
        label="cutover manifest",
    )
    output_directory = _absolute_path(
        output_directory,
        label="output directory",
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
        raise FrozenFinalRestorePhaseEvidenceError(
            "cutover manifest or derived plan is invalid"
        ) from exc
    plan_sha256 = plan["plan_sha256"]
    journal = _read_journal(
        manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
    )
    spec = VERIFY.PHASE_SPEC_BY_NAME[PHASE]
    role_paths = _parse_path_mapping(
        role_validation,
        expected=spec.roles,
        label="role validation",
    )
    claim_paths = _parse_path_mapping(
        claim_source,
        expected=tuple(VERIFY.PHASE_CLAIM_RULES[PHASE]),
        label="claim source",
    )
    expected_prior = CONTROLLER.PHASES[
        : CONTROLLER.PHASES.index(PHASE)
    ]
    prior_paths = _parse_path_mapping(
        prior_phase_evidence,
        expected=expected_prior,
        label="prior phase evidence",
    )
    prior_records, prior_digests = _read_prior_records(
        prior_paths,
        journal=journal,
    )
    observed_now = now or datetime.now(timezone.utc)
    if (
        not isinstance(observed_now, datetime)
        or observed_now.tzinfo is None
        or observed_now.utcoffset() is None
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "verification time must include a timezone"
        )
    observed_now = observed_now.astimezone(timezone.utc)
    try:
        (
            role_request_sha256,
            role_source_sha256,
            role_observed_at,
        ) = VERIFY._read_role_validation_records(  # noqa: SLF001
            [
                f"{role}={role_paths[role]}"
                for role in spec.roles
            ],
            phase=PHASE,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
        )
        (
            dynamic_claim_values,
            claim_source_sha256,
        ) = VERIFY._read_claim_source_records(  # noqa: SLF001
            [
                f"{claim}={claim_paths[claim]}"
                for claim in VERIFY.PHASE_CLAIM_RULES[PHASE]
            ],
            phase=PHASE,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            now=observed_now,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "role validation or normalized claim source is invalid"
        ) from exc
    phase_max_age = VERIFY.PHASE_MAX_AGE.get(
        PHASE,
        VERIFY.MAX_EVIDENCE_AGE,
    )
    for role, value in role_observed_at.items():
        observed_at = _timestamp(
            value,
            label=f"{role} role observation",
        )
        if (
            observed_at > observed_now + VERIFY.MAX_FUTURE_SKEW
            or observed_now - observed_at > phase_max_age
        ):
            raise FrozenFinalRestorePhaseEvidenceError(
                f"{role} role validation observation is not fresh"
            )
    (
        normalized_claim_values,
        claim_observed_at,
    ) = _claim_source_details(
        claim_paths,
        expected_hashes=claim_source_sha256,
    )
    document = _build_document(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        journal=journal,
        prior_records=prior_records,
        prior_digests=prior_digests,
        role_request_sha256=role_request_sha256,
        role_source_sha256=role_source_sha256,
        role_observed_at=role_observed_at,
        dynamic_claim_values=dynamic_claim_values,
        normalized_claim_values=normalized_claim_values,
        claim_source_sha256=claim_source_sha256,
        claim_observed_at=claim_observed_at,
    )
    payload = _canonical_json(document) + b"\n"
    evidence_sha256 = hashlib.sha256(payload).hexdigest()
    try:
        verification = VERIFY.verify_phase_evidence(
            document,
            expected_phase=PHASE,
            expected_campaign_id=manifest["campaign_id"],
            expected_operation_id=manifest["operation_id"],
            expected_release_sha=manifest["release_sha"],
            expected_legacy_release_sha=manifest["legacy_release_sha"],
            expected_manifest_sha256=manifest_sha256,
            expected_plan_sha256=plan_sha256,
            expected_approval_sha256=manifest["artifacts"][
                "cutover_approval_sha256"
            ],
            expected_phase_evidence_schema_sha256=manifest["artifacts"][
                "phase_evidence_schema_sha256"
            ],
            expected_manifest_artifacts=dict(manifest["artifacts"]),
            expected_role_request_sha256=dict(role_request_sha256),
            expected_role_source_artifact_sha256=dict(
                role_source_sha256
            ),
            expected_role_observed_at=dict(role_observed_at),
            expected_dynamic_claim_values=dict(dynamic_claim_values),
            expected_claim_source_sha256=dict(claim_source_sha256),
            expected_prior_phase_evidence_sha256=dict(prior_digests),
            prior_phase_evidence_records=dict(prior_records),
            now=observed_now,
            evidence_file_sha256=evidence_sha256,
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "constructed shadow_restore evidence failed self-verification"
        ) from exc
    if (
        verification["status"] != "verified"
        or verification["evidence_sha256"] != evidence_sha256
        or verification["phase"] != PHASE
        or verification["production_contacted"] is not False
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "phase evidence self-verification result is invalid"
        )
    output = (
        output_directory
        / f"{PHASE}.{evidence_sha256}.json"
    )
    confirmation = (
        "publish-production-shadow-restore-phase-evidence:"
        f"{manifest['operation_id']}:{evidence_sha256}"
    )
    return PreparedEvidence(
        document=document,
        payload=payload,
        evidence_sha256=evidence_sha256,
        output=output,
        required_confirmation=confirmation,
        verification=verification,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
    )


def _assert_derived_source_hashes(
    prepared: PreparedEvidence,
    inputs: DerivedEvidenceInputs,
) -> None:
    observed_roles = {
        row["role"]: row["source_artifact_sha256"]
        for row in prepared.document["role_attestations"]
    }
    observed_claims = {
        claim: row["source_sha256"]
        for claim, row in prepared.document["claims"].items()
    }
    if (
        observed_roles != dict(inputs.role_source_sha256)
        or observed_claims != dict(inputs.claim_source_sha256)
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "prepared evidence differs from its derivation source hashes"
        )


def _publish_derived(
    prepared: PreparedEvidence,
    inputs: DerivedEvidenceInputs,
) -> str:
    rebound = _validated_derivation_inputs(
        inputs.derivation_path,
        inputs.derivation_sha256,
    )
    if rebound != inputs:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derivation inputs changed before publication"
        )
    refreshed = prepare_evidence(
        manifest_path=rebound.manifest_path,
        output_directory=rebound.output_directory,
        role_validation=rebound.role_validation,
        claim_source=rebound.claim_source,
        prior_phase_evidence=rebound.prior_phase_evidence,
        now=_utc_now(),
    )
    _assert_derived_source_hashes(refreshed, rebound)
    if (
        refreshed.payload != prepared.payload
        or refreshed.evidence_sha256 != prepared.evidence_sha256
        or refreshed.output != prepared.output
        or refreshed.manifest_sha256 != prepared.manifest_sha256
        or refreshed.plan_sha256 != prepared.plan_sha256
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "phase evidence inputs changed before publication"
        )
    prepared = refreshed
    _validate_output_directory(prepared.output.parent)
    try:
        os.lstat(prepared.output)
    except FileNotFoundError:
        output_exists = False
    except OSError as exc:
        raise FrozenFinalRestorePhaseEvidenceError(
            "phase evidence output cannot be inspected safely"
        ) from exc
    else:
        output_exists = True
    if output_exists:
        try:
            observed = read_secure_bytes(
                prepared.output,
                label="existing production shadow_restore phase evidence",
                owner_uid=0,
                max_size=MAX_JSON_BYTES,
            )
        except SecureFileError as exc:
            raise FrozenFinalRestorePhaseEvidenceError(
                "existing phase evidence output is unsafe"
            ) from exc
        if observed != prepared.payload:
            raise FrozenFinalRestorePhaseEvidenceError(
                "existing digest-derived phase evidence differs"
            )
        publication = "reused"
    else:
        try:
            write_secure_new_bytes(
                prepared.output,
                prepared.payload,
                label="production shadow_restore phase evidence",
                mode=OUTPUT_MODE,
                max_size=MAX_JSON_BYTES,
            )
            publication = "created"
        except SecureFileError as exc:
            # A concurrent create is acceptable only when its secure readback
            # is byte-for-byte the same digest-derived evidence.
            try:
                observed = read_secure_bytes(
                    prepared.output,
                    label=(
                        "concurrently published production shadow_restore "
                        "phase evidence"
                    ),
                    owner_uid=0,
                    max_size=MAX_JSON_BYTES,
                )
            except SecureFileError as read_exc:
                raise FrozenFinalRestorePhaseEvidenceError(
                    "phase evidence output could not be published safely"
                ) from read_exc
            if observed != prepared.payload:
                raise FrozenFinalRestorePhaseEvidenceError(
                    "existing digest-derived phase evidence differs"
                ) from exc
            publication = "reused"
    observed = read_secure_bytes(
        prepared.output,
        label="published production shadow_restore phase evidence",
        owner_uid=0,
        max_size=MAX_JSON_BYTES,
    )
    if (
        observed != prepared.payload
        or hashlib.sha256(observed).hexdigest()
        != prepared.evidence_sha256
    ):
        raise FrozenFinalRestorePhaseEvidenceError(
            "published phase evidence readback differs"
        )
    return publication


def _result_base(prepared: PreparedEvidence) -> dict[str, Any]:
    return {
        "schema": PUBLICATION_SCHEMA,
        "phase": PHASE,
        "operation": prepared.document["operation"],
        "campaign_id": prepared.document["campaign_id"],
        "operation_id": prepared.document["operation_id"],
        "release_sha": prepared.document["release_sha"],
        "manifest_sha256": prepared.manifest_sha256,
        "plan_sha256": prepared.plan_sha256,
        "evidence_sha256": prepared.evidence_sha256,
        "output": os.fspath(prepared.output),
        "required_confirmation": prepared.required_confirmation,
        "self_verification_status": prepared.verification["status"],
        "journal_mutated": False,
        "network_io": False,
        "docker_invoked": False,
        "ssh_invoked": False,
        "object_storage_contacted": False,
        "production_contacted": False,
    }


def execute(
    *,
    manifest_path: Path,
    output_directory: Path,
    role_validation: Sequence[str],
    claim_source: Sequence[str],
    prior_phase_evidence: Sequence[str],
    apply: bool = False,
    confirm: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if apply:
        raise FrozenFinalRestorePhaseEvidenceError(
            "standalone apply is disabled; a verified derivation receipt "
            "is required"
        )
    prepared = prepare_evidence(
        manifest_path=manifest_path,
        output_directory=output_directory,
        role_validation=role_validation,
        claim_source=claim_source,
        prior_phase_evidence=prior_phase_evidence,
        now=now,
    )
    if confirm is not None:
        raise FrozenFinalRestorePhaseEvidenceError(
            "--confirm is valid only with derivation-backed apply"
        )
    return {
        **_result_base(prepared),
        "status": "planned",
        "publication": "planned",
        "output_mutated": False,
    }


def execute_derived(
    *,
    derivation_path: Path,
    derivation_sha256: str,
    apply: bool = False,
    confirm: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if apply and now is not None:
        raise FrozenFinalRestorePhaseEvidenceError(
            "live derived apply does not accept a caller-selected clock"
        )
    observed_now = _utc_now() if apply else now
    inputs = _validated_derivation_inputs(
        derivation_path,
        derivation_sha256,
    )
    prepared = prepare_evidence(
        manifest_path=inputs.manifest_path,
        output_directory=inputs.output_directory,
        role_validation=inputs.role_validation,
        claim_source=inputs.claim_source,
        prior_phase_evidence=inputs.prior_phase_evidence,
        now=observed_now,
    )
    _assert_derived_source_hashes(prepared, inputs)
    confirmation = (
        "publish-production-shadow-restore-derived-evidence:"
        f"{prepared.document['operation_id']}:"
        f"{inputs.derivation_sha256}:{prepared.evidence_sha256}"
    )
    base = {
        **_result_base(prepared),
        "derivation_path": os.fspath(inputs.derivation_path),
        "derivation_sha256": inputs.derivation_sha256,
        "required_confirmation": confirmation,
    }
    if not apply:
        if confirm is not None:
            raise FrozenFinalRestorePhaseEvidenceError(
                "--confirm is valid only with --apply"
            )
        return {
            **base,
            "status": "planned",
            "publication": "planned",
            "output_mutated": False,
        }
    if confirm != confirmation:
        raise FrozenFinalRestorePhaseEvidenceError(
            "derived apply requires the exact digest-bound confirmation"
        )
    publication = _publish_derived(prepared, inputs)
    return {
        **base,
        "status": "published",
        "publication": publication,
        "output_mutated": publication == "created",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--derivation", type=Path)
    parser.add_argument("--derivation-sha256")
    parser.add_argument(
        "--role-validation",
        action="append",
        default=[],
        metavar="ROLE=/ABS/PATH",
    )
    parser.add_argument(
        "--claim-source",
        action="append",
        default=[],
        metavar="CLAIM=/ABS/PATH",
    )
    parser.add_argument(
        "--prior-phase-evidence",
        action="append",
        default=[],
        metavar="PHASE=/ABS/PATH",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        derived = (
            args.derivation is not None
            or args.derivation_sha256 is not None
        )
        if derived:
            if (
                args.derivation is None
                or args.derivation_sha256 is None
                or args.manifest is not None
                or args.output_directory is not None
                or args.role_validation
                or args.claim_source
                or args.prior_phase_evidence
            ):
                raise FrozenFinalRestorePhaseEvidenceError(
                    "derived publication accepts only one receipt and digest"
                )
            result = execute_derived(
                derivation_path=args.derivation,
                derivation_sha256=args.derivation_sha256,
                apply=args.apply,
                confirm=args.confirm,
            )
        else:
            if args.manifest is None or args.output_directory is None:
                raise FrozenFinalRestorePhaseEvidenceError(
                    "standalone plan requires manifest and output directory"
                )
            result = execute(
                manifest_path=args.manifest,
                output_directory=args.output_directory,
                role_validation=args.role_validation,
                claim_source=args.claim_source,
                prior_phase_evidence=args.prior_phase_evidence,
                apply=args.apply,
                confirm=args.confirm,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        FrozenFinalRestorePhaseEvidenceError,
        CONTROLLER.CutoverContractError,
        VERIFY.PhaseEvidenceError,
        SecureFileError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "journal_mutated": False,
                    "network_io": False,
                    "docker_invoked": False,
                    "ssh_invoked": False,
                    "object_storage_contacted": False,
                    "production_contacted": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
