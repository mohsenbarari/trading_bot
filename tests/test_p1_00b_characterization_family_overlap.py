"""P1-00b: durable same-identity overlap and child-process reload per family."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.notifications import mark_notification_read
from core.enums import MessageType, NotificationCategory, NotificationLevel, UserAccountStatus, UserRole
from core.offer_source import OfferSourceSurface
from core.services import session_service
from core.services import user_account_status_service as identity_status
from core.services.chat_service import persist_sent_direct_message
from core.services.market_schedule_service import MarketScheduleEvaluation
from core.services.market_transition_service import apply_market_schedule_transition
from core.services.offer_creation_service import (
    OfferCreationCommand,
    OfferCreationQuotaPolicy,
    create_authoritative_offer_with_outcome,
)
from core.services.session_service import handle_login_session, hash_token
from core.services.telegram_admin_broadcast_service import create_telegram_admin_broadcast
from core.services.telegram_notification_outbox_service import (
    claim_next_telegram_notification_outbox,
)
from core.services.trade_webapp_delivery_service import deliver_webapp_trade_notification
from core.sync_worker import mark_change_log_delivered
from core.utils import utc_now
from models.change_log import ChangeLog
from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox
from models.market_runtime_state import MarketRuntimeState
from models.message import Message
from models.notification import Notification
from models.offer import Offer, OfferType
from models.session import Platform, UserSession
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastReceipt,
)
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.trade_delivery_receipt import TradeDeliveryReceipt

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
CHILD_FAMILY = "P1_00B_FAMILY"
CHILD_DB = "P1_00B_RESTART_DB"
SAME_ADMIN_KEY = "p1-00b-same-admin-key"
SAME_OFFER_KEY = "p1-00b-same-offer-key"
SAME_AUTH_TOKEN = "p1-00b-same-user-token"
FAMILIES = (
    "AUTH",
    "IDENTITY",
    "OFFER",
    "TRADE",
    "MARKET",
    "NOTIFY",
    "MSG",
    "TG",
    "ADMIN",
    "SYNC",
)


class YieldingSession:
    """Async adapter: yield after first lookup and before commit."""

    def __init__(self, sync_session, *, after_lookup=None, before_commit=None, event_log=None, worker_id=None):
        self._session = sync_session
        self.after_lookup = after_lookup
        self.before_commit = before_commit
        self.event_log = event_log
        self.worker_id = worker_id
        self._lookups = 0

    def _note(self, kind):
        if self.event_log is not None:
            self.event_log.append((self.worker_id, kind, self._lookups))

    async def execute(self, statement, params=None):
        result = self._session.execute(statement)
        self._lookups += 1
        self._note("lookup")
        if self.after_lookup and self._lookups == 1:
            await self.after_lookup()
        return result

    async def scalar(self, statement, params=None):
        result = await self.execute(statement, params)
        try:
            return result.scalar()
        except Exception:
            return result.scalar_one_or_none() if hasattr(result, "scalar_one_or_none") else None

    async def get(self, model, ident, **kwargs):
        loaded = self._session.get(model, ident)
        self._lookups += 1
        self._note("lookup")
        if self.after_lookup and self._lookups == 1:
            await self.after_lookup()
        return loaded

    def add(self, obj):
        self._session.add(obj)

    def add_all(self, objs):
        self._session.add_all(objs)

    async def flush(self):
        self._session.flush()

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj)

    async def commit(self):
        self._note("pre_commit")
        if self.before_commit:
            await self.before_commit()
        self._note("commit")
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    def close(self):
        self._session.close()

    def get_bind(self):
        return self._session.get_bind()

    @property
    def bind(self):
        return self._session.get_bind()

    async def delete(self, obj):
        self._session.delete(obj)

    def begin(self):
        session = self

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class _EventLog:
    def __init__(self, record):
        self._record = record

    def append(self, item):
        self._record(item)


class _SessionBox:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _engine(path: str):
    engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    def _on_connect(conn, _rec):
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

    event.listen(engine, "connect", _on_connect)
    return engine


def _run_overlap(worker_coro):
    """Two OS threads so sqlite busy-wait cannot freeze the peer's commit."""
    lookup_barrier = threading.Barrier(2, timeout=45)
    events: list = []
    events_lock = threading.Lock()
    results: dict = {}
    errors: dict = {}

    def _record(item):
        with events_lock:
            events.append(item)

    def thread_main(wid: str):
        async def after_lookup():
            lookup_barrier.wait()

        try:
            results[wid] = asyncio.run(worker_coro(wid, after_lookup, _record))
        except Exception as exc:
            errors[wid] = exc

    first = threading.Thread(target=thread_main, args=("a",))
    second = threading.Thread(target=thread_main, args=("b",))
    first.start()
    second.start()
    first.join(60)
    second.join(60)
    if errors:
        raise next(iter(errors.values()))
    return results["a"], results["b"], events


def _exec_sql(engine, sql: str) -> None:
    with engine.begin() as conn:
        for statement in sql.strip().split(";"):
            if statement.strip():
                conn.execute(text(statement))


def _assert_overlap(events, workers=("a", "b")) -> None:
    first_lookup = {}
    first_commit_index = None
    for index, (wid, kind, _count) in enumerate(events):
        if kind == "lookup" and wid not in first_lookup:
            first_lookup[wid] = index
        if kind == "commit" and first_commit_index is None:
            first_commit_index = index
    assert set(first_lookup) == set(workers)
    assert first_commit_index is not None
    assert max(first_lookup.values()) < first_commit_index


def _child(family: str, db_path: str) -> dict:
    env = os.environ.copy()
    env[CHILD_FAMILY] = family
    env[CHILD_DB] = db_path
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
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


AUTH_SQL = """
CREATE TABLE user_sessions (
    id CHAR(36) PRIMARY KEY,
    user_id INTEGER NOT NULL,
    device_name VARCHAR(255) NOT NULL,
    device_ip VARCHAR(45),
    home_server VARCHAR(16) NOT NULL,
    platform VARCHAR(32) NOT NULL,
    refresh_token_hash VARCHAR(255),
    is_primary BOOLEAN NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_active_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME
);
"""

OFFER_SQL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, offer_overtime_minutes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE commodities (id INTEGER PRIMARY KEY);
CREATE TABLE offers (
    id INTEGER PRIMARY KEY,
    offer_public_id VARCHAR(40) NOT NULL UNIQUE,
    version_id INTEGER NOT NULL DEFAULT 1,
    user_id INTEGER,
    actor_user_id INTEGER,
    home_server VARCHAR(16) NOT NULL,
    offer_type VARCHAR(16) NOT NULL,
    settlement_type VARCHAR(32) NOT NULL DEFAULT 'cash',
    commodity_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price INTEGER NOT NULL,
    exclude_from_competitive_price BOOLEAN NOT NULL DEFAULT 0,
    price_warning_type VARCHAR(64),
    expire_reason VARCHAR(32),
    expired_at DATETIME,
    expired_by_user_id INTEGER,
    expired_by_actor_user_id INTEGER,
    expire_source_surface VARCHAR(32),
    expire_source_server VARCHAR(16),
    remaining_quantity INTEGER,
    is_wholesale BOOLEAN NOT NULL DEFAULT 1,
    lot_sizes JSON,
    original_lot_sizes JSON,
    overtime_minutes_snapshot INTEGER NOT NULL DEFAULT 0,
    overtime_trade_committed BOOLEAN NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL,
    notes VARCHAR(200),
    channel_message_id INTEGER,
    republished_offer_id INTEGER,
    republished_from_offer_public_id VARCHAR(40),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    home_site VARCHAR(16),
    sync_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key VARCHAR(64) UNIQUE,
    idempotency_fingerprint_version INTEGER,
    idempotency_fingerprint VARCHAR(128),
    archived BOOLEAN NOT NULL DEFAULT 0
);
INSERT INTO users (id, offer_overtime_minutes) VALUES (41, 0);
INSERT INTO commodities (id) VALUES (1);
"""

ADMIN_SQL = """
CREATE TABLE telegram_admin_broadcasts (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    content_kind VARCHAR(16) NOT NULL,
    telegram_media_file_id VARCHAR(1024),
    telegram_media_file_unique_id VARCHAR(256),
    media_duration_seconds INTEGER,
    media_width INTEGER,
    media_height INTEGER,
    media_file_size INTEGER,
    creation_key VARCHAR(64) UNIQUE,
    created_by_id INTEGER NOT NULL,
    audience_type VARCHAR(16) NOT NULL,
    target_groups JSON,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL,
    queued_at DATETIME,
    started_at DATETIME,
    completed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    queue_last_handed_off_at DATETIME
);
CREATE TABLE telegram_admin_broadcast_receipts (
    id INTEGER PRIMARY KEY,
    broadcast_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    telegram_id_at_enqueue INTEGER,
    dedupe_key VARCHAR(192) UNIQUE,
    status VARCHAR(32) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

NOTIFY_SQL = """
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level VARCHAR(16) NOT NULL DEFAULT 'info',
    category VARCHAR(32) NOT NULL DEFAULT 'system',
    dedupe_key VARCHAR(180),
    extra_payload JSON
);
"""

IDENTITY_SQL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    account_name VARCHAR(64) NOT NULL,
    role VARCHAR(32) NOT NULL,
    account_status VARCHAR(32) NOT NULL,
    deactivated_at DATETIME,
    messenger_grace_expires_at DATETIME,
    messenger_blocked_at DATETIME,
    telegram_id INTEGER,
    sync_version INTEGER NOT NULL DEFAULT 1,
    trading_restricted_until DATETIME,
    max_daily_trades INTEGER,
    max_active_commodities INTEGER,
    max_daily_requests INTEGER,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    can_block_users BOOLEAN DEFAULT 1,
    max_blocked_users INTEGER DEFAULT 5
);
CREATE TABLE telegram_delivery_jobs (id INTEGER PRIMARY KEY);
CREATE TABLE offers (id INTEGER PRIMARY KEY);
CREATE TABLE trades (id INTEGER PRIMARY KEY);
CREATE TABLE customer_relations (id INTEGER PRIMARY KEY);
"""

TRADE_SQL = """
CREATE TABLE trades (id INTEGER PRIMARY KEY);
CREATE TABLE offers (id INTEGER PRIMARY KEY);
CREATE TABLE notifications (id INTEGER PRIMARY KEY);
CREATE TABLE trade_delivery_receipts (
    id INTEGER PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    dedupe_key VARCHAR(192) NOT NULL UNIQUE,
    trade_id INTEGER,
    trade_number INTEGER NOT NULL,
    offer_id INTEGER,
    recipient_user_id INTEGER NOT NULL,
    recipient_role VARCHAR(32) NOT NULL,
    channel VARCHAR(16) NOT NULL,
    destination_server VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL,
    reason VARCHAR(120),
    notification_id INTEGER,
    telegram_message_id INTEGER,
    audit_payload JSON,
    worker_id VARCHAR(64),
    lease_until DATETIME,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at DATETIME,
    last_error TEXT,
    last_error_class VARCHAR(120),
    event_created_at DATETIME,
    sent_at DATETIME,
    terminal_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    UNIQUE(event_type, trade_number, recipient_user_id, channel)
);
"""

TG_SQL = """
CREATE TABLE users (id INTEGER PRIMARY KEY);
CREATE TABLE telegram_notification_outbox (
    id INTEGER PRIMARY KEY,
    dedupe_key VARCHAR(192) NOT NULL UNIQUE,
    source_type VARCHAR(80) NOT NULL,
    source_id VARCHAR(120),
    recipient_user_id INTEGER,
    telegram_id_at_enqueue INTEGER,
    telegram_id_at_send INTEGER,
    text TEXT NOT NULL,
    parse_mode VARCHAR(32),
    status VARCHAR(32) NOT NULL,
    reason VARCHAR(120),
    last_error_class VARCHAR(64),
    last_error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    worker_id VARCHAR(64),
    lease_until DATETIME,
    next_retry_at DATETIME,
    queue_job_id INTEGER,
    queue_handed_off_at DATETIME,
    telegram_message_id INTEGER,
    sent_at DATETIME,
    terminal_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);
"""

MARKET_SQL = """
CREATE TABLE market_runtime_state (
    id INTEGER PRIMARY KEY,
    is_open BOOLEAN NOT NULL DEFAULT 0,
    active_web_notice_visible BOOLEAN NOT NULL DEFAULT 0,
    offers_since_last_open INTEGER NOT NULL DEFAULT 0,
    last_transition_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);
"""

SYNC_SQL = """
CREATE TABLE change_log (
    id INTEGER PRIMARY KEY,
    operation VARCHAR(10) NOT NULL,
    table_name VARCHAR(50) NOT NULL,
    record_id INTEGER NOT NULL,
    data JSON NOT NULL,
    timestamp DATETIME NOT NULL,
    hash VARCHAR(64),
    synced BOOLEAN DEFAULT 0,
    verified BOOLEAN DEFAULT 0,
    delivery_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_delivery_error VARCHAR(120),
    last_delivery_attempt_at DATETIME,
    next_delivery_attempt_at DATETIME,
    quarantined_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

MSG_SQL = """
CREATE TABLE users (id INTEGER PRIMARY KEY);
CREATE TABLE chats (id INTEGER PRIMARY KEY);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER,
    sender_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    reply_to_message_id INTEGER,
    forwarded_from_id INTEGER,
    forwarded_from_name_override VARCHAR(255),
    content TEXT NOT NULL,
    message_type VARCHAR(32) NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT 0,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);
"""


def _offer_command():
    return OfferCreationCommand(
        source_surface=OfferSourceSurface.WEBAPP,
        owner_user_id=41,
        actor_user_id=41,
        offer_type=OfferType.SELL,
        commodity_id=1,
        quantity=10,
        price=1000000,
        is_wholesale=True,
        idempotency_key=SAME_OFFER_KEY,
    )


class P100bFamilyOverlapCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_two_concurrent_commands_injects(self):
        """AUTH two_concurrent_commands: injects handle_login_session / create_session overlap."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "auth.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, AUTH_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed_db = YieldingSession(seed)
            user = SimpleNamespace(
                id=13,
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                max_sessions=1,
            )
            with patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch.object(
                session_service, "publish_session_revocation", new=AsyncMock()
            ), patch.object(session_service, "blacklist_session", new=AsyncMock()):
                seeded = await handle_login_session(seed_db, user, SAME_AUTH_TOKEN, "web", home_server="foreign")
            self.assertEqual(seeded["action"], "session_created")
            seed_db.close()

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    with patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch.object(
                        session_service, "publish_session_revocation", new=AsyncMock()
                    ), patch.object(session_service, "blacklist_session", new=AsyncMock()):
                        return await handle_login_session(
                            db,
                            user,
                            SAME_AUTH_TOKEN,
                            "web",
                            suspended_refresh_token=SAME_AUTH_TOKEN,
                            home_server="foreign",
                        )
                finally:
                    db.close()

            first, second, events = _run_overlap(worker)
            engine.dispose()
            _assert_overlap(events)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM user_sessions WHERE refresh_token_hash=?",
                    (hash_token(SAME_AUTH_TOKEN),),
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual({first["action"], second["action"]}, {"session_created"})

    async def test_auth_process_restart_injects(self):
        """AUTH process_restart: child interpreter reloads persisted same-user session."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "auth-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, AUTH_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            db = YieldingSession(Session())
            user = SimpleNamespace(
                id=13,
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                max_sessions=1,
            )
            try:
                with patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch.object(
                    session_service, "publish_session_revocation", new=AsyncMock()
                ), patch.object(session_service, "blacklist_session", new=AsyncMock()):
                    created = await handle_login_session(db, user, SAME_AUTH_TOKEN, "web", home_server="foreign")
            finally:
                db.close()
                engine.dispose()
            self.assertEqual(created["action"], "session_created")
            payload = _child("AUTH", db_path)
            self.assertTrue(payload["duplicate_replay"])
            self.assertEqual(payload["row_count"], 1)

    async def test_identity_two_concurrent_commands_injects(self):
        """IDENTITY two_concurrent_commands: injects two transition_user_account_status calls."""
        from tests.test_p1_00_characterization_admin_account_status import (
            DurableSession,
            IsolatedBase,
            MappedAccountUser,
            _persist_user,
            _reload_user,
            _seed_overtime,
            _service_patches,
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "identity.sqlite")
            engine = _engine(db_path)
            IsolatedBase.metadata.create_all(engine)
            with engine.begin() as conn:
                for name in ("telegram_delivery_jobs", "offers", "trades", "customer_relations"):
                    conn.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            from models.notification import Notification
            from models.offer_request import OfferRequest
            from models.telegram_notification_outbox import TelegramNotificationOutbox

            Notification.__table__.create(engine, checkfirst=True)
            TelegramNotificationOutbox.__table__.create(engine, checkfirst=True)
            OfferRequest.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            _seed_overtime(sync)
            _persist_user(sync)
            sync.close()

            async def worker(wid: str, after_lookup, record):
                class OverlapSession(DurableSession):
                    def __init__(self, sync_session):
                        super().__init__(sync_session)
                        self.worker_id = wid
                        self._lookups = 0

                    async def execute(self, statement, params=None):
                        self._lookups += 1
                        if self._lookups == 1:
                            with self._session.no_autoflush:
                                result = await DurableSession.execute(self, statement, params)
                            record((self.worker_id, "lookup", self._lookups))
                            await after_lookup()
                            return result
                        result = await DurableSession.execute(self, statement, params)
                        record((self.worker_id, "lookup", self._lookups))
                        return result

                    async def commit(self):
                        record((self.worker_id, "pre_commit", self._lookups))
                        await super().commit()
                        record((self.worker_id, "commit", self._lookups))

                    def close(self):
                        self._session.close()

                started = _service_patches()
                for item in started:
                    item.start()
                fresh = Session()
                user = fresh.get(MappedAccountUser, 9)
                db = OverlapSession(fresh)
                try:
                    result = await identity_status.transition_user_account_status(
                        db, user, UserAccountStatus.INACTIVE
                    )
                    await db.commit()
                    return result
                finally:
                    for item in started:
                        item.stop()
                    db.close()

            first, second, events = _run_overlap(worker)
            _assert_overlap(events)
            self.assertTrue(first.changed or second.changed)
            reloaded, later = _reload_user(Session)
            self.assertEqual(reloaded.account_status, UserAccountStatus.INACTIVE)
            later.close()
            engine.dispose()

    async def test_identity_process_restart_injects(self):
        """IDENTITY process_restart: injects transition_user_account_status replay."""
        from tests.test_p1_00_characterization_admin_account_status import (
            DurableSession,
            IsolatedBase,
            MappedAccountUser,
            _persist_user,
            _seed_overtime,
            _service_patches,
        )
        from models.notification import Notification
        from models.offer_request import OfferRequest
        from models.telegram_notification_outbox import TelegramNotificationOutbox

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "identity-restart.sqlite")
            engine = _engine(db_path)
            IsolatedBase.metadata.create_all(engine)
            with engine.begin() as conn:
                for name in ("telegram_delivery_jobs", "offers", "trades", "customer_relations"):
                    conn.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
            Notification.__table__.create(engine, checkfirst=True)
            TelegramNotificationOutbox.__table__.create(engine, checkfirst=True)
            OfferRequest.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            _seed_overtime(sync)
            user = _persist_user(sync)
            db = DurableSession(sync)
            started = _service_patches()
            for item in started:
                item.start()
            try:
                first = await identity_status.transition_user_account_status(
                    db, user, UserAccountStatus.INACTIVE
                )
                await db.commit()
            finally:
                for item in started:
                    item.stop()
            self.assertTrue(first.changed)
            sync.close()
            engine.dispose()
            payload = _child("IDENTITY", db_path)
            self.assertFalse(payload["changed"])
            self.assertEqual(payload["account_status"], UserAccountStatus.INACTIVE.value)

    async def test_offer_two_concurrent_commands_injects(self):
        """OFFER two_concurrent_commands: injects create_authoritative_offer same-key overlap."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "offer.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, OFFER_SQL)
            CoinIntelligenceMarketOutbox.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            owner = SimpleNamespace(id=41, offer_overtime_minutes=0)
            policy = OfferCreationQuotaPolicy(max_active_offers=10, enforce_user_limits=False)

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    return await create_authoritative_offer_with_outcome(
                        db,
                        _offer_command(),
                        validate_market=False,
                        quota_policy=policy,
                    )
                except (IntegrityError, OperationalError):
                    await db.rollback()
                    return SimpleNamespace(created=False, offer=None)
                finally:
                    db.close()

            started = [
                patch(
                    "core.services.offer_creation_service._lock_offer_owner",
                    new=AsyncMock(return_value=owner),
                ),
                patch(
                    "core.services.customer_relation_service.enforce_customer_offer_limits_for_creation",
                    new=AsyncMock(),
                ),
                patch("core.user_counter_sync.increment_user_counters"),
            ]
            for item in started:
                item.start()
            try:
                first, second, events = _run_overlap(worker)
            finally:
                for item in started:
                    item.stop()
            engine.dispose()
            _assert_overlap(events)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM offers WHERE idempotency_key=?", (SAME_OFFER_KEY,)).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertTrue(first.created or second.created)

    async def test_offer_process_restart_injects(self):
        """OFFER process_restart: child interpreter reloads persisted same-key offer."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "offer-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, OFFER_SQL)
            CoinIntelligenceMarketOutbox.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            db = YieldingSession(Session())
            owner = SimpleNamespace(id=41, offer_overtime_minutes=0)
            policy = OfferCreationQuotaPolicy(max_active_offers=10, enforce_user_limits=False)
            try:
                with patch(
                    "core.services.offer_creation_service._lock_offer_owner",
                    new=AsyncMock(return_value=owner),
                ), patch(
                    "core.services.customer_relation_service.enforce_customer_offer_limits_for_creation",
                    new=AsyncMock(),
                ), patch(
                    "core.user_counter_sync.increment_user_counters",
                ):
                    created = await create_authoritative_offer_with_outcome(
                        db,
                        _offer_command(),
                        validate_market=False,
                        quota_policy=policy,
                    )
            finally:
                db.close()
                engine.dispose()
            self.assertTrue(created.created)
            payload = _child("OFFER", db_path)
            self.assertTrue(payload["duplicate_replay"])
            self.assertEqual(payload["row_count"], 1)

    async def test_admin_two_concurrent_commands_injects(self):
        """ADMIN two_concurrent_commands: injects create_telegram_admin_broadcast creation_key."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "admin.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, ADMIN_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    result = await create_telegram_admin_broadcast(
                        db,
                        actor=actor,
                        content="hello",
                        audience_type=TelegramAdminBroadcastAudienceType.ALL,
                        creation_key=SAME_ADMIN_KEY,
                    )
                    await db.commit()
                    return result
                except (IntegrityError, OperationalError):
                    await db.rollback()
                    return None
                finally:
                    db.close()

            recipients = patch(
                "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
                new=AsyncMock(return_value=[]),
            )
            recipients.start()
            try:
                first, second, events = _run_overlap(worker)
            finally:
                recipients.stop()
            engine.dispose()
            _assert_overlap(events)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM telegram_admin_broadcasts WHERE creation_key=?",
                    (SAME_ADMIN_KEY,),
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertTrue(first is not None or second is not None)

    async def test_admin_process_restart_injects(self):
        """ADMIN process_restart: injects create_telegram_admin_broadcast replay."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "admin-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, ADMIN_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            db = YieldingSession(Session())
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
            try:
                with patch(
                    "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
                    new=AsyncMock(return_value=[]),
                ):
                    created = await create_telegram_admin_broadcast(
                        db,
                        actor=actor,
                        content="hello",
                        audience_type=TelegramAdminBroadcastAudienceType.ALL,
                        creation_key=SAME_ADMIN_KEY,
                    )
                    await db.commit()
            finally:
                db.close()
                engine.dispose()
            self.assertEqual(created.broadcast.creation_key, SAME_ADMIN_KEY)
            payload = _child("ADMIN", db_path)
            self.assertTrue(payload["duplicate_replay"])
            self.assertEqual(payload["row_count"], 1)

    async def test_notify_two_concurrent_commands_injects(self):
        """NOTIFY two_concurrent_commands: injects two mark_notification_read calls."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "notify.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, NOTIFY_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                Notification(
                    id=11,
                    user_id=5,
                    message="n",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
            )
            seed.commit()
            seed.close()
            user = SimpleNamespace(id=5)
            redis = SimpleNamespace(set=AsyncMock())

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    await mark_notification_read(11, current_user=user, db=db, redis=redis)
                    return True
                finally:
                    db.close()

            with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()):
                _first, _second, events = _run_overlap(worker)
            engine.dispose()
            _assert_overlap(events)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT is_read, COUNT(*) FROM notifications WHERE id=11").fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(row[1] if len(row) > 1 else 1, 1)

    async def test_notify_process_restart_injects(self):
        """NOTIFY process_restart: injects mark_notification_read already-read replay."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "notify-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, NOTIFY_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                Notification(
                    id=11,
                    user_id=5,
                    message="n",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
            )
            seed.commit()
            seed.close()
            db = YieldingSession(Session())
            try:
                with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync:
                    await mark_notification_read(11, current_user=SimpleNamespace(id=5), db=db, redis=SimpleNamespace())
                    self.assertEqual(sync.await_count, 1)
            finally:
                db.close()
                engine.dispose()
            payload = _child("NOTIFY", db_path)
            self.assertTrue(payload["already_read"])
            self.assertEqual(payload["commit_count"], 0)

    async def test_trade_two_concurrent_commands_injects(self):
        """TRADE two_concurrent_commands: injects deliver_webapp_trade_notification same identity."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "trade.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, TRADE_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    return await deliver_webapp_trade_notification(
                        db,
                        trade_number=3,
                        recipient_user_id=5,
                        message="done",
                        current_server="foreign",
                        publish_after_commit=False,
                    )
                except (IntegrityError, OperationalError):
                    await db.rollback()
                    return SimpleNamespace(status="loser")
                finally:
                    db.close()

            first, second, events = _run_overlap(worker)
            engine.dispose()
            _assert_overlap(events)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM trade_delivery_receipts").fetchone()[0]
            self.assertEqual(count, 1)
            statuses = {getattr(first, "status", None), getattr(second, "status", None)}
            self.assertTrue(statuses & {"queued_for_iran", "loser", "already_sent"})

    async def test_trade_process_restart_injects(self):
        """TRADE process_restart: injects deliver_webapp_trade_notification already sent/queued."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "trade-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, TRADE_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            db = YieldingSession(Session())
            try:
                first = await deliver_webapp_trade_notification(
                    db,
                    trade_number=3,
                    recipient_user_id=5,
                    message="done",
                    current_server="foreign",
                    publish_after_commit=False,
                )
            finally:
                db.close()
                engine.dispose()
            self.assertEqual(first.status, "queued_for_iran")
            payload = _child("TRADE", db_path)
            self.assertEqual(payload["row_count"], 1)
            self.assertTrue(payload["duplicate_replay"])

    async def test_tg_two_concurrent_commands_injects(self):
        """TG two_concurrent_commands: injects deliver_claimed_telegram_notification_outbox / claim busy."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "tg.sqlite")
            engine = _engine(db_path)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE telegram_delivery_jobs (id INTEGER PRIMARY KEY)"))
            TelegramNotificationOutbox.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                TelegramNotificationOutbox(
                    id=21,
                    dedupe_key="tg-same",
                    source_type="account_status",
                    text="hello",
                    status=TelegramNotificationOutboxStatus.PENDING,
                    recipient_user_id=5,
                    telegram_id_at_enqueue=91,
                )
            )
            seed.commit()
            seed.close()

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    claimed = await claim_next_telegram_notification_outbox(
                        db,
                        current_server="foreign",
                        lease_seconds=30,
                        worker_id=f"worker-{wid}",
                    )
                    await db.commit()
                    return claimed
                finally:
                    db.close()

            owner_patch = patch(
                "core.services.telegram_notification_outbox_service._assert_legacy_direct_delivery_owner"
            )
            owner_patch.start()
            try:
                first, second, events = _run_overlap(worker)
            finally:
                owner_patch.stop()
            engine.dispose()
            _assert_overlap(events)
            winners = [row for row in (first, second) if row is not None]
            self.assertGreaterEqual(len(winners), 1)
            with sqlite3.connect(db_path) as conn:
                status, worker = conn.execute(
                    "SELECT status, worker_id FROM telegram_notification_outbox WHERE id=21"
                ).fetchone()
            self.assertEqual(status, TelegramNotificationOutboxStatus.SENDING.value)
            self.assertIn(worker, {"worker-a", "worker-b"})

    async def test_tg_process_restart_injects(self):
        """TG process_restart: injects deliver_claimed_telegram_notification_outbox already sent."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "tg-restart.sqlite")
            engine = _engine(db_path)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE telegram_delivery_jobs (id INTEGER PRIMARY KEY)"))
            TelegramNotificationOutbox.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                TelegramNotificationOutbox(
                    id=21,
                    dedupe_key="tg-same",
                    source_type="account_status",
                    text="hello",
                    status=TelegramNotificationOutboxStatus.PENDING,
                    recipient_user_id=5,
                    telegram_id_at_enqueue=91,
                )
            )
            seed.commit()
            db = YieldingSession(seed)
            with patch(
                "core.services.telegram_notification_outbox_service._assert_legacy_direct_delivery_owner"
            ):
                claimed = await claim_next_telegram_notification_outbox(
                    db, current_server="foreign", lease_seconds=30, worker_id="worker-a"
                )
                await db.commit()
            self.assertIsNotNone(claimed)
            db.close()
            engine.dispose()
            payload = _child("TG", db_path)
            self.assertTrue(payload["already_claimed"])
            self.assertEqual(payload["row_count"], 1)

    async def test_market_two_concurrent_commands_injects(self):
        """MARKET two_concurrent_commands: injects apply_market_schedule_transition no-op overlap."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, MARKET_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(MarketRuntimeState(id=1, is_open=False, active_web_notice_visible=False, offers_since_last_open=0))
            seed.commit()
            seed.close()
            evaluation = MarketScheduleEvaluation(
                is_open=True,
                reason="open",
                timezone="Asia/Tehran",
                next_transition_at=None,
            )

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    return await apply_market_schedule_transition(db, evaluation)
                finally:
                    db.close()

            started = [
                patch("core.services.market_transition_service.current_server", return_value="iran"),
                patch(
                    "core.services.market_transition_service._acquire_market_runtime_lock",
                    new=AsyncMock(),
                ),
                patch(
                    "core.services.market_transition_service.reconcile_market_channel_notice_for_state",
                    new=AsyncMock(),
                ),
                patch("core.services.market_transition_service.publish_event_sync"),
            ]
            for item in started:
                item.start()
            try:
                first, second, events = _run_overlap(worker)
            finally:
                for item in started:
                    item.stop()
            engine.dispose()
            _assert_overlap(events)
            self.assertTrue(first.changed or second.changed)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT COUNT(*), is_open FROM market_runtime_state").fetchone()
            self.assertEqual(row[0], 1)
            self.assertEqual(int(row[1]), 1)

    async def test_market_process_restart_injects(self):
        """MARKET process_restart: injects apply_market_schedule_transition replay."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, MARKET_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(MarketRuntimeState(id=1, is_open=True, active_web_notice_visible=False, offers_since_last_open=0))
            seed.commit()
            db = YieldingSession(seed)
            evaluation = MarketScheduleEvaluation(
                is_open=True,
                reason="open",
                timezone="Asia/Tehran",
                next_transition_at=None,
            )
            with patch(
                "core.services.market_transition_service.current_server",
                return_value="iran",
            ), patch(
                "core.services.market_transition_service._acquire_market_runtime_lock",
                new=AsyncMock(),
            ):
                first = await apply_market_schedule_transition(db, evaluation)
            self.assertFalse(first.changed)
            db.close()
            engine.dispose()
            payload = _child("MARKET", db_path)
            self.assertFalse(payload["changed"])
            self.assertEqual(payload["row_count"], 1)

    async def test_sync_two_concurrent_commands_injects(self):
        """SYNC two_concurrent_commands: injects two mark_change_log_delivered calls."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "sync.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, SYNC_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                ChangeLog(
                    id=19,
                    operation="UPDATE",
                    table_name="users",
                    record_id=1,
                    data={"id": 1},
                    timestamp=datetime(2026, 9, 4, 1, 0, 0),
                    hash="abc",
                    synced=False,
                )
            )
            seed.commit()
            seed.close()

            async def worker(wid: str, after_lookup, record):
                db = YieldingSession(
                    Session(),
                    after_lookup=after_lookup,
                    event_log=_EventLog(record),
                    worker_id=wid,
                )
                try:
                    await db.execute(text("SELECT id FROM change_log WHERE id=19"))
                    with patch("core.db.AsyncSessionLocal", return_value=_SessionBox(db)):
                        return await mark_change_log_delivered({"change_log_id": 19})
                finally:
                    db.close()

            first, second, events = _run_overlap(worker)
            engine.dispose()
            _assert_overlap(events)
            self.assertEqual(sorted((first, second), reverse=True)[0], 1)
            self.assertEqual(min(first, second), 0)
            with sqlite3.connect(db_path) as conn:
                synced = conn.execute("SELECT synced FROM change_log WHERE id=19").fetchone()[0]
            self.assertTrue(synced)

    async def test_sync_process_restart_injects(self):
        """SYNC process_restart: injects mark_change_log_delivered already synced."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "sync-restart.sqlite")
            engine = _engine(db_path)
            _exec_sql(engine, SYNC_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            seed = Session()
            seed.add(
                ChangeLog(
                    id=19,
                    operation="UPDATE",
                    table_name="users",
                    record_id=1,
                    data={"id": 1},
                    timestamp=datetime(2026, 9, 4, 1, 0, 0),
                    hash="abc",
                    synced=False,
                )
            )
            seed.commit()
            db = YieldingSession(seed)
            with patch("core.db.AsyncSessionLocal", return_value=_SessionBox(db)):
                first = await mark_change_log_delivered({"change_log_id": 19})
            self.assertEqual(first, 1)
            db.close()
            engine.dispose()
            payload = _child("SYNC", db_path)
            self.assertEqual(payload["rowcount"], 0)
            self.assertEqual(payload["row_count"], 1)

    def test_msg_two_concurrent_and_restart_are_not_applicable(self):
        """MSG persist_sent_direct_message has no command identity; cells stay NOT_APPLICABLE."""
        source = persist_sent_direct_message
        text = source.__doc__ or ""
        self.assertIn("Persist one new direct message", text)
        blob = Path(REPO_ROOT / "core/services/chat_service.py").read_text(encoding="utf-8")
        start = blob.find("async def persist_sent_direct_message")
        fragment = blob[start : start + 1200]
        self.assertNotIn("idempotency_key", fragment)
        payload = _load_json("scenario-records.json")
        for variant_id in ("MSG__TWO-CONCURRENT-COMMANDS", "MSG__PROCESS-RESTART"):
            cell = next(item for item in payload["family_boundary_variants"] if item["variant_id"] == variant_id)
            self.assertEqual(cell["applicability"], "NOT_APPLICABLE")
            self.assertEqual(cell["eligibility"]["decision"], "NOT_APPLICABLE")
            self.assertIn("no command identity", cell["eligibility"]["reason"])

    async def test_msg_overlapping_persists_create_independent_rows(self):
        """MSG has no same-key collision: overlapping persist_sent_direct_message inserts two rows."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg.sqlite")
            engine = _engine(db_path)
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
                conn.execute(text("CREATE TABLE chats (id INTEGER PRIMARY KEY)"))
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sender = SimpleNamespace(id=1)
            receiver = SimpleNamespace(id=2)

            async def worker():
                db = YieldingSession(Session())
                try:
                    with patch(
                        "core.services.chat_service.sync_direct_message_threading",
                        new=AsyncMock(),
                    ), patch(
                        "core.services.chat_service.reload_direct_message",
                        new=AsyncMock(side_effect=lambda _db, message_id, **_k: SimpleNamespace(id=message_id)),
                    ):
                        return await persist_sent_direct_message(
                            db,
                            sender=sender,
                            receiver=receiver,
                            content="same-text",
                            message_type=MessageType.TEXT,
                        )
                finally:
                    db.close()

            await asyncio.gather(worker(), worker())
            engine.dispose()
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(count, 2)


def _child_main() -> None:
    family = os.environ[CHILD_FAMILY]
    db_path = os.environ[CHILD_DB]
    payload = asyncio.run(_child_replay(family, db_path))
    print(json.dumps(payload))


async def _child_replay(family: str, db_path: str) -> dict:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = YieldingSession(Session())
    try:
        if family == "AUTH":
            user = SimpleNamespace(
                id=13,
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                max_sessions=1,
            )
            with patch.object(session_service, "_is_accountant_user", new=AsyncMock(return_value=False)), patch.object(
                session_service, "publish_session_revocation", new=AsyncMock()
            ), patch.object(session_service, "blacklist_session", new=AsyncMock()), patch.object(
                session_service, "create_session", new=AsyncMock(side_effect=AssertionError("must replay"))
            ):
                result = await handle_login_session(
                    db,
                    user,
                    SAME_AUTH_TOKEN,
                    "web",
                    suspended_refresh_token=SAME_AUTH_TOKEN,
                    home_server="foreign",
                )
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]
            return {"duplicate_replay": result["replaced_session_count"] == 0, "row_count": row_count}
        if family == "IDENTITY":
            from tests.test_p1_00_characterization_admin_account_status import (
                DurableSession,
                MappedAccountUser,
                _reload_user,
                _service_patches,
            )

            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            reloaded, fresh = _reload_user(Session)
            retry_db = DurableSession(fresh)
            retry_started = _service_patches()
            for item in retry_started:
                item.start()
            try:
                replay = await identity_status.transition_user_account_status(
                    retry_db, reloaded, UserAccountStatus.INACTIVE
                )
            finally:
                for item in retry_started:
                    item.stop()
                fresh.close()
            return {
                "changed": bool(replay.changed),
                "account_status": getattr(reloaded.account_status, "value", reloaded.account_status),
            }
        if family == "OFFER":
            owner = SimpleNamespace(id=41, offer_overtime_minutes=0)
            policy = OfferCreationQuotaPolicy(max_active_offers=10, enforce_user_limits=False)
            with patch(
                "core.services.offer_creation_service._lock_offer_owner",
                new=AsyncMock(return_value=owner),
            ), patch(
                "core.services.customer_relation_service.enforce_customer_offer_limits_for_creation",
                new=AsyncMock(),
            ), patch(
                "core.user_counter_sync.increment_user_counters",
            ):
                result = await create_authoritative_offer_with_outcome(
                    db,
                    _offer_command(),
                    validate_market=False,
                    quota_policy=policy,
                )
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
            return {"duplicate_replay": not result.created, "row_count": row_count}
        if family == "ADMIN":
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN)
            with patch(
                "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
                new=AsyncMock(return_value=[]),
            ):
                result = await create_telegram_admin_broadcast(
                    db,
                    actor=actor,
                    content="hello",
                    audience_type=TelegramAdminBroadcastAudienceType.ALL,
                    creation_key=SAME_ADMIN_KEY,
                )
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0]
            return {"duplicate_replay": result.receipt_count == 0 and result.broadcast.creation_key == SAME_ADMIN_KEY, "row_count": row_count}
        if family == "NOTIFY":
            commits = {"n": 0}

            class CountSession(YieldingSession):
                async def commit(self):
                    commits["n"] += 1
                    await super().commit()

            counted = CountSession(Session())
            with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync:
                await mark_notification_read(
                    11, current_user=SimpleNamespace(id=5), db=counted, redis=SimpleNamespace()
                )
            counted.close()
            return {"already_read": sync.await_count == 0, "commit_count": commits["n"]}
        if family == "TRADE":
            result = await deliver_webapp_trade_notification(
                db,
                trade_number=3,
                recipient_user_id=5,
                message="done",
                current_server="foreign",
                publish_after_commit=False,
            )
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM trade_delivery_receipts").fetchone()[0]
            return {
                "duplicate_replay": result.status in {"queued_for_iran", "already_sent"} and not result.receipt_created,
                "row_count": row_count,
            }
        if family == "TG":
            with patch(
                "core.services.telegram_notification_outbox_service._assert_legacy_direct_delivery_owner"
            ):
                claimed = await claim_next_telegram_notification_outbox(
                    db, current_server="foreign", lease_seconds=30, worker_id="child"
                )
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM telegram_notification_outbox").fetchone()[0]
            return {"already_claimed": claimed is None, "row_count": row_count}
        if family == "MARKET":
            evaluation = MarketScheduleEvaluation(
                is_open=True,
                reason="open",
                timezone="Asia/Tehran",
                next_transition_at=None,
            )
            with patch(
                "core.services.market_transition_service.current_server",
                return_value="iran",
            ), patch(
                "core.services.market_transition_service._acquire_market_runtime_lock",
                new=AsyncMock(),
            ):
                result = await apply_market_schedule_transition(db, evaluation)
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM market_runtime_state").fetchone()[0]
            return {"changed": result.changed, "row_count": row_count}
        if family == "SYNC":
            with patch("core.db.AsyncSessionLocal", return_value=_SessionBox(db)):
                rowcount = await mark_change_log_delivered({"change_log_id": 19})
            with sqlite3.connect(db_path) as conn:
                row_count = conn.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
            return {"rowcount": rowcount, "row_count": row_count}
        raise AssertionError(family)
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    if os.environ.get(CHILD_FAMILY):
        _child_main()
    else:
        unittest.main()
