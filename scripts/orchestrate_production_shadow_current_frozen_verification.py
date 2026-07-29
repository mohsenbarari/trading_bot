#!/usr/bin/env python3
"""Prove the post-snapshot Finland writer freeze under a fresh lease epoch.

The controller first proves that the frozen-final snapshot lease was consumed,
then obtains a fresh two-host legacy-frozen Nginx receipt.  It holds the next
strictly chained live lease while both Finland hosts run ``verify-current``.
Only controller journals and create-only verification evidence are written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    orchestrate_production_shadow_frozen_snapshots as FROZEN,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import production_shadow_finland_stage as FINLAND_STAGE  # noqa: E402
from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402


PLAN_SCHEMA = "production-shadow-current-frozen-verification-plan-v1"
JOURNAL_SCHEMA = "production-shadow-current-frozen-verification-journal-v1"
OUTCOME_SCHEMA = "production-shadow-current-frozen-verification-outcome-v1"
RECEIPT_SCHEMA = "production-shadow-current-frozen-verification-receipt-v1"
RESULT_SCHEMA = "production-shadow-current-frozen-verification-result-v1"
OWNER_ACTION = FROZEN.FREEZE.VERIFY_CURRENT_OWNER_ACTION
OWNER_OUTCOME = "current-frozen-verified"
DIRECTORY_NAME = "current-frozen-verification"
JOURNAL_FILENAME = "journal.json"
LOCK_FILENAME = "lock"
HOST_RESULTS_DIRECTORY = "host-results"
RECEIPTS_DIRECTORY = "receipts"
ROLES = FROZEN.ROLES
ZERO_SHA256 = FROZEN.ZERO_SHA256
MAX_JSON_BYTES = FROZEN.MAX_JSON_BYTES
MAX_CROSS_HOST_SKEW_SECONDS = (
    NGINX.READBACK_MAX_CROSS_HOST_SKEW_SECONDS
)
LEASE_CONSUMPTION_FIELDS = frozenset(
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


class CurrentFrozenVerificationError(RuntimeError):
    """The fresh two-host zero-writer epoch could not be proven."""


Checkpoint = Callable[[str], None]
NowFn = Callable[[], int]


def canonical_json(value: Any) -> bytes:
    try:
        return FROZEN.canonical_json(value)
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(str(exc)) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    try:
        return FROZEN._nonzero_sha256(value, label=label)
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(str(exc)) from exc


def _paths(operation_id: str, release_sha: str) -> dict[str, Any]:
    base = FROZEN.canonical_paths(operation_id, release_sha)
    root = base["controller_root"] / DIRECTORY_NAME
    return {
        **base,
        "verification_root": root,
        "verification_journal": root / JOURNAL_FILENAME,
        "verification_lock": root / LOCK_FILENAME,
        "host_results": root / HOST_RESULTS_DIRECTORY,
        "verification_receipts": root / RECEIPTS_DIRECTORY,
    }


def _ensure_directories(
    paths: Mapping[str, Any],
    *,
    required_uid: int,
) -> None:
    FROZEN._ensure_controller_directories(
        paths,
        required_uid=required_uid,
    )
    FROZEN._ensure_private_child(
        paths["verification_root"],
        parent=paths["controller_root"],
        label="current frozen verification root",
        required_uid=required_uid,
    )
    for key, label in (
        ("host_results", "current frozen host result root"),
        ("verification_receipts", "current frozen receipt root"),
    ):
        FROZEN._ensure_private_child(
            paths[key],
            parent=paths["verification_root"],
            label=label,
            required_uid=required_uid,
        )


def _load_inputs(
    *,
    aggregate_path: Path,
    bot_fi_nginx_manifest: Path,
    bot_fi_nginx_archive: Path,
    webapp_fi_nginx_manifest: Path,
    webapp_fi_nginx_archive: Path,
    known_hosts: Path,
    ssh_identity: Path,
) -> NGINX.CoordinatorInputs:
    try:
        return NGINX.load_inputs(
            aggregate_path=aggregate_path,
            bot_fi_manifest=bot_fi_nginx_manifest,
            bot_fi_archive=bot_fi_nginx_archive,
            webapp_fi_manifest=webapp_fi_nginx_manifest,
            webapp_fi_archive=webapp_fi_nginx_archive,
            known_hosts=known_hosts,
            ssh_identity=ssh_identity,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise CurrentFrozenVerificationError(
            "Nginx coordinator inputs are invalid"
        ) from exc


def _load_bindings(
    inputs: NGINX.CoordinatorInputs,
    *,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
) -> dict[str, SOURCE.SnapshotBinding]:
    try:
        return FROZEN.load_bindings(
            operation_id=inputs.operation_id,
            release_sha=inputs.release_sha,
            bot_fi_binding=bot_fi_binding,
            webapp_fi_binding=webapp_fi_binding,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            "frozen-final binding closure is invalid"
        ) from exc


def _capture_receipt_path(
    inputs: NGINX.CoordinatorInputs,
    digest: str,
) -> Path:
    digest = _nonzero_sha256(
        digest,
        label="capture lease state receipt SHA-256",
    )
    return FROZEN.canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        state_receipt_sha256=digest,
    )["state_receipt"]


def _validate_capture_completion(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture_state_receipt_path: Path,
    capture_state_receipt_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    capture_state_receipt_sha256 = _nonzero_sha256(
        capture_state_receipt_sha256,
        label="capture receipt SHA-256",
    )
    if capture_state_receipt_path != _capture_receipt_path(
        inputs,
        capture_state_receipt_sha256,
    ):
        raise CurrentFrozenVerificationError(
            "capture receipt path is not canonical"
        )
    try:
        capture_receipt, observed_receipt_sha256 = (
            NGINX.load_state_receipt(
                capture_state_receipt_path,
                "legacy-frozen",
                inputs.operation_id,
                inputs.release_sha,
                inputs.release_tree_sha,
                inputs.aggregate_sha256,
                allow_historical=True,
            )
        )
    except NGINX.NginxCoordinatorError as exc:
        raise CurrentFrozenVerificationError(
            "capture receipt is invalid"
        ) from exc
    if observed_receipt_sha256 != capture_state_receipt_sha256:
        raise CurrentFrozenVerificationError(
            "capture receipt digest differs"
        )
    paths = FROZEN.canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
    )
    try:
        journal = FROZEN._read_journal(
            paths["journal"],
            inputs=inputs,
            bindings=bindings,
            state_receipt_sha256=capture_state_receipt_sha256,
            required_uid=required_uid,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            "frozen-final capture journal is invalid"
        ) from exc
    if journal is None or journal["status"] != "complete":
        raise CurrentFrozenVerificationError(
            "frozen-final capture is not complete"
        )
    for role in ROLES:
        try:
            observed_collection = FROZEN._verify_collected_role(
                role=role,
                binding=bindings[role],
                freeze_sha256=journal["roles"][role][
                    "freeze_evidence_sha256"
                ],
                lease_claim_sha256=journal["lease"]["claim_sha256"],
                paths=paths,
            )
        except FROZEN.FrozenSnapshotOrchestratorError as exc:
            raise CurrentFrozenVerificationError(
                f"{role} frozen-final collection is invalid"
            ) from exc
        if observed_collection != journal["roles"][role]["collection"]:
            raise CurrentFrozenVerificationError(
                f"{role} frozen-final collection changed"
            )
    outcome = FROZEN._outcome_document(inputs=inputs, journal=journal)
    outcome_payload = canonical_json(outcome)
    outcome_sha256 = _sha256(outcome_payload)
    if outcome_sha256 != journal["outcome_sha256"]:
        raise CurrentFrozenVerificationError(
            "capture outcome digest differs"
        )
    try:
        persisted_outcome = FROZEN._read_file(
            paths["outcome"],
            label="frozen-final capture outcome",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_JSON_BYTES,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            "capture outcome is unavailable"
        ) from exc
    if persisted_outcome != outcome_payload:
        raise CurrentFrozenVerificationError(
            "capture outcome changed"
        )
    r1_claim_sha256 = _nonzero_sha256(
        journal["lease"]["claim_sha256"],
        label="capture lease claim SHA-256",
    )
    r1_claim_path = FROZEN.canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        lease_claim_sha256=r1_claim_sha256,
    )["lease_claim"]
    try:
        r1_claim, observed_claim_sha256 = (
            NGINX.load_live_lease_claim_material(
                r1_claim_path,
                state_receipt_path=capture_state_receipt_path,
                expected_claim_sha256=r1_claim_sha256,
                expected_state_receipt_sha256=(
                    capture_state_receipt_sha256
                ),
                operation_id=inputs.operation_id,
                release_sha=inputs.release_sha,
                release_tree_sha=inputs.release_tree_sha,
                aggregate_sha256=inputs.aggregate_sha256,
            )
        )
        consumption = NGINX._load_consumption_audit(
            inputs,
            claim=r1_claim,
            claim_sha256=r1_claim_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise CurrentFrozenVerificationError(
            "capture lease ledger is invalid"
        ) from exc
    if (
        observed_claim_sha256 != r1_claim_sha256
        or r1_claim.get("owner_action")
        != FROZEN.FREEZE.CAPTURE_OWNER_ACTION
        or consumption is None
    ):
        raise CurrentFrozenVerificationError(
            "capture lease was not exactly consumed"
        )
    consumption_document, consumption_sha256 = consumption
    if (
        consumption_sha256 != journal["consumption_sha256"]
        or consumption_document["outcome"]
        != "handoff-shadow-readonly"
        or consumption_document["outcome_sha256"] != outcome_sha256
        or consumption_document["final_state"] != "legacy-frozen"
        or consumption_document["final_state_receipt_sha256"]
        != capture_state_receipt_sha256
        or consumption_document["readiness_audit_sha256"] is not None
    ):
        raise CurrentFrozenVerificationError(
            "capture lease consumption binding differs"
        )
    return {
        "receipt": capture_receipt,
        "receipt_sha256": capture_state_receipt_sha256,
        "claim": r1_claim,
        "claim_path": r1_claim_path,
        "claim_sha256": r1_claim_sha256,
        "claim_epoch": r1_claim["claim_epoch"],
        "outcome_sha256": outcome_sha256,
        "consumption_sha256": consumption_sha256,
        "journal": journal,
    }


def confirmation_phrase(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture_state_receipt_sha256: str,
) -> str:
    role_bindings = ":".join(
        bindings[role].canonical_sha256 for role in ROLES
    )
    return (
        "VERIFY-CURRENT-PRODUCTION-FROZEN-WRITERS:"
        f"{inputs.operation_id}:{inputs.release_sha}:"
        f"{capture_state_receipt_sha256}:{role_bindings}"
    )


def render_plan(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "legacy_release_sha": bindings["bot_fi"].legacy_release_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "capture_state_receipt_sha256": capture["receipt_sha256"],
        "capture_lease_claim_sha256": capture["claim_sha256"],
        "capture_lease_claim_epoch": capture["claim_epoch"],
        "capture_lease_consumption_sha256": capture[
            "consumption_sha256"
        ],
        "roles": list(ROLES),
        "next_actions": [
            "fresh-legacy-frozen-nginx-readback",
            "hold-strictly-chained-live-lease",
            "verify-current-bot-fi",
            "verify-current-webapp-fi",
            "consume-verification-live-lease",
            "publish-create-only-two-role-receipt",
        ],
        "required_confirmation": confirmation_phrase(
            inputs=inputs,
            bindings=bindings,
            capture_state_receipt_sha256=capture["receipt_sha256"],
        ),
        "runner_invoked": False,
        "network_contacted": False,
        "docker_contacted": False,
        "source_mutated": False,
        "current_mutated": False,
        "service_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def _fresh_readback(
    *,
    inputs: NGINX.CoordinatorInputs,
    aggregate_path: Path,
    bot_fi_nginx_manifest: Path,
    bot_fi_nginx_archive: Path,
    webapp_fi_nginx_manifest: Path,
    webapp_fi_nginx_archive: Path,
    known_hosts: Path,
    ssh_identity: Path,
    runner: Any,
    now_fn: NowFn,
) -> tuple[dict[str, Any], Path, str]:
    kwargs = {
        "aggregate_path": aggregate_path,
        "bot_fi_manifest": bot_fi_nginx_manifest,
        "bot_fi_archive": bot_fi_nginx_archive,
        "webapp_fi_manifest": webapp_fi_nginx_manifest,
        "webapp_fi_archive": webapp_fi_nginx_archive,
        "action": "readback",
        "apply": True,
        "confirm": NGINX.confirmation_phrase(
            operation_id=inputs.operation_id,
            release_sha=inputs.release_sha,
            action="readback",
            target_state=None,
        ),
        "known_hosts": known_hosts,
        "ssh_identity": ssh_identity,
    }
    if runner is not None:
        kwargs["runner"] = runner
    try:
        result = NGINX.execute_coordinator(**kwargs)
    except NGINX.NginxCoordinatorError as exc:
        raise CurrentFrozenVerificationError(
            "fresh legacy-frozen Nginx readback failed"
        ) from exc
    if (
        result.get("status") != "read-back"
        or result.get("state") != "legacy-frozen"
        or result.get("operation_id") != inputs.operation_id
        or result.get("release_sha") != inputs.release_sha
        or result.get("release_tree_sha") != inputs.release_tree_sha
        or result.get("aggregate_sha256") != inputs.aggregate_sha256
        or any(
            result.get(field) is not False
            for field in (
                "active_configuration_mutated",
                "current_mutated",
                "container_mutated",
                "volume_mutated",
                "data_mutated",
            )
        )
        or not isinstance(result.get("state_receipt_path"), str)
    ):
        raise CurrentFrozenVerificationError(
            "fresh Nginx readback result differs"
        )
    receipt_sha256 = _nonzero_sha256(
        result.get("state_receipt_sha256"),
        label="fresh Nginx receipt SHA-256",
    )
    receipt_path = _capture_receipt_path(inputs, receipt_sha256)
    if Path(result["state_receipt_path"]) != receipt_path:
        raise CurrentFrozenVerificationError(
            "fresh Nginx receipt path differs"
        )
    try:
        receipt, observed = NGINX.load_state_receipt(
            receipt_path,
            "legacy-frozen",
            inputs.operation_id,
            inputs.release_sha,
            inputs.release_tree_sha,
            inputs.aggregate_sha256,
            observed_at_epoch=now_fn(),
        )
    except NGINX.NginxCoordinatorError as exc:
        raise CurrentFrozenVerificationError(
            "fresh Nginx receipt is invalid"
        ) from exc
    if (
        observed != receipt_sha256
        or receipt["schema"]
        != NGINX.PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
    ):
        raise CurrentFrozenVerificationError(
            "fresh Nginx receipt binding differs"
        )
    return receipt, receipt_path, receipt_sha256


def _journal_state_sha256(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned["state_sha256"] = ZERO_SHA256
    return _sha256(canonical_json(unsigned))


def _journal_document(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture: Mapping[str, Any],
    fresh_receipt: Mapping[str, Any],
    fresh_receipt_path: Path,
    fresh_receipt_sha256: str,
    claim: Mapping[str, Any],
    claim_path: Path,
    claim_sha256: str,
) -> dict[str, Any]:
    document = {
        "schema": JOURNAL_SCHEMA,
        "status": "active",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "legacy_release_sha": bindings["bot_fi"].legacy_release_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "bindings": {
            role: bindings[role].canonical_sha256 for role in ROLES
        },
        "capture": {
            "state_receipt_sha256": capture["receipt_sha256"],
            "lease_claim_sha256": capture["claim_sha256"],
            "lease_claim_epoch": capture["claim_epoch"],
            "outcome_sha256": capture["outcome_sha256"],
            "consumption_sha256": capture["consumption_sha256"],
        },
        "fresh_receipt": {
            "path": str(fresh_receipt_path),
            "sha256": fresh_receipt_sha256,
            "readback_challenge_sha256": fresh_receipt[
                "readback_challenge_sha256"
            ],
            "issued_at_epoch": fresh_receipt["issued_at_epoch"],
            "expires_at_epoch": fresh_receipt["expires_at_epoch"],
            "captured_at_epoch": fresh_receipt["captured_at_epoch"],
        },
        "lease": {
            "path": str(claim_path),
            "sha256": claim_sha256,
            "epoch": claim["claim_epoch"],
            "nonce": claim["nonce"],
            "previous_claim_sha256": claim["previous_claim_sha256"],
        },
        "roles": {role: None for role in ROLES},
        "outcome_sha256": None,
        "consumption_sha256": None,
        "receipt_path": None,
        "receipt_sha256": None,
        "last_error_sha256": ZERO_SHA256,
        "state_sha256": ZERO_SHA256,
    }
    document["state_sha256"] = _journal_state_sha256(document)
    return document


def _validate_host_result(
    document: Mapping[str, Any],
    *,
    inputs: NGINX.CoordinatorInputs,
    binding: SOURCE.SnapshotBinding,
    role: str,
    fresh_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_sha256: str,
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    request = FROZEN.build_host_request(
        action="verify-current",
        inputs=inputs,
        role=role,
        binding_sha256=binding.canonical_sha256,
        state_receipt_sha256=fresh_receipt["_sha256"],
        lease_claim_sha256=claim_sha256,
        release_file_sha256={
            key: "1" * 64 for key in FROZEN.RELEASE_FILE_KEYS
        },
    )
    try:
        result = FROZEN._validate_host_result(
            document,
            request=request,
            expected_claim_epoch=claim["claim_epoch"],
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            f"{role} current-freeze result is invalid"
        ) from exc
    expected_role_readback = fresh_receipt["readbacks"][role]
    expected_freeze_sha256 = capture["journal"]["roles"][role][
        "freeze_evidence_sha256"
    ]
    if (
        result["readback_challenge_sha256"]
        != fresh_receipt["readback_challenge_sha256"]
        or result["issued_at_epoch"] != fresh_receipt["issued_at_epoch"]
        or result["expires_at_epoch"] != fresh_receipt["expires_at_epoch"]
        or result["captured_at_epoch"]
        < expected_role_readback["captured_at_epoch"]
        or result["previous_live_lease_claim_sha256"]
        != capture["claim_sha256"]
        or result["freeze_evidence_live_lease_claim_sha256"]
        != capture["claim_sha256"]
        or result["freeze_evidence_sha256"] != expected_freeze_sha256
        or result["role_freeze_generation_sha256"]
        != expected_role_readback["generation_sha256"]
        or result["freeze_generation_sha256"]
        != fresh_receipt["global_generation_sha256"]
    ):
        raise CurrentFrozenVerificationError(
            f"{role} current-freeze result binding differs"
        )
    return result


def _validate_journal(
    document: Mapping[str, Any],
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture: Mapping[str, Any],
    fresh_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_path: Path,
    claim_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "nginx_aggregate_sha256",
        "bindings",
        "capture",
        "fresh_receipt",
        "lease",
        "roles",
        "outcome_sha256",
        "consumption_sha256",
        "receipt_path",
        "receipt_sha256",
        "last_error_sha256",
        "state_sha256",
    }
    expected = _journal_document(
        inputs=inputs,
        bindings=bindings,
        capture=capture,
        fresh_receipt=fresh_receipt,
        fresh_receipt_path=Path(fresh_receipt["_path"]),
        fresh_receipt_sha256=fresh_receipt["_sha256"],
        claim=claim,
        claim_path=claim_path,
        claim_sha256=claim_sha256,
    )
    static_fields = {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "nginx_aggregate_sha256",
        "bindings",
        "capture",
        "fresh_receipt",
        "lease",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or any(document.get(key) != expected[key] for key in static_fields)
        or document["status"]
        not in {
            "active",
            "ready-to-consume",
            "consumed",
            "complete",
            "reconciliation-required",
        }
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLES)
        or (
            document["last_error_sha256"] != ZERO_SHA256
            and _nonzero_sha256(
                document["last_error_sha256"],
                label="journal error SHA-256",
            )
            != document["last_error_sha256"]
        )
        or document["state_sha256"] != _journal_state_sha256(document)
    ):
        raise CurrentFrozenVerificationError(
            "current-freeze journal identity or state differs"
        )
    for role in ROLES:
        row = document["roles"][role]
        if row is None:
            continue
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256", "result"}
        ):
            raise CurrentFrozenVerificationError(
                f"{role} journaled current-freeze result differs"
            )
        _nonzero_sha256(
            row["sha256"],
            label=f"{role} host result SHA-256",
        )
        result = _validate_host_result(
            row["result"],
            inputs=inputs,
            binding=bindings[role],
            role=role,
            fresh_receipt=fresh_receipt,
            claim=claim,
            claim_sha256=claim_sha256,
            capture=capture,
        )
        if (
            row["path"]
            != str(_host_result_path(inputs, claim_sha256, role))
            or row["sha256"] != _sha256(canonical_json(result))
        ):
            raise CurrentFrozenVerificationError(
                f"{role} journaled host result digest differs"
            )
        try:
            persisted = FROZEN._read_file(
                Path(row["path"]),
                label=f"{role} persisted current-freeze result",
                required_uid=required_uid,
                expected_mode=0o600,
                maximum=MAX_JSON_BYTES,
            )
        except FROZEN.FrozenSnapshotOrchestratorError as exc:
            raise CurrentFrozenVerificationError(
                f"{role} persisted current-freeze result is invalid"
            ) from exc
        if persisted != canonical_json(result):
            raise CurrentFrozenVerificationError(
                f"{role} persisted current-freeze result changed"
            )
    complete_roles = all(document["roles"][role] is not None for role in ROLES)
    for field in ("outcome_sha256", "consumption_sha256", "receipt_sha256"):
        if document[field] is not None:
            _nonzero_sha256(document[field], label=f"journal {field}")
    if (
        document["status"] in {
            "ready-to-consume",
            "consumed",
            "complete",
        }
        and (
            not complete_roles
            or document["outcome_sha256"] is None
        )
    ):
        raise CurrentFrozenVerificationError(
            "current-freeze journal completion closure differs"
        )
    if document["status"] in {"consumed", "complete"} and (
        document["consumption_sha256"] is None
    ):
        raise CurrentFrozenVerificationError(
            "current-freeze consumed journal lacks consumption"
        )
    if document["status"] == "complete" and (
        document["receipt_path"] is None
        or document["receipt_sha256"] is None
    ):
        raise CurrentFrozenVerificationError(
            "current-freeze complete journal lacks receipt"
        )
    return json.loads(canonical_json(document).decode("ascii"))


def _read_document(
    path: Path,
    *,
    label: str,
    required_uid: int,
) -> dict[str, Any]:
    try:
        payload = FROZEN._read_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=FROZEN.MAX_JOURNAL_BYTES,
        )
        return FROZEN._strict_json(payload, label=label)
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(str(exc)) from exc


def _maybe_read_document(
    path: Path,
    *,
    label: str,
    required_uid: int,
) -> dict[str, Any] | None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CurrentFrozenVerificationError(
            f"{label} cannot be inspected"
        ) from exc
    return _read_document(
        path,
        label=label,
        required_uid=required_uid,
    )


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    required_uid: int,
    create: bool,
) -> None:
    journal["state_sha256"] = _journal_state_sha256(journal)
    try:
        FROZEN._write_private_atomic(
            path,
            journal,
            required_uid=required_uid,
            create=create,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            "current-freeze journal write failed"
        ) from exc


def _host_result_path(
    inputs: NGINX.CoordinatorInputs,
    claim_sha256: str,
    role: str,
) -> Path:
    return (
        _paths(inputs.operation_id, inputs.release_sha)["host_results"]
        / f"{claim_sha256}-{role}.json"
    )


def _persist_host_result(
    *,
    inputs: NGINX.CoordinatorInputs,
    role: str,
    result: Mapping[str, Any],
    claim_sha256: str,
    required_uid: int,
) -> dict[str, Any]:
    payload = canonical_json(result)
    digest = _sha256(payload)
    path = _host_result_path(inputs, claim_sha256, role)
    try:
        os.lstat(path)
    except FileNotFoundError:
        try:
            FROZEN._write_private_atomic(
                path,
                result,
                required_uid=required_uid,
                create=True,
            )
        except FROZEN.FrozenSnapshotOrchestratorError as exc:
            raise CurrentFrozenVerificationError(
                f"{role} host result could not be persisted"
            ) from exc
    except OSError as exc:
        raise CurrentFrozenVerificationError(
            f"{role} host result cannot be inspected"
        ) from exc
    try:
        existing = FROZEN._read_file(
            path,
            label=f"{role} existing current-freeze host result",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_JSON_BYTES,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            f"{role} existing host result is invalid"
        ) from exc
    if existing != payload:
        raise CurrentFrozenVerificationError(
            f"{role} existing host result differs"
        )
    return {
        "path": str(path),
        "sha256": digest,
        "result": json.loads(payload.decode("ascii")),
    }


def _outcome_document(
    *,
    inputs: NGINX.CoordinatorInputs,
    capture: Mapping[str, Any],
    fresh_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_sha256: str,
    roles: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": OUTCOME_SCHEMA,
        "status": "verified-current-frozen",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "capture_lease_claim_sha256": capture["claim_sha256"],
        "capture_lease_consumption_sha256": capture[
            "consumption_sha256"
        ],
        "fresh_state_receipt_sha256": fresh_receipt["_sha256"],
        "readback_challenge_sha256": fresh_receipt[
            "readback_challenge_sha256"
        ],
        "lease_claim_sha256": claim_sha256,
        "lease_claim_epoch": claim["claim_epoch"],
        "previous_live_lease_claim_sha256": claim[
            "previous_claim_sha256"
        ],
        "host_result_sha256": {
            role: roles[role]["sha256"] for role in ROLES
        },
        "all_roles_verified": True,
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
        "source_stopped_or_restarted": False,
        "current_mutated": False,
        "service_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def _receipt_document(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture: Mapping[str, Any],
    fresh_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_sha256: str,
    roles: Mapping[str, Any],
    outcome_sha256: str,
    consumption: Mapping[str, Any],
    consumption_sha256: str,
    captured_at_epoch: int,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "verified-current-frozen",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "legacy_release_sha": bindings["bot_fi"].legacy_release_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "bindings": {
            role: bindings[role].canonical_sha256 for role in ROLES
        },
        "capture_state_receipt_sha256": capture["receipt_sha256"],
        "capture_lease_claim_sha256": capture["claim_sha256"],
        "capture_lease_claim_epoch": capture["claim_epoch"],
        "capture_outcome_sha256": capture["outcome_sha256"],
        "capture_lease_consumption_sha256": capture[
            "consumption_sha256"
        ],
        "fresh_state_receipt_sha256": fresh_receipt["_sha256"],
        "readback_challenge_sha256": fresh_receipt[
            "readback_challenge_sha256"
        ],
        "issued_at_epoch": fresh_receipt["issued_at_epoch"],
        "expires_at_epoch": fresh_receipt["expires_at_epoch"],
        "captured_at_epoch": captured_at_epoch,
        "lease_claim_sha256": claim_sha256,
        "lease_claim_epoch": claim["claim_epoch"],
        "previous_live_lease_claim_sha256": claim[
            "previous_claim_sha256"
        ],
        "host_results": {
            role: roles[role]["result"] for role in ROLES
        },
        "host_result_sha256": {
            role: roles[role]["sha256"] for role in ROLES
        },
        "freeze_generation_sha256": fresh_receipt[
            "global_generation_sha256"
        ],
        "freeze_evidence": {
            role: {
                "live_lease_claim_sha256": roles[role]["result"][
                    "freeze_evidence_live_lease_claim_sha256"
                ],
                "sha256": roles[role]["result"][
                    "freeze_evidence_sha256"
                ],
            }
            for role in ROLES
        },
        "outcome_sha256": outcome_sha256,
        "lease_consumption": dict(consumption),
        "lease_consumption_sha256": consumption_sha256,
        "all_roles_verified": True,
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
        "source_stopped_or_restarted": False,
        "current_mutated": False,
        "service_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def validate_current_frozen_verification_receipt(
    document: Mapping[str, Any],
    *,
    expected_operation_id: str,
    expected_release_sha: str,
    expected_release_tree_sha: str,
    expected_legacy_release_sha: str,
    expected_nginx_aggregate_sha256: str,
    expected_bindings: Mapping[str, str],
    expected_capture_state_receipt_sha256: str,
    observed_at_epoch: int | None = None,
    allow_historical_completed: bool = False,
) -> dict[str, Any]:
    """Validate the immutable completed R2 receipt.

    Historical mode is reconciliation-only: it permits publication/readback
    after the R2 challenge expired, but does not make the consumed claim live
    again and must never be used to run another host verification.
    """
    fields = {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "legacy_release_sha",
        "nginx_aggregate_sha256",
        "bindings",
        "capture_state_receipt_sha256",
        "capture_lease_claim_sha256",
        "capture_lease_claim_epoch",
        "capture_outcome_sha256",
        "capture_lease_consumption_sha256",
        "fresh_state_receipt_sha256",
        "readback_challenge_sha256",
        "issued_at_epoch",
        "expires_at_epoch",
        "captured_at_epoch",
        "lease_claim_sha256",
        "lease_claim_epoch",
        "previous_live_lease_claim_sha256",
        "host_results",
        "host_result_sha256",
        "freeze_generation_sha256",
        "freeze_evidence",
        "outcome_sha256",
        "lease_consumption",
        "lease_consumption_sha256",
        "all_roles_verified",
        "legacy_writer_process_count",
        "writer_database_client_count",
        "file_mutator_process_count",
        "source_stopped_or_restarted",
        "current_mutated",
        "service_mutated",
        "container_mutated",
        "volume_mutated",
        "data_mutated",
    }
    identity = {
        "operation_id": expected_operation_id,
        "release_sha": expected_release_sha,
        "release_tree_sha": expected_release_tree_sha,
        "legacy_release_sha": expected_legacy_release_sha,
        "nginx_aggregate_sha256": expected_nginx_aggregate_sha256,
        "capture_state_receipt_sha256": (
            expected_capture_state_receipt_sha256
        ),
    }
    if observed_at_epoch is None:
        observed_at_epoch = int(time.time())
    if (
        type(observed_at_epoch) is not int
        or observed_at_epoch < 1
        or type(allow_historical_completed) is not bool
        or not isinstance(document, dict)
        or set(document) != fields
        or document["schema"] != RECEIPT_SCHEMA
        or document["status"] != "verified-current-frozen"
        or any(document.get(key) != value for key, value in identity.items())
        or document["bindings"] != dict(expected_bindings)
        or set(expected_bindings) != set(ROLES)
        or type(document["capture_lease_claim_epoch"]) is not int
        or document["capture_lease_claim_epoch"] < 1
        or type(document["lease_claim_epoch"]) is not int
        or document["lease_claim_epoch"]
        != document["capture_lease_claim_epoch"] + 1
        or document["previous_live_lease_claim_sha256"]
        != document["capture_lease_claim_sha256"]
        or type(document["issued_at_epoch"]) is not int
        or type(document["expires_at_epoch"]) is not int
        or type(document["captured_at_epoch"]) is not int
        or not (
            1
            <= document["issued_at_epoch"]
            <= document["captured_at_epoch"]
            <= document["expires_at_epoch"]
        )
        or (
            not allow_historical_completed
            and observed_at_epoch > document["expires_at_epoch"]
        )
        or not isinstance(document["host_results"], dict)
        or set(document["host_results"]) != set(ROLES)
        or not isinstance(document["host_result_sha256"], dict)
        or set(document["host_result_sha256"]) != set(ROLES)
        or not isinstance(document["freeze_evidence"], dict)
        or set(document["freeze_evidence"]) != set(ROLES)
        or document["all_roles_verified"] is not True
        or document["legacy_writer_process_count"] != 0
        or document["writer_database_client_count"] != 0
        or document["file_mutator_process_count"] != 0
        or document["source_stopped_or_restarted"] is not False
        or any(
            document[field] is not False
            for field in (
                "current_mutated",
                "service_mutated",
                "container_mutated",
                "volume_mutated",
                "data_mutated",
            )
        )
    ):
        raise CurrentFrozenVerificationError(
            "current-frozen verification receipt differs"
        )
    for field in (
        "capture_state_receipt_sha256",
        "capture_lease_claim_sha256",
        "capture_outcome_sha256",
        "capture_lease_consumption_sha256",
        "fresh_state_receipt_sha256",
        "readback_challenge_sha256",
        "lease_claim_sha256",
        "previous_live_lease_claim_sha256",
        "freeze_generation_sha256",
        "outcome_sha256",
        "lease_consumption_sha256",
    ):
        _nonzero_sha256(document[field], label=f"receipt {field}")
    consumption = document["lease_consumption"]
    if (
        not isinstance(consumption, dict)
        or set(consumption) != LEASE_CONSUMPTION_FIELDS
        or consumption.get("schema") != NGINX.LIVE_LEASE_CONSUMPTION_SCHEMA
        or consumption.get("status") != "consumed"
        or consumption.get("owner_action") != OWNER_ACTION
        or consumption.get("operation_id") != expected_operation_id
        or consumption.get("release_sha") != expected_release_sha
        or consumption.get("release_tree_sha") != expected_release_tree_sha
        or consumption.get("aggregate_sha256")
        != expected_nginx_aggregate_sha256
        or consumption.get("claim_sha256")
        != document["lease_claim_sha256"]
        or consumption.get("claim_epoch")
        != document["lease_claim_epoch"]
        or not isinstance(consumption.get("claim_nonce"), str)
        or NGINX.LIVE_LEASE_NONCE_RE.fullmatch(
            consumption["claim_nonce"]
        )
        is None
        or consumption["claim_nonce"] == ZERO_SHA256
        or consumption.get("outcome") != OWNER_OUTCOME
        or consumption.get("outcome_sha256")
        != document["outcome_sha256"]
        or consumption.get("readiness_audit_sha256") is not None
        or consumption.get("final_state") != "legacy-frozen"
        or consumption.get("final_state_receipt_sha256")
        != document["fresh_state_receipt_sha256"]
        or consumption.get("controller_authoritative") is not True
        or consumption.get("automatic") is not False
        or type(consumption.get("controller_journal_event_count")) is not int
        or consumption["controller_journal_event_count"] < 0
        or type(consumption.get("controller_evidence_count")) is not int
        or consumption["controller_evidence_count"] < 1
        or type(consumption.get("consumer_pid")) is not int
        or consumption["consumer_pid"] < 1
        or type(consumption.get("adopted_after_crash")) is not bool
        or consumption.get("controller_lock_path")
        != os.fspath(
            _paths(expected_operation_id, expected_release_sha)[
                "nginx_secret"
            ]
            / "coordinator.lock"
        )
        or _sha256(canonical_json(consumption))
        != document["lease_consumption_sha256"]
    ):
        raise CurrentFrozenVerificationError(
            "current-frozen lease consumption binding differs"
        )
    for field in (
        "controller_journal_sha256",
        "controller_evidence_tail_sha256",
        "consumption_nonce",
    ):
        _nonzero_sha256(
            consumption[field],
            label=f"receipt consumption {field}",
        )
    captured_times: list[int] = []
    for role in ROLES:
        result = document["host_results"][role]
        if (
            not isinstance(result, dict)
            or set(result) != FROZEN.HOST_CURRENT_VERIFY_FIELDS
            or result["schema"] != FROZEN.HOST_CURRENT_VERIFY_SCHEMA
            or result["status"] != "verified-current-frozen"
            or result["role"] != role
            or result["operation_id"] != expected_operation_id
            or result["release_sha"] != expected_release_sha
            or result["release_tree_sha"] != expected_release_tree_sha
            or result["binding_sha256"] != expected_bindings[role]
            or result["state_receipt_sha256"]
            != document["fresh_state_receipt_sha256"]
            or result["readback_challenge_sha256"]
            != document["readback_challenge_sha256"]
            or result["issued_at_epoch"] != document["issued_at_epoch"]
            or result["expires_at_epoch"] != document["expires_at_epoch"]
            or result["lease_claim_sha256"]
            != document["lease_claim_sha256"]
            or result["lease_claim_epoch"]
            != document["lease_claim_epoch"]
            or result["previous_live_lease_claim_sha256"]
            != document["capture_lease_claim_sha256"]
            or result["freeze_generation_sha256"]
            != document["freeze_generation_sha256"]
            or result["freeze_evidence_live_lease_claim_sha256"]
            != document["capture_lease_claim_sha256"]
            or result["legacy_writer_process_count"] != 0
            or result["writer_database_client_count"] != 0
            or result["file_mutator_process_count"] != 0
            or result["source_stopped_or_restarted"] is not False
            or result["database_container_running"] is not True
            or result["redis_container_running"] is not True
            or result["pull_policy"] != "never"
            or not (
                document["issued_at_epoch"]
                <= result["captured_at_epoch"]
                <= document["expires_at_epoch"]
            )
            or not isinstance(result["source_container_ids"], dict)
            or set(result["source_container_ids"])
            != set(SOURCE.SOURCE_CONTAINERS)
            or not isinstance(result["writer_container_ids"], dict)
            or set(result["writer_container_ids"])
            != {
                kind
                for kind, _name, _service in FROZEN.FREEZE.ROLE_WRITERS[
                    role
                ]
            }
            or any(
                result[field] is not False
                for field in (
                    "source_mutated",
                    "current_mutated",
                    "service_mutated",
                    "container_mutated",
                    "volume_mutated",
                    "data_mutated",
                    "production_mutated",
                )
            )
            or document["host_result_sha256"][role]
            != _sha256(canonical_json(result))
            or document["freeze_evidence"][role]
            != {
                "live_lease_claim_sha256": (
                    document["capture_lease_claim_sha256"]
                ),
                "sha256": result["freeze_evidence_sha256"],
            }
        ):
            raise CurrentFrozenVerificationError(
                f"{role} receipt result binding differs"
            )
        for field in (
            "readback_challenge_sha256",
            "lease_claim_sha256",
            "previous_live_lease_claim_sha256",
            "freeze_evidence_live_lease_claim_sha256",
            "freeze_evidence_sha256",
            "role_freeze_generation_sha256",
            "freeze_generation_sha256",
            "journal_sha256",
        ):
            _nonzero_sha256(
                result[field],
                label=f"{role} receipt result {field}",
            )
        for values in (
            result["source_container_ids"].values(),
            result["writer_container_ids"].values(),
        ):
            if any(
                not isinstance(value, str)
                or FROZEN.FREEZE.CONTAINER_ID_RE.fullmatch(value) is None
                or value == ZERO_SHA256
                for value in values
            ):
                raise CurrentFrozenVerificationError(
                    f"{role} receipt container identities differ"
                )
        captured_times.append(result["captured_at_epoch"])
    if (
        max(captured_times) - min(captured_times)
        > MAX_CROSS_HOST_SKEW_SECONDS
        or document["captured_at_epoch"] < max(captured_times)
    ):
        raise CurrentFrozenVerificationError(
            "current-frozen receipt cross-host time closure differs"
        )
    return json.loads(canonical_json(document).decode("ascii"))


def load_current_frozen_verification_receipt(
    path: Path,
    *,
    expected_sha256: str,
    expected_operation_id: str,
    expected_release_sha: str,
    expected_release_tree_sha: str,
    expected_legacy_release_sha: str,
    expected_nginx_aggregate_sha256: str,
    expected_bindings: Mapping[str, str],
    expected_capture_state_receipt_sha256: str,
    required_uid: int = 0,
    observed_at_epoch: int | None = None,
    allow_historical_completed: bool = False,
) -> tuple[dict[str, Any], str]:
    """Load a controller-canonical completed R2 verification receipt."""
    expected_sha256 = _nonzero_sha256(
        expected_sha256,
        label="current-frozen receipt SHA-256",
    )
    expected_path = (
        _paths(expected_operation_id, expected_release_sha)[
            "verification_receipts"
        ]
        / f"{expected_sha256}.json"
    )
    if path != expected_path:
        raise CurrentFrozenVerificationError(
            "current-frozen receipt path is not canonical"
        )
    document = _read_document(
        path,
        label="current-frozen verification receipt",
        required_uid=required_uid,
    )
    observed = _sha256(canonical_json(document))
    if observed != expected_sha256:
        raise CurrentFrozenVerificationError(
            "current-frozen receipt digest differs"
        )
    return (
        validate_current_frozen_verification_receipt(
            document,
            expected_operation_id=expected_operation_id,
            expected_release_sha=expected_release_sha,
            expected_release_tree_sha=expected_release_tree_sha,
            expected_legacy_release_sha=expected_legacy_release_sha,
            expected_nginx_aggregate_sha256=(
                expected_nginx_aggregate_sha256
            ),
            expected_bindings=expected_bindings,
            expected_capture_state_receipt_sha256=(
                expected_capture_state_receipt_sha256
            ),
            observed_at_epoch=observed_at_epoch,
            allow_historical_completed=allow_historical_completed,
        ),
        observed,
    )


def _persist_receipt(
    *,
    inputs: NGINX.CoordinatorInputs,
    document: Mapping[str, Any],
    required_uid: int,
) -> tuple[Path, str]:
    payload = canonical_json(document)
    digest = _sha256(payload)
    path = _paths(inputs.operation_id, inputs.release_sha)[
        "verification_receipts"
    ] / f"{digest}.json"
    try:
        existing = FROZEN._read_file(
            path,
            label="existing current-frozen verification receipt",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=FROZEN.MAX_JOURNAL_BYTES,
        )
    except FROZEN.FrozenSnapshotOrchestratorError:
        try:
            FROZEN._write_private_atomic(
                path,
                document,
                required_uid=required_uid,
                create=True,
            )
        except FROZEN.FrozenSnapshotOrchestratorError as exc:
            raise CurrentFrozenVerificationError(
                "current-frozen receipt publication failed"
            ) from exc
        existing = payload
    if existing != payload:
        raise CurrentFrozenVerificationError(
            "existing current-frozen receipt differs"
        )
    return path, digest


def _controller_result(
    *,
    inputs: NGINX.CoordinatorInputs,
    receipt_path: Path,
    receipt_sha256: str,
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "verified-current-frozen",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "receipt_path": str(receipt_path),
        "receipt_sha256": receipt_sha256,
        "lease_claim_sha256": journal["lease"]["sha256"],
        "lease_claim_epoch": journal["lease"]["epoch"],
        "lease_consumption_sha256": journal["consumption_sha256"],
        "all_roles_verified": True,
        "source_stopped_or_restarted": False,
        "current_mutated": False,
        "service_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
    }


def _finalize_consumed_journal(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    capture: Mapping[str, Any],
    fresh_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    claim_sha256: str,
    consumption: Mapping[str, Any],
    consumption_sha256: str,
    journal: dict[str, Any],
    journal_path: Path,
    required_uid: int,
    observed_at_epoch: int,
) -> dict[str, Any]:
    expected_outcome_sha256 = _sha256(
        canonical_json(
            _outcome_document(
                inputs=inputs,
                capture=capture,
                fresh_receipt=fresh_receipt,
                claim=claim,
                claim_sha256=claim_sha256,
                roles=journal["roles"],
            )
        )
    )
    if (
        any(journal["roles"][role] is None for role in ROLES)
        or journal["outcome_sha256"] is None
        or journal["outcome_sha256"] != expected_outcome_sha256
        or consumption["owner_action"] != OWNER_ACTION
        or consumption["claim_sha256"] != claim_sha256
        or consumption["claim_epoch"] != claim["claim_epoch"]
        or consumption["outcome"] != OWNER_OUTCOME
        or consumption["outcome_sha256"] != journal["outcome_sha256"]
        or consumption["readiness_audit_sha256"] is not None
        or consumption["final_state"] != "legacy-frozen"
        or consumption["final_state_receipt_sha256"]
        != fresh_receipt["_sha256"]
        or _sha256(canonical_json(consumption)) != consumption_sha256
    ):
        raise CurrentFrozenVerificationError(
            "consumed current-freeze journal binding differs"
        )
    captured_at_epoch = max(
        journal["roles"][role]["result"]["captured_at_epoch"]
        for role in ROLES
    )
    if captured_at_epoch > fresh_receipt["expires_at_epoch"]:
        raise CurrentFrozenVerificationError(
            "consumed current-freeze evidence expired before publication"
        )
    receipt = _receipt_document(
        inputs=inputs,
        bindings=bindings,
        capture=capture,
        fresh_receipt=fresh_receipt,
        claim=claim,
        claim_sha256=claim_sha256,
        roles=journal["roles"],
        outcome_sha256=journal["outcome_sha256"],
        consumption=consumption,
        consumption_sha256=consumption_sha256,
        captured_at_epoch=captured_at_epoch,
    )
    receipt = validate_current_frozen_verification_receipt(
        receipt,
        expected_operation_id=inputs.operation_id,
        expected_release_sha=inputs.release_sha,
        expected_release_tree_sha=inputs.release_tree_sha,
        expected_legacy_release_sha=bindings[
            "bot_fi"
        ].legacy_release_sha,
        expected_nginx_aggregate_sha256=inputs.aggregate_sha256,
        expected_bindings={
            role: bindings[role].canonical_sha256 for role in ROLES
        },
        expected_capture_state_receipt_sha256=capture[
            "receipt_sha256"
        ],
        observed_at_epoch=observed_at_epoch,
        allow_historical_completed=True,
    )
    receipt_path, receipt_sha256 = _persist_receipt(
        inputs=inputs,
        document=receipt,
        required_uid=required_uid,
    )
    if journal["receipt_path"] not in {None, str(receipt_path)} or journal[
        "receipt_sha256"
    ] not in {None, receipt_sha256}:
        raise CurrentFrozenVerificationError(
            "completed current-freeze receipt binding differs"
        )
    journal["consumption_sha256"] = consumption_sha256
    journal["receipt_path"] = str(receipt_path)
    journal["receipt_sha256"] = receipt_sha256
    journal["status"] = "complete"
    journal["last_error_sha256"] = ZERO_SHA256
    _write_journal(
        journal_path,
        journal,
        required_uid=required_uid,
        create=False,
    )
    return _controller_result(
        inputs=inputs,
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
        journal=journal,
    )


def orchestrate(
    *,
    aggregate_path: Path,
    bot_fi_nginx_manifest: Path,
    bot_fi_nginx_archive: Path,
    webapp_fi_nginx_manifest: Path,
    webapp_fi_nginx_archive: Path,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
    capture_state_receipt_path: Path,
    capture_state_receipt_sha256: str,
    known_hosts: Path = FROZEN.KNOWN_HOSTS,
    ssh_identity: Path = FROZEN.DEFAULT_SSH_IDENTITY,
    resume_claim_path: Path | None = None,
    resume_claim_sha256: str | None = None,
    resume_claim_nonce: str | None = None,
    apply: bool = False,
    confirm: str | None = None,
    runner: Any = None,
    required_uid: int = 0,
    checkpoint: Checkpoint | None = None,
    observed_host_addresses: set[str] | None = None,
    now_fn: NowFn = lambda: int(time.time()),
) -> dict[str, Any]:
    inputs = _load_inputs(
        aggregate_path=aggregate_path,
        bot_fi_nginx_manifest=bot_fi_nginx_manifest,
        bot_fi_nginx_archive=bot_fi_nginx_archive,
        webapp_fi_nginx_manifest=webapp_fi_nginx_manifest,
        webapp_fi_nginx_archive=webapp_fi_nginx_archive,
        known_hosts=known_hosts,
        ssh_identity=ssh_identity,
    )
    bindings = _load_bindings(
        inputs,
        bot_fi_binding=bot_fi_binding,
        webapp_fi_binding=webapp_fi_binding,
    )
    capture = _validate_capture_completion(
        inputs=inputs,
        bindings=bindings,
        capture_state_receipt_path=capture_state_receipt_path,
        capture_state_receipt_sha256=capture_state_receipt_sha256,
        required_uid=required_uid,
    )
    plan = render_plan(inputs=inputs, bindings=bindings, capture=capture)
    if not apply:
        if confirm is not None:
            raise CurrentFrozenVerificationError(
                "--confirm is valid only with --apply"
            )
        return plan
    if confirm != plan["required_confirmation"]:
        raise CurrentFrozenVerificationError(
            "current-freeze verification confirmation mismatch"
        )
    if os.geteuid() != required_uid or required_uid != 0:
        raise CurrentFrozenVerificationError(
            "current-freeze verification must run as root"
        )
    try:
        FINLAND_STAGE._verify_role_host(
            "bot_fi",
            observed_host_addresses=observed_host_addresses,
        )
        FROZEN._assert_ssh_material(
            ssh_identity,
            known_hosts=known_hosts,
            required_uid=required_uid,
        )
    except (
        FINLAND_STAGE.FinlandStageError,
        FROZEN.FrozenSnapshotOrchestratorError,
    ) as exc:
        raise CurrentFrozenVerificationError(
            "controller host or SSH material is invalid"
        ) from exc
    paths = _paths(inputs.operation_id, inputs.release_sha)
    _ensure_directories(paths, required_uid=required_uid)
    callback = checkpoint if checkpoint is not None else (lambda _name: None)
    resume_supplied = (
        resume_claim_path is not None,
        resume_claim_sha256 is not None,
        resume_claim_nonce is not None,
    )
    if any(resume_supplied) and not all(resume_supplied):
        raise CurrentFrozenVerificationError(
            "resume requires exact claim path, digest, and nonce"
        )
    binding_paths = {
        "bot_fi": bot_fi_binding,
        "webapp_fi": webapp_fi_binding,
    }
    release_file_sha256 = FROZEN._release_file_hashes(
        inputs.operation_id,
        inputs.release_sha,
        required_uid=required_uid,
    )

    try:
        lock_context = FROZEN._controller_lock(
            paths["verification_lock"],
            required_uid=required_uid,
        )
    except FROZEN.FrozenSnapshotOrchestratorError as exc:
        raise CurrentFrozenVerificationError(
            "current-freeze controller lock is unavailable"
        ) from exc
    with lock_context:
        preliminary = _maybe_read_document(
            paths["verification_journal"],
            label="current-freeze verification journal",
            required_uid=required_uid,
        )
        journal_before_lease: dict[str, Any] | None = None
        if preliminary is None and not any(resume_supplied):
            fresh_receipt, fresh_receipt_path, fresh_receipt_sha256 = (
                _fresh_readback(
                    inputs=inputs,
                    aggregate_path=aggregate_path,
                    bot_fi_nginx_manifest=bot_fi_nginx_manifest,
                    bot_fi_nginx_archive=bot_fi_nginx_archive,
                    webapp_fi_nginx_manifest=webapp_fi_nginx_manifest,
                    webapp_fi_nginx_archive=webapp_fi_nginx_archive,
                    known_hosts=known_hosts,
                    ssh_identity=ssh_identity,
                    runner=runner,
                    now_fn=now_fn,
                )
            )
            if fresh_receipt_sha256 == capture["receipt_sha256"]:
                raise CurrentFrozenVerificationError(
                    "post-snapshot Nginx receipt was replayed"
                )
            fresh_receipt = dict(fresh_receipt)
            fresh_receipt["_path"] = str(fresh_receipt_path)
            fresh_receipt["_sha256"] = fresh_receipt_sha256
            lease_context = NGINX.hold_coordinator_live_lease(
                inputs=inputs,
                owner_action=OWNER_ACTION,
                legacy_frozen_receipt_path=fresh_receipt_path,
                legacy_frozen_receipt_sha256=fresh_receipt_sha256,
            )
        elif preliminary is None:
            assert resume_claim_path is not None
            assert resume_claim_sha256 is not None
            assert resume_claim_nonce is not None
            resume_claim_sha256 = _nonzero_sha256(
                resume_claim_sha256,
                label="orphaned resume claim SHA-256",
            )
            expected_claim_path = FROZEN.canonical_paths(
                inputs.operation_id,
                inputs.release_sha,
                lease_claim_sha256=resume_claim_sha256,
            )["lease_claim"]
            if resume_claim_path != expected_claim_path:
                raise CurrentFrozenVerificationError(
                    "orphaned resume claim path is not canonical"
                )
            try:
                claim_material, receipt_material = (
                    NGINX._load_claim_from_controller(
                        inputs,
                        resume_claim_path,
                        resume_claim_sha256,
                    )
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "orphaned verification claim is invalid"
                ) from exc
            if (
                claim_material["nonce"] != resume_claim_nonce
                or claim_material["owner_action"] != OWNER_ACTION
                or claim_material["claim_epoch"]
                != capture["claim_epoch"] + 1
                or claim_material["previous_claim_sha256"]
                != capture["claim_sha256"]
            ):
                raise CurrentFrozenVerificationError(
                    "orphaned verification claim chain differs"
                )
            fresh_receipt_path = Path(
                claim_material["legacy_frozen_receipt_path"]
            )
            fresh_receipt_sha256 = claim_material[
                "legacy_frozen_receipt_sha256"
            ]
            try:
                fresh_receipt, observed = NGINX.load_state_receipt(
                    fresh_receipt_path,
                    "legacy-frozen",
                    inputs.operation_id,
                    inputs.release_sha,
                    inputs.release_tree_sha,
                    inputs.aggregate_sha256,
                    observed_at_epoch=now_fn(),
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "orphaned verification receipt is expired or invalid"
                ) from exc
            if (
                observed != fresh_receipt_sha256
                or fresh_receipt != receipt_material
                or fresh_receipt_sha256 == capture["receipt_sha256"]
            ):
                raise CurrentFrozenVerificationError(
                    "orphaned verification receipt differs"
                )
            fresh_receipt = dict(fresh_receipt)
            fresh_receipt["_path"] = str(fresh_receipt_path)
            fresh_receipt["_sha256"] = fresh_receipt_sha256
            lease_context = NGINX.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=OWNER_ACTION,
                claim_path=resume_claim_path,
                expected_claim_sha256=resume_claim_sha256,
                expected_nonce=resume_claim_nonce,
            )
        else:
            assert preliminary is not None
            fresh_row = preliminary.get("fresh_receipt")
            lease_row = preliminary.get("lease")
            if not isinstance(fresh_row, dict) or not isinstance(
                lease_row, dict
            ):
                raise CurrentFrozenVerificationError(
                    "resume journal material is incomplete"
                )
            fresh_receipt_path = Path(fresh_row["path"])
            fresh_receipt_sha256 = fresh_row["sha256"]
            if fresh_receipt_path != _capture_receipt_path(
                inputs,
                fresh_receipt_sha256,
            ):
                raise CurrentFrozenVerificationError(
                    "journaled fresh Nginx receipt path is not canonical"
                )
            try:
                fresh_receipt, observed = NGINX.load_state_receipt(
                    fresh_receipt_path,
                    "legacy-frozen",
                    inputs.operation_id,
                    inputs.release_sha,
                    inputs.release_tree_sha,
                    inputs.aggregate_sha256,
                    allow_historical=True,
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "resumed fresh Nginx receipt is expired or invalid"
                ) from exc
            if (
                observed != fresh_receipt_sha256
                or fresh_receipt.get("schema")
                != NGINX.PRE_FREEZE_FRESH_READBACK_RECEIPT_SCHEMA
            ):
                raise CurrentFrozenVerificationError(
                    "resumed fresh Nginx receipt digest differs"
                )
            fresh_receipt = dict(fresh_receipt)
            fresh_receipt["_path"] = str(fresh_receipt_path)
            fresh_receipt["_sha256"] = fresh_receipt_sha256
            claim_path = Path(lease_row["path"])
            claim_sha256 = lease_row["sha256"]
            expected_claim_path = FROZEN.canonical_paths(
                inputs.operation_id,
                inputs.release_sha,
                lease_claim_sha256=claim_sha256,
            )["lease_claim"]
            if claim_path != expected_claim_path:
                raise CurrentFrozenVerificationError(
                    "journaled verification claim path is not canonical"
                )
            try:
                claim_material, observed_claim_sha256 = (
                    NGINX.load_live_lease_claim_material(
                        claim_path,
                        state_receipt_path=fresh_receipt_path,
                        expected_claim_sha256=claim_sha256,
                        expected_state_receipt_sha256=(
                            fresh_receipt_sha256
                        ),
                        operation_id=inputs.operation_id,
                        release_sha=inputs.release_sha,
                        release_tree_sha=inputs.release_tree_sha,
                        aggregate_sha256=inputs.aggregate_sha256,
                    )
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "journaled verification claim is invalid"
                ) from exc
            if (
                observed_claim_sha256 != claim_sha256
                or claim_material["owner_action"] != OWNER_ACTION
                or claim_material["nonce"] != lease_row["nonce"]
                or claim_material["claim_epoch"]
                != capture["claim_epoch"] + 1
                or claim_material["previous_claim_sha256"]
                != capture["claim_sha256"]
            ):
                raise CurrentFrozenVerificationError(
                    "journaled verification claim chain differs"
                )
            journal_before_lease = _validate_journal(
                preliminary,
                inputs=inputs,
                bindings=bindings,
                capture=capture,
                fresh_receipt=fresh_receipt,
                claim=claim_material,
                claim_path=claim_path,
                claim_sha256=claim_sha256,
                required_uid=required_uid,
            )
            try:
                consumed = NGINX._load_consumption_audit(
                    inputs,
                    claim=claim_material,
                    claim_sha256=claim_sha256,
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "journaled verification consumption is invalid"
                ) from exc
            if consumed is not None:
                if any(resume_supplied):
                    raise CurrentFrozenVerificationError(
                        "consumed verification claim cannot be resumed"
                    )
                return _finalize_consumed_journal(
                    inputs=inputs,
                    bindings=bindings,
                    capture=capture,
                    fresh_receipt=fresh_receipt,
                    claim=claim_material,
                    claim_sha256=claim_sha256,
                    consumption=consumed[0],
                    consumption_sha256=consumed[1],
                    journal=journal_before_lease,
                    journal_path=paths["verification_journal"],
                    required_uid=required_uid,
                    observed_at_epoch=now_fn(),
                )
            try:
                current_receipt, current_observed = (
                    NGINX.load_state_receipt(
                        fresh_receipt_path,
                        "legacy-frozen",
                        inputs.operation_id,
                        inputs.release_sha,
                        inputs.release_tree_sha,
                        inputs.aggregate_sha256,
                        observed_at_epoch=now_fn(),
                    )
                )
            except NGINX.NginxCoordinatorError as exc:
                raise CurrentFrozenVerificationError(
                    "unconsumed verification receipt is expired or invalid"
                ) from exc
            if (
                current_observed != fresh_receipt_sha256
                or current_receipt != {
                    key: value
                    for key, value in fresh_receipt.items()
                    if not key.startswith("_")
                }
            ):
                raise CurrentFrozenVerificationError(
                    "unconsumed verification receipt changed"
                )
            if not all(resume_supplied):
                raise CurrentFrozenVerificationError(
                    "unconsumed current-freeze claim requires exact resume"
                )
            if (
                resume_claim_path != claim_path
                or resume_claim_sha256 != claim_sha256
                or resume_claim_nonce != lease_row["nonce"]
            ):
                raise CurrentFrozenVerificationError(
                    "resume claim differs from current-freeze journal"
                )
            lease_context = NGINX.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=OWNER_ACTION,
                claim_path=resume_claim_path,
                expected_claim_sha256=resume_claim_sha256,
                expected_nonce=resume_claim_nonce,
            )
        with lease_context as lease:
            claim = lease.claim
            if (
                claim["owner_action"] != OWNER_ACTION
                or claim["claim_epoch"] != capture["claim_epoch"] + 1
                or claim["previous_claim_sha256"]
                != capture["claim_sha256"]
                or claim["legacy_frozen_receipt_sha256"]
                != fresh_receipt_sha256
            ):
                raise CurrentFrozenVerificationError(
                    "verification lease is not the strict R1-to-R2 chain"
                )
            if preliminary is None:
                journal = _journal_document(
                    inputs=inputs,
                    bindings=bindings,
                    capture=capture,
                    fresh_receipt=fresh_receipt,
                    fresh_receipt_path=fresh_receipt_path,
                    fresh_receipt_sha256=fresh_receipt_sha256,
                    claim=claim,
                    claim_path=lease.claim_path,
                    claim_sha256=lease.claim_sha256,
                )
                _write_journal(
                    paths["verification_journal"],
                    journal,
                    required_uid=required_uid,
                    create=True,
                )
            else:
                assert journal_before_lease is not None
                journal = journal_before_lease
            try:
                for role in ROLES:
                    lease.verify()
                    if journal["roles"][role] is None:
                        FROZEN._install_role_material(
                            role=role,
                            lease=lease,
                            inputs=inputs,
                            bindings=bindings,
                            binding_paths=binding_paths,
                            state_receipt_path=fresh_receipt_path,
                            state_receipt_sha256=fresh_receipt_sha256,
                            release_file_sha256=release_file_sha256,
                            paths=FROZEN.canonical_paths(
                                inputs.operation_id,
                                inputs.release_sha,
                                state_receipt_sha256=(
                                    fresh_receipt_sha256
                                ),
                                lease_claim_sha256=lease.claim_sha256,
                            ),
                            ssh_identity=ssh_identity,
                            known_hosts=known_hosts,
                            runner=runner,
                            required_uid=required_uid,
                            checkpoint=callback,
                        )
                        result = FROZEN._invoke_bound_action(
                            action="verify-current",
                            role=role,
                            lease=lease,
                            inputs=inputs,
                            bindings=bindings,
                            state_receipt_sha256=fresh_receipt_sha256,
                            release_file_sha256=release_file_sha256,
                            paths=FROZEN.canonical_paths(
                                inputs.operation_id,
                                inputs.release_sha,
                                state_receipt_sha256=(
                                    fresh_receipt_sha256
                                ),
                                lease_claim_sha256=lease.claim_sha256,
                            ),
                            ssh_identity=ssh_identity,
                            known_hosts=known_hosts,
                            runner=runner,
                            checkpoint=callback,
                        )
                        result = _validate_host_result(
                            result,
                            inputs=inputs,
                            binding=bindings[role],
                            role=role,
                            fresh_receipt=fresh_receipt,
                            claim=claim,
                            claim_sha256=lease.claim_sha256,
                            capture=capture,
                        )
                        journal["roles"][role] = _persist_host_result(
                            inputs=inputs,
                            role=role,
                            result=result,
                            claim_sha256=lease.claim_sha256,
                            required_uid=required_uid,
                        )
                        _write_journal(
                            paths["verification_journal"],
                            journal,
                            required_uid=required_uid,
                            create=False,
                        )
                    else:
                        _validate_host_result(
                            journal["roles"][role]["result"],
                            inputs=inputs,
                            binding=bindings[role],
                            role=role,
                            fresh_receipt=fresh_receipt,
                            claim=claim,
                            claim_sha256=lease.claim_sha256,
                            capture=capture,
                        )
                captured_times = [
                    journal["roles"][role]["result"][
                        "captured_at_epoch"
                    ]
                    for role in ROLES
                ]
                if (
                    max(captured_times) - min(captured_times)
                    > MAX_CROSS_HOST_SKEW_SECONDS
                ):
                    raise CurrentFrozenVerificationError(
                        "current-freeze host verification skew is excessive"
                    )
                captured_at_epoch = now_fn()
                if not (
                    max(captured_times)
                    <= captured_at_epoch
                    <= fresh_receipt["expires_at_epoch"]
                ):
                    raise CurrentFrozenVerificationError(
                        "current-freeze verification expired before consume"
                    )
                outcome = _outcome_document(
                    inputs=inputs,
                    capture=capture,
                    fresh_receipt=fresh_receipt,
                    claim=claim,
                    claim_sha256=lease.claim_sha256,
                    roles=journal["roles"],
                )
                outcome_sha256 = _sha256(canonical_json(outcome))
                journal["outcome_sha256"] = outcome_sha256
                journal["status"] = "ready-to-consume"
                _write_journal(
                    paths["verification_journal"],
                    journal,
                    required_uid=required_uid,
                    create=False,
                )
                lease.verify()
                _consumption_path, consumption_sha256 = lease.consume(
                    outcome=OWNER_OUTCOME,
                    outcome_sha256=outcome_sha256,
                )
                try:
                    loaded_consumption = NGINX._load_consumption_audit(
                        inputs,
                        claim=claim,
                        claim_sha256=lease.claim_sha256,
                    )
                except NGINX.NginxCoordinatorError as exc:
                    raise CurrentFrozenVerificationError(
                        "verification lease consumption is invalid"
                    ) from exc
                if (
                    loaded_consumption is None
                    or loaded_consumption[1] != consumption_sha256
                    or loaded_consumption[0]["outcome"] != OWNER_OUTCOME
                    or loaded_consumption[0]["outcome_sha256"]
                    != outcome_sha256
                ):
                    raise CurrentFrozenVerificationError(
                        "verification lease consumption readback differs"
                    )
                consumption_document = loaded_consumption[0]
                journal["consumption_sha256"] = consumption_sha256
                journal["status"] = "consumed"
                _write_journal(
                    paths["verification_journal"],
                    journal,
                    required_uid=required_uid,
                    create=False,
                )
                return _finalize_consumed_journal(
                    inputs=inputs,
                    bindings=bindings,
                    capture=capture,
                    fresh_receipt=fresh_receipt,
                    claim=claim,
                    claim_sha256=lease.claim_sha256,
                    consumption=consumption_document,
                    consumption_sha256=consumption_sha256,
                    journal=journal,
                    journal_path=paths["verification_journal"],
                    required_uid=required_uid,
                    observed_at_epoch=now_fn(),
                )
            except BaseException as exc:
                if journal["status"] != "complete":
                    journal["status"] = "reconciliation-required"
                    journal["last_error_sha256"] = _sha256(
                        (
                            f"{type(exc).__name__}:{str(exc)}"
                        ).encode("utf-8", errors="replace")
                    )
                    try:
                        _write_journal(
                            paths["verification_journal"],
                            journal,
                            required_uid=required_uid,
                            create=False,
                        )
                    except BaseException as journal_error:
                        raise exc from journal_error
                raise
        raise CurrentFrozenVerificationError(
            "current-freeze verification ended without a receipt"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, required=True)
    parser.add_argument("--bot-fi-nginx-manifest", type=Path, required=True)
    parser.add_argument("--bot-fi-nginx-archive", type=Path, required=True)
    parser.add_argument(
        "--webapp-fi-nginx-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--webapp-fi-nginx-archive",
        type=Path,
        required=True,
    )
    parser.add_argument("--bot-fi-binding", type=Path, required=True)
    parser.add_argument("--webapp-fi-binding", type=Path, required=True)
    parser.add_argument(
        "--capture-state-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--capture-state-receipt-sha256",
        required=True,
    )
    parser.add_argument("--known-hosts", type=Path, default=FROZEN.KNOWN_HOSTS)
    parser.add_argument(
        "--ssh-identity",
        type=Path,
        default=FROZEN.DEFAULT_SSH_IDENTITY,
    )
    parser.add_argument("--resume-claim-path", type=Path)
    parser.add_argument("--resume-claim-sha256")
    parser.add_argument("--resume-claim-nonce")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = orchestrate(
            aggregate_path=args.aggregate,
            bot_fi_nginx_manifest=args.bot_fi_nginx_manifest,
            bot_fi_nginx_archive=args.bot_fi_nginx_archive,
            webapp_fi_nginx_manifest=args.webapp_fi_nginx_manifest,
            webapp_fi_nginx_archive=args.webapp_fi_nginx_archive,
            bot_fi_binding=args.bot_fi_binding,
            webapp_fi_binding=args.webapp_fi_binding,
            capture_state_receipt_path=args.capture_state_receipt,
            capture_state_receipt_sha256=(
                args.capture_state_receipt_sha256
            ),
            known_hosts=args.known_hosts,
            ssh_identity=args.ssh_identity,
            resume_claim_path=args.resume_claim_path,
            resume_claim_sha256=args.resume_claim_sha256,
            resume_claim_nonce=args.resume_claim_nonce,
            apply=args.apply,
            confirm=args.confirm,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except CurrentFrozenVerificationError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
