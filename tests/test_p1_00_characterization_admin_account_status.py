"""HIGH-B: account-status uses the live service against a mapped session-bound User."""

from __future__ import annotations

import inspect
import json
import socket
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

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
from models.user import UserRole as ModelUserRole

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ACCOUNT_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-TOGGLE-ACCOUNT-STATUS-L1134"
BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"
LOCAL_REDIS_HOSTS = {"127.0.0.1", "localhost", "::1"}


class IsolatedBase(DeclarativeBase):
    pass


class MappedAccountUser(IsolatedBase):
    """Equivalent ORM mapping of durable User status fields on isolated sqlite."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    account_name = Column(String, nullable=False, default="ali")
    role = Column(
        Enum(ModelUserRole, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=ModelUserRole.STANDARD,
    )
    account_status = Column(
        Enum(UserAccountStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=UserAccountStatus.ACTIVE,
    )
    deactivated_at = Column(DateTime, nullable=True)
    messenger_grace_expires_at = Column(DateTime, nullable=True)
    messenger_blocked_at = Column(DateTime, nullable=True)
    telegram_id = Column(Integer, nullable=True)
    sync_version = Column(Integer, nullable=False, default=1)
    trading_restricted_until = Column(DateTime, nullable=True)
    max_daily_trades = Column(Integer, nullable=True)
    max_active_commodities = Column(Integer, nullable=True)
    max_daily_requests = Column(Integer, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    can_block_users = Column(Boolean, default=True)
    max_blocked_users = Column(Integer, default=5)


class IsolatedRedis:
    constructed: list[dict] = []

    def __init__(self, *args, **kwargs):
        IsolatedRedis.constructed.append({"args": args, "kwargs": kwargs, "connected": False})

    async def incr(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def publish(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def get(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def close(self):
        return None


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
    """Async adapter over an isolated sqlite session. Does not mock product SQL commits."""

    def __init__(self, sync_session, *, fail_outbox=False):
        self._session = sync_session
        self.fail_outbox = fail_outbox
        self.commit_count = 0
        self.wakeup_attempts = 0

    async def execute(self, statement, params=None):
        blob = f"{statement} {getattr(statement, 'text', '')}"
        if "pg_notify" in blob.lower():
            self.wakeup_attempts += 1
            return SimpleNamespace()
        if "FROM users" in blob and "notifications" not in blob.lower():
            loaded = self._session.get(MappedAccountUser, 9)
            return SimpleNamespace(scalar_one_or_none=lambda: loaded)
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
    IsolatedBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for name in ("telegram_delivery_jobs", "offers", "trades", "customer_relations"):
            conn.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
    Notification.__table__.create(engine)
    TelegramNotificationOutbox.__table__.create(engine)
    OfferRequest.__table__.create(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    sync = Session()
    return engine, Session, sync


def _persist_user(
    sync,
    *,
    status=UserAccountStatus.ACTIVE,
    role=ModelUserRole.STANDARD,
    telegram_id=91,
    deactivated_at=None,
):
    if status == UserAccountStatus.INACTIVE and deactivated_at is None:
        deactivated_at = datetime(2026, 5, 16, 12, 0, 0)
    row = MappedAccountUser(
        id=9,
        account_name="ali",
        role=role,
        account_status=status,
        telegram_id=telegram_id,
        deactivated_at=deactivated_at,
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
        sync_version=3,
        is_deleted=False,
        can_block_users=True,
        max_blocked_users=5,
    )
    sync.add(row)
    sync.commit()
    return row


def _reload_user(Session, user_id=9):
    fresh = Session()
    return fresh.get(MappedAccountUser, user_id), fresh


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


def _service_patches():
    return [
        patch("core.services.user_account_status_service.current_server", return_value="iran"),
        patch("core.services.offer_overtime_request_service.current_server", return_value="iran"),
        patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=30)),
        ),
        patch(
            "core.services.user_account_status_service._build_activation_join_line",
            new=AsyncMock(return_value=None),
        ),
    ]


def _callback():
    return SimpleNamespace(
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
        data="user_toggle_account_status_9",
    )


@contextmanager
def _hermetic_network():
    IsolatedRedis.constructed.clear()
    blocked: list[tuple[str, int]] = []
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2:
            host = str(address[0])
            port = int(address[1])
            if port == 6379 and host in LOCAL_REDIS_HOSTS:
                blocked.append((host, port))
                raise OSError("hermetic: localhost redis blocked")
        return real_connect(self, address)

    started = [
        patch("redis.asyncio.Redis", IsolatedRedis),
        patch("redis.Redis", IsolatedRedis),
        patch("core.utils.redis.Redis", IsolatedRedis),
        patch("core.utils.publish_user_event", new=AsyncMock()),
        patch("core.web_push.schedule_notification_web_push", return_value=None),
        patch.object(socket.socket, "connect", guarded_connect),
    ]
    for item in started:
        item.start()
    try:
        yield {"blocked": blocked, "redis_constructed": IsolatedRedis.constructed}
    finally:
        for item in reversed(started):
            item.stop()


def _assert_no_localhost_redis(test, probe):
    test.assertEqual(probe["blocked"], [])
    for item in probe["redis_constructed"]:
        test.assertFalse(item.get("connected"))
        kwargs = item.get("kwargs") or {}
        host = kwargs.get("host")
        port = kwargs.get("port")
        if host in LOCAL_REDIS_HOSTS and port in {6379, None}:
            test.fail(f"live Redis constructor reached localhost: {item}")


class P100AdminAccountStatusCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_deactivation_persists_mapped_user_after_inner_commit(self):
        engine, Session, sync = _make_store()
        _seed_overtime(sync)
        user = _persist_user(sync)
        db = DurableSession(sync)
        started = _service_patches()
        for item in started:
            item.start()
        try:
            with _hermetic_network() as probe:
                before_blocked = list(probe["blocked"])
                result = await status_service.transition_user_account_status(
                    db, user, UserAccountStatus.INACTIVE
                )
                after_blocked = list(probe["blocked"])
                _assert_no_localhost_redis(self, probe)
        finally:
            for item in started:
                item.stop()
        self.assertEqual(before_blocked, [])
        self.assertEqual(after_blocked, [])
        self.assertTrue(result.changed)
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

        reloaded, fresh = _reload_user(Session)
        self.assertIsInstance(reloaded, MappedAccountUser)
        self.assertEqual(reloaded.account_status, UserAccountStatus.INACTIVE)
        self.assertIsNotNone(reloaded.deactivated_at)
        self.assertIsNotNone(reloaded.messenger_grace_expires_at)
        self.assertIsNone(reloaded.messenger_blocked_at)

        retry_db = DurableSession(fresh)
        retry = await status_service.transition_user_account_status(
            retry_db, reloaded, UserAccountStatus.INACTIVE
        )
        self.assertFalse(retry.changed)
        self.assertEqual(fresh.query(Notification).count(), 1)
        self.assertEqual(fresh.query(TelegramNotificationOutbox).count(), 1)
        self.assertEqual(retry_db.commit_count, 0)
        again, later = _reload_user(Session)
        self.assertEqual(again.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(later.query(Notification).count(), 1)
        fresh.close()
        later.close()
        engine.dispose()

    async def test_activation_persists_mapped_user_after_inner_commit(self):
        engine, Session, sync = _make_store()
        user = _persist_user(sync, status=UserAccountStatus.INACTIVE)
        db = DurableSession(sync)
        started = _service_patches()
        for item in started:
            item.start()
        try:
            with _hermetic_network() as probe:
                result = await status_service.transition_user_account_status(
                    db, user, UserAccountStatus.ACTIVE
                )
                _assert_no_localhost_redis(self, probe)
        finally:
            for item in started:
                item.stop()
        self.assertTrue(result.changed)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(Notification).one().level, NotificationLevel.SUCCESS)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 1)
        reloaded, fresh = _reload_user(Session)
        self.assertEqual(reloaded.account_status, UserAccountStatus.ACTIVE)
        self.assertIsNone(reloaded.deactivated_at)
        self.assertIsNone(reloaded.messenger_grace_expires_at)
        self.assertIsNone(reloaded.messenger_blocked_at)

        retry_db = DurableSession(fresh)
        retry = await status_service.transition_user_account_status(
            retry_db, reloaded, UserAccountStatus.ACTIVE
        )
        self.assertFalse(retry.changed)
        self.assertEqual(fresh.query(Notification).count(), 1)
        self.assertEqual(fresh.query(TelegramNotificationOutbox).count(), 1)
        self.assertEqual(retry_db.commit_count, 0)
        fresh.close()
        engine.dispose()

    async def test_redis_and_localhost_are_isolated_before_and_after_transition(self):
        engine, Session, sync = _make_store()
        _seed_overtime(sync)
        user = _persist_user(sync)
        db = DurableSession(sync)
        started = _service_patches()
        for item in started:
            item.start()
        try:
            with _hermetic_network() as probe:
                self.assertEqual(probe["blocked"], [])
                await status_service.transition_user_account_status(
                    db, user, UserAccountStatus.INACTIVE
                )
                self.assertEqual(probe["blocked"], [])
                _assert_no_localhost_redis(self, probe)
        finally:
            for item in started:
                item.stop()
        reloaded, fresh = _reload_user(Session)
        self.assertEqual(reloaded.account_status, UserAccountStatus.INACTIVE)
        self.assertEqual(fresh.query(Notification).count(), 1)
        fresh.close()
        engine.dispose()

    async def test_outbox_failure_after_inner_commit_keeps_status_and_notification(self):
        engine, Session, sync = _make_store()
        _seed_overtime(sync)
        user = _persist_user(sync)
        db = DurableSession(sync, fail_outbox=True)
        started = _service_patches()
        for item in started:
            item.start()
        try:
            with _hermetic_network() as probe:
                with self.assertRaisesRegex(RuntimeError, "outbox enqueue failed"):
                    await status_service.transition_user_account_status(
                        db, user, UserAccountStatus.INACTIVE
                    )
                _assert_no_localhost_redis(self, probe)
        finally:
            for item in started:
                item.stop()
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 0)
        self.assertEqual(sync.query(OfferRequest).one().result_status, OfferRequestStatus.OVERTIME_INVALIDATED)
        reloaded, fresh = _reload_user(Session)
        self.assertEqual(reloaded.account_status, UserAccountStatus.INACTIVE)
        self.assertIsNotNone(reloaded.deactivated_at)
        self.assertEqual(fresh.query(Notification).count(), 1)
        self.assertEqual(fresh.query(TelegramNotificationOutbox).count(), 0)
        fresh.close()
        engine.dispose()

    async def test_handler_denied_paths_do_not_write(self):
        engine, Session, sync = _make_store()
        _seed_overtime(sync)
        _persist_user(sync, role=ModelUserRole.SUPER_ADMIN)
        db = DurableSession(sync)
        with _hermetic_network() as probe:
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
                await handle_user_toggle_account_status(_callback(), user=None)
                await handle_user_toggle_account_status(
                    _callback(),
                    user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
                )
            _assert_no_localhost_redis(self, probe)
        self.assertEqual(sync.query(Notification).count(), 0)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 0)
        self.assertEqual(sync.query(OfferRequest).one().result_status, OfferRequestStatus.OVERTIME_QUEUED)
        self.assertEqual(db.commit_count, 0)
        reloaded, fresh = _reload_user(Session)
        self.assertEqual(reloaded.account_status, UserAccountStatus.ACTIVE)
        fresh.close()
        engine.dispose()

    async def test_handler_uses_live_transition_then_may_commit_again(self):
        engine, Session, sync = _make_store()
        _seed_overtime(sync)
        _persist_user(sync)
        db = DurableSession(sync)
        source = inspect.getsource(handle_user_toggle_account_status)
        self.assertIn("transition_user_account_status", source)
        self.assertIn("session.commit", source)
        started = _service_patches()
        for item in started:
            item.start()
        try:
            with _hermetic_network() as probe, patch(
                "core.admin_authority.current_server", return_value="iran"
            ), patch(
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
                await handle_user_toggle_account_status(
                    _callback(), user=SimpleNamespace(role=UserRole.SUPER_ADMIN)
                )
                _assert_no_localhost_redis(self, probe)
        finally:
            for item in started:
                item.stop()
        self.assertEqual(sync.query(Notification).count(), 1)
        self.assertEqual(sync.query(TelegramNotificationOutbox).count(), 1)
        self.assertGreaterEqual(db.commit_count, 1)
        reloaded, fresh = _reload_user(Session)
        self.assertEqual(reloaded.account_status, UserAccountStatus.INACTIVE)
        fresh.close()
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
        self.assertIn("session-bound", talk)
        self.assertNotIn("service writes then handler commits", talk)
        self.assertNotIn("remain in memory", talk)
        self.assertEqual(contract["events_outbox"]["status"], "APPLICABLE")
        self.assertEqual(contract["ordering"]["status"], "APPLICABLE")
        self.assertIn("tests/test_p1_00_characterization_admin_account_status.py", account["test_refs"])
        toggle = next(
            item for item in payload["behavior_contracts"] if item["contract_id"] == "BC-ADMIN-TOGGLE_MUTATION-V1"
        )
        self.assertNotIn("handle_user_toggle_account_status", json.dumps(toggle, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
