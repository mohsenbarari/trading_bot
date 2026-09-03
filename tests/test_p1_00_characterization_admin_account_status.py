"""HIGH-B: account-status uses the live service against an isolated durable store."""

from __future__ import annotations

import inspect
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from bot.handlers.admin_users import handle_user_toggle_account_status
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus, UserRole
from core.services import user_account_status_service as status_service
from models.notification import Notification
from models.offer_request import (
    OfferRequest,
    OfferRequestSourceSurface,
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.telegram_notification_outbox import TelegramNotificationOutbox

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ACCOUNT_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-TOGGLE-ACCOUNT-STATUS-L1134"
BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


class _Nested:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session.begin_nested()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DurableSession:
    """Async adapter over an isolated sqlite session. Does not mock product services."""

    def __init__(self, sync_session, *, fail_outbox=False):
        self._session = sync_session
        self.fail_outbox = fail_outbox
        self.commit_count = 0
        self.wakeup_attempts = 0
        self.loaded_user = None

    async def execute(self, statement, params=None):
        blob = f"{statement} {getattr(statement, 'text', '')}"
        if "pg_notify" in blob.lower():
            self.wakeup_attempts += 1
            return SimpleNamespace()
        if self.loaded_user is not None and "FROM users" in blob and "notifications" not in blob.lower():
            return SimpleNamespace(scalar_one_or_none=lambda: self.loaded_user)
        return self._session.execute(statement)

    def add(self, obj):
        if self.fail_outbox and isinstance(obj, TelegramNotificationOutbox) and self.commit_count >= 1:
            raise RuntimeError("outbox enqueue failed after notification commit")
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._session.commit()
        self.commit_count += 1

    async def refresh(self, obj):
        self._session.refresh(obj)

    async def rollback(self):
        self._session.rollback()

    def begin_nested(self):
        return _Nested(self._session)


class _SessionBox:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_store():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        for name in ("users", "telegram_delivery_jobs", "offers", "trades", "customer_relations"):
            conn.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
    Notification.__table__.create(engine)
    TelegramNotificationOutbox.__table__.create(engine)
    OfferRequest.__table__.create(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    sync = Session()
    return engine, sync


def _user(*, status=UserAccountStatus.ACTIVE, role=UserRole.STANDARD, telegram_id=91):
    return SimpleNamespace(
        id=9,
        role=role,
        account_name="ali",
        account_status=status,
        telegram_id=telegram_id,
        deactivated_at=None if status == UserAccountStatus.ACTIVE else datetime(2026, 5, 16, 12, 0, 0),
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
        sync_version=3,
        trading_restricted_until=None,
        max_daily_trades=None,
        max_active_commodities=None,
        max_daily_requests=None,
        is_deleted=False,
        can_block_users=True,
        max_blocked_users=5,
    )


def _seed_overtime(sync, *, user_id=9):
    row = OfferRequest(
        request_home_server="iran",
        offer_public_id="ofr_ot",
        requested_quantity=1,
        request_source_surface=OfferRequestSourceSurface.WEBAPP,
        request_source_server="iran",
        requester_user_id=user_id,
        offer_owner_user_id=None,
        workflow_kind=OfferRequestWorkflow.OVERTIME,
        result_status=OfferRequestStatus.OVERTIME_QUEUED,
        received_at=datetime(2026, 9, 3, 12, 0, 0),
    )
    sync.add(row)
    sync.commit()
    return row


def _patches():
    return [
        patch("core.services.user_account_status_service.current_server", return_value="iran"),
        patch("core.services.offer_overtime_request_service.current_server", return_value="iran"),
        patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=30)),
        ),
    ]


def _callback():
    return SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        data="user_toggle_account_status_9",
    )


class P100AdminAccountStatusCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_transition_commits_notification_before_outbox(self):
        engine, sync = _make_store()
        _seed_overtime(sync)
        db = DurableSession(sync)
        user = _user()
        started = _patches()
        for item in started:
            item.start()
        try:
            result = await status_service.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
        finally:
            for item in started:
                item.stop()
        self.assertTrue(result.changed)
        self.assertEqual(user.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(sync.query(Notification).count(), 1)
        notice = sync.query(Notification).one()
        self.assertEqual(notice.user_id, 9)
        self.assertEqual(notice.level, NotificationLevel.WARNING)
        self.assertEqual(notice.category, NotificationCategory.SYSTEM)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 1)
        outbox = sync.query(TelegramNotificationOutbox).one()
        self.assertEqual(outbox.recipient_user_id, 9)
        self.assertEqual(outbox.telegram_id_at_enqueue, 91)
        self.assertEqual(sync.query(OfferRequest).one().result_status, OfferRequestStatus.OVERTIME_INVALIDATED)
        self.assertGreaterEqual(db.wakeup_attempts, 1)
        self.assertEqual(db.commit_count, 1)

        replay = await status_service.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
        self.assertFalse(replay.changed)
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 1)
        self.assertEqual(db.commit_count, 1)
        engine.dispose()

    async def test_redis_realtime_absence_when_broker_is_down(self):
        engine, sync = _make_store()
        _seed_overtime(sync)
        db = DurableSession(sync)
        user = _user()
        started = _patches()
        for item in started:
            item.start()
        try:
            await status_service.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
        finally:
            for item in started:
                item.stop()
        self.assertEqual(sync.query(Notification).count(), 1)
        try:
            conn = sqlite3.connect(":memory:")
            conn.close()
        finally:
            pass
        import redis

        try:
            client = redis.Redis(host="127.0.0.1", port=6379, socket_connect_timeout=0.2)
            self.assertIsNone(client.get("user:9:unread_count"))
        except Exception:
            # Live create_user_notification swallows Redis errors; no durable Redis effect exists here.
            self.assertEqual(sync.query(Notification).count(), 1)
        engine.dispose()

    async def test_outbox_failure_after_inner_commit_keeps_notification(self):
        engine, sync = _make_store()
        _seed_overtime(sync)
        db = DurableSession(sync, fail_outbox=True)
        user = _user()
        started = _patches()
        for item in started:
            item.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "outbox enqueue failed"):
                await status_service.transition_user_account_status(db, user, UserAccountStatus.INACTIVE)
        finally:
            for item in started:
                item.stop()
        self.assertEqual(user.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 0)
        self.assertEqual(sync.query(OfferRequest).one().result_status, OfferRequestStatus.OVERTIME_INVALIDATED)
        engine.dispose()

    async def test_handler_denied_paths_do_not_write(self):
        engine, sync = _make_store()
        _seed_overtime(sync)
        protected = _user(role=UserRole.SUPER_ADMIN)
        db = DurableSession(sync)
        db.loaded_user = protected
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            await handle_user_toggle_account_status(_callback(), user=None)
            await handle_user_toggle_account_status(
                _callback(),
                user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )
        self.assertEqual(sync.query(Notification).count(), 0)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 0)
        self.assertEqual(sync.query(OfferRequest).one().result_status, OfferRequestStatus.OVERTIME_QUEUED)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(protected.account_status, UserAccountStatus.ACTIVE)
        engine.dispose()

    async def test_handler_uses_live_transition_then_may_commit_again(self):
        engine, sync = _make_store()
        _seed_overtime(sync)
        target = _user()
        db = DurableSession(sync)
        db.loaded_user = target
        source = inspect.getsource(handle_user_toggle_account_status)
        self.assertIn("transition_user_account_status", source)
        self.assertIn("session.commit", source)
        started = _patches()
        for item in started:
            item.start()
        try:
            with patch("core.admin_authority.current_server", return_value="iran"), patch(
                "bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)
            ), patch(
                "bot.handlers.admin_users.get_user_profile_text",
                new=AsyncMock(return_value="profile"),
            ), patch(
                "bot.handlers.admin_users.get_user_settings_keyboard",
                return_value="KB",
            ), patch(
                "bot.handlers.admin_users.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ):
                await handle_user_toggle_account_status(_callback(), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        finally:
            for item in started:
                item.stop()
        self.assertEqual(target.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 1)
        self.assertGreaterEqual(db.commit_count, 1)
        engine.dispose()

    def test_account_status_contract_records_inner_commit_order(self):
        payload = _load_json("scenario-records.json")
        seeds = {item["behavior_id"]: item for item in _load_json("surface-bot.json")["entries"]}
        account = seeds[ACCOUNT_SEED]
        block = seeds[BLOCK_SEED]
        self.assertEqual(account["operation_kind"], "ACCOUNT_STATUS_TRANSITION")
        self.assertEqual(block["operation_kind"], "TOGGLE_MUTATION")
        contract = next(
            item
            for item in payload["behavior_contracts"]
            if item["contract_id"] == "BC-ADMIN-ACCOUNT_STATUS_TRANSITION-V1"
        )
        talk = json.dumps(contract, ensure_ascii=False).lower()
        self.assertIn("create_user_notification", talk)
        self.assertIn("internal commit", talk)
        self.assertIn("outbox", talk)
        self.assertIn("overtime", talk)
        self.assertNotIn("service writes then handler commits", talk)
        self.assertEqual(contract["events_outbox"]["status"], "APPLICABLE")
        self.assertEqual(contract["ordering"]["status"], "APPLICABLE")
        self.assertIn("tests/test_p1_00_characterization_admin_account_status.py", account["test_refs"])
        toggle = next(
            item for item in payload["behavior_contracts"] if item["contract_id"] == "BC-ADMIN-TOGGLE_MUTATION-V1"
        )
        self.assertNotIn("handle_user_toggle_account_status", json.dumps(toggle, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
