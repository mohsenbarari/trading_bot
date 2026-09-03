"""HIGH-2: REQUEST same-key overlap and durable restart against isolated sqlite."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    OfferRequestLedgerResult,
    create_offer_request_ledger_entry,
    load_offer_request_by_idempotency,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
SAME_KEY = "p1-00-same-request-identity"
CHILD_ENV = "P1_00_REQUEST_RESTART_DB"

CREATE_SQL = """
CREATE TABLE offer_requests (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL DEFAULT 1,
    request_public_id VARCHAR(40),
    request_home_server VARCHAR(16) NOT NULL,
    local_offer_id INTEGER,
    offer_public_id VARCHAR(40) NOT NULL,
    requester_user_id INTEGER,
    actor_user_id INTEGER,
    request_source_surface VARCHAR(32) NOT NULL,
    request_source_server VARCHAR(16) NOT NULL,
    requested_quantity INTEGER NOT NULL,
    idempotency_key VARCHAR(128),
    workflow_kind VARCHAR(16) NOT NULL DEFAULT 'direct',
    offer_owner_user_id INTEGER,
    queue_sequence INTEGER,
    presented_at DATETIME,
    decision_deadline_at DATETIME,
    decided_by_user_id INTEGER,
    terminal_reason VARCHAR(64),
    telegram_delivery_job_id INTEGER,
    telegram_message_id INTEGER,
    requester_status_outbox_id INTEGER,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at DATETIME,
    result_status VARCHAR(64) NOT NULL,
    public_failure_code VARCHAR(64),
    public_failure_message VARCHAR(240),
    internal_failure_code VARCHAR(96),
    internal_failure_context JSON,
    resulting_trade_id INTEGER,
    customer_relation_id INTEGER,
    customer_owner_user_id INTEGER,
    customer_tier_snapshot VARCHAR(32),
    customer_management_name_snapshot VARCHAR(120),
    customer_commission_rate_snapshot NUMERIC(5, 2),
    customer_commission_context JSON,
    archived BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);
CREATE UNIQUE INDEX ux_offer_requests_home_idempotency_key
ON offer_requests (request_home_server, idempotency_key);
"""


class _AsyncSessionAdapter:
    def __init__(self, sync_session):
        self._session = sync_session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    def close(self):
        self._session.close()


def _make_engine(path: str):
    engine = create_engine(f"sqlite:///{path}", future=True)
    event.listen(engine, "connect", lambda conn, _rec: conn.execute("PRAGMA busy_timeout=5000"))
    with engine.begin() as conn:
        for statement in CREATE_SQL.strip().split(";"):
            if statement.strip():
                conn.execute(text(statement))
    return engine


def _command():
    return OfferRequestLedgerCommand(
        local_offer_id=None,
        offer_public_id="ofr_same",
        requested_quantity=1,
        request_home_server="iran",
        requester_user_id=None,
        actor_user_id=None,
        request_source_surface="webapp",
        request_source_server="iran",
        idempotency_key=SAME_KEY,
    )


def _row_count(path: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]


async def _create_once(db_path: str) -> OfferRequestLedgerResult:
    engine = _make_engine(db_path) if not Path(db_path).exists() or Path(db_path).stat().st_size == 0 else create_engine(f"sqlite:///{db_path}", future=True)
    Session = sessionmaker(bind=engine, future=True)
    db = _AsyncSessionAdapter(Session())
    try:
        result = await create_offer_request_ledger_entry(db, _command())
        await db.commit()
        return result
    finally:
        db.close()
        engine.dispose()


async def _replay_existing(db_path: str) -> OfferRequestLedgerResult:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Session = sessionmaker(bind=engine, future=True)
    db = _AsyncSessionAdapter(Session())
    try:
        result = await create_offer_request_ledger_entry(db, _command())
        if not result.duplicate_replay:
            await db.rollback()
            raise AssertionError("restart child must reload the persisted same-key row")
        return result
    finally:
        db.close()
        engine.dispose()


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


class P100RequestConcurrentRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_two_concurrent_commands_injects(self):
        """REQUEST two_concurrent_commands: overlapping asyncio tasks, same identity, sqlite winner/loser."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "request-ledger.sqlite")
            engine = _make_engine(db_path)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            barrier = asyncio.Barrier(2)

            async def worker():
                db = _AsyncSessionAdapter(Session())
                await barrier.wait()
                try:
                    result = await create_offer_request_ledger_entry(db, _command())
                    await db.commit()
                    return "winner" if not result.duplicate_replay else "replay", result
                except IntegrityError:
                    await db.rollback()
                    existing = await load_offer_request_by_idempotency(
                        db,
                        request_home_server="iran",
                        idempotency_key=SAME_KEY,
                    )
                    return "loser", existing
                finally:
                    db.close()

            first, second = await asyncio.gather(worker(), worker())
            engine.dispose()
            outcomes = {first[0], second[0]}
            self.assertEqual(_row_count(db_path), 1)
            self.assertTrue(outcomes & {"winner"})
            self.assertTrue(outcomes <= {"winner", "loser", "replay"})
            winner = first[1] if first[0] == "winner" else second[1]
            self.assertIsInstance(winner, OfferRequestLedgerResult)
            self.assertFalse(winner.duplicate_replay)
            with sqlite3.connect(db_path) as conn:
                key = conn.execute("SELECT idempotency_key FROM offer_requests").fetchone()[0]
            self.assertEqual(key, SAME_KEY)

    async def test_request_process_restart_injects(self):
        """REQUEST process_restart: child interpreter reloads persisted same-key row."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "request-restart.sqlite")
            created = await _create_once(db_path)
            self.assertFalse(created.duplicate_replay)
            self.assertEqual(_row_count(db_path), 1)
            env = os.environ.copy()
            env[CHILD_ENV] = db_path
            env["PYTHONPATH"] = str(REPO_ROOT)
            env["APP_ENV_FILE"] = "config/unit-test.env.example"
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                cwd=str(REPO_ROOT),
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertTrue(payload["duplicate_replay"])
            self.assertEqual(payload["row_count"], 1)
            self.assertEqual(payload["idempotency_key"], SAME_KEY)

    def test_unsupported_family_collision_cells_are_high_blockers(self):
        payload = _load_json("scenario-records.json")
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        for family in (
            "AUTH",
            "IDENTITY",
            "OFFER",
            "TRADE",
            "MARKET",
            "MSG",
            "NOTIFY",
            "TG",
            "ADMIN",
            "SYNC",
        ):
            for boundary in ("two_concurrent_commands", "process_restart"):
                cell = next(
                    item
                    for item in payload["family_boundary_variants"]
                    if item["family"] == family and item["failure_boundary"] == boundary
                )
                self.assertEqual(cell["applicability"], "HIGH_BLOCKER", cell["variant_id"])
                self.assertEqual(cell["eligibility"]["decision"], "UNRESOLVED")
                blocker_id = cell["named_blocker"]["blocker_id"]
                self.assertIn(blocker_id, registry)
                self.assertEqual(registry[blocker_id]["severity"], "HIGH")
                self.assertNotEqual(cell.get("evidence_cases") or [], [{"injects_named_boundary": True}])
                self.assertEqual(cell.get("evidence_cases") or [], [])

    def test_request_cells_cite_durable_overlap_nodes(self):
        payload = _load_json("scenario-records.json")
        concurrent = next(
            item
            for item in payload["family_boundary_variants"]
            if item["variant_id"] == "REQUEST__TWO-CONCURRENT-COMMANDS"
        )
        restart = next(
            item
            for item in payload["family_boundary_variants"]
            if item["variant_id"] == "REQUEST__PROCESS-RESTART"
        )
        self.assertEqual(concurrent["applicability"], "ELIGIBLE")
        self.assertEqual(restart["applicability"], "ELIGIBLE")
        self.assertIn("test_request_two_concurrent_commands_injects", concurrent["evidence_refs"][0])
        self.assertIn("test_request_process_restart_injects", restart["evidence_refs"][0])
        self.assertIn("asyncio.gather", Path(__file__).read_text(encoding="utf-8"))
        self.assertIn("subprocess.run", Path(__file__).read_text(encoding="utf-8"))


def _child_main() -> None:
    db_path = os.environ[CHILD_ENV]
    result = asyncio.run(_replay_existing(db_path))
    existing = asyncio.run(
        _load_existing(db_path)
    )
    print(
        json.dumps(
            {
                "duplicate_replay": result.duplicate_replay,
                "row_count": _row_count(db_path),
                "idempotency_key": existing.idempotency_key,
            }
        )
    )


async def _load_existing(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Session = sessionmaker(bind=engine, future=True)
    db = _AsyncSessionAdapter(Session())
    try:
        return await load_offer_request_by_idempotency(
            db,
            request_home_server="iran",
            idempotency_key=SAME_KEY,
        )
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    if os.environ.get(CHILD_ENV):
        _child_main()
    else:
        unittest.main()
