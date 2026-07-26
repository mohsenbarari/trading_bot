#!/usr/bin/env python3
"""Rehearse one Alembic downgrade/forward cycle on an isolated clone."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402


DATABASE = re.compile(r"fm_[0-9a-f]{20}_rollback\Z")
REVISION = re.compile(r"[A-Za-z0-9_]{4,128}\Z")


class LegacyRollbackProbeError(RuntimeError):
    pass


def run(database: str, expected_head: str) -> dict:
    if DATABASE.fullmatch(database) is None or REVISION.fullmatch(expected_head) is None:
        raise LegacyRollbackProbeError("rollback probe identity is invalid")
    source = make_url(str(settings.sync_database_url))
    if source.get_backend_name() != "postgresql" or not source.database:
        raise LegacyRollbackProbeError("rollback probe requires PostgreSQL")
    target = source.set(database=database)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", target.render_as_string(hide_password=False))

    engine = create_engine(target)
    try:
        with engine.connect() as connection:
            before = sorted(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
        if before != [expected_head]:
            raise LegacyRollbackProbeError("rollback clone does not start at expected head")
        command.downgrade(config, "-1")
        with engine.connect() as connection:
            downgraded = sorted(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
        if not downgraded or downgraded == before:
            raise LegacyRollbackProbeError("rollback did not move to the prior revision")
        command.upgrade(config, expected_head)
        with engine.connect() as connection:
            restored = sorted(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
    finally:
        engine.dispose()
    if restored != before:
        raise LegacyRollbackProbeError("forward recovery did not restore the release head")
    return {
        "schema": "three-site-full-matrix-legacy-rollback-probe-v1",
        "status": "passed",
        "database": database,
        "before": before,
        "downgraded": downgraded,
        "restored": restored,
        "downgrade_changed_revision": True,
        "forward_restored_exact_head": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.database, args.expected_head), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1)
