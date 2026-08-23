#!/usr/bin/env python3
"""Fail-closed preflight for Telegram bot split topology. Prints no secrets."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_bot_runtime_role import TelegramBotRuntimeRoleError
from core.telegram_bot_runtime_topology import (
    assert_telegram_bot_deploy_topology,
    describe_telegram_bot_runtime_topology,
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Telegram bot split topology.")
    parser.add_argument("--bot-role", default=os.environ.get("TELEGRAM_BOT_RUNTIME_ROLE", "all"))
    parser.add_argument(
        "--split-enabled",
        default=os.environ.get("TELEGRAM_BOT_SPLIT_ENABLED", "false"),
    )
    parser.add_argument(
        "--executor-enabled",
        default=os.environ.get("TELEGRAM_BOT_EXECUTOR_ENABLED", "false"),
    )
    parser.add_argument("--queue-owner-present", choices=("yes", "no", "unknown"), default="unknown")
    args = parser.parse_args(argv)
    split_enabled = _truthy(args.split_enabled)
    executor_enabled = _truthy(args.executor_enabled)
    owner_present = {
        "yes": True,
        "no": False,
        "unknown": None,
    }[args.queue_owner_present]
    try:
        deploy = assert_telegram_bot_deploy_topology(
            split_enabled=split_enabled,
            bot_role=args.bot_role,
            executor_enabled=executor_enabled,
        )
    except TelegramBotRuntimeRoleError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    report = describe_telegram_bot_runtime_topology(
        role=args.bot_role,
        split_enabled=split_enabled,
        queue_owner_present=owner_present if owner_present is not None else deploy.queue_owner_present,
    )
    payload = report.as_dict()
    payload["ok"] = report.can_start
    payload["executor_enabled"] = executor_enabled
    print(json.dumps(payload, sort_keys=True))
    if not report.can_start:
        return 3
    if not report.promotable:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
