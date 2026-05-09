import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks, HTTPException

from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.enums import NotificationCategory, NotificationLevel, UserRole
from models.offer import OfferStatus, OfferType
from models.trade import TradeStatus, TradeType


class FakeStaleDataError(Exception):
    pass


class FakeExecuteResult:
    def __init__(self, *, single=None, single_or_none=None):
        self._single = single
        self._single_or_none = single_or_none

    def scalar_one(self):
        return self._single

    def scalar_one_or_none(self):
        return self._single_or_none


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None, scalar_result=None, commit_side_effect=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.scalar_result = scalar_result
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.rollback = AsyncMock()
        self.refresh = AsyncMock()
        self.added = []

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
        self.added.append(item)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "trading_restricted_until": None,
        "mobile_number": "09120000000",
        "account_name": "buyer",
        "telegram_id": 555,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_offer(**overrides):
    data = {
        "id": 7,
        "status": OfferStatus.ACTIVE,
        "user_id": 9,
        "quantity": 4,
        "remaining_quantity": 4,
        "is_wholesale": True,
        "lot_sizes": None,
        "offer_type": OfferType.SELL,
        "price": 123456,
        "commodity_id": 1,
        "commodity": SimpleNamespace(name="Gold"),
        "user": SimpleNamespace(account_name="seller", mobile_number="09125555555", telegram_id=999),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TradesRouterAuthoritativeSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_trade_authoritatively_converts_stale_commit_to_conflict(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(
            get_results=[offer],
            execute_results=[FakeExecuteResult(single=locked_user), FakeExecuteResult(single_or_none=None)],
            commit_side_effect=FakeStaleDataError("stale"),
            scalar_result=10000,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    BackgroundTasks(),
                    db=db,
                    current_user=locked_user,
                )

        db.rollback.assert_awaited_once()
        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید.")

    async def test_execute_trade_authoritatively_persists_trade_and_runs_side_effects(self):
        locked_user = make_user()
        offer = make_offer()
        reloaded_trade = SimpleNamespace(id=88)
        user_for_counter = make_user()
        db = FakeDB(
            get_results=[offer, user_for_counter],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=9999,
        )
        background_tasks = BackgroundTasks()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ), patch("api.routers.trades.update_channel_buttons", new=AsyncMock(return_value=True)) as update_buttons_mock, patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 88, "trade_number": 10000},
        ) as response_mock:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=4),
                background_tasks,
                db=db,
                current_user=locked_user,
            )

        self.assertEqual(len(db.added), 1)
        new_trade = db.added[0]
        self.assertEqual(new_trade.trade_number, 10000)
        self.assertEqual(new_trade.offer_id, 7)
        self.assertEqual(new_trade.trade_type, TradeType.BUY)
        self.assertEqual(new_trade.status, TradeStatus.COMPLETED)
        self.assertEqual(new_trade.quantity, 4)
        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        db.refresh.assert_awaited_once_with(offer, ["user", "commodity"])
        db.commit.assert_awaited_once()
        update_buttons_mock.assert_awaited_once_with(offer)
        self.assertEqual(len(background_tasks.tasks), 2)
        self.assertEqual(notif_mock.await_count, 2)
        self.assertEqual(
            notif_mock.await_args_list[0].kwargs,
            {
                "level": NotificationLevel.SUCCESS,
                "category": NotificationCategory.TRADE,
            },
        )
        counter_mock.assert_awaited_once_with(db, user_for_counter, "trade", 4)
        self.assertEqual(publish_mock.await_count, 2)
        self.assertEqual(publish_mock.await_args_list[0].args[0], "trade:created")
        self.assertEqual(publish_mock.await_args_list[1].args[0], "offer:updated")
        self.assertEqual(publish_mock.await_args_list[1].args[1]["status"], "completed")
        response_mock.assert_called_once_with(reloaded_trade)
        self.assertEqual(result, {"id": 88, "trade_number": 10000})

    async def test_execute_trade_authoritatively_updates_retail_lots_and_tolerates_side_effect_failures(self):
        locked_user = make_user()
        offer = make_offer(
            offer_type=OfferType.BUY,
            is_wholesale=False,
            lot_sizes=[3, 1],
            quantity=4,
            remaining_quantity=4,
        )
        reloaded_trade = SimpleNamespace(id=89)
        user_for_counter = make_user()
        db = FakeDB(
            get_results=[offer, user_for_counter],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10001,
        )
        background_tasks = BackgroundTasks()

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 3, []),
        ), patch("sqlalchemy.orm.attributes.flag_modified") as flag_modified, patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(side_effect=RuntimeError("button failure")),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(side_effect=RuntimeError("notif failure")),
        ) as notif_mock, patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 89, "trade_number": 10001},
        ) as response_mock, patch("api.routers.trades.logger") as logger:
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=3),
                background_tasks,
                db=db,
                current_user=locked_user,
            )

        self.assertEqual(offer.remaining_quantity, 1)
        self.assertEqual(offer.lot_sizes, [1])
        flag_modified.assert_called_once_with(offer, "lot_sizes")
        self.assertEqual(len(background_tasks.tasks), 2)
        notif_mock.assert_awaited_once()
        counter_mock.assert_awaited_once_with(db, user_for_counter, "trade", 3)
        self.assertEqual(publish_mock.await_count, 2)
        logger.error.assert_called_once()
        response_mock.assert_called_once_with(reloaded_trade)
        self.assertEqual(result, {"id": 89, "trade_number": 10001})

    async def test_execute_trade_authoritatively_allows_full_remaining_retail_trade_and_clears_lots(self):
        locked_user = make_user()
        offer = make_offer(
            offer_type=OfferType.BUY,
            is_wholesale=False,
            lot_sizes=[20, 15, 10],
            quantity=45,
            remaining_quantity=25,
        )
        reloaded_trade = SimpleNamespace(id=90)
        user_for_counter = make_user()
        db = FakeDB(
            get_results=[offer, user_for_counter],
            execute_results=[
                FakeExecuteResult(single=locked_user),
                FakeExecuteResult(single=reloaded_trade),
            ],
            scalar_result=10002,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 25, [25, 15, 10]),
        ), patch("sqlalchemy.orm.attributes.flag_modified") as flag_modified, patch(
            "api.routers.trades.update_channel_buttons",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.trades.create_user_notification",
            new=AsyncMock(),
        ), patch(
            "api.routers.trades.increment_user_counter",
            new=AsyncMock(),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()), patch(
            "api.routers.trades.trade_to_response",
            return_value={"id": 90, "trade_number": 10003},
        ):
            result = await _execute_trade_authoritatively(
                TradeCreate(offer_id=7, quantity=25),
                BackgroundTasks(),
                db=db,
                current_user=locked_user,
            )

        self.assertEqual(offer.remaining_quantity, 0)
        self.assertEqual(offer.status, OfferStatus.COMPLETED)
        self.assertIsNone(offer.lot_sizes)
        flag_modified.assert_called_once_with(offer, "lot_sizes")
        self.assertEqual(result, {"id": 90, "trade_number": 10003})

    async def test_execute_trade_authoritatively_reraises_non_stale_commit_errors(self):
        locked_user = make_user()
        offer = make_offer()
        db = FakeDB(
            get_results=[offer],
            execute_results=[FakeExecuteResult(single=locked_user), FakeExecuteResult(single_or_none=None)],
            commit_side_effect=RuntimeError("db boom"),
            scalar_result=10002,
        )

        with patch("api.routers.trades.check_user_limits", return_value=(True, None)), patch(
            "api.routers.trades._is_offer_expired_for_trade",
            new=AsyncMock(return_value=False),
        ), patch("core.services.block_service.is_blocked", new=AsyncMock(return_value=(False, None))), patch(
            "api.routers.trades.validate_offer_trade_amount",
            return_value=(True, None, 4, []),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                await _execute_trade_authoritatively(
                    TradeCreate(offer_id=7, quantity=4),
                    BackgroundTasks(),
                    db=db,
                    current_user=locked_user,
                )

        self.assertEqual(str(exc_info.exception), "db boom")


if __name__ == "__main__":
    unittest.main()