#!/usr/bin/env python3
"""Run the guarded real-PostgreSQL Witness and commit-fence source gate."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import unittest

import asyncpg
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
DATABASE_ENV = "WRITER_FENCING_TEST_DATABASE_URL"
DATABASE_PREFIX = "stage4_registration_writerfence_"
EXPECTED_TESTS = 5


def guarded_url() -> str:
    value = str(os.getenv(DATABASE_ENV, "")).strip()
    try:
        parsed = make_url(value)
    except Exception as exc:
        raise SystemExit("writer witness PostgreSQL gate URL is invalid") from exc
    if not str(parsed.database or "").startswith(DATABASE_PREFIX):
        raise SystemExit("writer witness PostgreSQL gate refused a non-scratch database")
    if str(parsed.host or "") in {"", "localhost", "127.0.0.1", "::1"}:
        raise SystemExit("writer witness PostgreSQL gate requires its isolated Compose database")
    return value


async def bootstrap(value: str) -> None:
    parsed = make_url(value)
    connection = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port or 5432,
        database=parsed.database,
    )
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await connection.execute(
            (ROOT / "deploy/writer-witness-drill/001_webapp_writer_local.sql").read_text(
                encoding="utf-8"
            )
        )
        await connection.execute(
            (ROOT / "deploy/writer-witness/001_initial.sql").read_text(encoding="utf-8")
        )
        await connection.execute(
            """
            INSERT INTO webapp_writer_state (
                authority, active_site, writer_epoch, control_state,
                transition_id, witness_lease_id, witness_lease_expires_at,
                witness_proof_hash, witness_transition_id, updated_by, reason
            ) VALUES (
                'webapp', 'webapp_fi', 10, 'active',
                'integration-active-term', 'integration-lease',
                clock_timestamp() + interval '180 seconds', repeat('a', 64),
                'integration-witness-term', 'postgres-gate', 'guarded source gate'
            )
            """
        )
    finally:
        await connection.close()


def main() -> int:
    value = guarded_url()
    asyncio.run(bootstrap(value))
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        (
            "tests.test_writer_witness_postgres",
            "tests.test_writer_fencing_postgres",
        )
    )
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    if result.testsRun != EXPECTED_TESTS:
        raise SystemExit(
            f"writer witness PostgreSQL gate ran {result.testsRun}, expected {EXPECTED_TESTS}"
        )
    if result.skipped:
        raise SystemExit(
            f"writer witness PostgreSQL gate skipped {len(result.skipped)} guarded tests"
        )
    if not result.wasSuccessful():
        raise SystemExit("writer witness PostgreSQL gate failed")
    print(
        '{"status":"passed","gate":"writer-witness-real-postgres",'
        f'"tests":{result.testsRun},"skipped":0}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
