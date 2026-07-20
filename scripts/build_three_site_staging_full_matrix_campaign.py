#!/usr/bin/env python3
"""Prepare and finalize a dual-approved immutable staging Full Matrix campaign."""

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

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import sha256_secure_file, write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import (
    BOUND_ARTIFACTS,
    CAMPAIGN_SCHEMA,
    PHASES,
    PHASE_SCENARIOS,
    SHA256,
    SHA40,
    FullMatrixCampaignError,
    _policy,
    secure_json,
    verify_campaign,
)


APPROVAL_REQUEST_SCHEMA = "three-site-staging-full-matrix-approval-request-v1"
APPROVAL_SCHEMA = "three-site-staging-full-matrix-approval-v1"


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


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    mappings = _mapping(args.bound_artifact)
    if (
        SHA40.fullmatch(str(args.baseline_sha)) is None
        or SHA40.fullmatch(str(args.activation_sha)) is None
        or args.baseline_sha == args.activation_sha
        or not str(args.object_bucket).startswith("staging-")
        or type(args.repetitions) is not int
        or args.repetitions != 2
        or type(args.valid_hours) is not int
        or not 1 <= args.valid_hours <= 72
    ):
        raise FullMatrixCampaignError("Full Matrix preparation scope is invalid")
    inventory = secure_json(mappings["provisioned_inventory"], label="provisioned inventory")
    campaign_id = _campaign_id(inventory.get("campaign_id"))
    if (
        inventory.get("schema") != "three-site-staging-inventory-v1"
        or inventory.get("inventory_stage") != "provisioned"
        or inventory.get("release_sha") != args.activation_sha
    ):
        raise FullMatrixCampaignError(
            "provisioned inventory must be re-attested at the activation SHA"
        )
    _queue_transition(
        mappings["queue_activation_transition"],
        baseline_sha=args.baseline_sha,
        activation_sha=args.activation_sha,
    )
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
            "prefix": f"full-matrix/{campaign_id}/",
            "versioned": True,
            "private": True,
        },
        "repetitions": args.repetitions,
        "required_phases": list(PHASES),
        "required_scenarios": {
            phase: list(scenarios) for phase, scenarios in PHASE_SCENARIOS.items()
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
    request = {
        "schema": APPROVAL_REQUEST_SCHEMA,
        "campaign_id": campaign_id,
        "campaign_hash": campaign_hash,
        "release_sha": args.activation_sha,
        "approver_policy_hash": policy_hash,
        "signature_payload_encoding": "lowercase-hex-ascii",
        "signature_payload": campaign_hash,
    }
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
        "status": "awaiting_two_approvals",
        "campaign_id": campaign_id,
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
    approvals: list[dict[str, str]] = []
    for path in args.approval:
        value = secure_json(path, label="Full Matrix approval")
        if (
            set(value) != {"schema", "campaign_id", "campaign_hash", "operator", "key_id", "signature"}
            or value.get("schema") != APPROVAL_SCHEMA
            or value.get("campaign_id") != campaign.get("campaign_id")
            or value.get("campaign_hash") != campaign_hash
        ):
            raise FullMatrixCampaignError("Full Matrix approval is for another campaign")
        approvals.append(
            {
                "operator": value["operator"],
                "key_id": value["key_id"],
                "signature": value["signature"],
            }
        )
    if len(approvals) != 2:
        raise FullMatrixCampaignError("exactly two Full Matrix approval files are required")
    campaign["approvals"] = approvals
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
    finalize_parser.add_argument("--approval", type=Path, action="append", default=[])
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
