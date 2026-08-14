#!/usr/bin/env python3
"""Fail-closed, read-only preflight for schema-preserving queue rollback."""
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
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.services.telegram_delivery_rollback_service import (
    inspect_telegram_delivery_forward_rollback_readiness,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


DATABASE_URL_ENV = "TELEGRAM_QUEUE_ROLLBACK_DATABASE_URL"
_SYNTHETIC_DATABASE = re.compile(r"^telegram_queue_stage3_[a-z0-9_]+_(?:test|scratch)$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class TelegramForwardRollbackConfigurationError(RuntimeError):
    pass


def validate_environment(
    environment: str,
    database_name: str,
    *,
    acknowledge_production_read_only: bool,
) -> None:
    environment = str(environment or "").strip().lower()
    database_name = str(database_name or "").strip().lower()
    if environment == "synthetic-test":
        if not _SYNTHETIC_DATABASE.fullmatch(database_name):
            raise TelegramForwardRollbackConfigurationError(
                "synthetic_test_database_name_invalid"
            )
        return
    if environment == "staging":
        if "staging" not in database_name or "prod" in database_name:
            raise TelegramForwardRollbackConfigurationError(
                "staging_database_name_invalid"
            )
        return
    if environment == "production" and acknowledge_production_read_only:
        if "test" in database_name or "staging" in database_name:
            raise TelegramForwardRollbackConfigurationError(
                "production_database_name_invalid"
            )
        return
    raise TelegramForwardRollbackConfigurationError(
        "production_requires_explicit_read_only_acknowledgement"
        if environment == "production"
        else "rollback_environment_invalid"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check forward rollback readiness without downgrading schema."
    )
    parser.add_argument(
        "--environment",
        required=True,
        choices=("synthetic-test", "staging", "production"),
    )
    parser.add_argument("--ack-production-read-only", action="store_true")
    parser.add_argument("--expected-database-name", required=True)
    parser.add_argument("--expected-schema-head", required=True)
    parser.add_argument("--execution-owner", default="legacy")
    parser.add_argument("--queue-worker-enabled", action="store_true")
    parser.add_argument("--cutover-ready", action="store_true")
    parser.add_argument("--producer-quiesced", action="store_true")
    parser.add_argument("--migration-stage-skipped", action="store_true")
    parser.add_argument("--backup-manifest-sha256", required=True)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


async def build_report(args: argparse.Namespace) -> dict[str, object]:
    raw_url = str(os.getenv(DATABASE_URL_ENV, "")).strip()
    if not raw_url:
        raise TelegramForwardRollbackConfigurationError(
            "rollback_database_url_missing"
        )
    target = make_url(raw_url)
    expected_database = str(args.expected_database_name).strip()
    if str(target.database or "") != expected_database:
        raise TelegramForwardRollbackConfigurationError(
            "database_url_expected_name_mismatch"
        )
    validate_environment(
        args.environment,
        expected_database,
        acknowledge_production_read_only=args.ack_production_read_only,
    )
    if target.get_backend_name() != "postgresql":
        raise TelegramForwardRollbackConfigurationError(
            "rollback_preflight_requires_postgresql"
        )
    backup_verified = bool(_SHA256.fullmatch(str(args.backup_manifest_sha256)))
    if not backup_verified:
        raise TelegramForwardRollbackConfigurationError(
            "backup_manifest_sha256_invalid"
        )

    engine = create_async_engine(
        target.set(drivername="postgresql+asyncpg"),
        pool_pre_ping=True,
    )
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                actual_database = str(
                    (await connection.execute(text("SELECT current_database()"))).scalar_one()
                )
                if actual_database != expected_database:
                    raise TelegramForwardRollbackConfigurationError(
                        "connected_database_expected_name_mismatch"
                    )
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    readiness = await inspect_telegram_delivery_forward_rollback_readiness(
                        session,
                        current_server="foreign",
                        expected_schema_head=args.expected_schema_head,
                        execution_owner=args.execution_owner,
                        queue_worker_enabled=args.queue_worker_enabled,
                        cutover_ready=args.cutover_ready,
                        producer_quiesced=args.producer_quiesced,
                        migration_stage_skipped=args.migration_stage_skipped,
                        backup_manifest_verified=backup_verified,
                    )
                finally:
                    await session.close()
    finally:
        await engine.dispose()
    return {
        "schema_version": 1,
        "environment": args.environment,
        "database_mode": "transaction_read_only",
        "readiness": readiness,
    }


def _write_report(path: Path, report: dict[str, object]) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    scan = scan_paths([path])
    scan_path = path.with_suffix(path.suffix + ".security-scan.json")
    scan_path.write_text(
        json.dumps(scan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "security_scan_status": scan["status"],
        "security_scan_finding_count": scan["finding_count"],
        "report_path": str(path),
        "security_scan_path": str(scan_path),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = asyncio.run(build_report(args))
        evidence = _write_report(args.report, report) if args.report else None
        print(
            json.dumps(
                {"report": report, "evidence": evidence},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        if evidence and evidence["security_scan_status"] != "clean":
            return 3
        return 0 if report["readiness"]["decision"] == "ready" else 2
    except TelegramForwardRollbackConfigurationError as exc:
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
