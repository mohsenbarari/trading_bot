import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from api.routers.trades import TradeCreate, _execute_trade_authoritatively, _is_time_limit_expired_offer
from core.enums import UserAccountStatus, UserRole
from core.services.offer_expiry_service import OfferExpiryReason
from models.offer import OfferStatus, OfferType
from models.offer_request import OfferRequest, OfferRequestStatus


class FakeExecuteResult:
    def __init__(self, *, single=None, single_or_none=None):
        self._single = single
        self._single_or_none = single_or_none

    def scalar_one(self):
        return self._single

    def scalar_one_or_none(self):
        return self._single_or_none


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None, scalar_result=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.scalar_result = scalar_result
        self.refresh = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.added = []
        self.offer_requests = []

    async def get(self, _model, _id, **_kwargs):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def scalar(self, _stmt):
        return self.scalar_result

    def add(self, item):
        if isinstance(item, OfferRequest):
            self.offer_requests.append(item)
            return
        self.added.append(item)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "trading_restricted_until": None,
        "mobile_number": "09120000000",
        "account_name": "user5",
        "telegram_id": 555,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_offer(**overrides):
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": 9,
        "quantity": 10,
        "remaining_quantity": 10,
        "is_wholesale": False,
        "lot_sizes": [6, 4],
        "offer_type": OfferType.SELL,
        "price": 123456,
        "offer_public_id": "ofr_guard_7",
        "home_server": "foreign",
        "commodity_id": 1,
        "commodity": SimpleNamespace(name="Gold"),
        "user": SimpleNamespace(account_name="owner", mobile_number="09125555555", telegram_id=999),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_context(owner_user, actor_user=None):
    actor = actor_user or owner_user
    return SimpleNamespace(owner_user=owner_user, actor_user=actor, relation=None, is_accountant_context=owner_user.id != actor.id)


class TradesRouterAuthoritativeGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        customer_relation_patcher = patch(
            "api.routers.trades.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        )
        customer_relation_patcher.start()
        self.addCleanup(customer_relation_patcher.stop)
        trade_relation_map_patcher = patch(
            "api.routers.trades._load_trade_customer_relation_map_for_user_ids",
            new=AsyncMock(return_value={}),
        )
        trade_relation_map_patcher.start()
        self.addCleanup(trade_relation_map_patcher.stop)
        market_eval_patcher = patch(
            "api.routers.trades.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True, reason="daily_window_open")),
        )
        self.market_eval_mock = market_eval_patcher.start()
        self.addCleanup(market_eval_patcher.stop)

    async def test_execute_trade_authoritatively_rejects_watch_restricted_and_limit_failures(self):
        trade_data = TradeCreate(offer_id=7, quantity=4)

        with self.assertRaises(HTTPException) as exc_info:
            await _execute_trade_authoritatively(
                trade_data,
                BackgroundTasks(),
                db=FakeDB(),
                context=make_context(make_user(id=5), make_user(id=9)),
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حسابدار دسترسی به بازار ندارد.")

        with self.assertRaises(HTTPException) as exc_info:
            await _execute_trade_authoritatively(
                trade_data,
                BackgroundTasks(),
                db=FakeDB(),
                context=make_context(make_user(role=UserRole.WATCH)),
            )
        self.assertEqual(exc_info.exception.status_code, 403)

        with self.assertRaises(HTTPException) as exc_info:
            await _execute_trade_authoritatively(
                trade_data,
                BackgroundTasks(),
                db=FakeDB(),
                context=make_context(make_user(trading_restricted_until=datetime.utcnow() + timedelta(minutes=10))),
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب شما مسدود است.")

        with self.assertRaises(HTTPException) as exc_info:
            await _execute_trade_authoritatively(
                trade_data,
                BackgroundTasks(),
                db=FakeDB(),
                context=make_context(make_user(account_status=UserAccountStatus.INACTIVE)),
            )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "حساب شما غیرفعال است و امکان انجام معامله ندارید.")

        self.market_eval_mock.return_value = SimpleNamespace(is_open=False, reason="after_daily_window_close")
        with patch("api.routers.trades.check_user_limits") as limits_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    trade_data,
                    BackgroundTasks(),
                    db=FakeDB(),
                    context=make_context(make_user()),
                )
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(
            exc_info.exception.detail,
            "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.",
        )
        limits_mock.assert_not_called()
        self.market_eval_mock.return_value = SimpleNamespace(is_open=True, reason="daily_window_open")

        db = FakeDB(execute_results=[FakeExecuteResult(single=make_user())])
        with patch("api.routers.trades.check_user_limits", return_value=(False, "trade blocked")):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(trade_data, BackgroundTasks(), db=db, context=make_context(make_user()))
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "trade blocked")

    async def test_execute_trade_authoritatively_rejects_manual_expired_offer_even_inside_edge_grace(self):
        locked_user = make_user()
        manual_expired_offer = make_offer(status=OfferStatus.EXPIRED, expire_reason=OfferExpiryReason.MANUAL)
        db = FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[manual_expired_offer])

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))) as block_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    BackgroundTasks(),
                    db=db,
                    context=make_context(locked_user),
                    edge_received_at=datetime.utcnow(),
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "این لفظ دیگر فعال نیست.")
        self.assertEqual(len(db.offer_requests), 1)
        self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_OFFER_EXPIRED)
        self.assertEqual(db.offer_requests[0].public_failure_code, "offer_not_active")
        block_mock.assert_not_awaited()
        db.commit.assert_awaited_once()

    def test_is_time_limit_expired_offer_only_matches_time_limit_reason(self):
        self.assertTrue(
            _is_time_limit_expired_offer(
                make_offer(status=OfferStatus.EXPIRED, expire_reason=OfferExpiryReason.TIME_LIMIT)
            )
        )
        self.assertFalse(
            _is_time_limit_expired_offer(
                make_offer(status=OfferStatus.EXPIRED, expire_reason=OfferExpiryReason.MANUAL)
            )
        )
        self.assertFalse(
            _is_time_limit_expired_offer(
                make_offer(status=OfferStatus.ACTIVE, expire_reason=OfferExpiryReason.TIME_LIMIT)
            )
        )

    async def test_execute_trade_authoritatively_rejects_missing_inactive_self_and_blocked_offers(self):
        trade_data = TradeCreate(offer_id=7, quantity=4)
        locked_user = make_user()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    trade_data,
                    BackgroundTasks(),
                    db=FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[None]),
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 404)

        inactive_offer = make_offer(status=OfferStatus.EXPIRED)
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=True),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    trade_data,
                    BackgroundTasks(),
                    db=FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[inactive_offer]),
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "این لفظ دیگر فعال نیست.")

        own_offer = make_offer(user_id=5)
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    trade_data,
                    BackgroundTasks(),
                    db=FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[own_offer]),
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "نمی‌توانید روی لفظ خودتان معامله کنید.")

        blocked_offer = make_offer()
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(True, None))):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    trade_data,
                    BackgroundTasks(),
                    db=FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[blocked_offer]),
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "امکان انجام این معامله وجود ندارد.")

    async def test_execute_trade_authoritatively_returns_lot_suggestion_payload(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[offer])

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(False, "این لات دیگر موجود نیست.", None, [6, 4]),
        ), patch(
            "api.routers.trades.build_lot_unavailable_suggestion_payload",
            return_value={"kind": "lot_suggestion", "available_amounts": [6, 4]},
        ):
            response = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=5),
                BackgroundTasks(),
                db=db,
                context=make_context(locked_user),
            )

        db.refresh.assert_awaited_once_with(offer, ["commodity"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.body), {"kind": "lot_suggestion", "available_amounts": [6, 4]})
        self.assertEqual(len(db.offer_requests), 1)
        self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_LOT_UNAVAILABLE)
        self.assertEqual(db.offer_requests[0].public_failure_code, "lot_unavailable")
        db.commit.assert_awaited_once()

    async def test_execute_trade_authoritatively_rejects_hot_offer_contention_with_ledger(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[offer])

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._try_lock_trade_offer_execution",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4, idempotency_key="idem-busy"),
                    BackgroundTasks(),
                    db=db,
                    context=make_context(locked_user),
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید.")
        self.assertEqual(db.added, [])
        self.assertEqual(len(db.offer_requests), 1)
        self.assertEqual(db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_CONFLICT)
        self.assertEqual(db.offer_requests[0].public_failure_code, "offer_contention")
        self.assertEqual(db.offer_requests[0].internal_failure_code, "offer_execution_lock_busy")
        self.assertIsNone(db.offer_requests[0].idempotency_key)
        self.assertEqual(db.offer_requests[0].internal_failure_context["idempotency_key_present"], True)
        db.commit.assert_awaited_once()

    async def test_execute_trade_authoritatively_rejects_invalid_amount_and_reuses_idempotent_trade(self):
        locked_user = make_user()
        offer = make_offer(is_wholesale=True, lot_sizes=None)

        invalid_db = FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[offer])
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(False, "bad amount", None, []),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=5),
                    BackgroundTasks(),
                    db=invalid_db,
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "bad amount")
        self.assertEqual(len(invalid_db.offer_requests), 1)
        self.assertEqual(invalid_db.offer_requests[0].result_status, OfferRequestStatus.REJECTED_BUSINESS_RULE)
        self.assertEqual(invalid_db.offer_requests[0].public_failure_code, "invalid_quantity")
        invalid_db.commit.assert_awaited_once()

        existing_trade = SimpleNamespace(id=88, offer_user_id=offer.user_id, responder_user_id=locked_user.id)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single_or_none=None),
                FakeExecuteResult(single_or_none=existing_trade),
            ],
            get_results=[offer],
        )
        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch(
            "api.routers.trades.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch("api.routers.trades.trade_to_response", return_value={"id": 88}) as response_mock:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4, idempotency_key="idem-1"),
                BackgroundTasks(),
                db=db,
                context=make_context(locked_user),
            )

        db.refresh.assert_awaited_once_with(offer, ["user", "commodity"])
        response_mock.assert_called_once_with(existing_trade, identity_map={}, customer_relation_map={})
        self.assertEqual(result, {"id": 88})

    async def test_execute_trade_authoritatively_rejects_duplicate_failed_request_without_mutation(self):
        locked_user = make_user()
        offer = make_offer(is_wholesale=True, lot_sizes=None)
        existing_ledger = SimpleNamespace(
            result_status=OfferRequestStatus.REJECTED_BUSINESS_RULE,
            public_failure_message="درخواست قبلی رد شده است.",
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single_or_none=existing_ledger),
            ],
            get_results=[offer],
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4, idempotency_key="failed-replay"),
                    BackgroundTasks(),
                    db=db,
                    context=make_context(locked_user),
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "درخواست قبلی رد شده است.")
        self.assertEqual(db.added, [])
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
