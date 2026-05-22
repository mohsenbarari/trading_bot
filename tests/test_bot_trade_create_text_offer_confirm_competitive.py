import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm


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


class BotTradeCreateTextOfferConfirmCompetitiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.market_patcher = patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True))
        self.market_patcher.start()
        self.addCleanup(self.market_patcher.stop)

    async def test_handle_text_offer_confirm_handles_active_offer_cap_and_competitive_price_failure(self):
        user = SimpleNamespace(id=1, limitations_expire_at=None)
        base_data = {
            "quantity": 12,
            "trade_type": "buy",
            "commodity_id": 7,
            "commodity_name": "سکه",
            "price": 123456,
            "is_wholesale": True,
            "lot_sizes": None,
            "notes": None,
        }

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), from_user=SimpleNamespace(id=555))
        state = SimpleNamespace(get_data=AsyncMock(return_value=base_data), clear=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession([3])),
        ):
            await handle_text_offer_confirm(callback, state, user=user, bot=SimpleNamespace())
        self.assertIn("حداکثر 3 لفظ فعال", callback.message.edit_text.await_args.args[0])
        state.clear.assert_awaited_once()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), from_user=SimpleNamespace(id=555))
        state = SimpleNamespace(get_data=AsyncMock(return_value=base_data), clear=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "core.utils.check_user_limits", side_effect=[(True, None), (True, None)]
        ), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            side_effect=[FakeSessionContext(FakeSession([0])), FakeSessionContext(FakeSession())],
        ), patch("core.services.trade_service.validate_competitive_price", new=AsyncMock(return_value=(False, "قیمت رقابتی نیست"))):
            await handle_text_offer_confirm(callback, state, user=user, bot=SimpleNamespace())
        callback.message.edit_text.assert_awaited_once_with("قیمت رقابتی نیست", parse_mode="Markdown")
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()