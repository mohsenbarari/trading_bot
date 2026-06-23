#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "bot_webapp_candidate_full_matrix_v1"
PRODUCTION_GATE_STATUS = "blocked_until_owner_staging_validation"
MATRIX_DESIGN_DOC = REPO_ROOT / "docs" / "BOT_WEBAPP_CANDIDATE_FULL_MATRIX_DESIGN.md"
DEFAULT_USER_COUNT = 200
DEFAULT_ATTEMPTS_PER_SCENARIO = 5
DEFAULT_TARGET_RPS = 20.0
DEFAULT_TELEGRAM_RATIO = 0.6
DEFAULT_DB_POOL_SIZE = 8
DEFAULT_DB_MAX_OVERFLOW = 8
DEFAULT_WRITE_MAX_CONCURRENCY = 4


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def utc_prefix() -> str:
    return f"P7_STAGE_CANDIDATE_FULL_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"


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


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "invalid_json", "path": str(path)}
    return payload if isinstance(payload, dict) else {"status": "invalid_json", "path": str(path)}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_market_command(args: argparse.Namespace) -> list[str]:
    return [
        str(REPO_ROOT / "scripts" / "run_staging_comprehensive_load_matrix.sh"),
        "--prefix",
        args.prefix,
        "--artifact-dir",
        str(args.artifact_dir),
        "--users",
        str(args.users),
        "--attempts-per-scenario",
        str(args.attempts_per_scenario),
        "--target-rps",
        str(args.target_rps),
        "--telegram-ratio",
        str(args.telegram_ratio),
        "--write-max-concurrency",
        str(args.write_max_concurrency),
    ]


def build_notification_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(REPO_ROOT / "scripts" / "report_trade_notification_delivery_matrix.py"),
        "--check",
        "--output",
        str(args.artifact_dir / "trade-notification-delivery-matrix.json"),
    ]


def build_stage11_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        str(REPO_ROOT / "scripts" / "report_trade_delivery_staging_validation.py"),
        "matrix",
        "--repo-root",
        str(REPO_ROOT),
        "--output",
        str(args.artifact_dir / "trade-delivery-stage11-matrix.json"),
    ]


def run_streamed(command: list[str], *, env: dict[str, str], dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"status": "planned", "command": command}
    result = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    if result.returncode != 0:
        return {"status": "failed", "returncode": result.returncode, "command": command}
    return {"status": "passed", "returncode": result.returncode, "command": command}


def run_captured(command: list[str], *, stdout_path: Path, stderr_path: Path, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"status": "planned", "command": command}
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        return {"status": "failed", "returncode": result.returncode, "command": command}
    return {"status": "passed", "returncode": result.returncode, "command": command}


def build_summary(
    args: argparse.Namespace,
    *,
    market_run: dict[str, Any],
    notification_run: dict[str, Any],
    stage11_run: dict[str, Any],
) -> dict[str, Any]:
    market_payload = read_json(args.artifact_dir / "comprehensive-matrix.json")
    notification_payload = read_json(args.artifact_dir / "trade-notification-delivery-matrix.json")
    stage11_payload = read_json(args.artifact_dir / "trade-delivery-stage11-matrix.json")

    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "staging",
        "branch": run_git_value(["branch", "--show-current"]),
        "commit": run_git_value(["rev-parse", "HEAD"]),
        "dry_run": bool(args.dry_run),
        "matrix_design": {
            "path": str(MATRIX_DESIGN_DOC),
            "status": "required_before_owner_manual_staging_validation",
            "outage_scope": ["stable", "short_under_2m", "medium_around_60m"],
            "layers": [
                "deterministic_baseline_gates",
                "market_behavior_matrix",
                "trade_notification_delivery_audience_matrix",
                "runtime_delivery_receipt_matrix",
                "short_outage_matrix",
                "medium_outage_matrix",
                "targeted_join_scenarios",
                "ui_publication_evidence",
                "artifact_contract",
            ],
        },
        "no_pressure_profile": {
            "users": args.users,
            "attempts_per_scenario": args.attempts_per_scenario,
            "target_rps": args.target_rps,
            "telegram_ratio": args.telegram_ratio,
            "db_pool_size": args.db_pool_size,
            "db_max_overflow": args.db_max_overflow,
            "write_max_concurrency": args.write_max_concurrency,
        },
        "production_gate": {
            "status": PRODUCTION_GATE_STATUS,
            "reason": "This candidate full matrix is staging evidence only and cannot authorize production by itself.",
        },
        "artifacts": {
            "directory": str(args.artifact_dir),
            "summary": str(args.artifact_dir / "candidate-full-matrix-summary.json"),
            "market_matrix": str(args.artifact_dir / "comprehensive-matrix.json"),
            "notification_delivery_matrix": str(args.artifact_dir / "trade-notification-delivery-matrix.json"),
            "trade_delivery_stage11_matrix": str(args.artifact_dir / "trade-delivery-stage11-matrix.json"),
        },
        "market_matrix": {
            "run": market_run,
            "status": market_payload.get("status"),
            "scenario_count": market_payload.get("scenario_count"),
            "family_counts": market_payload.get("family_counts"),
            "failed_scenarios": market_payload.get("failed_scenarios", []),
            "production_gate": (market_payload.get("production_gate") or {}).get("status"),
        },
        "notification_delivery_matrix": {
            "run": notification_run,
            "scenario_count": notification_payload.get("scenario_count"),
            "actor_pair_count": notification_payload.get("actor_pair_count"),
            "surface_pair_count": notification_payload.get("surface_pair_count"),
            "outage_class_count": notification_payload.get("outage_class_count"),
            "production_gate": (notification_payload.get("production_gate") or {}).get("status"),
        },
        "trade_delivery_stage11_matrix": {
            "run": stage11_run,
            "status": stage11_payload.get("status"),
            "scenario_count": stage11_payload.get("scenario_count"),
            "manual_signoff_required": stage11_payload.get("manual_signoff_required"),
            "production_gate": (stage11_payload.get("production_gate") or {}).get("status"),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the candidate Bot/WebApp full staging matrix without pressure."
    )
    parser.add_argument("--prefix", default=os.environ.get("PREFIX") or utc_prefix())
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--users", type=int, default=env_int("USER_COUNT", DEFAULT_USER_COUNT))
    parser.add_argument(
        "--attempts-per-scenario",
        type=int,
        default=env_int("ATTEMPTS_PER_SCENARIO", DEFAULT_ATTEMPTS_PER_SCENARIO),
    )
    parser.add_argument("--target-rps", type=float, default=env_float("TARGET_RPS", DEFAULT_TARGET_RPS))
    parser.add_argument(
        "--telegram-ratio",
        type=float,
        default=env_float("TELEGRAM_RATIO", DEFAULT_TELEGRAM_RATIO),
    )
    parser.add_argument("--db-pool-size", type=int, default=env_int("DB_POOL_SIZE", DEFAULT_DB_POOL_SIZE))
    parser.add_argument(
        "--db-max-overflow",
        type=int,
        default=env_int("DB_MAX_OVERFLOW", DEFAULT_DB_MAX_OVERFLOW),
    )
    parser.add_argument(
        "--write-max-concurrency",
        type=int,
        default=env_int("WRITE_MAX_CONCURRENCY", DEFAULT_WRITE_MAX_CONCURRENCY),
    )
    parser.add_argument("--dry-run", action="store_true", help="Write the planned summary without hitting staging.")
    args = parser.parse_args(argv)
    if args.artifact_dir is None:
        args.artifact_dir = Path("/tmp/trading-bot-staging-candidate-full") / args.prefix
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DB_POOL_SIZE"] = str(args.db_pool_size)
    env["DB_MAX_OVERFLOW"] = str(args.db_max_overflow)

    market_run = run_streamed(build_market_command(args), env=env, dry_run=args.dry_run)
    notification_run = run_captured(
        build_notification_command(args),
        stdout_path=args.artifact_dir / "trade-notification-delivery-matrix.stdout.json",
        stderr_path=args.artifact_dir / "trade-notification-delivery-matrix.stderr.log",
        dry_run=args.dry_run,
    )
    stage11_run = run_captured(
        build_stage11_command(args),
        stdout_path=args.artifact_dir / "trade-delivery-stage11-matrix.stdout.json",
        stderr_path=args.artifact_dir / "trade-delivery-stage11-matrix.stderr.log",
        dry_run=args.dry_run,
    )

    summary = build_summary(
        args,
        market_run=market_run,
        notification_run=notification_run,
        stage11_run=stage11_run,
    )
    write_json(args.artifact_dir / "candidate-full-matrix-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

    failed = [run for run in (market_run, notification_run, stage11_run) if run.get("status") == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
