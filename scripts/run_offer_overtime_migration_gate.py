#!/usr/bin/env python3
"""Close the Stage 1 overtime migration gate on an isolated scratch database.

Requires:
  STAGE1_MIGRATION_TEST_DATABASE_URL=postgresql://.../stage1_migration_*
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REVISION = "a274f5a6b8c9"
HEAD_REVISION = "e8a4b5c6d7e9"
REQUIRED_INDEXES = (
    "ux_offer_requests_overtime_active_per_offer",
    "ux_offer_requests_overtime_owner_occupied",
    "ix_offer_requests_overtime_queue_order",
    "ix_offer_requests_overtime_open_by_requester",
)
REQUIRED_STATUSES = (
    "overtime_queued",
    "overtime_delivering",
    "overtime_presented",
    "overtime_rejected_by_owner",
    "overtime_decision_expired",
    "overtime_cancelled_by_requester",
    "overtime_invalidated",
    "overtime_delivery_expired",
    "overtime_rejected_requester_limit",
)


class MigrationGateError(RuntimeError):
    pass


def _database_url() -> str:
    raw = str(os.getenv("STAGE1_MIGRATION_TEST_DATABASE_URL", "")).strip()
    if not raw:
        raise MigrationGateError("STAGE1_MIGRATION_TEST_DATABASE_URL is required")
    url = make_url(raw)
    name = str(url.database or "").lower()
    if not name.startswith("stage1_migration_"):
        raise MigrationGateError("target must be a stage1_migration_* scratch database")
    return url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)


def _run_alembic(sync_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = str(REPO_ROOT)
    env["TRADING_BOT_EXPECTED_ALEMBIC_HEAD"] = HEAD_REVISION
    completed = subprocess.run(
        [sys.executable, "scripts/run_guarded_scratch_alembic.py", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise MigrationGateError(
            (completed.stderr or completed.stdout or "alembic failed").strip()
        )


def _scalar(engine, sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _all(engine, sql: str, **params):
    with engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


def _insert_user(connection, *, telegram_id: int, account_name: str) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO users (
                telegram_id, account_name, mobile_number, full_name, role,
                has_bot_access, address, must_change_password,
                home_server, offer_overtime_minutes
            )
            VALUES (
                :telegram_id, :account_name, :mobile, :full_name, 'STANDARD',
                false, '', false,
                'foreign', 0
            )
            RETURNING id
            """
        ),
        {
            "telegram_id": telegram_id,
            "account_name": account_name,
            "mobile": f"09{telegram_id}",
            "full_name": account_name,
        },
    ).scalar_one()


def _ensure_probe_parents(engine) -> tuple[int, int]:
    with engine.begin() as connection:
        owner_id = _insert_user(connection, telegram_id=910001, account_name="gate_owner")
        requester_id = _insert_user(
            connection, telegram_id=910002, account_name="gate_requester"
        )
        commodity_id = connection.execute(
            text(
                """
                INSERT INTO commodities (name)
                VALUES ('gate-probe')
                RETURNING id
                """
            )
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO offers (
                    user_id, commodity_id, offer_type, settlement_type,
                    quantity, remaining_quantity, price, status, home_server,
                    offer_public_id, overtime_minutes_snapshot, overtime_trade_committed,
                    version_id, is_wholesale
                )
                VALUES (
                    :owner_id, :commodity_id, 'SELL', 'CASH',
                    10, 10, 1000, 'ACTIVE', 'foreign',
                    'ofr_gate_probe_001', 0, false, 1, false
                )
                """
            ),
            {"owner_id": owner_id, "commodity_id": commodity_id},
        )
    return owner_id, requester_id


def _insert_overtime_row(engine, *, owner_id: int, requester_id: int, public_id: str, status: str, queue_sequence: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO offer_requests (
                    request_home_server, offer_public_id,
                    request_source_server, requested_quantity, result_status,
                    request_public_id, workflow_kind, offer_owner_user_id,
                    queue_sequence, requester_user_id, actor_user_id,
                    request_source_surface
                )
                VALUES (
                    'foreign', 'ofr_gate_probe_001',
                    'foreign', 1, CAST(:status AS offerrequeststatus),
                    :public_id, 'overtime', :owner_id,
                    :queue_sequence, :requester_id, :requester_id,
                    'webapp'
                )
                """
            ),
            {
                "status": status,
                "public_id": public_id,
                "owner_id": owner_id,
                "requester_id": requester_id,
                "queue_sequence": queue_sequence,
            },
        )


def _cleanup_probe_rows(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM offer_requests
                WHERE offer_public_id = 'ofr_gate_probe_001'
                   OR request_public_id LIKE 'req_gate_probe_%'
                """
            )
        )
        connection.execute(
            text("DELETE FROM offers WHERE offer_public_id = 'ofr_gate_probe_001'")
        )
        connection.execute(
            text(
                """
                DELETE FROM users
                WHERE account_name IN ('gate_owner', 'gate_requester')
                """
            )
        )
        connection.execute(text("DELETE FROM commodities WHERE name = 'gate-probe'"))


def _probe_unique_indexes(engine) -> dict[str, object]:
    _cleanup_probe_rows(engine)
    owner_id, requester_id = _ensure_probe_parents(engine)
    _insert_overtime_row(
        engine,
        owner_id=owner_id,
        requester_id=requester_id,
        public_id="req_gate_probe_a",
        status="overtime_queued",
        queue_sequence=1,
    )

    barrier = threading.Barrier(2)
    errors: list[str] = []

    def racing_insert(public_id: str) -> str:
        barrier.wait(timeout=5)
        try:
            _insert_overtime_row(
                engine,
                owner_id=owner_id,
                requester_id=requester_id,
                public_id=public_id,
                status="overtime_queued",
                queue_sequence=2,
            )
            return "inserted"
        except IntegrityError:
            return "unique_violation"
        except Exception as exc:  # pragma: no cover - unexpected
            errors.append(f"{type(exc).__name__}:{exc}")
            return "error"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(racing_insert, "req_gate_probe_b"),
            pool.submit(racing_insert, "req_gate_probe_c"),
        ]
        outcomes = [future.result() for future in as_completed(futures)]

    if errors:
        raise MigrationGateError(f"concurrent probe crashed: {errors}")
    if outcomes.count("unique_violation") < 1:
        raise MigrationGateError(
            f"expected at least one unique violation for concurrent writers; got {outcomes}"
        )
    if outcomes.count("inserted") > 1:
        raise MigrationGateError(
            f"unique index failed to serialize concurrent writers; got {outcomes}"
        )
    _cleanup_probe_rows(engine)
    return {"outcomes": sorted(outcomes), "owner_id": owner_id, "requester_id": requester_id}


def _verify_schema(engine) -> dict[str, object]:
    revision = _scalar(engine, "SELECT version_num FROM alembic_version")
    if revision != HEAD_REVISION:
        raise MigrationGateError(f"expected head {HEAD_REVISION}, got {revision}")

    statuses = {
        row[0]
        for row in _all(
            engine,
            """
            SELECT enumlabel
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'offerrequeststatus'
            """,
        )
    }
    missing_statuses = sorted(set(REQUIRED_STATUSES) - statuses)
    if missing_statuses:
        raise MigrationGateError(f"missing overtime statuses: {missing_statuses}")

    indexes = {
        row[0]
        for row in _all(
            engine,
            "SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names)",
            names=list(REQUIRED_INDEXES),
        )
    }
    missing_indexes = sorted(set(REQUIRED_INDEXES) - indexes)
    if missing_indexes:
        raise MigrationGateError(f"missing overtime indexes: {missing_indexes}")

    default_minutes = _scalar(
        engine,
        """
        SELECT column_default
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'offer_overtime_minutes'
        """,
    )
    if default_minutes is None or "0" not in str(default_minutes):
        raise MigrationGateError(f"users.offer_overtime_minutes default is not zero: {default_minutes}")

    predicates = {
        row[0]: row[1]
        for row in _all(
            engine,
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE indexname = ANY(:names)
            """,
            names=list(REQUIRED_INDEXES),
        )
    }
    for name, definition in predicates.items():
        if "result_status::text" in definition:
            raise MigrationGateError(f"{name} still uses non-immutable ::text cast")

    return {
        "revision": revision,
        "statuses_present": sorted(REQUIRED_STATUSES),
        "indexes_present": sorted(REQUIRED_INDEXES),
        "users_offer_overtime_default": str(default_minutes),
    }


def main() -> int:
    evidence_dir = REPO_ROOT / "tmp" / "offer-overtime-evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report_path = evidence_dir / "stage1-migration-gate-report.json"
    report: dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "head": HEAD_REVISION,
        "baseline": BASELINE_REVISION,
    }
    try:
        sync_url = _database_url()
        database_name = make_url(sync_url).database
        report["database_name"] = database_name
        engine = create_engine(sync_url, pool_pre_ping=True, pool_size=4)

        _run_alembic(sync_url, "upgrade", "head")
        report["upgrade_head"] = _verify_schema(engine)
        report["concurrency_probe"] = _probe_unique_indexes(engine)

        _run_alembic(sync_url, "downgrade", BASELINE_REVISION)
        after_down = _scalar(engine, "SELECT version_num FROM alembic_version")
        if after_down != BASELINE_REVISION:
            raise MigrationGateError(f"downgrade stopped at {after_down}")
        report["downgrade_baseline"] = after_down

        _run_alembic(sync_url, "upgrade", "head")
        report["reupgrade_head"] = _verify_schema(engine)
        report["status"] = "passed"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "passed", "report": str(report_path)}, sort_keys=True))
        return 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": "failed", "error": report["error"]}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
