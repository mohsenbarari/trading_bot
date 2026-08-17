#!/usr/bin/env python3
"""Reconcile recipient-less notification outbox rows without provider calls."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.services.telegram_notification_outbox_orphan_reconciliation_service import (
    reconcile_orphaned_telegram_notification_outbox,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


DATABASE_URL_ENV = "TELEGRAM_NOTIFICATION_OUTBOX_RECONCILE_DATABASE_URL"
APPLY_CONFIRMATION = "STAGING OUTBOX ORPHAN RECONCILIATION APPLY"
_SYNTHETIC_DATABASE = re.compile(
    r"^telegram_queue_stage3_[a-z0-9_]+_(?:test|scratch)$"
)


class TelegramNotificationOutboxReconciliationConfigurationError(RuntimeError):
    pass


def _validate_target(environment: str, database_name: str) -> None:
    normalized_environment = str(environment or "").strip().lower()
    normalized_database = str(database_name or "").strip().lower()
    if normalized_environment == "production":
        raise TelegramNotificationOutboxReconciliationConfigurationError(
            "production_environment_is_forbidden"
        )
    if normalized_environment == "staging":
        if "staging" not in normalized_database or "prod" in normalized_database:
            raise TelegramNotificationOutboxReconciliationConfigurationError(
                "staging_database_name_invalid"
            )
        return
    if normalized_environment == "synthetic-test":
        if _SYNTHETIC_DATABASE.fullmatch(normalized_database) is None:
            raise TelegramNotificationOutboxReconciliationConfigurationError(
                "synthetic_database_name_invalid"
            )
        return
    raise TelegramNotificationOutboxReconciliationConfigurationError(
        "environment_invalid"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--environment",
        required=True,
        choices=("synthetic-test", "staging"),
    )
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict[str, object]:
    raw_url = str(os.getenv(DATABASE_URL_ENV, "")).strip()
    if not raw_url:
        raise TelegramNotificationOutboxReconciliationConfigurationError(
            "database_url_missing"
        )
    target = make_url(raw_url)
    expected_database = str(args.expected_database_name or "").strip()
    if str(target.database or "") != expected_database:
        raise TelegramNotificationOutboxReconciliationConfigurationError(
            "database_url_expected_name_mismatch"
        )
    _validate_target(args.environment, expected_database)
    if target.get_backend_name() != "postgresql":
        raise TelegramNotificationOutboxReconciliationConfigurationError(
            "postgresql_required"
        )
    if args.apply and args.confirm != APPLY_CONFIRMATION:
        raise TelegramNotificationOutboxReconciliationConfigurationError(
            "apply_confirmation_mismatch"
        )

    engine = create_async_engine(
        target.set(drivername="postgresql+asyncpg"),
        pool_pre_ping=True,
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            actual_database = str(
                (await db.execute(text("SELECT current_database()"))).scalar_one()
            )
            if actual_database != expected_database:
                raise TelegramNotificationOutboxReconciliationConfigurationError(
                    "connected_database_expected_name_mismatch"
                )
            report = await reconcile_orphaned_telegram_notification_outbox(
                db,
                current_server="foreign",
                dry_run=not args.apply,
                limit=args.limit,
            )
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
    finally:
        await engine.dispose()

    payload: dict[str, object] = {
        "schema_version": 1,
        "environment": args.environment,
        "database_mode": "apply" if args.apply else "dry_run",
        "production_authorized": False,
        "inspected_count": report.inspected_count,
        "reconciled_count": report.reconciled_count,
        "preserved_non_reconcilable_count": (
            report.preserved_non_reconcilable_count
        ),
        "remaining_reconcilable_count": report.remaining_reconcilable_count,
        "provider_network_calls": report.provider_network_calls,
        "status": (
            "blocked_non_reconcilable"
            if report.preserved_non_reconcilable_count
            else "partial"
            if args.apply and report.remaining_reconcilable_count
            else "applied" if args.apply else "dry_run"
        ),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        security_scan = scan_paths([args.report])
        scan_path = args.report.with_suffix(
            args.report.suffix + ".security-scan.json"
        )
        scan_path.write_text(
            json.dumps(security_scan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload["security_scan_status"] = security_scan["status"]
        payload["security_scan_finding_count"] = security_scan["finding_count"]
    return payload


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        payload = asyncio.run(run(args))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return (
            2
            if payload["status"]
            in {"blocked_non_reconcilable", "partial"}
            else 0
        )
    except TelegramNotificationOutboxReconciliationConfigurationError as exc:
        print(
            json.dumps(
                {
                    "status": "configuration_error",
                    "error_code": str(exc),
                    "production_authorized": False,
                    "provider_network_calls": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
