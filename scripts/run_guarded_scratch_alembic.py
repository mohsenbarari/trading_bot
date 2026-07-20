#!/usr/bin/env python3
"""Run Alembic only after proving the target is an isolated scratch database."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DATABASE_PATTERN = re.compile(
    r"^(?:stage1_migration|stage1_counter|stage2_registration|stage3_registration|stage4_registration|market_stage7|market_stage8|market_stage9|market_stage10|market_stage11|market_stage12|market_stage13|market_stage15|telegram_queue_stage3)_[a-z0-9_]+$"
)
DENIED_DATABASE_NAMES = frozenset(
    {
        "postgres",
        "template0",
        "template1",
        "trading_bot",
        "trading_bot_db",
        "trading_bot_staging",
        "trading_bot_production",
    }
)
ALLOWED_COMMANDS = frozenset({"upgrade", "downgrade", "current", "history"})


class ScratchMigrationSafetyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScratchMigrationTarget:
    database_name: str
    sync_url: URL


def _database_endpoint(url: URL) -> tuple[object, ...]:
    return (
        str(url.username or ""),
        str(url.password or ""),
        str(url.host or ""),
        int(url.port or 5432),
        str(url.database or "").lower(),
        tuple(sorted((url.query or {}).items())),
    )


def validate_scratch_database_urls(
    *,
    sync_database_url: str,
    database_url: str,
) -> ScratchMigrationTarget:
    if os.getenv("TRADING_BOT_MIGRATION_MODE", "").strip().lower() != "scratch":
        raise ScratchMigrationSafetyError("scratch migration mode is required")
    if not sync_database_url or not database_url:
        raise ScratchMigrationSafetyError("both database URLs are required")
    try:
        sync_url = make_url(sync_database_url)
        app_url = make_url(database_url)
    except Exception as exc:
        raise ScratchMigrationSafetyError("database URL parsing failed") from exc
    if _database_endpoint(sync_url) != _database_endpoint(app_url):
        raise ScratchMigrationSafetyError("database URLs do not identify the same target")

    database_name = str(sync_url.database or "").strip().lower()
    if database_name in DENIED_DATABASE_NAMES:
        raise ScratchMigrationSafetyError("known runtime database targets are forbidden")
    if not SCRATCH_DATABASE_PATTERN.fullmatch(database_name):
        raise ScratchMigrationSafetyError("database name is not an allowed scratch target")
    return ScratchMigrationTarget(database_name=database_name, sync_url=sync_url)


def validate_checkout(*, expected_checkout: str, repo_root: Path = REPO_ROOT) -> None:
    if not expected_checkout:
        raise ScratchMigrationSafetyError("expected checkout is required")
    expected = Path(expected_checkout).expanduser().resolve()
    actual = repo_root.resolve()
    if expected != actual:
        raise ScratchMigrationSafetyError("expected checkout does not match script checkout")
    if not (actual / "alembic.ini").is_file() or not (actual / "migrations" / "env.py").is_file():
        raise ScratchMigrationSafetyError("checkout is missing Alembic sources")


def validate_alembic_source(*, repo_root: Path = REPO_ROOT) -> str:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise ScratchMigrationSafetyError("Alembic source must have exactly one head")
    expected_head = os.getenv("TRADING_BOT_EXPECTED_ALEMBIC_HEAD", "").strip()
    if expected_head and heads[0] != expected_head:
        raise ScratchMigrationSafetyError("Alembic source head does not match expectation")
    return heads[0]


def verify_connected_database(target: ScratchMigrationTarget) -> None:
    sync_url = target.sync_url.set(drivername="postgresql+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connected_database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    finally:
        engine.dispose()
    if connected_database.lower() != target.database_name:
        raise ScratchMigrationSafetyError("connected database does not match guarded target")


def _validate_command(argv: list[str]) -> None:
    if not argv or argv[0] not in ALLOWED_COMMANDS:
        raise ScratchMigrationSafetyError("only guarded Alembic data-plane commands are allowed")
    if argv[0] in {"upgrade", "downgrade"} and len(argv) != 2:
        raise ScratchMigrationSafetyError("upgrade/downgrade requires exactly one revision")


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    try:
        _validate_command(arguments)
        target = validate_scratch_database_urls(
            sync_database_url=os.getenv("SYNC_DATABASE_URL", ""),
            database_url=os.getenv("DATABASE_URL", ""),
        )
        validate_checkout(
            expected_checkout=os.getenv("TRADING_BOT_EXPECTED_CHECKOUT", ""),
        )
        source_head = validate_alembic_source()
        verify_connected_database(target)
    except ScratchMigrationSafetyError as exc:
        print(f"scratch migration refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"scratch migration preflight failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2

    print(
        f"scratch migration preflight passed: database={target.database_name} head={source_head}",
        file=sys.stderr,
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(REPO_ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
