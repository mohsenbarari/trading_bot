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

    def test_deferred_family_collision_cells_use_later_stage(self):
        payload = _load_json("scenario-records.json")
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        for family in ("IDENTITY", "OFFER", "TRADE", "MARKET", "TG", "SYNC"):
            for boundary in ("two_concurrent_commands", "process_restart"):
                cell = next(
                    item
                    for item in payload["family_boundary_variants"]
                    if item["family"] == family and item["failure_boundary"] == boundary
                )
                self.assertEqual(cell["applicability"], "HIGH_BLOCKER", cell["variant_id"])
                self.assertEqual(cell["eligibility"]["decision"], "DEFERRED")
                blocker = registry[cell["named_blocker"]["blocker_id"]]
                self.assertEqual(blocker["disposition"], "DEFERRED_BLOCKER")
                self.assertEqual(blocker["status"], "DEFERRED_BLOCKER")
                self.assertNotEqual(blocker["resolution_stage"], "CODEX_FINAL_REVIEW")
                self.assertTrue(blocker["observed_fact"])
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


class P100FamilyOverlapCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_two_concurrent_commands_injects(self):
        """AUTH two_concurrent_commands: overlapping handle_login_session same token, one durable slot."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from uuid import uuid4

        from core.enums import UserRole
        from core.services import session_service

        durable: list = []
        barrier = asyncio.Barrier(2)
        lock = asyncio.Lock()

        async def get_active(*_args, **_kwargs):
            return [item for item in durable if item.is_active]

        async def fake_create(*_args, **kwargs):
            await barrier.wait()
            async with lock:
                session = SimpleNamespace(id=uuid4(), is_active=True, is_primary=True)
                for item in durable:
                    item.is_active = False
                durable.append(session)
                return session

        user = SimpleNamespace(id=14, role=UserRole.STANDARD, max_sessions=1)
        with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
            session_service, "get_active_sessions", new=get_active
        ), patch.object(session_service, "create_session", new=fake_create), patch.object(
            session_service, "_is_accountant_user", new=AsyncMock(return_value=False)
        ), patch(
            "core.services.session_service.get_effective_max_sessions", return_value=1
        ), patch.object(session_service, "publish_session_revocation", new=AsyncMock()):
            first, second = await asyncio.gather(
                session_service.handle_login_session(SimpleNamespace(commit=AsyncMock()), user, "same-auth-token", "web"),
                session_service.handle_login_session(SimpleNamespace(commit=AsyncMock()), user, "same-auth-token", "web"),
            )
        self.assertEqual(sum(1 for item in durable if item.is_active), 1)
        self.assertEqual({first["action"], second["action"]}, {"session_created"})

    async def test_auth_process_restart_injects(self):
        """AUTH process_restart: reconstructed same-auth-token session is revived, not recreated."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from uuid import uuid4

        from core.enums import UserRole
        from core.services import session_service

        reconstructed = SimpleNamespace(
            id=uuid4(),
            is_active=True,
            is_primary=True,
            refresh_token_hash=session_service.hash_token("same-auth-token"),
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: reconstructed)),
            commit=AsyncMock(),
        )
        create = AsyncMock(side_effect=AssertionError("restart must revive"))
        user = SimpleNamespace(id=14, role=UserRole.STANDARD, max_sessions=1)
        with patch.object(session_service, "_is_inactive_account", return_value=False), patch.object(
            session_service, "create_session", new=create
        ), patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch(
            "core.services.session_service.get_effective_max_sessions", return_value=1
        ), patch.object(session_service, "publish_session_revocation", new=AsyncMock()):
            replay = await session_service.handle_login_session(
                db,
                user,
                "same-auth-token",
                "web",
                suspended_refresh_token="same-auth-token",
            )
        self.assertIs(replay["session"], reconstructed)
        create.assert_not_awaited()

    async def test_notify_two_concurrent_commands_injects(self):
        """NOTIFY two_concurrent_commands: overlapping mark_notification_read on one unread row."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from api.routers import notifications as notify_router

        notification = SimpleNamespace(id=11, user_id=6, is_read=False)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: notification)),
            commit=AsyncMock(),
        )
        barrier = asyncio.Barrier(2)

        async def worker():
            await barrier.wait()
            return await notify_router.mark_notification_read(
                11, current_user=SimpleNamespace(id=6), db=db, redis=SimpleNamespace()
            )

        with patch.object(notify_router, "sync_unread_count", new=AsyncMock()) as sync:
            await asyncio.gather(worker(), worker())
        self.assertTrue(notification.is_read)
        self.assertGreaterEqual(db.commit.await_count, 1)
        self.assertGreaterEqual(sync.await_count, 1)

    async def test_notify_process_restart_injects(self):
        """NOTIFY process_restart: already-read durable row stays read and skips write."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from api.routers import notifications as notify_router

        reconstructed = SimpleNamespace(id=12, user_id=7, is_read=True)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: reconstructed)),
            commit=AsyncMock(),
        )
        with patch.object(notify_router, "sync_unread_count", new=AsyncMock()) as sync:
            await notify_router.mark_notification_read(
                12, current_user=SimpleNamespace(id=7), db=db, redis=SimpleNamespace()
            )
        self.assertTrue(reconstructed.is_read)
        db.commit.assert_not_awaited()
        sync.assert_not_awaited()

    async def test_msg_two_concurrent_commands_injects(self):
        """MSG two_concurrent_commands: persist_sent_direct_message has no identity so two overlaps add two rows."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from core.enums import MessageType
        from core.services.chat_service import persist_sent_direct_message

        class _DB:
            def __init__(self):
                self.added = []
                self.commit = AsyncMock()
                self.refresh = AsyncMock()

            def add(self, obj):
                obj.id = len(self.added) + 1
                self.added.append(obj)

        db = _DB()
        sender = SimpleNamespace(id=1)
        receiver = SimpleNamespace(id=2)
        barrier = asyncio.Barrier(2)

        async def worker(content):
            await barrier.wait()
            return await persist_sent_direct_message(
                db,
                sender=sender,
                receiver=receiver,
                content=content,
                message_type=MessageType.TEXT,
            )

        with patch("core.services.chat_service.sync_direct_message_threading", new=AsyncMock()), patch(
            "core.services.chat_service.reload_direct_message",
            new=AsyncMock(side_effect=lambda _db, message_id: next(item for item in db.added if item.id == message_id)),
        ):
            await asyncio.gather(worker("alpha"), worker("beta"))
        self.assertEqual(len(db.added), 2)
        self.assertEqual({item.content for item in db.added}, {"alpha", "beta"})

    async def test_msg_process_restart_injects(self):
        """MSG process_restart: same content after reload appends another row because no command identity exists."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from core.enums import MessageType
        from core.services.chat_service import persist_sent_direct_message

        class _DB:
            def __init__(self):
                self.added = []
                self.commit = AsyncMock()
                self.refresh = AsyncMock()

            def add(self, obj):
                obj.id = len(self.added) + 1
                self.added.append(obj)

        db = _DB()
        sender = SimpleNamespace(id=1)
        receiver = SimpleNamespace(id=2)
        with patch("core.services.chat_service.sync_direct_message_threading", new=AsyncMock()), patch(
            "core.services.chat_service.reload_direct_message",
            new=AsyncMock(side_effect=lambda _db, message_id: next(item for item in db.added if item.id == message_id)),
        ):
            first = await persist_sent_direct_message(
                db, sender=sender, receiver=receiver, content="same-text", message_type=MessageType.TEXT
            )
            replay = await persist_sent_direct_message(
                db, sender=sender, receiver=receiver, content="same-text", message_type=MessageType.TEXT
            )
        self.assertEqual(len(db.added), 2)
        self.assertIsNot(first, replay)

    async def test_admin_two_concurrent_commands_injects(self):
        """ADMIN two_concurrent_commands: overlapping handle_admin_toggle_block last flip wins."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from bot.handlers.admin_users import handle_admin_toggle_block
        from core.enums import UserRole
        from tests.test_p1_00_characterization_admin_toggle_behavior import FakeSession, _callback, _target

        target = _target(can_block_users=True)
        session = FakeSession(target)
        barrier = asyncio.Barrier(2)

        async def worker():
            await barrier.wait()
            await handle_admin_toggle_block(_callback("admin_toggle_block_9"), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))

        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch("bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ):
            await asyncio.gather(worker(), worker())
        self.assertEqual(session.commit.await_count, 2)
        self.assertIsInstance(target.can_block_users, bool)

    async def test_admin_process_restart_injects(self):
        """ADMIN process_restart: reconstructed can_block_users flips once after reload."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from bot.handlers.admin_users import handle_admin_toggle_block
        from core.enums import UserRole
        from tests.test_p1_00_characterization_admin_toggle_behavior import FakeSession, _callback, _target

        reconstructed = _target(can_block_users=True)
        session = FakeSession(reconstructed)
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch("bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ):
            await handle_admin_toggle_block(_callback("admin_toggle_block_9"), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertFalse(reconstructed.can_block_users)
        session.commit.assert_awaited_once()


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
