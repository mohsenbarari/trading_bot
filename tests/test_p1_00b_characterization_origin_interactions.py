"""P1-00b: combined Offer/Request-origin interactions on the live trade path."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.enums import SettlementType, UserAccountStatus, UserRole
from core.offer_source import OfferSourceSurface, offer_home_server_for_source
from core.services.bot_access_policy import evaluate_bot_access_local_state
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    apply_offer_expiry,
)
from core.services.offer_request_ledger_service import load_offer_request_by_idempotency
from core.services.trade_notification_audience_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    WEBAPP_CHANNEL,
    TradeNotificationAudience,
    TradeNotificationAudienceRecipient,
    TradeNotificationChannelRequirement,
)
from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox
from models.commodity import Commodity
from models.customer_relation import CustomerTier
from models.notification import Notification
from models.offer import Offer, OfferStatus, OfferType
from models.user import User
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus, OfferRequestWorkflow
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import TradeDeliveryReceipt

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ORIGIN_PAIRS = (
    ("WEBAPP", "WEBAPP"),
    ("WEBAPP", "TELEGRAM_BOT"),
    ("TELEGRAM_BOT", "WEBAPP"),
    ("TELEGRAM_BOT", "TELEGRAM_BOT"),
    ("INTERNAL", "WEBAPP"),
    ("INTERNAL", "TELEGRAM_BOT"),
)
REQUIRED_INTERACTIONS = (
    "OUTCOME-SUCCESS",
    "OUTCOME-REJECT",
    "PERMISSION-DENIED",
    "RETRY",
    "CONCURRENT",
    "UNLINKED-BOT",
    "EXPIRY-CANCEL-REPUBLISH-TERMINAL",
    "POLICY-SNAPSHOT",
    "NOTIFICATION",
    "FAILURE-BEFORE-COMMIT",
    "FAILURE-AFTER-COMMIT-BEFORE-SIDE-EFFECT",
    "SIDE-EFFECT-RETRY",
)


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _surface(origin: str) -> OfferSourceSurface:
    if origin == "TELEGRAM_BOT":
        return OfferSourceSurface.TELEGRAM_BOT
    if origin == "WEBAPP":
        return OfferSourceSurface.WEBAPP
    return OfferSourceSurface.INTERNAL_SYNC


def _home(origin: str) -> str:
    if origin == "INTERNAL":
        return offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran")
    return offer_home_server_for_source(_surface(origin))


def _request_surface(origin: str) -> OfferRequestSourceSurface:
    return (
        OfferRequestSourceSurface.TELEGRAM_BOT
        if origin == "TELEGRAM_BOT"
        else OfferRequestSourceSurface.WEBAPP
    )


class _Result:
    def __init__(self, *, single=None, single_or_none=None, use_none=False):
        self._single = single
        self._single_or_none = single_or_none
        self._use_none = use_none

    def scalar_one(self):
        return self._single

    def scalar_one_or_none(self):
        if self._use_none:
            return self._single_or_none
        return self._single_or_none if self._single_or_none is not None else self._single

    def scalars(self):
        rows = [self._single] if self._single is not None else []
        return SimpleNamespace(all=lambda: rows, first=lambda: self._single)

    def scalar(self):
        return self._single


_SQLITE_TABLE_TOKENS = (
    "offer_request",
    "trade_delivery_receipt",
    "notifications",
)


class OriginSession:
    """Live trade adapter: User/Offer stay in memory; Trade/OfferRequest/receipts use isolated sqlite."""

    def __init__(
        self,
        sync_session,
        *,
        offer,
        owner,
        after_lookup=None,
        before_commit=None,
        event_log=None,
        worker_id=None,
        fail_commit=False,
        fail_after_commit=False,
    ):
        self._session = sync_session
        self.offer = offer
        self.owner = owner
        self.after_lookup = after_lookup
        self.before_commit = before_commit
        self.event_log = event_log
        self.worker_id = worker_id
        self.fail_commit = fail_commit
        self.fail_after_commit = fail_after_commit
        self.added = []
        self.offer_requests = []
        self.commit_count = 0
        self._lookups = 0
        self.refresh = AsyncMock()
        self.rollback = AsyncMock(side_effect=self._rollback)
        if isinstance(owner, User) and isinstance(offer, Offer):
            existing = self._session.get(User, owner.id)
            if existing is None:
                _persist_graph(self._session, owner, offer)

    @asynccontextmanager
    async def begin_nested(self):
        yield self

    def get_bind(self):
        return self._session.get_bind()

    @property
    def bind(self):
        return self._session.get_bind()

    def _note(self, kind):
        if self.event_log is not None:
            self.event_log.append((self.worker_id, kind, self._lookups))

    def _rollback(self):
        self._session.rollback()

    def _is_request_statement(self, statement) -> bool:
        return "offer_request" in str(statement).lower()

    def _is_sqlite_statement(self, statement) -> bool:
        blob = str(statement).lower()
        return any(token in blob for token in _SQLITE_TABLE_TOKENS)

    def _is_trade_statement(self, statement) -> bool:
        blob = str(statement).lower()
        return "trades." in blob or " from trades" in blob or " join trades" in blob

    def _live_trade(self):
        return next((item for item in self.added if isinstance(item, Trade)), None)

    async def execute(self, statement, params=None):
        if self._is_request_statement(statement) or self._is_sqlite_statement(statement):
            self._drop_duplicate_graph_inserts()
            with self._session.no_autoflush:
                result = self._session.execute(statement)
            if self._is_request_statement(statement):
                self._lookups += 1
                self._note("lookup")
                if self.after_lookup and self._lookups == 1:
                    await self.after_lookup()
            return result
        if self._is_trade_statement(statement):
            trade = self._live_trade()
            return _Result(single=trade, single_or_none=trade, use_none=True)
        blob = str(statement).lower()
        if "telegram_notification" in blob or "outbox" in blob:
            return _Result(single=None, single_or_none=None, use_none=True)
        return _Result(single=self.owner, single_or_none=None)

    async def get(self, model, ident, **kwargs):
        name = getattr(model, "__name__", str(model))
        if name == "Offer" or ident == getattr(self.offer, "id", None):
            return self.offer
        if name == "Trade":
            live = next((item for item in self.added if getattr(item, "id", None) == ident), None)
            if live is not None:
                return live
            return self._session.get(model, ident)
        return self._session.get(model, ident)

    def add(self, obj):
        if isinstance(obj, OfferRequest):
            self._session.add(obj)
            self.offer_requests.append(obj)
            return
        if isinstance(obj, (Trade, TradeDeliveryReceipt, Notification)):
            self._session.add(obj)
            self.added.append(obj)
            return
        self.added.append(obj)

    def _drop_duplicate_graph_inserts(self):
        for inst in list(self._session.new):
            if isinstance(inst, (Commodity, User, Offer)):
                self._session.expunge(inst)

    async def flush(self):
        self._drop_duplicate_graph_inserts()
        self._session.flush()

    async def commit(self):
        self._note("pre_commit")
        if self.before_commit:
            await self.before_commit()
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected failure_before_commit")
        self._note("commit")
        self._drop_duplicate_graph_inserts()
        self._session.commit()
        self.commit_count += 1
        if self.fail_after_commit and self.commit_count == 1:
            raise RuntimeError("injected failure_after_commit_before_side_effect")

    async def scalar(self, statement, params=None):
        return 10000

    async def refresh(self, obj, attribute_names=None):
        return None

    def close(self):
        self._session.close()


def _make_owner(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "full_name": "buyer",
        "address": "tehran",
        "trading_restricted_until": None,
        "mobile_number": "09120000000",
        "account_name": "buyer",
        "telegram_id": 555,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "trades_count": 0,
        "commodities_traded_count": 0,
        "channel_messages_count": 0,
        "counter_epoch": 1,
        "limitations_expire_at": None,
        "max_daily_trades": None,
        "max_active_commodities": None,
        "home_server": "iran",
        "sync_version": 1,
    }
    data.update(overrides)
    return User(**data)


def _make_offer(offer_origin: str, **overrides):
    home = _home(offer_origin)
    seller = overrides.pop("user", None) or User(
        id=9,
        account_name="seller",
        mobile_number="09125555555",
        full_name="seller",
        address="tehran",
        telegram_id=999,
        account_status=UserAccountStatus.ACTIVE,
        role=UserRole.STANDARD,
        home_server=home,
        sync_version=1,
    )
    commodity = overrides.pop("commodity", None) or Commodity(id=1, name="Gold")
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": seller.id,
        "quantity": 4,
        "remaining_quantity": 4,
        "is_wholesale": True,
        "lot_sizes": None,
        "offer_type": OfferType.SELL,
        "settlement_type": SettlementType.TOMORROW,
        "price": 123456,
        "offer_public_id": f"ofr_{offer_origin.lower()}_7",
        "home_server": home,
        "commodity_id": 1,
        "created_at": datetime(2026, 9, 4, 0, 0, 0),
        "overtime_minutes_snapshot": 0,
        "version_id": 1,
    }
    data.update(overrides)
    offer = Offer(**data)
    offer.user = seller
    offer.commodity = commodity
    return offer


def _persist_graph(sync_session, owner, offer):
    if getattr(offer, "commodity", None) is not None:
        sync_session.merge(offer.commodity)
    if getattr(offer, "user", None) is not None:
        sync_session.merge(offer.user)
    sync_session.merge(owner)
    sync_session.merge(offer)
    sync_session.commit()


def _context(owner, *, accountant=False):
    return SimpleNamespace(
        owner_user=owner,
        actor_user=owner,
        relation=None,
        is_accountant_context=accountant,
    )


def _sqlite_requests(path: str):
    engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(engine, "connect", lambda conn, _rec: conn.execute("PRAGMA busy_timeout=30000"))
    event.listen(engine, "connect", lambda conn, _rec: conn.execute("PRAGMA journal_mode=WAL"))
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    account_name VARCHAR NOT NULL,
                    mobile_number VARCHAR NOT NULL,
                    normalized_account_name VARCHAR NOT NULL DEFAULT '',
                    normalized_mobile_number VARCHAR NOT NULL DEFAULT '',
                    telegram_id BIGINT,
                    username VARCHAR,
                    full_name VARCHAR NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    avatar_file_id VARCHAR(36),
                    role VARCHAR NOT NULL DEFAULT 'عادی',
                    account_status VARCHAR NOT NULL DEFAULT 'active',
                    deactivated_at DATETIME,
                    messenger_grace_expires_at DATETIME,
                    messenger_blocked_at DATETIME,
                    has_bot_access BOOLEAN NOT NULL DEFAULT 1,
                    bot_onboarding_required_step INTEGER NOT NULL DEFAULT 0,
                    bot_onboarding_completed_step INTEGER NOT NULL DEFAULT 0,
                    bot_onboarding_completed_at DATETIME,
                    is_deleted BOOLEAN DEFAULT 0,
                    deleted_at DATETIME,
                    admin_password_hash VARCHAR(255),
                    must_change_password BOOLEAN NOT NULL DEFAULT 0,
                    offer_overtime_minutes INTEGER DEFAULT 0 NOT NULL,
                    trading_restricted_until DATETIME,
                    max_daily_trades INTEGER,
                    max_active_commodities INTEGER,
                    max_daily_requests INTEGER,
                    limitations_expire_at DATETIME,
                    trades_count INTEGER NOT NULL DEFAULT 0,
                    commodities_traded_count INTEGER NOT NULL DEFAULT 0,
                    channel_messages_count INTEGER NOT NULL DEFAULT 0,
                    counter_epoch BIGINT DEFAULT 1 NOT NULL,
                    max_sessions INTEGER NOT NULL DEFAULT 1,
                    max_accountants INTEGER NOT NULL DEFAULT 3,
                    max_customers INTEGER NOT NULL DEFAULT 5,
                    home_server VARCHAR(16) NOT NULL DEFAULT 'foreign',
                    sync_version BIGINT DEFAULT 1 NOT NULL,
                    can_block_users BOOLEAN DEFAULT 1,
                    max_blocked_users INTEGER DEFAULT 10,
                    last_seen_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    Commodity.__table__.create(engine, checkfirst=True)
    Offer.__table__.create(engine, checkfirst=True)
    OfferRequest.__table__.create(engine, checkfirst=True)
    Trade.__table__.create(engine, checkfirst=True)
    Notification.__table__.create(engine, checkfirst=True)
    TradeDeliveryReceipt.__table__.create(engine, checkfirst=True)
    CoinIntelligenceMarketOutbox.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_offer_requests_home_idempotency_key "
                "ON offer_requests (request_home_server, idempotency_key)"
            )
        )
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine, Session


def _identity_audience(_db, user_ids, **_k):
    return [int(uid) for uid in user_ids if uid is not None]


def _trade_to_response(trade, *args, **kwargs):
    return {
        "id": getattr(trade, "id", None),
        "trade_number": getattr(trade, "trade_number", None),
        "status": getattr(getattr(trade, "status", None), "value", getattr(trade, "status", None)),
    }


def _audience_for_trade(home: str):
    async def _build(_db, trade, **_k):
        trade_number = getattr(trade, "trade_number", None)
        offer_user_id = int(getattr(trade, "offer_user_id", 9) or 9)
        responder_id = int(getattr(trade, "responder_user_id", 5) or 5)

        def _recipient(uid, role, side, other):
            return TradeNotificationAudienceRecipient(
                recipient_user_id=uid,
                recipient_role=role,
                principal_user_id=uid,
                side=side,
                counterparty_user_id=other,
                webapp_message="trade completed",
                extra_payload={"trade_number": trade_number, "trade_id": getattr(trade, "id", None)},
                channel_requirements=(
                    TradeNotificationChannelRequirement(
                        channel=WEBAPP_CHANNEL,
                        destination_server="iran",
                        required=True,
                        reason="webapp_required",
                        message="trade completed",
                    ),
                ),
            )

        return TradeNotificationAudience(
            event_type=TRADE_COMPLETED_EVENT_TYPE,
            trade_id=getattr(trade, "id", None),
            trade_number=trade_number,
            offer_id=getattr(trade, "offer_id", 7),
            offer_home_server=home,
            trade_path_kind="direct",
            trade_path_summary="direct",
            recipients=(
                _recipient(responder_id, "responder", "responder", offer_user_id),
                _recipient(offer_user_id, "offer_user", "offer", responder_id),
            ),
        )

    return _build


def _request_patches(home: str, *, market=None, relation=None, settings=None):
    market = market or SimpleNamespace(is_open=True, reason="daily_window_open", timezone="Asia/Tehran")
    settings = settings or SimpleNamespace(offer_expiry_minutes=0)
    return [
        patch("api.routers.trades.get_active_customer_relation_for_customer", new=AsyncMock(return_value=relation)),
        patch("api.routers.trades.enforce_customer_trade_limits_for_trade", new=AsyncMock()),
        patch("api.routers.trades._load_trade_customer_relation_map_for_user_ids", new=AsyncMock(return_value={})),
        patch("api.routers.realtime.publish_user_event", new=AsyncMock()),
        patch(
            "api.routers.trades.evaluate_current_market_schedule",
            new=AsyncMock(return_value=market),
        ),
        patch(
            "core.services.trade_delivery_receipt_service.build_trade_completion_notification_audience",
            new=AsyncMock(side_effect=_audience_for_trade(home)),
        ),
        patch("api.routers.trades._is_offer_expired_for_trade", new=AsyncMock(return_value=False)),
        patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))),
        patch("api.routers.trades.validate_offer_trade_amount", return_value=(True, None, 4, [])),
        patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=_identity_audience),
        ),
        patch("api.routers.trades.load_accountant_chat_identity_map", new=AsyncMock(return_value={})),
        patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)),
        patch(
            "core.services.telegram_overtime_owner_approval_legacy_service.enqueue_legacy_overtime_owner_approval_delivery",
            new=AsyncMock(return_value=SimpleNamespace(undeliverable_reason=None, job_id=1, outbox=SimpleNamespace(id=11))),
        ),
        patch(
            "core.services.telegram_overtime_owner_approval_queue_service.enqueue_overtime_owner_approval_delivery",
            new=AsyncMock(return_value=SimpleNamespace(undeliverable_reason=None, job_id=1, outbox=SimpleNamespace(id=11))),
        ),
        patch(
            "core.services.telegram_overtime_requester_status_service.schedule_requester_status_presented_edit",
            new=AsyncMock(),
        ),
        patch(
            "core.services.telegram_offer_channel_service.request_offer_channel_state_refresh",
            new=AsyncMock(),
        ),
        patch("api.routers.realtime.publish_event", new=AsyncMock()),
        patch(
            "core.services.trade_webapp_delivery_service.should_suppress_user_notification",
            return_value=False,
        ),
        patch(
            "core.services.trade_webapp_delivery_service._safe_publish_webapp_side_effect",
            new=AsyncMock(return_value=(False, None)),
        ),
        patch("api.routers.trades.trade_to_response", side_effect=_trade_to_response),
        patch("api.routers.trades.current_server", return_value=home),
        patch("core.services.offer_request_ledger_service.current_server", return_value=home),
        patch("core.services.offer_overtime_request_service.current_server", return_value=home),
        patch("core.server_routing.current_server", return_value=home),
        patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=settings),
        ),
    ]


def _sqlite_counts(path: str) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        requests = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM trade_delivery_receipts").fetchone()[0]
        notes = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        outbox = conn.execute("SELECT COUNT(*) FROM coin_intelligence_market_outbox").fetchone()[0]
        trade_number = conn.execute("SELECT trade_number FROM trades LIMIT 1").fetchone()
        resulting = conn.execute("SELECT resulting_trade_id FROM offer_requests LIMIT 1").fetchone()
        workflow = conn.execute("SELECT workflow_kind FROM offer_requests LIMIT 1").fetchone()
    return {
        "trades": trades,
        "offer_requests": requests,
        "receipts": receipts,
        "notifications": notes,
        "outbox": outbox,
        "trade_number": trade_number[0] if trade_number else None,
        "resulting_trade_id": resulting[0] if resulting else None,
        "workflow_kind": workflow[0] if workflow else None,
    }


class P100bOriginInteractionCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def _success(self, offer_origin: str, request_origin: str, *, idempotency="origin-success"):
        home = _home(offer_origin)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "origin.sqlite")
            engine, Session = _sqlite_requests(db_path)
            owner = _make_owner()
            offer = _make_offer(offer_origin)
            db = OriginSession(Session(), offer=offer, owner=owner)
            started = _request_patches(home)
            for item in started:
                item.start()
            try:
                result = await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4, idempotency_key=idempotency),
                    BackgroundTasks(),
                    db=db,
                    context=_context(owner),
                    request_source_surface=_request_surface(request_origin),
                    request_source_server=home if request_origin != "TELEGRAM_BOT" else "foreign",
                )
            finally:
                for item in started:
                    item.stop()
                db.close()
                engine.dispose()
            counts = _sqlite_counts(db_path)
            self.assertEqual(counts["trades"], 1)
            self.assertEqual(counts["offer_requests"], 1)
            self.assertEqual(counts["receipts"], 2)
            self.assertIsNotNone(counts["trade_number"])
            self.assertGreaterEqual(int(counts["trade_number"]), 10000)
            self.assertIsNotNone(counts["resulting_trade_id"])
            self.assertEqual(result["trade_number"], counts["trade_number"])
            self.assertEqual(len(db.offer_requests), 1)
            ledger = db.offer_requests[0]
            self.assertEqual(ledger.request_source_surface, _request_surface(request_origin))
            self.assertEqual(offer.home_server, home)
            self.assertEqual(ledger.result_status, OfferRequestStatus.COMPLETED_TRADE)
            self.assertEqual(owner.trades_count, 1)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT request_source_surface, offer_public_id, result_status, resulting_trade_id FROM offer_requests"
                ).fetchone()
                trade_row = conn.execute(
                    "SELECT id, trade_number, status, offer_id, responder_user_id, offer_user_id FROM trades"
                ).fetchone()
            self.assertEqual(row[0], _request_surface(request_origin).value)
            self.assertEqual(row[1], offer.offer_public_id)
            self.assertEqual(trade_row[1], counts["trade_number"])
            self.assertEqual(str(trade_row[2]).lower(), "completed")
            self.assertEqual(trade_row[3], 7)
            self.assertEqual(row[3], trade_row[0])
            return db_path, ledger, counts

    async def test_origin_outcome_success_injects(self):
        """OUTCOME-SUCCESS: live _execute_trade_authoritatively joins admission, offer home, request surface."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                await self._success(offer_origin, request_origin, idempotency=f"ok-{offer_origin}-{request_origin}")

    async def test_origin_outcome_reject_injects(self):
        """OUTCOME-REJECT: live path rejects own-offer and persists both origins."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    engine, Session = _sqlite_requests(str(Path(tmp) / "rej.sqlite"))
                    owner = _make_owner(id=9)
                    offer = _make_offer(offer_origin, user_id=9)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaises(HTTPException) as raised:
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4, idempotency_key=f"rej-{offer_origin}"),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertEqual(raised.exception.status_code, 400)
                    self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_BUSINESS_RULE)
                    self.assertEqual(db.offer_requests[0].request_source_surface, _request_surface(request_origin))
                    self.assertEqual(offer.home_server, home)

    async def test_origin_permission_denied_injects(self):
        """PERMISSION-DENIED: accountant and WATCH fail closed on the live execute path."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    engine, Session = _sqlite_requests(str(Path(tmp) / "perm.sqlite"))
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaises(HTTPException) as raised:
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner, accountant=True),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertEqual(raised.exception.status_code, 403)
                    self.assertEqual(db.offer_requests, [])
                    watch = _make_owner(role=UserRole.WATCH)
                    engine2, Session2 = _sqlite_requests(str(Path(tmp) / "watch.sqlite"))
                    db2 = OriginSession(Session2(), offer=offer, owner=watch)
                    started2 = _request_patches(home)
                    for item in started2:
                        item.start()
                    try:
                        with self.assertRaises(HTTPException) as watch_err:
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4),
                                BackgroundTasks(),
                                db=db2,
                                context=_context(watch),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started2:
                            item.stop()
                        db2.close()
                        engine2.dispose()
                    self.assertEqual(watch_err.exception.status_code, 403)

    async def test_origin_retry_injects(self):
        """RETRY: second live execute replays the same-key request without a second row."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                key = f"retry-{offer_origin}-{request_origin}"
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "retry.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=key),
                            BackgroundTasks(),
                            db=db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                        replay_offer = _make_offer(offer_origin, remaining_quantity=0, status=OfferStatus.COMPLETED)
                        replay_db = OriginSession(Session(), offer=replay_offer, owner=owner)
                        replay_db.added.extend(db.added)
                        replay = await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=key),
                            BackgroundTasks(),
                            db=replay_db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 1)
                    self.assertIsNotNone(replay)

    async def test_origin_concurrent_injects(self):
        """CONCURRENT: overlap after request lookup and before commit; one durable row."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                key = f"conc-{offer_origin}-{request_origin}"
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "conc.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    lookup_barrier = threading.Barrier(2, timeout=45)
                    events: list = []
                    events_lock = threading.Lock()
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    _persist_graph(Session(), owner, offer)

                    class _Log:
                        def append(self, item):
                            with events_lock:
                                events.append(item)

                    started = _request_patches(home)
                    for item in started:
                        item.start()

                    def thread_main(wid: str):
                        async def after_lookup():
                            lookup_barrier.wait()

                        async def run():
                            db = OriginSession(
                                Session(),
                                offer=offer,
                                owner=owner,
                                after_lookup=after_lookup,
                                event_log=_Log(),
                                worker_id=wid,
                            )
                            try:
                                return await _execute_trade_authoritatively(
                                    TradeCreate(offer_id=7, quantity=4, idempotency_key=key),
                                    BackgroundTasks(),
                                    db=db,
                                    context=_context(owner),
                                    request_source_surface=_request_surface(request_origin),
                                    request_source_server=home,
                                )
                            except (IntegrityError, HTTPException, OperationalError, AttributeError):
                                await db.rollback()
                                return "loser"
                            finally:
                                db.close()

                        asyncio.run(run())

                    try:
                        first = threading.Thread(target=thread_main, args=("a",))
                        second = threading.Thread(target=thread_main, args=("b",))
                        first.start()
                        second.start()
                        first.join(60)
                        second.join(60)
                    finally:
                        for item in started:
                            item.stop()
                    engine.dispose()
                    first_lookup = {}
                    first_commit = None
                    for index, (wid, kind, _count) in enumerate(events):
                        if kind == "lookup" and wid not in first_lookup:
                            first_lookup[wid] = index
                        if kind == "commit" and first_commit is None:
                            first_commit = index
                    self.assertEqual(set(first_lookup), {"a", "b"})
                    self.assertIsNotNone(first_commit)
                    self.assertLess(max(first_lookup.values()), first_commit)
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 1)

    async def test_origin_unlinked_bot_injects(self):
        """UNLINKED-BOT: Telegram entry rejects missing/denied users before the trade command."""
        from tests.test_p1_00b_characterization_telegram_unlinked import (
            P100bTelegramUnlinkedCharacterizationTests,
        )

        helper = P100bTelegramUnlinkedCharacterizationTests()
        await helper.test_telegram_entry_rejects_missing_and_locally_denied_users_injects()
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                offer = _make_offer(offer_origin)
                self.assertEqual(offer.home_server, _home(offer_origin))
                if request_origin != "TELEGRAM_BOT":
                    continue
                missing = evaluate_bot_access_local_state(None)
                self.assertFalse(missing.allowed)

    async def test_origin_expiry_cancel_republish_terminal_injects(self):
        """EXPIRY-CANCEL-REPUBLISH-TERMINAL: expire keeps offer origin and rejects later request."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                offer = _make_offer(offer_origin)
                with patch("core.services.offer_expiry_service.current_server", return_value=home), patch(
                    "core.server_routing.current_server", return_value=home
                ):
                    apply_offer_expiry(
                        offer,
                        OfferExpiryCommand(
                            reason=OfferExpiryReason.TIME_LIMIT,
                            source_surface=OfferExpirySourceSurface.SYSTEM
                            if offer_origin == "INTERNAL"
                            else (
                                OfferExpirySourceSurface.WEBAPP
                                if request_origin == "WEBAPP"
                                else OfferExpirySourceSurface.TELEGRAM_BOT
                            ),
                            source_server=home,
                            require_active=True,
                        ),
                    )
                self.assertEqual(offer.status, OfferStatus.EXPIRED)
                self.assertEqual(offer.home_server, home)
                with tempfile.TemporaryDirectory() as tmp:
                    engine, Session = _sqlite_requests(str(Path(tmp) / "exp.sqlite"))
                    owner = _make_owner()
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with patch(
                            "api.routers.trades._is_offer_expired_for_trade",
                            new=AsyncMock(return_value=True),
                        ):
                            with self.assertRaises(HTTPException):
                                await _execute_trade_authoritatively(
                                    TradeCreate(offer_id=7, quantity=4, idempotency_key=f"exp-{offer_origin}"),
                                    BackgroundTasks(),
                                    db=db,
                                    context=_context(owner),
                                    request_source_surface=_request_surface(request_origin),
                                    request_source_server=home,
                                )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertTrue(db.offer_requests)
                    self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_OFFER_EXPIRED)
                    self.assertEqual(db.offer_requests[0].request_source_surface, _request_surface(request_origin))

    async def test_origin_policy_snapshot_injects(self):
        """POLICY-SNAPSHOT: live ledger stores surface, offer id, tier, and commission."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                relation = SimpleNamespace(
                    id=44,
                    owner_user_id=9,
                    customer_tier=CustomerTier.TIER_1,
                    management_name="desk",
                    commission_rate=1.5,
                    min_trade_quantity=1,
                    max_trade_quantity=10,
                    max_daily_trades=8,
                    max_daily_commodity_volume=20,
                )
                with tempfile.TemporaryDirectory() as tmp:
                    engine, Session = _sqlite_requests(str(Path(tmp) / "snap.sqlite"))
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home, relation=relation)
                    for item in started:
                        item.start()
                    try:
                        await _execute_trade_authoritatively(
                            TradeCreate(
                                offer_id=7,
                                quantity=4,
                                idempotency_key=f"snap-{offer_origin}-{request_origin}",
                            ),
                            BackgroundTasks(),
                            db=db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    ledger = db.offer_requests[0]
                    counts = _sqlite_counts(str(Path(tmp) / "snap.sqlite"))
                    self.assertEqual(ledger.request_source_surface, _request_surface(request_origin))
                    self.assertEqual(ledger.offer_public_id, offer.offer_public_id)
                    self.assertEqual(ledger.customer_tier_snapshot, CustomerTier.TIER_1.value)
                    self.assertEqual(float(ledger.customer_commission_rate_snapshot), 1.5)
                    self.assertEqual(ledger.customer_relation_id, 44)
                    self.assertEqual(ledger.customer_management_name_snapshot, "desk")
                    self.assertEqual(counts["trades"], 1)
                    self.assertEqual(counts["offer_requests"], 1)
                    self.assertIsNotNone(counts["resulting_trade_id"])

    async def test_origin_notification_injects(self):
        """NOTIFICATION: live execute persists two receipts and delivers WebApp rows on iran."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "note.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=f"note-{offer_origin}"),
                            BackgroundTasks(),
                            db=db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    counts = _sqlite_counts(db_path)
                    self.assertEqual(counts["trades"], 1)
                    self.assertEqual(counts["offer_requests"], 1)
                    self.assertEqual(counts["receipts"], 2)
                    if home == "iran":
                        self.assertEqual(counts["notifications"], 2)
                    else:
                        self.assertEqual(counts["notifications"], 0)
                    with sqlite3.connect(db_path) as conn:
                        recipients = sorted(
                            row[0]
                            for row in conn.execute(
                                "SELECT recipient_user_id FROM trade_delivery_receipts"
                            )
                        )
                    self.assertEqual(recipients, [5, 9])
                    self.assertEqual(db.offer_requests[0].request_source_surface, _request_surface(request_origin))
                    self.assertEqual(len(db.offer_requests), 1)

    async def test_origin_failure_before_commit_injects(self):
        """FAILURE-BEFORE-COMMIT: injected commit failure leaves zero durable request rows."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "failb.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner, fail_commit=True)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaisesRegex(RuntimeError, "failure_before_commit"):
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4, idempotency_key=f"failb-{offer_origin}"),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 0)

    async def test_origin_failure_after_commit_before_side_effect_injects(self):
        """FAILURE-AFTER-COMMIT-BEFORE-SIDE-EFFECT: request row remains after post-commit fault."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "faila.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner, fail_after_commit=True)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaisesRegex(RuntimeError, "failure_after_commit_before_side_effect"):
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4, idempotency_key=f"faila-{offer_origin}"),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 1)

    async def test_origin_side_effect_retry_injects(self):
        """SIDE-EFFECT-RETRY: post-commit notify fails once, then a real retry delivers."""
        from api.routers.trades import create_user_notification
        from core.enums import NotificationCategory, NotificationLevel

        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                key = f"side-{offer_origin}-{request_origin}"
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "side.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    calls = {"n": 0}

                    async def _notify_fail(*_a, **_k):
                        calls["n"] += 1
                        raise RuntimeError("injected side_effect_retry")

                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    notify = patch("api.routers.trades.create_user_notification", new=_notify_fail)
                    notify.start()
                    try:
                        first = await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=key),
                            BackgroundTasks(),
                            db=db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                    finally:
                        notify.stop()
                        for item in started:
                            item.stop()
                    self.assertIsNotNone(first)
                    self.assertGreaterEqual(calls["n"], 1)
                    after_fail = _sqlite_counts(db_path)
                    self.assertEqual(after_fail["trades"], 1)
                    self.assertEqual(after_fail["offer_requests"], 1)
                    self.assertEqual(after_fail["receipts"], 2)
                    self.assertEqual(after_fail["notifications"], 0)

                    started2 = _request_patches(home)
                    for item in started2:
                        item.start()
                    try:
                        for recipient in (5, 9):
                            await create_user_notification(
                                db,
                                recipient,
                                "trade completed",
                                level=NotificationLevel.SUCCESS,
                                category=NotificationCategory.TRADE,
                                extra_payload={
                                    "trade_number": after_fail["trade_number"],
                                    "trade_id": after_fail["resulting_trade_id"],
                                    "offer_id": 7,
                                    "recipient_role": "trade_recipient",
                                },
                            )
                    finally:
                        for item in started2:
                            item.stop()
                        db.close()
                        engine.dispose()
                    retried = _sqlite_counts(db_path)
                    self.assertEqual(retried["trades"], 1)
                    self.assertEqual(retried["offer_requests"], 1)
                    self.assertEqual(retried["receipts"], 2)
                    if home == "iran":
                        self.assertEqual(retried["notifications"], 2)
                    else:
                        self.assertEqual(retried["notifications"], 0)

    async def test_origin_quota_and_closed_market_injects(self):
        """Quota and closed-market time contexts reject before a durable trade."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin, kind="quota"):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "quota.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner(
                        limitations_expire_at=datetime.utcnow() + timedelta(days=1),
                        max_daily_trades=0,
                        trades_count=0,
                    )
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaises(HTTPException) as raised:
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4, idempotency_key=f"quota-{offer_origin}"),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertEqual(raised.exception.status_code, 403)
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 0)
            with self.subTest(offer=offer_origin, request=request_origin, kind="closed"):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "closed.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(
                        home,
                        market=SimpleNamespace(is_open=False, reason="market_closed", timezone="Asia/Tehran"),
                    )
                    for item in started:
                        item.start()
                    try:
                        with self.assertRaises(HTTPException) as raised:
                            await _execute_trade_authoritatively(
                                TradeCreate(offer_id=7, quantity=4, idempotency_key=f"closed-{offer_origin}"),
                                BackgroundTasks(),
                                db=db,
                                context=_context(owner),
                                request_source_surface=_request_surface(request_origin),
                                request_source_server=home,
                            )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertEqual(raised.exception.status_code, 409)
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 0)

    async def test_origin_overtime_routes_to_approval_not_trade_injects(self):
        """Overtime intake persists a real overtime OfferRequest and no Trade row."""
        from fastapi.responses import JSONResponse

        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                created = datetime.utcnow() - timedelta(minutes=35)
                offer = _make_offer(
                    offer_origin,
                    created_at=created,
                    overtime_minutes_snapshot=10,
                )
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "ot.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    owner = _make_owner()
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    started = _request_patches(
                        home,
                        settings=SimpleNamespace(offer_expiry_minutes=30),
                    )
                    for item in started:
                        item.start()
                    try:
                        result = await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=f"ot-{offer_origin}-{request_origin}"),
                            BackgroundTasks(),
                            db=db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                            edge_received_at=datetime.utcnow(),
                        )
                    finally:
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertIsInstance(result, JSONResponse)
                    self.assertEqual(result.status_code, 202)
                    counts = _sqlite_counts(db_path)
                    self.assertEqual(counts["trades"], 0)
                    self.assertEqual(counts["offer_requests"], 1)
                    self.assertEqual(str(counts["workflow_kind"]).lower(), OfferRequestWorkflow.OVERTIME.value)
                    self.assertFalse(any(isinstance(item, Trade) for item in db.added))

    def test_required_origin_inventory_cites_combined_path_nodes(self):
        payload = _load_json("scenario-records.json")
        rows = payload["origin_required_interactions"]
        self.assertEqual(len(rows), 72)
        evidenced = 0
        blocked_explicit = 0
        not_applicable = 0
        for row in rows:
            self.assertIn(row["interaction"], REQUIRED_INTERACTIONS)
            self.assertNotIn("equivalence_reason", row)
            if row["applicability"] == "ELIGIBLE":
                evidenced += 1
                self.assertTrue(row.get("evidence_cases"))
                for case in row["evidence_cases"]:
                    self.assertTrue(
                        "test_p1_00b_characterization_origin_interactions.py" in case["node_id"]
                        or "test_p1_00b_characterization_telegram_unlinked.py" in case["node_id"],
                        case["node_id"],
                    )
                    self.assertIn("injects", case["node_id"])
            elif row["applicability"] == "NOT_APPLICABLE":
                not_applicable += 1
                reason = row["eligibility"]["reason"] if isinstance(row.get("eligibility"), dict) else row.get("reason")
                self.assertTrue(reason)
            else:
                self.assertEqual(row["applicability"], "HIGH_BLOCKER")
                self.assertEqual(row.get("disposition"), "BLOCKED_EXPLICIT")
                blocked_explicit += 1
        self.assertEqual(evidenced + blocked_explicit + not_applicable, 72)
        self.assertEqual(not_applicable, 3)
        self.assertEqual(evidenced, 69)
        self.assertEqual(blocked_explicit, 0)
        blocked = [
            row["interaction"]
            for row in rows
            if row["applicability"] == "HIGH_BLOCKER"
        ]
        self.assertEqual(blocked, [])


if __name__ == "__main__":
    unittest.main()
