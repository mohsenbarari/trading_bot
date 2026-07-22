#!/usr/bin/env python3
"""Run the closed, dual-approved failover saga for the isolated staging domain."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.dr_command_orchestration_adapter import (
    TypedOrchestrationAdapter,
    load_typed_operation_manifest,
)
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    load_approver_policy,
    parse_plan,
    run_orchestration,
    validate_plan_freshness,
    verify_two_person_approvals,
)
from core.dr_operation_ledger import WitnessOperationLedger
from core.dr_staging_operation_backend import (
    StagingTypedOperationBackend,
    load_staging_backend_config,
)
from core.secure_file_io import read_secure_text


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrOrchestrationError("orchestration input contains a duplicate key")
        result[key] = value
    return result


def _strict_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=1024 * 1024),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise DrOrchestrationError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise DrOrchestrationError(f"{label} must be an object")
    return value


def confirmation_phrase(operation_id: str, plan_hash: str) -> str:
    return f"orchestrate-staging-failover:{operation_id}:{plan_hash}"


def prepare(args: argparse.Namespace):  # noqa: ANN202
    plan = parse_plan(_strict_json(args.plan, "orchestration plan"))
    policy = load_approver_policy(args.approver_policy)
    verify_two_person_approvals(plan, policy)
    operations = load_typed_operation_manifest(args.command_manifest, plan=plan)
    config = load_staging_backend_config(
        args.backend_config,
        inventory=_strict_json(args.inventory, "provisioned staging inventory"),
        inventory_approval=_strict_json(
            args.inventory_approval, "staging inventory approval"
        ),
        inventory_signer_policy=_strict_json(
            args.inventory_signer_policy, "staging inventory signer policy"
        ),
    )
    backend = StagingTypedOperationBackend(config)
    backend.preflight(plan)
    return plan, operations, backend


async def run(args: argparse.Namespace) -> dict[str, Any]:
    plan, operations, backend = prepare(args)
    required = confirmation_phrase(plan.operation_id, plan.plan_hash)
    if not args.apply:
        validate_plan_freshness(plan)
        return {
            "status": "planned",
            "operation_id": plan.operation_id,
            "plan_hash": plan.plan_hash,
            "action": plan.action,
            "source_site": plan.source_site,
            "target_site": plan.target_site,
            "domain": plan.domain,
            "record": plan.record,
            "required_confirmation": required,
        }
    if args.confirm != required:
        raise DrOrchestrationError("staging failover confirmation mismatch")
    adapter = TypedOrchestrationAdapter(operations, backend=backend)
    ledger = WitnessOperationLedger(
        backend.config.witness_config,
        witness_public_key=backend.config.witness_public_key,
    )
    return await run_orchestration(
        plan,
        adapter=adapter,
        ledger=ledger,
        journal_path=args.journal,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--command-manifest", required=True, type=Path)
    parser.add_argument("--approver-policy", required=True, type=Path)
    parser.add_argument("--backend-config", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--inventory-approval", required=True, type=Path)
    parser.add_argument("--inventory-signer-policy", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
