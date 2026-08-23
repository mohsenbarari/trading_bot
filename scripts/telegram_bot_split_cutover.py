#!/usr/bin/env python3
"""Official staging split forward/rollback CLI. Prints no secrets."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.telegram_bot_split_compose_operator import (
    DEFAULT_BOT_PROFILE,
    DEFAULT_EXECUTOR_PROFILE,
    ComposeSplitOperator,
    subprocess_runner,
)
from core.telegram_bot_split_cutover import (
    SPLIT_ROLLBACK_CONFIRM,
    SPLIT_START_CONFIRM,
    InMemorySplitOperator,
    SplitCutoverController,
    SplitCutoverError,
    require_confirmation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Telegram split cutover.")
    parser.add_argument("action", choices=("forward", "rollback"))
    parser.add_argument(
        "--confirm",
        default=os.environ.get("STAGING_TELEGRAM_SPLIT_CONFIRM", ""),
    )
    parser.add_argument(
        "--operator",
        default="compose",
        choices=("compose", "memory"),
        help="compose mutates staging containers. memory is the dry-run/test path.",
    )
    parser.add_argument("--project-name", default=os.environ.get("STAGING_PROJECT_NAME", ""))
    parser.add_argument(
        "--compose-file",
        default=os.environ.get(
            "STAGING_COMPOSE_FILE",
            str(REPO_ROOT / "deploy/staging/docker-compose.staging.yml"),
        ),
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("STAGING_ENV_FILE", str(REPO_ROOT / ".env.staging")),
    )
    parser.add_argument(
        "--expected-sha",
        default=os.environ.get("STAGING_RELEASE_SHA", ""),
    )
    parser.add_argument("--bot-profile", default=DEFAULT_BOT_PROFILE)
    parser.add_argument("--executor-profile", default=DEFAULT_EXECUTOR_PROFILE)
    args = parser.parse_args(argv)
    expected = SPLIT_START_CONFIRM if args.action == "forward" else SPLIT_ROLLBACK_CONFIRM
    try:
        require_confirmation(args.confirm, expected)
        if args.operator == "memory":
            operator = InMemorySplitOperator.successful()
        else:
            operator = ComposeSplitOperator(
                subprocess_runner,
                project_name=args.project_name,
                compose_file=args.compose_file,
                env_file=args.env_file,
                expected_sha=args.expected_sha,
                bot_profile=args.bot_profile,
                executor_profile=args.executor_profile,
                sleep=time.sleep,
            )
    except SplitCutoverError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    controller = SplitCutoverController(operator)
    report = controller.forward() if args.action == "forward" else controller.rollback()
    print(json.dumps(report.as_dict(), sort_keys=True))
    if args.action == "forward":
        return 0 if report.ok else 3
    return 0 if report.ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
