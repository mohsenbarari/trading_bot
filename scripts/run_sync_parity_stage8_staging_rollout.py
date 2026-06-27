#!/usr/bin/env python3
"""Plan and preflight the Stage 8 sync-parity staging rollout.

The script is fail-closed by default: it writes an auditable plan without
touching staging data. Read-only preflight commands are allowed in `preflight`
mode. Commands that create staging fixtures require `execute` mode plus an
explicit confirmation environment variable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.sync_parity import build_table_parity_snapshot


SCHEMA_VERSION = "sync_parity_stage8_staging_rollout_v1"
EXPECTED_BRANCH = "candidate/sync-parity-hardening"
EXECUTION_CONFIRM_ENV = "SYNC_PARITY_STAGE8_STAGING_CONFIRM"
EXECUTION_CONFIRM_VALUE = "execute-stage8-staging-rollout"
STAGING_PROJECT_NAME = "trading_bot_staging"
STAGING_COMPOSE_FILE = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
STAGING_ENV_FILE = REPO_ROOT / ".env.staging"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: list[str]
    phase: str
    description: str
    mutates_staging: bool = False
    required: bool = True
    timeout_seconds: int = 300
    stdout_path: Path | None = None
    stderr_path: Path | None = None


def utc_prefix() -> str:
    return f"P8_STAGE_SYNC_PARITY_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"


def run_git_value(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_payload(command: CommandSpec) -> dict[str, Any]:
    return {
        "name": command.name,
        "args": command.args,
        "phase": command.phase,
        "description": command.description,
        "mutates_staging": command.mutates_staging,
        "required": command.required,
        "timeout_seconds": command.timeout_seconds,
        "stdout_path": str(command.stdout_path) if command.stdout_path else None,
        "stderr_path": str(command.stderr_path) if command.stderr_path else None,
    }


def staging_compose_args() -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        STAGING_PROJECT_NAME,
        "--env-file",
        str(STAGING_ENV_FILE),
        "-f",
        str(STAGING_COMPOSE_FILE),
    ]


def staging_app_exec_args(*args: str) -> list[str]:
    return [*staging_compose_args(), "exec", "-T", "app", *args]


def build_repair_fixture_snapshots(artifact_dir: Path) -> tuple[Path, Path]:
    fixtures_dir = artifact_dir / "repair-drift-fixture"
    local_snapshot = {
        "status": "ok",
        "schema_version": "sync_parity_snapshot_v1",
        "mode": "quick",
        "tables": {
            "offers": build_table_parity_snapshot(
                "offers",
                [
                    {
                        "id": 8081,
                        "offer_public_id": "stage8_drift_offer",
                        "version_id": 1,
                        "user_id": 7001,
                        "actor_user_id": 7001,
                        "home_server": "iran",
                        "offer_type": "sell",
                        "commodity_id": 11,
                        "quantity": 10,
                        "remaining_quantity": 10,
                        "price": 100000,
                        "exclude_from_competitive_price": False,
                        "price_warning_type": None,
                        "status": "active",
                        "is_wholesale": False,
                        "lot_sizes": None,
                        "original_lot_sizes": None,
                        "notes": "stage8 dry-run drift fixture",
                        "channel_message_id": None,
                        "idempotency_key": "stage8-drift-offer",
                        "archived": False,
                    }
                ],
            )
        },
    }
    peer_snapshot = {
        **local_snapshot,
        "tables": {
            "offers": build_table_parity_snapshot(
                "offers",
                [
                    {
                        **{
                            "id": 8081,
                            "offer_public_id": "stage8_drift_offer",
                            "version_id": 1,
                            "user_id": 7001,
                            "actor_user_id": 7001,
                            "home_server": "iran",
                            "offer_type": "sell",
                            "commodity_id": 11,
                            "quantity": 10,
                            "remaining_quantity": 10,
                            "price": 100000,
                            "exclude_from_competitive_price": False,
                            "price_warning_type": None,
                            "is_wholesale": False,
                            "lot_sizes": None,
                            "original_lot_sizes": None,
                            "notes": "stage8 dry-run drift fixture",
                            "channel_message_id": None,
                            "idempotency_key": "stage8-drift-offer",
                            "archived": False,
                        },
                        "status": "expired",
                    }
                ],
            )
        },
    }
    local_path = fixtures_dir / "local-snapshot.json"
    peer_path = fixtures_dir / "peer-snapshot.json"
    write_json(local_path, local_snapshot)
    write_json(peer_path, peer_snapshot)
    return local_path, peer_path


def build_preflight_commands(args: argparse.Namespace, *, local_snapshot: Path, peer_snapshot: Path) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="local_sync_guarantee_matrix",
            args=[sys.executable, "-m", "unittest", "tests.test_sync_guarantee_matrix"],
            phase="local_regression",
            description="Every synced table must have receiver, payload, parity, repair, and rule coverage.",
            timeout_seconds=900,
            stdout_path=artifact_dir / "local-sync-guarantee-matrix.stdout.log",
            stderr_path=artifact_dir / "local-sync-guarantee-matrix.stderr.log",
        ),
        CommandSpec(
            name="local_out_of_order_and_watermark_guards",
            args=[
                sys.executable,
                "-m",
                "unittest",
                "tests.test_sync_router_stale_events",
                "tests.test_sync_router_watermarks",
            ],
            phase="local_regression",
            description="Out-of-order, duplicate, stale, and watermark decisions must remain protected.",
            timeout_seconds=900,
            stdout_path=artifact_dir / "local-out-of-order-watermark.stdout.log",
            stderr_path=artifact_dir / "local-out-of-order-watermark.stderr.log",
        ),
        CommandSpec(
            name="staging_stack_ps",
            args=[str(REPO_ROOT / "scripts" / "deploy_staging.sh"), "ps"],
            phase="staging_read_only",
            description="Record the running staging containers before data-producing tests.",
            timeout_seconds=120,
            stdout_path=artifact_dir / "staging-ps.stdout.log",
            stderr_path=artifact_dir / "staging-ps.stderr.log",
        ),
        CommandSpec(
            name="staging_health",
            args=[str(REPO_ROOT / "scripts" / "deploy_staging.sh"), "health"],
            phase="staging_read_only",
            description="Confirm staging HTTP health before realistic two-server checks.",
            timeout_seconds=180,
            stdout_path=artifact_dir / "staging-health.stdout.log",
            stderr_path=artifact_dir / "staging-health.stderr.log",
        ),
        CommandSpec(
            name="staging_parity_snapshot_quick",
            args=staging_app_exec_args(
                "python",
                "scripts/compare_sync_parity.py",
                "snapshot",
                "--mode",
                "quick",
                "--max-rows-per-table",
                str(args.quick_max_rows),
            ),
            phase="staging_read_only",
            description="Capture a bounded quick parity baseline from the staging app container.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "staging-parity-quick.json",
            stderr_path=artifact_dir / "staging-parity-quick.stderr.log",
        ),
        CommandSpec(
            name="staging_parity_snapshot_deep",
            args=staging_app_exec_args(
                "python",
                "scripts/compare_sync_parity.py",
                "snapshot",
                "--mode",
                "deep",
                "--max-rows-per-table",
                str(args.deep_max_rows),
            ),
            phase="staging_read_only",
            description="Capture the deep synced-table baseline for post-test parity comparison.",
            timeout_seconds=600,
            stdout_path=artifact_dir / "staging-parity-deep.json",
            stderr_path=artifact_dir / "staging-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="trade_notification_delivery_matrix_catalog",
            args=[
                sys.executable,
                str(REPO_ROOT / "scripts" / "report_trade_notification_delivery_matrix.py"),
                "--check",
                "--output",
                str(artifact_dir / "trade-notification-delivery-matrix.json"),
            ],
            phase="staging_plan_contract",
            description="Assert the delivery-audience matrix still covers every role, surface, and outage class.",
            timeout_seconds=180,
            stdout_path=artifact_dir / "trade-notification-delivery-matrix.stdout.log",
            stderr_path=artifact_dir / "trade-notification-delivery-matrix.stderr.log",
        ),
        CommandSpec(
            name="candidate_full_matrix_dry_run",
            args=[
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_bot_webapp_candidate_full_matrix.py"),
                "--dry-run",
                "--prefix",
                args.prefix,
                "--artifact-dir",
                str(artifact_dir / "candidate-full-dry-run"),
            ],
            phase="staging_plan_contract",
            description="Build the full Bot/WebApp staging matrix plan without creating staging data.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "candidate-full-dry-run.stdout.json",
            stderr_path=artifact_dir / "candidate-full-dry-run.stderr.log",
        ),
        CommandSpec(
            name="targeted_join_matrix_dry_run",
            args=[
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_trade_delivery_targeted_join_matrix.py"),
                "--dry-run",
                "--check",
                "--allow-any-branch",
                "--prefix",
                args.prefix,
                "--output",
                str(artifact_dir / "trade-delivery-targeted-join-matrix.json"),
            ],
            phase="staging_plan_contract",
            description="Assert targeted trade/delivery join scenarios without hitting DB or Telegram.",
            timeout_seconds=180,
            stdout_path=artifact_dir / "trade-delivery-targeted-join.stdout.json",
            stderr_path=artifact_dir / "trade-delivery-targeted-join.stderr.log",
        ),
        CommandSpec(
            name="sync_repair_drift_dry_run",
            args=[
                sys.executable,
                str(REPO_ROOT / "scripts" / "sync_repair_tool.py"),
                "plan",
                "--local-snapshot",
                str(local_snapshot),
                "--peer-snapshot",
                str(peer_snapshot),
                "--direction",
                "local-to-peer",
                "--sample-limit",
                "5",
            ],
            phase="repair_dry_run",
            description="Prove the repair tooling produces a dry-run plan for business drift before staging mutation.",
            timeout_seconds=120,
            stdout_path=artifact_dir / "sync-repair-drift-dry-run.json",
            stderr_path=artifact_dir / "sync-repair-drift-dry-run.stderr.log",
        ),
        CommandSpec(
            name="staging_cleanup_dry_run_for_prefix",
            args=staging_app_exec_args(
                "python",
                "scripts/trading_core_probe_worker.py",
                "cleanup",
                "--prefix",
                args.prefix,
                "--dry-run",
            ),
            phase="cleanup_dry_run",
            description="Confirm cleanup scope is exact for the Stage 8 synthetic prefix before any mutating run.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "staging-cleanup-dry-run.json",
            stderr_path=artifact_dir / "staging-cleanup-dry-run.stderr.log",
        ),
    ]


def build_execution_commands(args: argparse.Namespace) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="staging_candidate_full_matrix_no_pressure",
            args=[
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_bot_webapp_candidate_full_matrix.py"),
                "--prefix",
                args.prefix,
                "--artifact-dir",
                str(artifact_dir / "candidate-full-no-pressure"),
                "--users",
                str(args.candidate_users),
                "--attempts-per-scenario",
                str(args.candidate_attempts_per_scenario),
                "--target-rps",
                str(args.candidate_target_rps),
                "--telegram-ratio",
                str(args.telegram_ratio),
                "--write-max-concurrency",
                str(args.candidate_write_max_concurrency),
            ],
            phase="staging_mutating_no_pressure",
            description="Run the realistic candidate matrix on staging with bounded, non-pressure settings.",
            mutates_staging=True,
            timeout_seconds=7200,
            stdout_path=artifact_dir / "candidate-full-no-pressure.stdout.json",
            stderr_path=artifact_dir / "candidate-full-no-pressure.stderr.log",
        ),
        CommandSpec(
            name="staging_candidate_controlled_load_optional",
            args=[
                str(REPO_ROOT / "scripts" / "run_staging_comprehensive_load_matrix.sh"),
                "--prefix",
                f"{args.prefix}CONTROLLED_",
                "--artifact-dir",
                str(artifact_dir / "controlled-load"),
                "--users",
                str(args.controlled_users),
                "--attempts-per-scenario",
                str(args.controlled_attempts_per_scenario),
                "--target-rps",
                str(args.controlled_target_rps),
                "--telegram-ratio",
                str(args.telegram_ratio),
                "--write-max-concurrency",
                str(args.controlled_write_max_concurrency),
            ],
            phase="staging_mutating_controlled_load",
            description="Optional controlled-load pass after the no-pressure matrix is clean.",
            mutates_staging=True,
            required=False,
            timeout_seconds=14400,
            stdout_path=artifact_dir / "controlled-load.stdout.log",
            stderr_path=artifact_dir / "controlled-load.stderr.log",
        ),
        CommandSpec(
            name="staging_cleanup_after_candidate_prefix",
            args=staging_app_exec_args(
                "python",
                "scripts/trading_core_probe_worker.py",
                "cleanup",
                "--prefix",
                args.prefix,
            ),
            phase="post_execution_cleanup",
            description="Hard-delete remaining synthetic rows for the exact Stage 8 prefix after test execution.",
            mutates_staging=True,
            timeout_seconds=600,
            stdout_path=artifact_dir / "staging-cleanup-after-candidate.json",
            stderr_path=artifact_dir / "staging-cleanup-after-candidate.stderr.log",
        ),
        CommandSpec(
            name="staging_cleanup_after_controlled_prefix",
            args=staging_app_exec_args(
                "python",
                "scripts/trading_core_probe_worker.py",
                "cleanup",
                "--prefix",
                f"{args.prefix}CONTROLLED_",
            ),
            phase="post_execution_cleanup",
            description="Hard-delete remaining synthetic rows for the optional controlled-load prefix.",
            mutates_staging=True,
            required=False,
            timeout_seconds=600,
            stdout_path=artifact_dir / "staging-cleanup-after-controlled.json",
            stderr_path=artifact_dir / "staging-cleanup-after-controlled.stderr.log",
        ),
    ]


def build_post_execution_checks(args: argparse.Namespace) -> list[CommandSpec]:
    artifact_dir = args.artifact_dir
    return [
        CommandSpec(
            name="staging_post_parity_snapshot_quick",
            args=staging_app_exec_args(
                "python",
                "scripts/compare_sync_parity.py",
                "snapshot",
                "--mode",
                "quick",
                "--max-rows-per-table",
                str(args.quick_max_rows),
            ),
            phase="post_execution_read_only",
            description="Capture quick parity after cleanup; compare with baseline outside the mutating window.",
            timeout_seconds=300,
            stdout_path=artifact_dir / "staging-post-parity-quick.json",
            stderr_path=artifact_dir / "staging-post-parity-quick.stderr.log",
        ),
        CommandSpec(
            name="staging_post_parity_snapshot_deep",
            args=staging_app_exec_args(
                "python",
                "scripts/compare_sync_parity.py",
                "snapshot",
                "--mode",
                "deep",
                "--max-rows-per-table",
                str(args.deep_max_rows),
            ),
            phase="post_execution_read_only",
            description="Capture deep parity after cleanup; no business drift may remain.",
            timeout_seconds=600,
            stdout_path=artifact_dir / "staging-post-parity-deep.json",
            stderr_path=artifact_dir / "staging-post-parity-deep.stderr.log",
        ),
        CommandSpec(
            name="staging_post_health",
            args=[str(REPO_ROOT / "scripts" / "deploy_staging.sh"), "health"],
            phase="post_execution_read_only",
            description="Confirm staging health after cleanup and parity snapshots.",
            timeout_seconds=180,
            stdout_path=artifact_dir / "staging-post-health.stdout.log",
            stderr_path=artifact_dir / "staging-post-health.stderr.log",
        ),
    ]


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    local_snapshot, peer_snapshot = build_repair_fixture_snapshots(args.artifact_dir)
    preflight = build_preflight_commands(args, local_snapshot=local_snapshot, peer_snapshot=peer_snapshot)
    execution = build_execution_commands(args)
    post_execution = build_post_execution_checks(args)
    current_branch = run_git_value(["branch", "--show-current"])

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "planned",
        "environment": "staging",
        "branch": current_branch,
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "prefix": args.prefix,
        "artifact_dir": str(args.artifact_dir),
        "mode": args.mode,
        "execute_requested": args.mode == "execute",
        "branch_gate": {
            "expected_branch": EXPECTED_BRANCH,
            "actual_branch": current_branch,
            "passed": current_branch == EXPECTED_BRANCH,
        },
        "execution_contract": {
            "production_deploy_allowed": False,
            "staging_data_mutation_requires_mode": "execute",
            "execution_confirm_env": EXECUTION_CONFIRM_ENV,
            "execution_confirm_value": EXECUTION_CONFIRM_VALUE,
            "test_data_scope": "exact synthetic prefix only",
            "telegram_policy": "real staging Telegram may be checked manually; automated dry-runs never call Telegram",
        },
        "coverage_contract": {
            "market_surface_pairs": [
                "webapp_offer__webapp_request",
                "webapp_offer__telegram_request",
                "telegram_offer__webapp_request",
                "telegram_offer__telegram_request",
            ],
            "actor_relation_flows": [
                "user_to_user",
                "user_to_tier1_same_owner",
                "user_to_tier2_same_owner",
                "user_to_tier1_different_owner",
                "user_to_tier2_different_owner",
                "tier1_to_user_same_owner",
                "tier2_to_user_same_owner",
                "tier1_to_user_different_owner",
                "tier2_to_user_different_owner",
                "tier1_to_tier1_same_owner",
                "tier1_to_tier2_same_owner",
                "tier2_to_tier1_same_owner",
                "tier2_to_tier2_same_owner",
                "tier1_to_tier1_different_owner",
                "tier1_to_tier2_different_owner",
                "tier2_to_tier1_different_owner",
                "tier2_to_tier2_different_owner",
            ],
            "outage_classes": ["stable", "short_under_2m", "medium_around_60m"],
            "sync_gates": ["delivery", "parity", "out_of_order_guard", "repair_dry_run", "cleanup_scope"],
        },
        "preflight": {
            "status": "planned",
            "commands": [command_payload(command) for command in preflight],
        },
        "execution_plan": {
            "status": "blocked_until_explicit_confirm",
            "commands": [command_payload(command) for command in execution],
        },
        "post_execution_checks": {
            "status": "planned_after_mutating_run",
            "commands": [command_payload(command) for command in post_execution],
        },
        "manual_checks": [
            "Create WebApp offer, request from Telegram, verify trade, Telegram post tag, WebApp notification, and cleanup.",
            "Create Telegram offer, request from WebApp, verify trade, Telegram post tag, WebApp notification, and cleanup.",
            "Manually expire active offers from each surface and verify the peer surface reflects terminal state.",
            "Verify customers see their owner as counterparty and accountants receive only allowed WebApp notifications.",
            "Review Telegram channel warnings separately when channel posts were manually deleted.",
        ],
        "exit_criteria": [
            "All required preflight commands pass.",
            "No-pressure candidate full matrix passes before optional controlled-load execution.",
            "Cleanup reports leave no synthetic data for the exact prefix.",
            "Post-test parity snapshots show no unexpected business drift.",
            "No sync retry backlog remains after the short/medium outage scenarios catch up.",
        ],
    }


def run_command(command: dict[str, Any]) -> dict[str, Any]:
    stdout_path = Path(command["stdout_path"]) if command.get("stdout_path") else None
    stderr_path = Path(command["stderr_path"]) if command.get("stderr_path") else None
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if stderr_path:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    try:
        result = subprocess.run(
            list(command["args"]),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=int(command["timeout_seconds"]),
            check=False,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if stdout_path:
            stdout_path.write_text(stdout, encoding="utf-8")
        if stderr_path:
            stderr_path.write_text(stderr, encoding="utf-8")
        status = "passed" if result.returncode == 0 else "failed"
        return {
            "name": command["name"],
            "status": status,
            "returncode": result.returncode,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout_path": str(stdout_path) if stdout_path else None,
            "stderr_path": str(stderr_path) if stderr_path else None,
        }
    except subprocess.TimeoutExpired as exc:
        if stdout_path and exc.stdout:
            stdout_path.write_text(str(exc.stdout), encoding="utf-8")
        if stderr_path and exc.stderr:
            stderr_path.write_text(str(exc.stderr), encoding="utf-8")
        return {
            "name": command["name"],
            "status": "timeout",
            "returncode": 124,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "stdout_path": str(stdout_path) if stdout_path else None,
            "stderr_path": str(stderr_path) if stderr_path else None,
        }


def execute_plan(plan: dict[str, Any], *, include_mutating: bool) -> tuple[dict[str, Any], int]:
    if not plan["branch_gate"]["passed"]:
        plan["status"] = "blocked_wrong_branch"
        return plan, 2
    if include_mutating and os.environ.get(EXECUTION_CONFIRM_ENV) != EXECUTION_CONFIRM_VALUE:
        plan["status"] = "blocked_execution_confirmation_missing"
        plan["execution_plan"]["status"] = "blocked_confirmation_missing"
        return plan, 2

    sections = ["preflight"]
    if include_mutating:
        sections.extend(["execution_plan", "post_execution_checks"])

    failed_required: list[str] = []
    for section_name in sections:
        section = plan[section_name]
        results = []
        for command in section["commands"]:
            if command["mutates_staging"] and not include_mutating:
                continue
            result = run_command(command)
            results.append(result)
            if command.get("required", True) and result["status"] != "passed":
                failed_required.append(command["name"])
                break
        section["results"] = results
        section["status"] = "passed" if not any(result["status"] != "passed" for result in results) else "failed"
        if failed_required:
            break

    plan["status"] = "passed" if not failed_required else "failed"
    if failed_required:
        plan["failed_required_commands"] = failed_required
    return plan, 0 if not failed_required else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "preflight", "execute"), default="plan")
    parser.add_argument("--prefix", default=os.environ.get("PREFIX") or utc_prefix())
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quick-max-rows", type=int, default=5000)
    parser.add_argument("--deep-max-rows", type=int, default=10000)
    parser.add_argument("--candidate-users", type=int, default=200)
    parser.add_argument("--candidate-attempts-per-scenario", type=int, default=5)
    parser.add_argument("--candidate-target-rps", type=float, default=20.0)
    parser.add_argument("--candidate-write-max-concurrency", type=int, default=4)
    parser.add_argument("--controlled-users", type=int, default=1000)
    parser.add_argument("--controlled-attempts-per-scenario", type=int, default=40)
    parser.add_argument("--controlled-target-rps", type=float, default=600.0)
    parser.add_argument("--controlled-write-max-concurrency", type=int, default=24)
    parser.add_argument("--telegram-ratio", type=float, default=0.6)
    args = parser.parse_args(argv)
    if args.artifact_dir is None:
        args.artifact_dir = Path("/tmp/trading-bot-stage8-sync-parity") / args.prefix
    if args.output is None:
        args.output = args.artifact_dir / "stage8-staging-rollout-plan.json"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(args)

    exit_code = 0
    if args.mode in {"preflight", "execute"}:
        plan, exit_code = execute_plan(plan, include_mutating=args.mode == "execute")

    write_json(args.output, plan)
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
