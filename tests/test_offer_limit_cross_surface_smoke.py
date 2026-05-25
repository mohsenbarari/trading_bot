import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.offers import OfferCreate, create_offer
from bot.handlers.trade_create import handle_text_offer_confirm
from core import trading_settings
from core.enums import UserRole
from models.offer import OfferStatus, OfferType


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class ApiSuccessDB:
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

    async def scalar(self, _stmt):
        raise AssertionError("scalar() should not be used in this success path")

    def add(self, item):
        self.added.append(item)

    async def _refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 701
        if getattr(item, "created_at", None) is None:
            item.created_at = datetime(2026, 1, 1, 12, 0, 0)
        return item


class ApiGuardDB:
    def __init__(self, shared_count):
        self.shared_count = shared_count

    async def scalar(self, _stmt):
        return self.shared_count["value"]

    async def get(self, _model, _id):
        return None


class ScalarSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])

    async def scalar(self, _stmt):
        if not self.scalar_values:
            raise AssertionError("Unexpected scalar() call")
        return self.scalar_values.pop(0)


class BotCreateSession:
    def __init__(self, shared_count):
        self.shared_count = shared_count
        self.added = []
        self._counted = False

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 901
        self.added.append(value)

    async def commit(self):
        if self.added and not self._counted:
            self.shared_count["value"] += 1
            self._counted = True

    async def refresh(self, _value):
        return None


class BotUpdateSession:
    def __init__(self, create_session):
        self.create_session = create_session

    async def get(self, model, key):
        if model.__name__ == "Offer":
            return self.create_session.added[0]
        if model.__name__ == "User":
            return SimpleNamespace(id=key)
        return None

    async def commit(self):
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


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


def make_reloaded_offer(*, offer_id=701):
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
        notes="urgent",
        status=OfferStatus.ACTIVE,
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        channel_message_id=None,
    )


class OfferLimitCrossSurfaceSmokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        trading_settings._fallback_cache = trading_settings.TradingSettings(max_active_offers=4)
        trading_settings._fallback_timestamp = time.time()

    def tearDown(self):
        trading_settings._fallback_cache = None
        trading_settings._fallback_timestamp = 0

    async def test_helper_sessions_cover_guard_and_fallback_paths(self):
        api_db = ApiSuccessDB()
        with self.assertRaisesRegex(AssertionError, "Unexpected get"):
            await api_db.get(None, 1)
        with self.assertRaisesRegex(AssertionError, "Unexpected execute"):
            await api_db.execute(None)
        with self.assertRaisesRegex(AssertionError, "should not be used"):
            await api_db.scalar(None)

        existing_item = SimpleNamespace(id=800, created_at=datetime(2026, 1, 2, 10, 0, 0))
        refreshed = await api_db._refresh(existing_item)
        self.assertIs(refreshed, existing_item)
        self.assertEqual(existing_item.id, 800)

        guard_db = ApiGuardDB({"value": 7})
        self.assertIsNone(await guard_db.get(None, 1))

        scalar_session = ScalarSession()
        with self.assertRaisesRegex(AssertionError, "Unexpected scalar"):
            await scalar_session.scalar(None)

        shared_count = {"value": 1}
        create_session = BotCreateSession(shared_count)
        await create_session.commit()
        self.assertEqual(shared_count["value"], 1)

        existing_offer = SimpleNamespace(id=55)
        create_session.add(existing_offer)
        self.assertEqual(existing_offer.id, 55)
        await create_session.commit()
        self.assertEqual(shared_count["value"], 2)
        await create_session.commit()
        self.assertEqual(shared_count["value"], 2)

        update_session = BotUpdateSession(create_session)
        self.assertIs(await update_session.get(type("Offer", (), {}), 1), existing_offer)
        user_obj = await update_session.get(type("User", (), {}), 9)
        self.assertEqual(user_obj.id, 9)
        self.assertIsNone(await update_session.get(type("Other", (), {}), 9))

    async def test_web_fifteenth_offer_blocks_bot_sixteenth_with_live_limit_message(self):
        shared_count = {"value": 14}
        live_settings = trading_settings.TradingSettings(max_active_offers=15, offer_expiry_minutes=30)
        commodity = SimpleNamespace(id=1)
        reloaded_offer = make_reloaded_offer(offer_id=701)
        db = ApiSuccessDB(get_results=[commodity], execute_results=[FakeExecuteResult(reloaded_offer)])

        async def fake_incr_active_offer_count(_user_id):
            shared_count["value"] += 1

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ), patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.get_trading_settings",
            return_value=live_settings,
        ), patch(
            "core.cache.get_active_offer_count",
            new=AsyncMock(return_value=shared_count["value"]),
        ), patch(
            "core.cache.incr_active_offer_count",
            new=AsyncMock(side_effect=fake_incr_active_offer_count),
        ), patch(
            "core.services.trade_service.validate_quantity",
            return_value=(True, None),
        ), patch("core.services.trade_service.validate_price", return_value=(True, None)), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch("api.routers.offers.current_server", return_value="foreign"), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.send_offer_to_channel",
            new=AsyncMock(return_value=777),
        ), patch(
            "api.routers.offers.register_market_offer_created",
            new=AsyncMock(),
        ), patch("api.routers.offers.increment_user_counter", new=AsyncMock()), patch(
            "api.routers.realtime.publish_event",
            new=AsyncMock(),
        ), patch(
            "api.routers.offers.offer_to_response",
            return_value={"id": 701, "channel_message_id": 777},
        ):
            result = await create_offer(make_offer(), db=db, current_user=make_user())

        self.assertEqual(result, {"id": 701, "channel_message_id": 777})
        self.assertEqual(shared_count["value"], 15)

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=500),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 10,
                    "trade_type": "buy",
                    "commodity_id": 1,
                    "commodity_name": "Gold",
                    "price": 123456,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )

        with patch("core.utils.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=live_settings,
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(ScalarSession([shared_count["value"]])),
        ):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=5, limitations_expire_at=None), bot=SimpleNamespace())

        error_text = callback.message.edit_text.await_args.args[0]
        self.assertIn("حداکثر 15 لفظ فعال", error_text)
        self.assertNotIn("حداکثر 4 لفظ فعال", error_text)

    async def test_bot_fifteenth_offer_blocks_web_sixteenth_with_live_limit_message(self):
        shared_count = {"value": 14}
        live_settings = trading_settings.TradingSettings(max_active_offers=15)

        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=500),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 10,
                    "trade_type": "buy",
                    "commodity_id": 1,
                    "commodity_name": "Gold",
                    "price": 123456,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": "bot-created",
                }
            ),
            clear=AsyncMock(),
        )
        create_session = BotCreateSession(shared_count)
        update_session = BotUpdateSession(create_session)
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=900), SimpleNamespace(message_id=901)])
        )

        with patch("core.utils.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "bot.handlers.trade_create._bot_market_is_open",
            new=AsyncMock(return_value=True),
        ), patch(
            "core.trading_settings.get_trading_settings",
            return_value=live_settings,
        ), patch(
            "core.services.trade_service.validate_competitive_price",
            new=AsyncMock(return_value=(True, None)),
        ), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ), patch("core.utils.increment_user_counter", new=AsyncMock()), patch(
            "bot.handlers.trade_create.settings",
            SimpleNamespace(channel_id=-100, bot_username="botname"),
        ), patch(
            "core.trading_settings._run_async_settings_loader_sync",
            return_value=live_settings,
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(ScalarSession([shared_count["value"]])),
                FakeSessionContext(ScalarSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(update_session),
            ],
        ):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=5, limitations_expire_at=None), bot=bot)

        self.assertEqual(shared_count["value"], 15)
        self.assertIn("منتشر شد", callback.message.edit_text.await_args.args[0])

        with patch("api.routers.offers.check_user_limits", side_effect=[(True, None), (True, None)]), patch(
            "api.routers.offers.evaluate_current_market_schedule",
            new=AsyncMock(return_value=SimpleNamespace(is_open=True)),
        ), patch(
            "api.routers.offers.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.offers.get_trading_settings",
            return_value=live_settings,
        ), patch(
            "core.cache.get_active_offer_count",
            new=AsyncMock(return_value=None),
        ), patch("core.cache.set_active_offer_count", new=AsyncMock()), patch(
            "core.services.trade_service.detect_offer_price_warning",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_offer(make_offer(), db=ApiGuardDB(shared_count), current_user=make_user())

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "شما حداکثر 15 لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید.",
        )
        self.assertNotIn("4 لفظ فعال", exc_info.exception.detail)


if __name__ == "__main__":
    unittest.main()