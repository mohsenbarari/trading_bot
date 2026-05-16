import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_trade_confirm


class BotTradeCreateConfirmChannelUnsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_confirm_reports_missing_channel_configuration(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
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

        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[FakeSessionContext(FakeSession([0])), FakeSessionContext(FakeSession())],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(True, None))), patch(
            "core.services.trade_service.detect_offer_price_warning", new=AsyncMock(return_value=None)
        ), patch(
            "bot.handlers.trade_create.settings", SimpleNamespace(channel_id=None, bot_username="botname")
        ):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1, limitations_expire_at=None), bot=SimpleNamespace())

        self.assertIn("کانال تنظیم نشده", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()


class FakeSession:
    def __init__(self, scalar_values=None):
        self.scalar_values = list(scalar_values or [])

    async def scalar(self, stmt):
        return self.scalar_values.pop(0)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


if __name__ == "__main__":
    unittest.main()