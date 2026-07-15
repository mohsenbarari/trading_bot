#!/usr/bin/env python3
"""Run the market PostgreSQL proof suites against guarded scratch databases."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url


DATABASES = {
    "MARKET_STAGE6_TEST_DATABASE_URL": "market_stage6_ci_test",
    "MARKET_STAGE7_TEST_DATABASE_URL": "market_stage7_ci_test",
    "MARKET_STAGE8_TEST_DATABASE_URL": "market_stage8_ci_test",
    "MARKET_STAGE9_TEST_DATABASE_URL": "market_stage9_ci_test",
    "MARKET_STAGE10_TEST_DATABASE_URL": "market_stage10_ci_test",
    "MARKET_STAGE11_TEST_DATABASE_URL": "market_stage11_ci_test",
    "MARKET_STAGE12_TEST_DATABASE_URL": "market_stage12_ci_test",
    "MARKET_STAGE13_TEST_DATABASE_URL": "market_stage13_ci_test",
    "MARKET_STAGE15_TEST_DATABASE_URL": "market_stage15_ci_test",
}

TEST_MODULES = (
    "tests.test_market_offer_admission_postgres",
    "tests.test_offer_quota_admission_postgres",
    "tests.test_trade_price_settlement_postgres",
    "tests.test_offer_publication_repair_postgres",
    "tests.test_offer_cancel_all_postgres",
    "tests.test_offer_expiry_receipts_postgres",
    "tests.test_active_offer_pagination_postgres",
    "tests.test_trade_history_pagination_postgres",
    "tests.test_offer_idempotency_fingerprint_postgres",
)


def _database_url(admin_url: str, database: str) -> str:
    parsed = make_url(admin_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("MARKET_POSTGRES_GATE_ADMIN_URL must be a PostgreSQL URL")
    return parsed.set(database=database, drivername="postgresql").render_as_string(
        hide_password=False
    )


def _connect(admin_url: str):
    parsed = make_url(admin_url).set(drivername="postgresql")
    return psycopg2.connect(parsed.render_as_string(hide_password=False))


def _create_scratch_databases(admin_url: str) -> None:
    connection = _connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            for database in DATABASES.values():
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (database,),
                )
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    finally:
        connection.close()


def _drop_scratch_databases(admin_url: str) -> None:
    connection = _connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            for database in DATABASES.values():
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database,),
                )
                cursor.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
                )
    finally:
        connection.close()


def _run_gate(admin_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    gate_redis_url = os.getenv(
        "MARKET_POSTGRES_GATE_REDIS_URL",
        "redis://127.0.0.1:6379/14",
    )
    parsed_redis = urlparse(gate_redis_url)
    if parsed_redis.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("MARKET_POSTGRES_GATE_REDIS_URL must target localhost")
    # Keep this proof independent from a developer or server checkout's .env.
    # The market suites seed foreign-owned fixtures, while registration v2
    # intentionally makes Iran authoritative for user creation. The legacy
    # user listener remains enabled so the mandatory outbox guard is still
    # exercised instead of bypassed.
    env.update(
        {
            "SERVER_MODE": "foreign",
            "ENVIRONMENT": "test",
            "REGISTRATION_SYNC_V2_ENABLED": "false",
            "BOT_USERNAME": "market_postgres_gate_bot",
            "FRONTEND_URL": "http://127.0.0.1:4173",
            "JWT_SECRET_KEY": "market_postgres_gate_secret",
            "REDIS_URL": gate_redis_url,
            # Some legacy event publishers still use host/port settings rather
            # than REDIS_URL. Pin both surfaces to the same disposable Redis.
            "REDIS_HOST": str(parsed_redis.hostname),
            "REDIS_PORT": str(parsed_redis.port or 6379),
        }
    )
    for variable, database in DATABASES.items():
        env[variable] = _database_url(admin_url, database)
    return subprocess.run(
        [sys.executable, "-m", "unittest", "-v", *TEST_MODULES],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin-url",
        default=os.getenv("MARKET_POSTGRES_GATE_ADMIN_URL", ""),
    )
    parser.add_argument("--log-path", default="tmp/market-postgres-gate.log")
    args = parser.parse_args()
    admin_url = str(args.admin_url).strip()
    if not admin_url:
        parser.error("--admin-url or MARKET_POSTGRES_GATE_ADMIN_URL is required")

    _create_scratch_databases(admin_url)
    try:
        result = _run_gate(admin_url)
        output = result.stdout + result.stderr
        log_path = Path(args.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
        print(output, end="")

        skipped = re.search(r"(?:skipped=|\bskipped\s+)([1-9]\d*)", output)
        if result.returncode != 0:
            return result.returncode
        if skipped:
            print("market PostgreSQL gate rejected skipped proof tests", file=sys.stderr)
            return 2
        return 0
    finally:
        _drop_scratch_databases(admin_url)


if __name__ == "__main__":
    raise SystemExit(main())
