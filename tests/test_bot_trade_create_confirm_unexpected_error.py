import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_trade_confirm
from models.offer import OfferStatus
from tests.offer_creation_quota_test_helpers import bypass_local_offer_quota


class FakeSession:
    def __init__(self, scalar_values=None, stored_offer=None):
        self.scalar_values = list(scalar_values or [])
        self.stored_offer = stored_offer
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 56
        self.added.append(value)
        self.stored_offer = value

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    async def get(self, model, key):
        return self.stored_offer


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateConfirmUnexpectedErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.market_patcher = patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True))
        self.market_patcher.start()
        self.addCleanup(self.market_patcher.stop)
        self.admission_patcher = patch(
            "core.services.offer_creation_service.acquire_market_offer_admission_fence",
            new=AsyncMock(),
        )
        self.admission_patcher.start()
        self.addCleanup(self.admission_patcher.stop)
        self.quota_patcher = patch(
            "core.services.offer_creation_service._admit_local_offer_quota",
            new=AsyncMock(side_effect=bypass_local_offer_quota),
        )
        self.quota_patcher.start()
        self.addCleanup(self.quota_patcher.stop)
        self.validation_settings_patcher = patch(
            "core.services.trade_service.get_trading_settings",
            return_value=SimpleNamespace(
                offer_min_quantity=1,
                offer_max_quantity=100_000,
                lot_min_size=1,
                lot_max_count=3,
            ),
        )
        self.validation_settings_patcher.start()
        self.addCleanup(self.validation_settings_patcher.stop)
        self.redis_connection_guard = patch(
            "core.redis.pool.get_connection",
            new=AsyncMock(side_effect=AssertionError("unexpected real Redis access")),
        )
        self.redis_connection_guard.start()
        self.addCleanup(self.redis_connection_guard.stop)
        self.database_connection_guard = patch(
            "core.db.AsyncSessionLocal",
            side_effect=AssertionError("unexpected real PostgreSQL access"),
        )
        self.database_connection_guard.start()
        self.addCleanup(self.database_connection_guard.stop)

    async def test_fake_session_helpers_cover_stored_offer_and_empty_rollback(self):
        session = FakeSession(stored_offer="stored")
        self.assertEqual(await session.get(None, None), "stored")

        already_identified = SimpleNamespace(id=999)
        session.add(already_identified)
        self.assertEqual(already_identified.id, 999)

        create_session = FakeSession()

        async def rollback_get(model, key):
            if create_session.added:
                return create_session.added[0]
            return None

        self.assertIsNone(await rollback_get(None, None))
        ctx = FakeSessionContext(session)
        self.assertIs(await ctx.__aenter__(), session)
        self.assertFalse(await ctx.__aexit__(None, None, None))

    async def test_handle_trade_confirm_rolls_back_offer_on_unexpected_error(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 12,
                    "trade_type": "buy",
                    "commodity_name": "سکه",
                    "price": 123456,
                    "commodity_id": 7,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )
        create_session = FakeSession()
        publish_session = FakeSession()
        rollback_session = FakeSession()

        async def rollback_get(model, key):
            if create_session.added:
                return create_session.added[0]
            return None

        publish_session.get = rollback_get
        rollback_session.get = rollback_get
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(publish_session),
                FakeSessionContext(rollback_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=bot)

        self.assertEqual(create_session.added[0].status, OfferStatus.EXPIRED)
        self.assertIn("لفظ ثبت نشد", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()

    async def test_handle_trade_confirm_logs_when_unexpected_error_rollback_fails(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 12,
                    "trade_type": "buy",
                    "commodity_name": "سکه",
                    "price": 123456,
                    "commodity_id": 7,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )
        create_session = FakeSession()
        publish_session = FakeSession()
        rollback_session = FakeSession()

        async def publish_get(model, key):
            if create_session.added:
                return create_session.added[0]
            return None

        publish_session.get = publish_get
        rollback_session.get = AsyncMock(side_effect=RuntimeError("rollback boom"))
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(publish_session),
                FakeSessionContext(rollback_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ), patch("bot.handlers.trade_create.logger.debug") as debug_mock:
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=bot)

        debug_mock.assert_called_once()
        self.assertIn("لفظ ثبت نشد", callback.message.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
