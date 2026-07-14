import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_trade_confirm


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])
        self.added = []

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 88
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


class BotTradeCreateConfirmSuccessRetailTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_handle_trade_confirm_builds_unique_retail_buttons(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
            from_user=SimpleNamespace(id=555),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "quantity": 12,
                    "trade_type": "sell",
                    "commodity_name": "سکه",
                    "price": 123456,
                    "commodity_id": 7,
                    "is_wholesale": False,
                    "lot_sizes": [6, 6],
                    "notes": None,
                }
            ),
            clear=AsyncMock(),
        )
        create_session = FakeSession()
        update_session = FakeSession()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=700), SimpleNamespace(message_id=701)]))

        async def update_get(model, key):
            if create_session.added and model.__name__ == "Offer":
                return create_session.added[0]
            if model.__name__ == "User":
                return SimpleNamespace(id=key)
            return None

        update_session.get = update_get

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
                FakeSessionContext(update_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.get_available_trade_amounts", return_value=[3, 2, 3, 1]
        ), patch("bot.handlers.trade_create.increment_user_counter", new=AsyncMock()), patch(
            "bot.handlers.trade_create.publish_offer_to_telegram_channel_once",
            new=AsyncMock(side_effect=fake_publish),
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=bot)

        trade_keyboard = bot.send_message.await_args_list[0].kwargs["reply_markup"]
        buttons = trade_keyboard.inline_keyboard[0]
        self.assertEqual([button.text for button in buttons], ["3 عدد", "2 عدد", "1 عدد"])
        self.assertEqual(buttons[0].callback_data, f"ct2:{create_session.added[0].offer_public_id}:3")
        self.assertEqual(buttons[1].callback_data, f"ct2:{create_session.added[0].offer_public_id}:2")
        self.assertEqual(buttons[2].callback_data, f"ct2:{create_session.added[0].offer_public_id}:1")


if __name__ == "__main__":
    unittest.main()
