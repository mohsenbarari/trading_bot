import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.offers import OfferCreate, create_offer
from core.enums import UserRole
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class FakeDB:
    def __init__(self, *, get_results=None, execute_results=None):
        self.get_results = list(get_results or [])
        self.execute_results = list(execute_results or [])
        self.commit = AsyncMock()
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.added = []

    async def get(self, _model, _id):
        if not self.get_results:
            raise AssertionError("Unexpected get() call")
        return self.get_results.pop(0)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, item):
        self.added.append(item)

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77
        if getattr(item, "created_at", None) is None:
            item.created_at = datetime(2026, 1, 1, 12, 0, 0)
        return item


def make_offer(**overrides):
    data = {
        "offer_type": "buy",
        "commodity_id": 1,
        "quantity": 10,
        "price": 123456,
        "is_wholesale": True,
        "lot_sizes": None,
        "notes": "urgent",
        "republished_from_id": None,
    }
    data.update(overrides)
    return OfferCreate(**data)


def make_user(**overrides):
    data = {
        "id": 5,
        "role": UserRole.STANDARD,
        "trading_restricted_until": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_reloaded_offer(*, offer_id=77, channel_message_id=None, notes="urgent"):
    return SimpleNamespace(
        id=offer_id,
        user_id=5,
        offer_type=OfferType.BUY,
        commodity_id=1,
        commodity=SimpleNamespace(name="Gold"),
        user=SimpleNamespace(account_name="user1"),
        quantity=10,
        remaining_quantity=10,
        price=123456,
        is_wholesale=True,
        lot_sizes=None,
        original_lot_sizes=None,
        notes=notes,
        status=OfferStatus.ACTIVE,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        channel_message_id=channel_message_id,
    )


class OffersRouterCreateSuccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_offer_stamps_home_server_and_links_republished_offer(self):
        commodity = SimpleNamespace(id=1)
        old_offer = SimpleNamespace(user_id=5, status=OfferStatus.ACTIVE, republished_offer_id=None)
        reloaded_offer = make_reloaded_offer(offer_id=77)
        db = FakeDB(
            get_results=[commodity, old_offer],
            execute_results=[FakeExecuteResult(reloaded_offer)],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=30)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch("api.routers.offers.current_server", return_value="iran"), patch(
            "api.routers.offers.send_offer_to_channel",
            new=AsyncMock(return_value=None),
        ), patch("core.cache.incr_active_offer_count", new=AsyncMock()) as incr_mock, patch(
            "api.routers.offers.increment_user_counter",
            new=AsyncMock(),
        ) as counter_mock, patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 77, "user_id": 5},
        ) as response_mock:
            result = await create_offer(
                make_offer(republished_from_id=99),
                db=db,
                current_user=current_user,
            )

        new_offer = db.added[0]
        self.assertEqual(new_offer.user_id, 5)
        self.assertEqual(new_offer.home_server, "iran")
        self.assertEqual(new_offer.offer_type, OfferType.BUY)
        self.assertEqual(old_offer.status, OfferStatus.EXPIRED)
        self.assertEqual(old_offer.republished_offer_id, 77)
        self.assertEqual(db.commit.await_count, 2)
        incr_mock.assert_awaited_once_with(5)
        counter_mock.assert_awaited_once_with(db, current_user, "channel_message")
        publish_mock.assert_awaited_once()
        response_mock.assert_called_once_with(reloaded_offer, async_settings, viewer_user_id=5, include_owner_identity=True)
        self.assertEqual(result, {"id": 77, "user_id": 5})

    async def test_create_offer_persists_channel_message_and_publishes_created_event(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=88)
        db = FakeDB(
            get_results=[commodity],
            execute_results=[FakeExecuteResult(reloaded_offer)],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.send_offer_to_channel",
            new=AsyncMock(return_value=555),
        ), patch("core.cache.incr_active_offer_count", new=AsyncMock()), patch(
            "api.routers.offers.increment_user_counter",
            new=AsyncMock(),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 88, "channel_message_id": 555},
        ) as response_mock:
            result = await create_offer(make_offer(), db=db, current_user=current_user)

        new_offer = db.added[0]
        self.assertEqual(new_offer.home_server, "foreign")
        self.assertEqual(reloaded_offer.channel_message_id, 555)
        self.assertEqual(db.commit.await_count, 2)
        publish_mock.assert_awaited_once_with(
            "offer:created",
            {
                "id": 88,
                "user_id": None,
                "offer_type": "buy",
                "commodity_id": 1,
                "commodity_name": "Gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 123456,
                "status": "active",
                "created_at": unittest.mock.ANY,
                "user_account_name": "",
                "is_own_offer": False,
                "notes": "urgent",
                "is_wholesale": True,
                "lot_sizes": None,
                "original_lot_sizes": None,
                "expires_at_ts": int(reloaded_offer.created_at.timestamp() + 15 * 60),
            },
        )
        response_mock.assert_called_once_with(reloaded_offer, async_settings, viewer_user_id=5, include_owner_identity=True)
        self.assertEqual(result, {"id": 88, "channel_message_id": 555})

    async def test_create_offer_tolerates_sse_expiry_calculation_failures(self):
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=99)
        reloaded_offer.created_at = SimpleNamespace(timestamp=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        db = FakeDB(
            get_results=[commodity],
            execute_results=[FakeExecuteResult(reloaded_offer)],
        )
        current_user = make_user()
        settings = SimpleNamespace(max_active_offers=5)
        async_settings = SimpleNamespace(offer_expiry_minutes=15)

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.get_trading_settings",
            return_value=settings,
        ), patch("core.cache.get_active_offer_count", new=AsyncMock(return_value=0)), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "api.routers.offers.send_offer_to_channel",
            new=AsyncMock(return_value=None),
        ), patch("core.cache.incr_active_offer_count", new=AsyncMock()), patch(
            "api.routers.offers.increment_user_counter",
            new=AsyncMock(),
        ), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=async_settings),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 99},
        ), patch("api.routers.offers.to_jalali_str", return_value=""):
            result = await create_offer(make_offer(), db=db, current_user=current_user)

        publish_mock.assert_awaited_once_with(
            "offer:created",
            {
                "id": 99,
                "user_id": None,
                "offer_type": "buy",
                "commodity_id": 1,
                "commodity_name": "Gold",
                "quantity": 10,
                "remaining_quantity": 10,
                "price": 123456,
                "status": "active",
                "created_at": "",
                "user_account_name": "",
                "is_own_offer": False,
                "notes": "urgent",
                "is_wholesale": True,
                "lot_sizes": None,
                "original_lot_sizes": None,
                "expires_at_ts": None,
            },
        )
        self.assertEqual(result, {"id": 99})


if __name__ == "__main__":
    unittest.main()