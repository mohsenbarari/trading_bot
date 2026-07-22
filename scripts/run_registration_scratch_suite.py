#!/usr/bin/env python3
"""Create isolated registration scratch databases, migrate, test, and remove them."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DATABASE_DENYLIST = {
    "postgres",
    "template0",
    "template1",
    "trading_bot",
    "trading_bot_db",
    "trading_bot_staging",
    "trading_bot_production",
}
POSTGRES_SYSTEM_IDENTIFIER_PATTERN = re.compile(r"^[1-9][0-9]{15,19}$")
SUITES = (
    ("stage1_migration", "STAGE1_MIGRATION_TEST_DATABASE_URL", "tests.test_registration_stage1_migration_postgres"),
    ("stage1_counter", "STAGE1_COUNTER_TEST_DATABASE_URL", "tests.test_user_counter_sync_postgres"),
    ("stage2_registration", "STAGE2_TEST_DATABASE_URL", "tests.test_authoritative_registration_postgres"),
    ("stage3_registration", "STAGE3_TEST_DATABASE_URL", "tests.test_stage3_invitation_creation_postgres"),
    ("stage4_registration", "STAGE4_TEST_DATABASE_URL", "tests.test_stage4_registration_reconciliation_postgres"),
)


class RegistrationScratchSuiteError(RuntimeError):
    pass


def _raise_termination(signum, _frame) -> None:
    raise RegistrationScratchSuiteError(f"terminated_by_signal:{signum}")


def _sync_url(raw_url: str):
    return make_url(raw_url).set(drivername="postgresql+psycopg2")


def _render(url) -> str:
    return url.render_as_string(hide_password=False)


def _database_snapshot(raw_runtime_url: str) -> tuple[str, str | None, int]:
    engine = create_engine(_sync_url(raw_runtime_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            database_name = str(
                connection.execute(text("SELECT current_database()")).scalar_one()
            ).strip()
            has_alembic_version = bool(
                connection.execute(
                    text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
                ).scalar_one()
            )
            revision = (
                connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar_one_or_none()
                if has_alembic_version
                else None
            )
            stage1_objects = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM (VALUES "
                        "(to_regclass('public.telegram_registration_intents')), "
                        "(to_regclass('public.telegram_registration_command_receipts')), "
                        "(to_regclass('public.invitation_sms_deliveries'))"
                        ") AS objects(object_name) WHERE object_name IS NOT NULL"
                    )
                ).scalar_one()
            )
            return database_name, (str(revision) if revision else None), stage1_objects
    finally:
        engine.dispose()


def _admin_engine(raw_runtime_url: str):
    runtime = _sync_url(raw_runtime_url)
    runtime_name = str(runtime.database or "").lower()
    if not runtime_name or runtime_name in {"postgres", "template0", "template1"}:
        raise RegistrationScratchSuiteError("runtime DATABASE_URL must identify the application database")
    return create_engine(runtime.set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def _verify_scratch_cluster(raw_runtime_url: str, expected_system_id: str) -> None:
    expected = str(expected_system_id or "").strip()
    if POSTGRES_SYSTEM_IDENTIFIER_PATTERN.fullmatch(expected) is None:
        raise RegistrationScratchSuiteError(
            "expected scratch-cluster system identifier is required"
        )
    engine = _admin_engine(raw_runtime_url)
    try:
        with engine.connect() as connection:
            observed = str(
                connection.execute(
                    text("SELECT system_identifier::text FROM pg_control_system()")
                ).scalar_one()
            )
    finally:
        engine.dispose()
    if observed != expected:
        raise RegistrationScratchSuiteError(
            "runtime database is not on the expected disposable scratch cluster"
        )


def _run(command: list[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(command, cwd=REPO_ROOT, env=env, check=False)
    if completed.returncode:
        raise RegistrationScratchSuiteError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}"
        )


def _test_command(module: str, *, coverage_file: str) -> list[str]:
    if not coverage_file:
        return [sys.executable, "-m", "unittest", "-v", module]
    return [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--branch",
        "--parallel-mode",
        "-m",
        "unittest",
        "-v",
        module,
    ]


def _migration_command(target: str, *, coverage_file: str) -> list[str]:
    command = [sys.executable]
    if coverage_file:
        command.extend(["-m", "coverage", "run", "--branch", "--parallel-mode"])
    command.extend(["scripts/run_guarded_scratch_alembic.py", "upgrade", target])
    return command


def main() -> int:
    runtime_url = str(os.getenv("DATABASE_URL", "")).strip()
    expected_checkout = str(os.getenv("TRADING_BOT_EXPECTED_CHECKOUT", "")).strip()
    if not runtime_url:
        print("registration scratch suite refused: DATABASE_URL is required", file=sys.stderr)
        return 2
    if str(os.getenv("STAGE9_SCRATCH_DATABASES_ALLOWED", "")).strip().lower() != "true":
        print("registration scratch suite refused: explicit scratch opt-in is required", file=sys.stderr)
        return 2
    environment = str(os.getenv("ENVIRONMENT", "")).strip().lower()
    if environment not in {"test", "testing", "ci"}:
        print(
            f"registration scratch suite refused: unsafe environment={environment or 'unset'}",
            file=sys.stderr,
        )
        return 2
    if Path(expected_checkout).resolve() != REPO_ROOT:
        print("registration scratch suite refused: checkout mismatch", file=sys.stderr)
        return 2

    try:
        _verify_scratch_cluster(
            runtime_url,
            os.getenv("TRADING_BOT_EXPECTED_SCRATCH_CLUSTER_SYSTEM_ID", ""),
        )
    except Exception as exc:
        print(
            f"registration scratch suite refused: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    before = _database_snapshot(runtime_url)
    if before[0].lower() in RUNTIME_DATABASE_DENYLIST - {"trading_bot_db"}:
        print("registration scratch suite refused: unexpected runtime database", file=sys.stderr)
        return 2
    coverage_file = str(os.getenv("STAGE9_COVERAGE_FILE", "")).strip()

    suffix = secrets.token_hex(6)
    runtime = _sync_url(runtime_url)
    scratch_names = [f"{prefix}_{suffix}" for prefix, _env_name, _module in SUITES]
    admin = _admin_engine(runtime_url)
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    for signum in previous_handlers:
        signal.signal(signum, _raise_termination)
    try:
        with admin.connect() as connection:
            existing = set(
                connection.execute(
                    text("SELECT datname FROM pg_database WHERE datname = ANY(:names)"),
                    {"names": scratch_names},
                ).scalars()
            )
            if existing:
                raise RegistrationScratchSuiteError("generated scratch database already exists")
            for database_name in scratch_names:
                connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        for (prefix, env_name, module), database_name in zip(SUITES, scratch_names, strict=True):
            scratch_sync = runtime.set(database=database_name)
            scratch_async = scratch_sync.set(drivername="postgresql+asyncpg")
            env = os.environ.copy()
            env.update(
                {
                    "TRADING_BOT_MIGRATION_MODE": "scratch",
                    "TRADING_BOT_EXPECTED_CHECKOUT": str(REPO_ROOT),
                    "SYNC_DATABASE_URL": _render(scratch_sync),
                    "DATABASE_URL": _render(scratch_async),
                    env_name: _render(scratch_async),
                }
            )
            if coverage_file:
                env["COVERAGE_FILE"] = coverage_file
            migration_target = "f7c8d9e0a1b2" if prefix == "stage1_migration" else "head"
            _run(
                _migration_command(migration_target, coverage_file=coverage_file),
                env=env,
            )
            _run(_test_command(module, coverage_file=coverage_file), env=env)
    except Exception as exc:
        print(f"registration scratch suite failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return_code = 1
    else:
        return_code = 0
    finally:
        with admin.connect() as connection:
            for database_name in scratch_names:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin.dispose()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    after = _database_snapshot(runtime_url)
    if after != before:
        print(
            f"registration scratch suite refused evidence: active database changed before={before} after={after}",
            file=sys.stderr,
        )
        return 2
    print(
        f"registration scratch suite complete: active_database={after[0]} revision={after[1]} stage1_objects={after[2]}",
        file=sys.stderr,
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
