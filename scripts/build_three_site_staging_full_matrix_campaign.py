#!/usr/bin/env python3
"""Prepare and finalize an action-approved immutable staging Full Matrix campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes
from core.human_approval import approval_subject
from core.secure_file_io import sha256_secure_file, write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    CAMPAIGN_SCHEMA,
    SHA256,
    SHA40,
    FullMatrixCampaignError,
    _policy,
    secure_json,
    verify_campaign,
    scenarios_for_execution_class,
)
from core.three_site_execution_safety import (
    EXECUTION_CLASSES,
    execution_class_is_host_destructive,
)
from scripts.three_site_staging_migration_journal import (
    ROLE_PHASES,
    _validate as validate_migration_journal,
)
from scripts.verify_three_site_queue_activation_transition import verify_transition
from scripts.verify_three_site_staging_inventory import (
    verify_approved_inventory,
)
from scripts.verify_three_site_staging_migration_plan import verify_migration_plan


def _mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or name not in BOUND_ARTIFACTS or name in result or not raw_path:
            raise FullMatrixCampaignError("--bound-artifact mapping is invalid")
        result[name] = Path(raw_path)
    if set(result) != BOUND_ARTIFACTS:
        raise FullMatrixCampaignError("--bound-artifact mapping is incomplete")
    return result


def _campaign_id(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except ValueError as exc:
        raise FullMatrixCampaignError("Full Matrix campaign_id is invalid") from exc


def _queue_transition(path: Path, *, baseline_sha: str, activation_sha: str) -> None:
    value = secure_json(path, label="Queue activation transition")
    fields = {
        "schema", "status", "baseline_sha", "activation_sha", "changed_path",
        "transition_diff_sha256", "verified_at",
    }
    if (
        set(value) != fields
        or value.get("schema") != "three-site-staging-queue-activation-transition-v1"
        or value.get("status") != "verified"
        or value.get("baseline_sha") != baseline_sha
        or value.get("activation_sha") != activation_sha
        or value.get("changed_path") != "core/telegram_delivery_runtime_policy.py"
        or SHA256.fullmatch(str(value.get("transition_diff_sha256") or "")) is None
    ):
        raise FullMatrixCampaignError(
            "Queue activation transition does not bind the requested lineage"
        )
    recomputed = verify_transition(
        REPO_ROOT,
        baseline_sha=baseline_sha,
        activation_sha=activation_sha,
        require_checkout=True,
    )
    stable_fields = fields - {"verified_at"}
    if any(value.get(field) != recomputed.get(field) for field in stable_fields):
        raise FullMatrixCampaignError(
            "Queue activation transition differs from direct Git verification"
        )


def _global_commit(
    value: dict[str, Any],
    *,
    campaign_id: str,
    release_sha: str,
    plan_sha256: str,
) -> None:
    fields = {
        "schema", "status", "campaign_id", "release_sha", "plan_sha256",
        "issued_at", "campaign_journals_sha256", "role_journals",
        "committed_role_states", "all_roles_committed",
    }
    states = value.get("committed_role_states") if isinstance(value, dict) else None
    hashes = value.get("role_journals") if isinstance(value, dict) else None
    if (
        set(value) != fields
        or value.get("schema") != "three-site-staging-global-commit-v2"
        or value.get("status") != "passed"
        or value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
        or value.get("plan_sha256") != plan_sha256
        or value.get("all_roles_committed") is not True
        or not isinstance(states, dict) or set(states) != set(ROLE_PHASES)
        or not isinstance(hashes, dict) or set(hashes) != set(ROLE_PHASES)
    ):
        raise FullMatrixCampaignError("global migration commit is not authoritative")
    try:
        issued = datetime.fromisoformat(str(value["issued_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullMatrixCampaignError("global migration commit timestamp is invalid") from exc
    current = datetime.now(timezone.utc)
    if (
        issued.tzinfo is None
        or current - issued.astimezone(timezone.utc) > timedelta(hours=24)
        or issued.astimezone(timezone.utc) > current + timedelta(minutes=2)
    ):
        raise FullMatrixCampaignError("global migration commit is stale/future-dated")
    for role, state in states.items():
        try:
            validate_migration_journal(state)
        except Exception as exc:
            raise FullMatrixCampaignError("global commit embeds an invalid role journal") from exc
        if (
            state.get("role") != role
            or state.get("campaign_id") != campaign_id
            or state.get("release_sha") != release_sha
            or state.get("plan_sha256") != plan_sha256
            or state.get("status") != "committed"
            or state.get("completed_phases") != list(ROLE_PHASES[role])
            or hashes.get(role) != state.get("state_sha256")
        ):
            raise FullMatrixCampaignError("global commit role state/hash is invalid")
    expected_campaign_hash = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if value.get("campaign_journals_sha256") != expected_campaign_hash:
        raise FullMatrixCampaignError("global commit campaign journal hash is invalid")


def _campaign_bundle_summary(
    value: dict[str, Any], *, campaign_id: str, release_sha: str, inventory_sha256: str
) -> None:
    fields = {
        "status", "campaign_id", "release_sha", "inventory_sha256",
        "campaign_bundle_sha256", "roles", "directional_pairwise_key_count",
        "database_credential_count", "file_attestation",
    }
    if (
        set(value) != fields
        or value.get("status") != "verified"
        or value.get("campaign_id") != campaign_id
        or value.get("release_sha") != release_sha
        or value.get("inventory_sha256") != inventory_sha256
        or SHA256.fullmatch(str(value.get("campaign_bundle_sha256", ""))) is None
        or value.get("roles") != ["bot-fi", "webapp-fi", "webapp-ir", "witness"]
        or value.get("directional_pairwise_key_count") != 4
        or type(value.get("database_credential_count")) is not int
        or value["database_credential_count"] < 10
        or value.get("file_attestation") is not True
    ):
        raise FullMatrixCampaignError("role campaign bundle is not fully attested")


def _verify_prerequisites(
    mappings: dict[str, Path], *, baseline_sha: str, activation_sha: str,
    execution_class: str,
) -> tuple[str, str, str]:
    inventory = secure_json(mappings["provisioned_inventory"], label="provisioned inventory")
    if inventory.get("release_sha") != activation_sha:
        raise FullMatrixCampaignError(
            "provisioned inventory must be re-attested at the activation SHA"
        )
    inventory_approval = secure_json(
        mappings["inventory_approval"], label="inventory approval"
    )
    human_approval_policy = secure_json(
        mappings["human_approval_policy"], label="human approval policy"
    )
    inventory_result = verify_approved_inventory(
        inventory,
        approval=inventory_approval,
        approval_policy=human_approval_policy,
        host_destructive=execution_class_is_host_destructive(execution_class),
    )
    if inventory_result["inventory_stage"] != "provisioned":
        raise FullMatrixCampaignError("Full Matrix requires an approved provisioned inventory")
    campaign_id = _campaign_id(inventory_result["campaign_id"])

    plan = secure_json(mappings["migration_plan"], label="migration plan")
    migration_result = verify_migration_plan(
        plan,
        approval=secure_json(mappings["migration_approval"], label="migration approval"),
        inventory=inventory,
        inventory_approval=inventory_approval,
        approval_policy=human_approval_policy,
        freeze_evidence=[
            secure_json(mappings["source_freeze_bot_fi"], label="Bot-FI source freeze"),
            secure_json(mappings["source_freeze_webapp_fi"], label="WebApp-FI source freeze"),
        ],
        image_inventories={
            role: secure_json(mappings[f"image_inventory_{role}"], label=f"{role} image inventory")
            for role in ("bot_fi", "webapp_fi", "webapp_ir", "witness")
        },
        backup_manifests={
            role: secure_json(mappings[f"source_backup_{role}"], label=f"{role} source backup")
            for role in ("bot_fi", "webapp_fi")
        },
        seed_manifests={
            role: secure_json(mappings[f"seed_manifest_{role}"], label=f"{role} seed manifest")
            for role in ("bot_fi", "webapp_fi")
        },
        require_fresh_approval=False,
    )
    if (
        migration_result.get("campaign_id") != campaign_id
        or migration_result.get("release_sha") != activation_sha
    ):
        raise FullMatrixCampaignError("approved migration differs from activation campaign")

    _global_commit(
        secure_json(mappings["global_commit"], label="global migration commit"),
        campaign_id=campaign_id,
        release_sha=activation_sha,
        plan_sha256=migration_result["plan_sha256"],
    )
    _campaign_bundle_summary(
        secure_json(mappings["campaign_bundle"], label="role campaign bundle"),
        campaign_id=campaign_id,
        release_sha=activation_sha,
        inventory_sha256=inventory_result["inventory_sha256"],
    )
    _queue_transition(
        mappings["queue_activation_transition"],
        baseline_sha=baseline_sha,
        activation_sha=activation_sha,
    )
    object_storage = inventory.get("object_storage")
    if not isinstance(object_storage, dict) or not isinstance(object_storage.get("bucket"), str):
        raise FullMatrixCampaignError("provisioned inventory lacks its Object Storage bucket")
    return (
        campaign_id,
        str(inventory_result["inventory_sha256"]),
        str(object_storage["bucket"]),
    )


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    mappings = _mapping(args.bound_artifact)
    try:
        gate_group_id = str(UUID(str(args.gate_group_id)))
    except ValueError as exc:
        raise FullMatrixCampaignError("Full Matrix gate_group_id is invalid") from exc
    if (
        SHA40.fullmatch(str(args.baseline_sha)) is None
        or SHA40.fullmatch(str(args.activation_sha)) is None
        or args.baseline_sha == args.activation_sha
        or args.execution_class not in EXECUTION_CLASSES
        or type(args.repetitions) is not int
        or args.repetitions != 2
        or type(args.valid_hours) is not int
        or not 1 <= args.valid_hours <= 72
    ):
        raise FullMatrixCampaignError("Full Matrix preparation scope is invalid")
    campaign_id, _inventory_hash, inventory_bucket = _verify_prerequisites(
        mappings, baseline_sha=args.baseline_sha, activation_sha=args.activation_sha,
        execution_class=args.execution_class,
    )
    if args.object_bucket != inventory_bucket:
        raise FullMatrixCampaignError(
            "Full Matrix Object Storage bucket differs from the approved inventory"
        )
    required_scenarios = scenarios_for_execution_class(args.execution_class)
    policy = secure_json(args.approver_policy, label="Full Matrix approver policy")
    _signers, policy_hash = _policy(policy, release_sha=args.activation_sha)
    hashes: dict[str, str] = {}
    for name, path in mappings.items():
        digest, size = sha256_secure_file(path, label=f"Full Matrix {name}")
        if size == 0:
            raise FullMatrixCampaignError(f"Full Matrix {name} artifact is empty")
        hashes[name] = digest
    generated = datetime.now(timezone.utc)
    campaign = {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "gate_group_id": gate_group_id,
        "execution_class": args.execution_class,
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(hours=args.valid_hours)).isoformat(),
        "baseline_sha": args.baseline_sha,
        "activation_sha": args.activation_sha,
        "release_sha": args.activation_sha,
        "official_staging_url": "https://staging.gold-trade.ir",
        "failover_test_url": "https://app.gold-trading.ir",
        "object_storage": {
            "region": "ir-thr-at1",
            "bucket": args.object_bucket,
            "prefix": (
                f"full-matrix/{gate_group_id}/{args.execution_class}/{campaign_id}/"
            ),
            "versioned": True,
            "private": True,
        },
        "repetitions": args.repetitions,
        "required_phases": list(required_scenarios),
        "required_scenarios": {
            phase: list(scenarios) for phase, scenarios in required_scenarios.items()
        },
        "no_skips": True,
        "cleanup_required": True,
        "production_forbidden": True,
        "bound_artifacts": hashes,
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    request = approval_subject(
        artifact_type=CAMPAIGN_SCHEMA,
        artifact_sha256=campaign_hash,
        release_sha=args.activation_sha,
        bindings={
            "campaign_id": campaign_id,
            "gate_group_id": gate_group_id,
            "execution_class": args.execution_class,
        },
    )
    write_secure_atomic_bytes(
        args.draft_output,
        canonical_json_bytes(campaign) + b"\n",
        label="Full Matrix draft campaign",
        mode=0o600,
    )
    write_secure_atomic_bytes(
        args.approval_request_output,
        canonical_json_bytes(request) + b"\n",
        label="Full Matrix approval request",
        mode=0o600,
    )
    return {
        "status": "awaiting_password_totp_approval",
        "campaign_id": campaign_id,
        "gate_group_id": gate_group_id,
        "execution_class": args.execution_class,
        "campaign_hash": campaign_hash,
        "release_sha": args.activation_sha,
        "draft_output": str(args.draft_output),
        "approval_request_output": str(args.approval_request_output),
    }


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    campaign = secure_json(args.draft, label="Full Matrix draft campaign")
    if campaign.get("approvals") != []:
        raise FullMatrixCampaignError("Full Matrix draft must not contain approvals")
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    approval = secure_json(args.approval, label="Full Matrix human approval")
    campaign["approvals"] = [approval]
    policy = secure_json(args.approver_policy, label="Full Matrix approver policy")
    verified = verify_campaign(campaign, approver_policy=policy)
    write_secure_atomic_bytes(
        args.output,
        canonical_json_bytes(campaign) + b"\n",
        label="approved Full Matrix campaign",
        mode=0o600,
    )
    return {**verified, "output": str(args.output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--baseline-sha", required=True)
    prepare_parser.add_argument("--activation-sha", required=True)
    prepare_parser.add_argument("--gate-group-id", required=True)
    prepare_parser.add_argument(
        "--execution-class", required=True, choices=sorted(EXECUTION_CLASSES)
    )
    prepare_parser.add_argument("--approver-policy", type=Path, required=True)
    prepare_parser.add_argument("--bound-artifact", action="append", default=[])
    prepare_parser.add_argument("--object-bucket", required=True)
    prepare_parser.add_argument("--repetitions", type=int, choices=(2,), default=2)
    prepare_parser.add_argument("--valid-hours", type=int, choices=range(1, 73), default=72)
    prepare_parser.add_argument("--draft-output", type=Path, required=True)
    prepare_parser.add_argument("--approval-request-output", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--draft", type=Path, required=True)
    finalize_parser.add_argument("--approver-policy", type=Path, required=True)
    finalize_parser.add_argument("--approval", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args) if args.action == "prepare" else finalize(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
