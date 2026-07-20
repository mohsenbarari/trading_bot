#!/usr/bin/env python3
"""Read-only Telegram queue health/shadow report for synthetic test or staging."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.services.telegram_delivery_observability_service import (
    TelegramQueueHealthThresholds,
    build_telegram_delivery_shadow_plan,
    inspect_telegram_delivery_queue_health,
    publish_telegram_delivery_health_metrics,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


DATABASE_URL_ENV = "TELEGRAM_QUEUE_OBSERVABILITY_DATABASE_URL"
_SYNTHETIC_DATABASE = re.compile(r"^telegram_queue_stage3_[a-z0-9_]+_(?:test|scratch)$")


class TelegramQueueObservabilityConfigurationError(RuntimeError):
    pass


def validate_observability_environment(environment: str, database_name: str) -> None:
    normalized_environment = str(environment or "").strip().lower()
    normalized_database = str(database_name or "").strip().lower()
    if normalized_environment == "production":
        raise TelegramQueueObservabilityConfigurationError(
            "production_environment_is_forbidden"
        )
    if normalized_environment == "synthetic-test":
        if not _SYNTHETIC_DATABASE.fullmatch(normalized_database):
            raise TelegramQueueObservabilityConfigurationError(
                "synthetic_test_database_name_invalid"
            )
        return
    if normalized_environment == "staging":
        if "staging" not in normalized_database or "prod" in normalized_database:
            raise TelegramQueueObservabilityConfigurationError(
                "staging_database_name_invalid"
            )
        return
    raise TelegramQueueObservabilityConfigurationError(
        "environment_must_be_synthetic_test_or_staging"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate read-only Telegram delivery health and shadow-order evidence. "
            "Production is intentionally unsupported."
        )
    )
    parser.add_argument(
        "--environment",
        required=True,
        choices=("synthetic-test", "staging"),
    )
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--sample-window-seconds", type=float, default=60.0)
    parser.add_argument("--include-shadow", action="store_true")
    parser.add_argument("--shadow-limit", type=int, default=25)
    parser.add_argument("--publish-metrics", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--warning-ready-depth", type=int, default=500)
    parser.add_argument("--stop-ready-depth", type=int, default=5_000)
    parser.add_argument("--warning-oldest-ready-age", type=float, default=10.0)
    parser.add_argument("--stop-oldest-ready-age", type=float, default=30.0)
    parser.add_argument("--stop-ambiguous", type=int, default=0)
    parser.add_argument("--stop-unresolved", type=int, default=0)
    parser.add_argument("--stop-blocked", type=int, default=0)
    parser.add_argument("--stop-missed-freshness", type=int, default=0)
    parser.add_argument("--stop-terminal-failure", type=int, default=0)
    parser.add_argument("--stop-expired-lease", type=int, default=10)
    parser.add_argument("--stop-provider-outcome-age", type=float, default=30.0)
    return parser.parse_args(argv)


def _thresholds(args: argparse.Namespace) -> TelegramQueueHealthThresholds:
    return TelegramQueueHealthThresholds(
        warning_ready_depth=args.warning_ready_depth,
        stop_ready_depth=args.stop_ready_depth,
        warning_oldest_ready_age_seconds=args.warning_oldest_ready_age,
        stop_oldest_ready_age_seconds=args.stop_oldest_ready_age,
        stop_ambiguous_count=args.stop_ambiguous,
        stop_unresolved_count=args.stop_unresolved,
        stop_blocked_count=args.stop_blocked,
        stop_missed_freshness_count=args.stop_missed_freshness,
        stop_terminal_failure_count=args.stop_terminal_failure,
        stop_expired_lease_count=args.stop_expired_lease,
        stop_provider_outcome_age_seconds=args.stop_provider_outcome_age,
    )


async def build_report(args: argparse.Namespace) -> dict[str, Any]:
    raw_url = str(os.getenv(DATABASE_URL_ENV, "")).strip()
    if not raw_url:
        raise TelegramQueueObservabilityConfigurationError(
            "observability_database_url_missing"
        )
    target = make_url(raw_url)
    expected_database = str(args.expected_database_name).strip()
    if str(target.database or "") != expected_database:
        raise TelegramQueueObservabilityConfigurationError(
            "database_url_expected_name_mismatch"
        )
    validate_observability_environment(args.environment, expected_database)
    if target.get_backend_name() != "postgresql":
        raise TelegramQueueObservabilityConfigurationError(
            "observability_requires_postgresql"
        )

    async_target = target.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(async_target, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                actual_database = str(
                    (await connection.execute(text("SELECT current_database()"))).scalar_one()
                )
                if actual_database != expected_database:
                    raise TelegramQueueObservabilityConfigurationError(
                        "connected_database_expected_name_mismatch"
                    )
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    health = await inspect_telegram_delivery_queue_health(
                        session,
                        current_server="foreign",
                        thresholds=_thresholds(args),
                        sample_window_seconds=args.sample_window_seconds,
                        run_id=args.run_id,
                    )
                    shadow = (
                        await build_telegram_delivery_shadow_plan(
                            session,
                            current_server="foreign",
                            limit_per_lane=args.shadow_limit,
                            run_id=args.run_id,
                        )
                        if args.include_shadow
                        else None
                    )
                finally:
                    await session.close()
    finally:
        await engine.dispose()

    if args.publish_metrics:
        publish_telegram_delivery_health_metrics(health)
    return {
        "schema_version": 1,
        "environment": args.environment,
        "database_mode": "transaction_read_only",
        "provider_network_calls": 0,
        "scope": "run" if args.run_id is not None else "all",
        "health": health,
        "shadow": shadow,
    }


def _write_evidence(report_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report_path.write_text(rendered, encoding="utf-8")
    security_scan = scan_paths([report_path])
    scan_path = report_path.with_suffix(report_path.suffix + ".security-scan.json")
    scan_path.write_text(
        json.dumps(security_scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report_path": str(report_path),
        "security_scan_path": str(scan_path),
        "security_scan_status": security_scan["status"],
        "security_scan_finding_count": security_scan["finding_count"],
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = asyncio.run(build_report(args))
        evidence = _write_evidence(args.report, report) if args.report else None
        output = {"report": report, "evidence": evidence}
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        if evidence and evidence["security_scan_status"] != "clean":
            return 3
        return 2 if report["health"]["decision"] == "stop" else 0
    except TelegramQueueObservabilityConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_code": str(exc),
                    "provider_network_calls": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "internal_error",
                    "error_class": type(exc).__name__,
                    "provider_network_calls": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
