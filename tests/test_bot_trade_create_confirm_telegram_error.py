import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from bot.handlers.trade_create import handle_trade_confirm
from models.offer import OfferStatus


class FakeSession:
    def __init__(self, scalar_values=None, stored_offer=None):
        self.scalar_values = list(scalar_values or [])
        self.stored_offer = stored_offer
        self.added = []
        self.commits = 0

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = 55
        self.added.append(value)
        self.stored_offer = value

    async def commit(self):
        self.commits += 1

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


class BotTradeCreateConfirmTelegramErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_confirm_rolls_back_offer_on_telegram_bad_request(self):
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
        rollback_session = FakeSession(stored_offer=None)

        async def rollback_get(model, key):
            if create_session.added:
                return create_session.added[0]
            return None

        rollback_session.get = rollback_get
        error = TelegramBadRequest(method=SimpleNamespace(__api_method__="sendMessage"), message="bad")
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[
                FakeSessionContext(FakeSession([0])),
                FakeSessionContext(FakeSession()),
                FakeSessionContext(create_session),
                FakeSessionContext(rollback_session),
            ],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=-100, bot_username="botname")
        ):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=bot)

        self.assertEqual(create_session.added[0].status, OfferStatus.EXPIRED)
        self.assertIn("خطا در ارسال به کانال", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()