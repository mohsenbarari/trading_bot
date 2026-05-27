import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.enums import UserAccountStatus, UserRole
from models.offer import OfferStatus, OfferType


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

    async def test_execute_trade_authoritatively_rejects_invalid_amount_and_reuses_idempotent_trade(self):
        locked_user = make_user()
        offer = make_offer(is_wholesale=True, lot_sizes=None)

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
                    db=FakeDB(execute_results=[FakeExecuteResult(single=locked_user)], get_results=[offer]),
                    context=make_context(locked_user),
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "bad amount")

        existing_trade = SimpleNamespace(id=88, offer_user_id=offer.user_id, responder_user_id=locked_user.id)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(single=locked_user),
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


if __name__ == "__main__":
    unittest.main()