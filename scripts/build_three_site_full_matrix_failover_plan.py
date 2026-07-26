#!/usr/bin/env python3
"""Prepare/finalize one JIT failover plan from the campaign-bound schedule."""

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

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.dr_command_orchestration_adapter import TYPED_OPERATIONS  # noqa: E402
from core.dr_failover_orchestrator import (  # noqa: E402
    ORCHESTRATION_SCHEMA,
    failover_readiness_commitment,
    failover_approval_subject,
    parse_plan,
    verify_human_failover_approval,
)
from core.dr_full_matrix_failover_schedule import (  # noqa: E402
    validate_schedule,
    scheduled_entry,
    verify_scheduled_plan,
)
from core.human_approval import load_human_approval_policy  # noqa: E402
from core.secure_file_io import (  # noqa: E402
    read_secure_text,
    write_secure_atomic_bytes,
)


class FullMatrixFailoverPlanBuildError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise FullMatrixFailoverPlanBuildError(
                "JIT failover input contains a duplicate field"
            )
        value[key] = item
    return value


def _json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=4 * 1024 * 1024),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise FullMatrixFailoverPlanBuildError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise FullMatrixFailoverPlanBuildError(f"{label} must be an object")
    return value


def _inventory_ips(inventory: dict[str, Any]) -> dict[str, str]:
    roles = inventory.get("roles")
    if not isinstance(roles, list):
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover inventory roles are invalid"
        )
    values = {
        str(row.get("role")): str(row.get("host_ip"))
        for row in roles
        if isinstance(row, dict)
        and row.get("role") in {"webapp_fi", "webapp_ir"}
    }
    if set(values) != {"webapp_fi", "webapp_ir"}:
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover inventory lacks both WebApp roles"
        )
    return values


def prepare_plan(
    *,
    schedule: dict[str, Any],
    inventory: dict[str, Any],
    classification: dict[str, Any],
    policy_payload: dict[str, Any],
    scenario_id: str,
    iteration: int,
    action: str,
    expected_epoch: int,
    generated_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if generated_at.tzinfo is None:
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover generation time requires a timezone"
        )
    summary = validate_schedule(
        schedule,
        campaign_id=str(schedule.get("campaign_id")),
        gate_group_id=str(schedule.get("gate_group_id")),
        execution_class=str(schedule.get("execution_class")),
        release_sha=str(schedule.get("release_sha")),
        repetitions=int(schedule.get("repetitions") or 0),
    )
    del summary
    entry = scheduled_entry(
        schedule,
        operation_id=next(
            (
                str(item.get("operation_id"))
                for item in schedule["entries"]
                if item.get("scenario_id") == scenario_id
                and item.get("iteration") == iteration
                and item.get("action") == action
            ),
            "",
        ),
        scenario_id=scenario_id,
        iteration=iteration,
        action=action,
    )
    if type(expected_epoch) is not int or expected_epoch < 1:
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover expected epoch is invalid"
        )
    policy = load_human_approval_policy(policy_payload)
    action_policy = policy.actions.get(action)
    if action_policy is None or "staging" not in action_policy.environments:
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover action is absent from approval policy"
        )
    ips = _inventory_ips(inventory)
    command_manifest = {
        "schema": "three-site-typed-operation-adapter-v1",
        "operation_id": entry["operation_id"],
        "operations": dict(TYPED_OPERATIONS),
    }
    generated = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    plan_payload = {
        "schema": ORCHESTRATION_SCHEMA,
        "operation_id": entry["operation_id"],
        "operation_nonce": entry["operation_nonce"],
        "generated_at": generated.isoformat(),
        "expires_at": (generated + timedelta(minutes=10)).isoformat(),
        "action": action,
        "source_site": entry["source_site"],
        "target_site": entry["target_site"],
        "expected_epoch": expected_epoch,
        "target_epoch": expected_epoch + 1,
        "release_sha": schedule["release_sha"],
        "domain": "gold-trading.ir",
        "record": "app",
        "expected_current_ip": ips[entry["source_site"]],
        "target_ip": ips[entry["target_site"]],
        "classification": classification,
        "rpo_policy": {
            "mode": "zero_loss",
            "max_unreplicated_events": 0,
            "approval_reason": None,
            "approval_ticket": None,
        },
        "command_manifest_hash": hashlib.sha256(
            canonical_json_bytes(command_manifest)
        ).hexdigest(),
        "approver_policy_hash": policy.policy_hash,
        "approvals": [],
    }
    plan_payload["readiness_commitment"] = failover_readiness_commitment(
        operation_id=entry["operation_id"],
        operation_nonce=entry["operation_nonce"],
        action=action,
        source_site=entry["source_site"],
        target_site=entry["target_site"],
        expected_epoch=expected_epoch,
        target_epoch=expected_epoch + 1,
        release_sha=schedule["release_sha"],
        domain="gold-trading.ir",
        record="app",
        command_manifest_hash=plan_payload["command_manifest_hash"],
    )
    parsed = parse_plan(plan_payload, require_approval=False)
    verify_scheduled_plan(
        schedule,
        plan=parsed,
        scenario_id=scenario_id,
        iteration=iteration,
    )
    return plan_payload, failover_approval_subject(parsed), command_manifest


def finalize_plan(
    *,
    draft: dict[str, Any],
    approval: dict[str, Any],
    schedule: dict[str, Any],
    policy_payload: dict[str, Any],
    scenario_id: str,
    iteration: int,
    witness_relay_public_key: str,
    now: datetime,
) -> dict[str, Any]:
    if draft.get("approvals") != []:
        raise FullMatrixFailoverPlanBuildError(
            "JIT failover draft already contains an approval"
        )
    payload = dict(draft)
    payload["approvals"] = [approval]
    plan = parse_plan(payload)
    verify_scheduled_plan(
        schedule,
        plan=plan,
        scenario_id=scenario_id,
        iteration=iteration,
    )
    verify_human_failover_approval(
        plan,
        policy_payload,
        now=now,
        require_fresh=True,
        witness_relay_public_key=witness_relay_public_key,
    )
    return payload


def _write(path: Path, value: dict[str, Any], *, label: str) -> None:
    write_secure_atomic_bytes(
        path,
        canonical_json_bytes(value) + b"\n",
        label=label,
        mode=0o600,
        max_size=4 * 1024 * 1024,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--schedule", required=True, type=Path)
    prepare.add_argument("--inventory", required=True, type=Path)
    prepare.add_argument("--classification", required=True, type=Path)
    prepare.add_argument("--policy", required=True, type=Path)
    prepare.add_argument("--scenario-id", required=True)
    prepare.add_argument("--iteration", required=True, type=int)
    prepare.add_argument("--action", required=True)
    prepare.add_argument("--expected-epoch", required=True, type=int)
    prepare.add_argument("--draft-output", required=True, type=Path)
    prepare.add_argument("--subject-output", required=True, type=Path)
    prepare.add_argument("--command-manifest-output", required=True, type=Path)

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--draft", required=True, type=Path)
    finalize.add_argument("--approval", required=True, type=Path)
    finalize.add_argument("--schedule", required=True, type=Path)
    finalize.add_argument("--policy", required=True, type=Path)
    finalize.add_argument("--scenario-id", required=True)
    finalize.add_argument("--iteration", required=True, type=int)
    finalize.add_argument("--witness-relay-public-key-file", required=True, type=Path)
    finalize.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            draft, subject, command_manifest = prepare_plan(
                schedule=_json(args.schedule, label="Full Matrix failover schedule"),
                inventory=_json(args.inventory, label="provisioned inventory"),
                classification=_json(
                    args.classification,
                    label="connectivity classification",
                ),
                policy_payload=_json(args.policy, label="approval policy"),
                scenario_id=args.scenario_id,
                iteration=args.iteration,
                action=args.action,
                expected_epoch=args.expected_epoch,
                generated_at=datetime.now(timezone.utc),
            )
            _write(
                args.draft_output,
                draft,
                label="JIT failover draft",
            )
            _write(
                args.subject_output,
                subject,
                label="JIT failover approval subject",
            )
            _write(
                args.command_manifest_output,
                command_manifest,
                label="JIT failover command manifest",
            )
            result = {
                "status": "awaiting_witness_relay_receipt",
                "operation_id": draft["operation_id"],
                "action": draft["action"],
                "expires_at": draft["expires_at"],
            }
        else:
            public_key = read_secure_text(
                args.witness_relay_public_key_file,
                label="Witness relay public key",
                max_size=16 * 1024,
            ).strip()
            payload = finalize_plan(
                draft=_json(args.draft, label="JIT failover draft"),
                approval=_json(args.approval, label="JIT failover relay receipt"),
                schedule=_json(args.schedule, label="Full Matrix failover schedule"),
                policy_payload=_json(args.policy, label="approval policy"),
                scenario_id=args.scenario_id,
                iteration=args.iteration,
                witness_relay_public_key=public_key,
                now=datetime.now(timezone.utc),
            )
            _write(args.output, payload, label="approved JIT failover plan")
            result = {
                "status": "approved",
                "operation_id": payload["operation_id"],
                "action": payload["action"],
                "expires_at": payload["expires_at"],
            }
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
