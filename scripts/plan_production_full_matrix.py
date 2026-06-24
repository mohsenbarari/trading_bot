#!/usr/bin/env python3
"""Build a side-effect-free production full-matrix command plan."""

from __future__ import annotations

import argparse
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_PREFIXES = ("PFM_", "PRODTEST_", "FMX_")
DEFAULT_ARTIFACT_ROOT = Path("/tmp/trading-bot-production-full-matrix")
IRAN_HOST = "root@87.107.3.22"
IRAN_PROJECT_DIR = "/srv/trading-bot/current"
PRODUCTION_CLEANUP_CONFIRM = "hard-delete-test-data"


class PlanError(ValueError):
    pass


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_prefix() -> str:
    return f"PFM_{utc_stamp()}_"


def validate_prefix(prefix: str) -> str:
    normalized = str(prefix or "").strip()
    if len(normalized) < 8:
        raise PlanError("prefix must be at least 8 characters")
    if any(char in normalized for char in "*?[]%"):
        raise PlanError("prefix must not contain wildcard characters")
    if not normalized.startswith(ALLOWED_PREFIXES):
        allowed = ", ".join(ALLOWED_PREFIXES)
        raise PlanError(f"prefix must start with one of: {allowed}")
    return normalized


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def iran(command: str) -> str:
    return shell_join(["ssh", IRAN_HOST, f"cd {IRAN_PROJECT_DIR} && {command}"])


def foreign_compose(*parts: str) -> str:
    return shell_join(["docker", "compose", *parts])


def iran_compose(*parts: str) -> str:
    return iran(shell_join(["docker-compose", *parts]))


def cleanup_command(*, role: str, prefix: str, artifact: str, dry_run: bool) -> str:
    args = [
        "exec",
        "-T",
    ]
    if not dry_run:
        args.extend(["-e", f"PRODUCTION_TEST_CLEANUP_CONFIRM={PRODUCTION_CLEANUP_CONFIRM}"])
    args.extend(
        [
            "app",
            "python",
            "scripts/trading_core_probe_worker.py",
            "cleanup",
            "--prefix",
            prefix,
        ]
    )
    if dry_run:
        args.append("--dry-run")
    else:
        args.append("--allow-production-hard-delete")
    args.extend(["--artifact", artifact])
    if role == "foreign":
        return foreign_compose(*args)
    if role == "iran":
        return iran_compose(*args)
    raise PlanError(f"unsupported cleanup role: {role}")


def isolation_command(*parts: str) -> str:
    return iran_compose("exec", "-T", "app", "python", "scripts/production_test_isolation.py", *parts)


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    prefix = validate_prefix(args.prefix)
    artifact_dir = Path(args.artifact_dir or (DEFAULT_ARTIFACT_ROOT / prefix))
    reason = args.reason or f"production_full_matrix_{utc_stamp()}"
    pre_artifact = lambda name: f"/tmp/{prefix}{name}.json"

    return {
        "schema_version": "production_full_matrix_plan_v1",
        "status": "planned",
        "mutates_production": False,
        "prefix": prefix,
        "artifact_dir": str(artifact_dir),
        "operator_account": args.operator_account,
        "profile": {
            "users": args.users,
            "attempts_per_scenario": args.attempts_per_scenario,
            "target_rps": args.target_rps,
            "telegram_ratio": args.telegram_ratio,
            "write_max_concurrency": args.write_max_concurrency,
        },
        "safety": {
            "allowed_prefixes": list(ALLOWED_PREFIXES),
            "cleanup_requires_confirmation_env": "PRODUCTION_TEST_CLEANUP_CONFIRM",
            "cleanup_confirmation_value": PRODUCTION_CLEANUP_CONFIRM,
            "keep_isolation_enabled_until_cleanup_verified": True,
            "do_not_print_bot_token": True,
        },
        "stages": [
            {
                "name": "local_preflight",
                "commands": [
                    "git branch --show-current",
                    "git status --short --branch",
                    "git rev-parse HEAD",
                    shell_join(["mkdir", "-p", str(artifact_dir)]),
                ],
            },
            {
                "name": "production_status",
                "commands": [
                    foreign_compose("ps"),
                    iran_compose("ps"),
                    "curl -fsS https://coin.gold-trade.ir/api/config",
                    isolation_command("status"),
                ],
            },
            {
                "name": "enable_isolation",
                "commands": [
                    isolation_command(
                        "enable",
                        "--allow-account-name",
                        args.operator_account,
                        "--allow-account-prefix",
                        "PFM_",
                        "--allow-account-prefix",
                        "PRODTEST_",
                        "--allow-account-prefix",
                        "FMX_",
                        "--reason",
                        reason,
                        "--yes-production",
                    ),
                    isolation_command("status"),
                ],
            },
            {
                "name": "pre_run_cleanup_dry_run",
                "commands": [
                    cleanup_command(
                        role="foreign",
                        prefix=prefix,
                        artifact=pre_artifact("foreign-core-cleanup-pre-dry-run"),
                        dry_run=True,
                    ),
                    cleanup_command(
                        role="iran",
                        prefix=prefix,
                        artifact=pre_artifact("iran-core-cleanup-pre-dry-run"),
                        dry_run=True,
                    ),
                ],
            },
            {
                "name": "catalog_dry_run",
                "commands": [
                    f"{shell_join(['python3', 'scripts/run_bot_webapp_comprehensive_load_matrix.py', '--prefix', prefix, '--list'])} > {shlex.quote(str(artifact_dir / 'comprehensive-catalog.json'))}",
                    shell_join(
                        [
                            "python3",
                            "scripts/report_trade_notification_delivery_matrix.py",
                            "--check",
                            "--output",
                            str(artifact_dir / "trade-notification-delivery-matrix.json"),
                        ]
                    ),
                    shell_join(
                        [
                            "python3",
                            "scripts/run_trade_delivery_targeted_join_matrix.py",
                            "--dry-run",
                            "--check",
                            "--allow-any-branch",
                            "--output",
                            str(artifact_dir / "trade-delivery-targeted-join-matrix.json"),
                        ]
                    ),
                ],
            },
            {
                "name": "production_execution_gap",
                "status": "requires_operator_or_future_executor",
                "notes": [
                    "The current one-shot 228-scenario runner is staging-bound.",
                    "Production execution must use the isolated prefix, real two-server placement, and patched/fake Telegram transport for synthetic users.",
                    "A small manual Telegram E2E pass with the real production bot/channel is still required after automated evidence.",
                ],
            },
            {
                "name": "post_run_cleanup_dry_run",
                "commands": [
                    cleanup_command(
                        role="foreign",
                        prefix=prefix,
                        artifact=pre_artifact("foreign-core-cleanup-post-dry-run"),
                        dry_run=True,
                    ),
                    cleanup_command(
                        role="iran",
                        prefix=prefix,
                        artifact=pre_artifact("iran-core-cleanup-post-dry-run"),
                        dry_run=True,
                    ),
                ],
            },
            {
                "name": "hard_delete_cleanup",
                "commands": [
                    cleanup_command(
                        role="foreign",
                        prefix=prefix,
                        artifact=pre_artifact("foreign-core-cleanup-hard-delete"),
                        dry_run=False,
                    ),
                    cleanup_command(
                        role="iran",
                        prefix=prefix,
                        artifact=pre_artifact("iran-core-cleanup-hard-delete"),
                        dry_run=False,
                    ),
                ],
            },
            {
                "name": "post_delete_verification_and_unlock",
                "commands": [
                    cleanup_command(
                        role="foreign",
                        prefix=prefix,
                        artifact=pre_artifact("foreign-core-cleanup-post-delete-dry-run"),
                        dry_run=True,
                    ),
                    cleanup_command(
                        role="iran",
                        prefix=prefix,
                        artifact=pre_artifact("iran-core-cleanup-post-delete-dry-run"),
                        dry_run=True,
                    ),
                    isolation_command("disable"),
                    isolation_command("status"),
                ],
            },
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=default_prefix())
    parser.add_argument("--artifact-dir")
    parser.add_argument("--operator-account", default="mohsen")
    parser.add_argument("--reason")
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--attempts-per-scenario", type=int, default=40)
    parser.add_argument("--target-rps", type=float, default=600.0)
    parser.add_argument("--telegram-ratio", type=float, default=0.6)
    parser.add_argument("--write-max-concurrency", type=int, default=24)
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default and kept for clarity.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(args)
    except PlanError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

