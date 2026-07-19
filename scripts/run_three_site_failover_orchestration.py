#!/usr/bin/env python3
"""Run/resume the two-person-approved failover saga through a no-shell adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from core.dr_command_orchestration_adapter import CommandOrchestrationAdapter, load_command_manifest
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    parse_plan,
    run_orchestration,
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
    public_keys = _strict_json(args.approver_public_keys, "approver public keys")
    if (
        not isinstance(public_keys, dict)
        or not public_keys
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in public_keys.items())
    ):
        raise DrOrchestrationError("approver public-key mapping is invalid")
    verify_two_person_approvals(plan, public_keys)
    commands = load_command_manifest(args.command_manifest, plan=plan)
    adapter = CommandOrchestrationAdapter(commands, timeout_seconds=args.command_timeout)
    return await run_orchestration(plan, adapter=adapter, journal_path=args.journal)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--approver-public-keys", required=True, type=Path)
    parser.add_argument("--command-manifest", required=True, type=Path)
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument("--command-timeout", type=int, default=120)
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
