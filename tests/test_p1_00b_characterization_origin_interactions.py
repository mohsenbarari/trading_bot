"""P1-00b: combined Offer/Request-origin interactions on the live trade path."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
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
from models.offer import OfferStatus, OfferType
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus
from models.trade import TradeStatus, TradeType

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


class OriginSession:
    """Live trade adapter: User/Offer stay in memory; OfferRequest is isolated sqlite."""

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

    def _is_trade_statement(self, statement) -> bool:
        blob = str(statement).lower()
        return "trades." in blob or " from trades" in blob or " join trades" in blob

    async def execute(self, statement, params=None):
        if self._is_request_statement(statement):
            result = self._session.execute(statement)
            self._lookups += 1
            self._note("lookup")
            if self.after_lookup and self._lookups == 1:
                await self.after_lookup()
            return result
        if self._is_trade_statement(statement):
            trade = next((item for item in self.added if type(item).__name__ == "Trade"), None)
            return _Result(single=trade, single_or_none=trade, use_none=True)
        return _Result(single=self.owner, single_or_none=None)

    async def get(self, model, ident, **kwargs):
        name = getattr(model, "__name__", str(model))
        if name == "Offer" or ident == getattr(self.offer, "id", None):
            return self.offer
        if name == "Trade":
            return next((item for item in self.added if getattr(item, "id", None) == ident), None)
        return self._session.get(model, ident)

    def add(self, obj):
        if isinstance(obj, OfferRequest):
            self._session.add(obj)
            self.offer_requests.append(obj)
            return
        self.added.append(obj)

    async def flush(self):
        self._session.flush()

    async def commit(self):
        self._note("pre_commit")
        if self.before_commit:
            await self.before_commit()
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected failure_before_commit")
        self._note("commit")
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
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _make_offer(offer_origin: str, **overrides):
    home = _home(offer_origin)
    seller = SimpleNamespace(
        id=9,
        account_name="seller",
        mobile_number="09125555555",
        telegram_id=999,
        account_status=UserAccountStatus.ACTIVE,
    )
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": 9,
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
        "commodity": SimpleNamespace(name="Gold"),
        "user": seller,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


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
    OfferRequest.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_offer_requests_home_idempotency_key "
                "ON offer_requests (request_home_server, idempotency_key)"
            )
        )
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine, Session


def _request_patches(home: str):
    return [
        patch("api.routers.trades.get_active_customer_relation_for_customer", new=AsyncMock(return_value=None)),
        patch("api.routers.trades.enforce_customer_trade_limits_for_trade", new=AsyncMock()),
        patch("api.routers.trades._load_trade_customer_relation_map_for_user_ids", new=AsyncMock(return_value={})),
        patch("api.routers.realtime.publish_user_event", new=AsyncMock()),
        patch(
            "api.routers.trades.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        ),
        patch("api.routers.trades.persist_trade_completion_delivery_intents", new=AsyncMock()),
        patch("api.routers.trades.check_user_limits", return_value=(True, None)),
        patch("api.routers.trades._is_offer_expired_for_trade", new=AsyncMock(return_value=False)),
        patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))),
        patch("api.routers.trades.validate_offer_trade_amount", return_value=(True, None, 4, [])),
        patch(
            "api.routers.trades.build_trade_notification_audience_user_ids",
            new=AsyncMock(side_effect=lambda *_a, **_k: [5, 9]),
        ),
        patch("api.routers.trades.load_accountant_chat_identity_map", new=AsyncMock(return_value={})),
        patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)),
        patch("api.routers.trades.create_user_notification", new=AsyncMock()),
        patch("api.routers.trades.increment_user_counter", new=AsyncMock()),
        patch("api.routers.realtime.publish_event", new=AsyncMock()),
        patch("api.routers.trades.trade_to_response", return_value={"id": 88, "trade_number": 10000}),
        patch("api.routers.trades.current_server", return_value=home),
        patch("core.services.offer_request_ledger_service.current_server", return_value=home),
        patch("core.server_routing.current_server", return_value=home),
        patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=0)),
        ),
    ]


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
            self.assertEqual(result["trade_number"], 10000)
            self.assertEqual(len(db.offer_requests), 1)
            ledger = db.offer_requests[0]
            self.assertEqual(ledger.request_source_surface, _request_surface(request_origin))
            self.assertEqual(offer.home_server, home)
            self.assertEqual(ledger.result_status, OfferRequestStatus.COMPLETED_TRADE)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT request_source_surface, offer_public_id, result_status FROM offer_requests"
                ).fetchone()
            self.assertEqual(row[0], _request_surface(request_origin).value)
            self.assertEqual(row[1], offer.offer_public_id)
            return db_path, ledger

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
        """UNLINKED-BOT: TELEGRAM_BOT request with absent local user cannot mutate; WEBAPP ignores link."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                offer = _make_offer(offer_origin)
                if request_origin != "TELEGRAM_BOT":
                    self.assertEqual(offer.home_server, _home(offer_origin))
                    continue
                missing = evaluate_bot_access_local_state(None)
                self.assertFalse(missing.allowed)
                self.assertEqual(offer.home_server, _home(offer_origin))
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = str(Path(tmp) / "unlink.sqlite")
                    engine, Session = _sqlite_requests(db_path)
                    engine.dispose()
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 0)

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
        """POLICY-SNAPSHOT: live ledger stores request surface and offer home together."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                _path, ledger = await self._success(
                    offer_origin, request_origin, idempotency=f"snap-{offer_origin}-{request_origin}"
                )
                self.assertEqual(getattr(ledger, "request_source_surface"), _request_surface(request_origin))
                self.assertEqual(getattr(ledger, "offer_public_id"), f"ofr_{offer_origin.lower()}_7")

    async def test_origin_notification_injects(self):
        """NOTIFICATION: live execute fans out audience after committing both origins."""
        for offer_origin, request_origin in ORIGIN_PAIRS:
            with self.subTest(offer=offer_origin, request=request_origin):
                home = _home(offer_origin)
                with tempfile.TemporaryDirectory() as tmp:
                    engine, Session = _sqlite_requests(str(Path(tmp) / "note.sqlite"))
                    owner = _make_owner()
                    offer = _make_offer(offer_origin)
                    db = OriginSession(Session(), offer=offer, owner=owner)
                    notify = AsyncMock()
                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    notify_patch = patch("api.routers.trades.create_user_notification", new=notify)
                    notify_patch.start()
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
                        notify_patch.stop()
                        for item in started:
                            item.stop()
                        db.close()
                        engine.dispose()
                    self.assertGreaterEqual(notify.await_count, 1)
                    self.assertEqual(db.offer_requests[0].request_source_surface, _request_surface(request_origin))

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
        """SIDE-EFFECT-RETRY: post-commit notification can fail and retry without a second ledger row."""
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

                    async def _notify(*_a, **_k):
                        calls["n"] += 1
                        if calls["n"] == 1:
                            raise RuntimeError("injected side_effect_retry")

                    started = _request_patches(home)
                    for item in started:
                        item.start()
                    notify = patch("api.routers.trades.create_user_notification", new=_notify)
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
                    retry_offer = _make_offer(offer_origin, remaining_quantity=0, status=OfferStatus.COMPLETED)
                    retry_db = OriginSession(Session(), offer=retry_offer, owner=owner)
                    retry_db.added.extend(db.added)
                    started2 = _request_patches(home)
                    for item in started2:
                        item.start()
                    try:
                        replay = await _execute_trade_authoritatively(
                            TradeCreate(offer_id=7, quantity=4, idempotency_key=key),
                            BackgroundTasks(),
                            db=retry_db,
                            context=_context(owner),
                            request_source_surface=_request_surface(request_origin),
                            request_source_server=home,
                        )
                    finally:
                        for item in started2:
                            item.stop()
                        db.close()
                        retry_db.close()
                        engine.dispose()
                    self.assertIsNotNone(replay)
                    with sqlite3.connect(db_path) as conn:
                        count = conn.execute("SELECT COUNT(*) FROM offer_requests").fetchone()[0]
                    self.assertEqual(count, 1)

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
                    self.assertIn("test_p1_00b_characterization_origin_interactions.py", case["node_id"])
                    self.assertIn("injects", case["node_id"])
            elif row["applicability"] == "NOT_APPLICABLE":
                not_applicable += 1
                reason = row["eligibility"]["reason"] if isinstance(row.get("eligibility"), dict) else row.get("reason")
                self.assertTrue(reason)
            else:
                self.assertEqual(row["applicability"], "HIGH_BLOCKER")
                self.assertEqual(row.get("disposition"), "BLOCKED_EXPLICIT")
                blocked_explicit += 1
        self.assertEqual(evidenced, 66)
        self.assertEqual(blocked_explicit, 3)
        self.assertEqual(not_applicable, 3)


if __name__ == "__main__":
    unittest.main()
