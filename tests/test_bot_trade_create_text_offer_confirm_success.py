import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 77
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    async def get(self, model, key):
        if self.added and model.__name__ == "Offer":
            return self.added[0]
        if model.__name__ == "User":
            return SimpleNamespace(id=key)
        return None


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateTextOfferConfirmSuccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.market_patcher = patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True))
        self.market_patcher.start()
        self.addCleanup(self.market_patcher.stop)

    async def test_fake_session_helpers_cover_existing_ids_and_empty_update_get(self):
        session = FakeSession()
        already_identified = SimpleNamespace(id=700)
        session.add(already_identified)
        self.assertEqual(already_identified.id, 700)
        self.assertIsNone(await session.commit())
        self.assertIsNone(await session.refresh(already_identified))

        offered = SimpleNamespace(id=None)
        session.add(offered)
        self.assertEqual(offered.id, 77)
        self.assertIs(await session.get(type("Offer", (), {}), 1), already_identified)
        self.assertEqual((await session.get(type("User", (), {}), 9)).id, 9)
        self.assertIsNone(await session.get(type("Other", (), {}), 3))

        context = FakeSessionContext(session)
        self.assertIs(await context.__aenter__(), session)
        self.assertFalse(await context.__aexit__(None, None, None))

        create_session = FakeSession()
        created_offer = SimpleNamespace(id=None)
        create_session.add(created_offer)

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User":
                return SimpleNamespace(id=key)
            return None

        self.assertIs(await update_get(type("Offer", (), {}), 1), created_offer)
        self.assertEqual((await update_get(type("User", (), {}), 2)).id, 2)
        self.assertIsNone(await update_get(type("Other", (), {}), 1))

    async def test_handle_text_offer_confirm_publishes_offer_and_updates_channel_message(self):
        offer_data = dict(quantity=12, trade_type="buy", commodity_id=7, commodity_name="ربع", price=123456, is_wholesale=True, lot_sizes=None, notes="فقط نقدی")
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value=offer_data),
            clear=AsyncMock(),
        )
        create_session = FakeSession()
        update_session = FakeSession()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=900), SimpleNamespace(message_id=901)]))

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "Commodity":
                return SimpleNamespace(id=key, name="ربع بهار")
            if model.__name__ == "User":
                return SimpleNamespace(id=key)
            return None

        update_session.get = update_get
        self.assertIsNone(await update_session.get(type("Other", (), {}), 4))

        async def fake_publish(_session, offer, publish_user, *, send_offer_to_channel, **_kwargs):
            message_id = await send_offer_to_channel(offer, publish_user)
            offer.channel_message_id = message_id
            return SimpleNamespace(message_id=message_id, error_code=None)

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(update_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "core.utils.increment_user_counter", new=AsyncMock()
        ) as increment_mock, patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_text_offer_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=bot)

        self.assertEqual(create_session.added[0].channel_message_id, 900)
        channel_text = bot.send_message.await_args_list[0].kwargs["text"]
        private_text = bot.send_message.await_args_list[1].kwargs["text"]
        self.assertIn("🟢خرید ربع بهار 12 عدد 123,456", channel_text)
        self.assertNotIn("🟢خرید ربع 12 عدد", channel_text)
        self.assertIn("🟢خرید ربع بهار 12 عدد 123,456", private_text)
        self.assertIn("منتشر شد", callback.message.edit_text.await_args.args[0])
        increment_mock.assert_awaited_once()
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
