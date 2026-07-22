#!/usr/bin/env python3
"""Prepare/finalize the action-approved aggregate of both Gate D campaigns."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes
from core.human_approval import approval_subject
from core.secure_file_io import write_secure_atomic_bytes
from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    SHARED_HOST_SAFE,
)
from core.three_site_full_matrix_campaign import _policy, secure_json
from core.three_site_full_matrix_gate import (
    AGGREGATE_SCHEMA,
    GateDAggregateError,
    verify_component_report,
    verify_gate_d_aggregate,
)


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    verified = verify_component_report(report)
    return {
        "campaign_id": verified["campaign_id"],
        "gate_group_id": verified["gate_group_id"],
        "execution_class": verified["execution_class"],
        "release_sha": verified["release_sha"],
        "campaign_hash": verified["campaign_hash"],
        "report_hash": verified["report_hash"],
        "scenario_catalog_sha256": verified["scenario_catalog_sha256"],
        "scenario_ids": verified["scenario_ids"],
        "scenario_execution_count": verified["scenario_execution_count"],
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    shared_report = secure_json(args.shared_report, label="shared-host-safe report")
    destructive_report = secure_json(
        args.destructive_report, label="dedicated-host-destructive report"
    )
    shared = _summary(shared_report)
    destructive = _summary(destructive_report)
    if (
        shared["execution_class"] != SHARED_HOST_SAFE
        or destructive["execution_class"] != DEDICATED_HOST_DESTRUCTIVE
        or shared["gate_group_id"] != destructive["gate_group_id"]
        or shared["release_sha"] != destructive["release_sha"]
        or shared["campaign_id"] == destructive["campaign_id"]
    ):
        raise GateDAggregateError(
            "Gate D component reports are not two independent campaigns in one group/SHA"
        )
    policy = secure_json(args.approver_policy, label="Gate D approver policy")
    try:
        _signers, policy_hash = _policy(policy, release_sha=shared["release_sha"])
    except Exception as exc:
        raise GateDAggregateError("Gate D approver policy is invalid") from exc
    generated = datetime.now(timezone.utc)
    aggregate = {
        "schema": AGGREGATE_SCHEMA,
        "gate_group_id": shared["gate_group_id"],
        "release_sha": shared["release_sha"],
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(hours=args.valid_hours)).isoformat(),
        "repetitions": 2,
        "component_reports": {
            SHARED_HOST_SAFE: shared_report,
            DEDICATED_HOST_DESTRUCTIVE: destructive_report,
        },
        "combined_scenario_count": len(shared["scenario_ids"])
        + len(destructive["scenario_ids"]),
        "combined_scenario_execution_count": shared["scenario_execution_count"]
        + destructive["scenario_execution_count"],
        "skip_count": 0,
        "cleanup_residue_count": 0,
        "production_touched": False,
        "approver_policy_hash": policy_hash,
        "approvals": [],
    }
    unsigned = {key: value for key, value in aggregate.items() if key != "approvals"}
    aggregate_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    request = approval_subject(
        artifact_type=AGGREGATE_SCHEMA,
        artifact_sha256=aggregate_hash,
        release_sha=aggregate["release_sha"],
        bindings={
            "gate_group_id": aggregate["gate_group_id"],
            "component_report_hashes": {
                name: aggregate["component_reports"][name]["report_hash"]
                for name in sorted(aggregate["component_reports"])
            },
        },
    )
    write_secure_atomic_bytes(
        args.draft_output,
        canonical_json_bytes(aggregate) + b"\n",
        label="Gate D draft aggregate",
        mode=0o600,
    )
    write_secure_atomic_bytes(
        args.approval_request_output,
        canonical_json_bytes(request) + b"\n",
        label="Gate D aggregate approval request",
        mode=0o600,
    )
    return {
        "status": "awaiting_password_totp_approval",
        "gate_group_id": aggregate["gate_group_id"],
        "release_sha": aggregate["release_sha"],
        "aggregate_hash": aggregate_hash,
        "combined_scenario_count": aggregate["combined_scenario_count"],
        "combined_scenario_execution_count": aggregate[
            "combined_scenario_execution_count"
        ],
        "draft_output": str(args.draft_output),
        "approval_request_output": str(args.approval_request_output),
    }


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    aggregate = secure_json(args.draft, label="Gate D draft aggregate")
    if aggregate.get("approvals") != []:
        raise GateDAggregateError("Gate D draft must not already contain approvals")
    aggregate["approvals"] = [
        secure_json(args.approval, label="Gate D human approval")
    ]
    policy = secure_json(args.approver_policy, label="Gate D approver policy")
    verified = verify_gate_d_aggregate(aggregate, approver_policy=policy)
    write_secure_atomic_bytes(
        args.output,
        canonical_json_bytes(aggregate) + b"\n",
        label="approved Gate D aggregate",
        mode=0o600,
    )
    return {**verified, "output": str(args.output)}


def verify(args: argparse.Namespace) -> dict[str, Any]:
    return verify_gate_d_aggregate(
        secure_json(args.aggregate, label="approved Gate D aggregate"),
        approver_policy=secure_json(args.approver_policy, label="Gate D approver policy"),
        require_fresh_approval=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--shared-report", type=Path, required=True)
    prepare_parser.add_argument("--destructive-report", type=Path, required=True)
    prepare_parser.add_argument("--approver-policy", type=Path, required=True)
    prepare_parser.add_argument("--valid-hours", type=int, choices=range(1, 25), default=24)
    prepare_parser.add_argument("--draft-output", type=Path, required=True)
    prepare_parser.add_argument("--approval-request-output", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--draft", type=Path, required=True)
    finalize_parser.add_argument("--approver-policy", type=Path, required=True)
    finalize_parser.add_argument("--approval", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--aggregate", type=Path, required=True)
    verify_parser.add_argument("--approver-policy", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            prepare(args)
            if args.action == "prepare"
            else finalize(args)
            if args.action == "finalize"
            else verify(args)
        )
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
