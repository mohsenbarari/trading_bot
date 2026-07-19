#!/usr/bin/env python3
"""Validate a failover package; live execution needs a reviewed typed backend."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from core.dr_command_orchestration_adapter import load_typed_operation_manifest
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    parse_plan,
    load_approver_policy,
    verify_two_person_approvals,
)
from core.secure_file_io import read_secure_text


def _strict_json(path: Path, label: str):  # noqa: ANN202
    try:
        return json.loads(read_secure_text(path, label=label, max_size=128 * 1024))
    except Exception as exc:
        raise DrOrchestrationError(f"{label} is invalid") from exc


async def run(args: argparse.Namespace) -> dict:
    plan = parse_plan(_strict_json(args.plan, "orchestration plan"))
    verify_two_person_approvals(plan, load_approver_policy())
    load_typed_operation_manifest(args.command_manifest, plan=plan)
    raise DrOrchestrationError(
        "live failover execution is disabled until the deployment injects the reviewed "
        "typed backend and independent Witness operation ledger"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--command-manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"status": "error", "error_class": type(exc).__name__}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
