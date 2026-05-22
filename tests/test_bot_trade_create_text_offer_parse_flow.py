import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import Trade, handle_text_offer
from core.enums import UserRole


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


class BotTradeCreateTextOfferParseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_text_offer_handles_non_offer_parse_error_active_cap_and_success(self):
        user = SimpleNamespace(role=UserRole.STANDARD, trading_restricted_until=None, id=1)

        closed_message = SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock())
        closed_state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock()
        ) as parse_mock:
            await handle_text_offer(closed_message, closed_state, user=user, bot=SimpleNamespace())
        closed_message.answer.assert_awaited_once_with("بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید.")
        parse_mock.assert_not_awaited()

        message = SimpleNamespace(text="خ ربع 30تا 75800", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(None, None))
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        message.answer.assert_not_awaited()

        error = SimpleNamespace(message="خطای قیمت")
        message = SimpleNamespace(text="خ ربع 30تا", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(None, error))
        ), patch("bot.handlers.trade_create._get_offer_suggestion", return_value="HINT"):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        self.assertIn("خطای قیمت", message.answer.await_args.args[0])
        self.assertIn("HINT", message.answer.await_args.args[0])

        parsed = SimpleNamespace(
            trade_type="buy",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
        )
        message = SimpleNamespace(text="خ سکه 12تا 123456", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))
        ), patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession([3])),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        self.assertIn("حداکثر 3 لفظ فعال", message.answer.await_args.args[0])

        parsed = SimpleNamespace(
            trade_type="sell",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=False,
            lot_sizes=[7, 5],
            notes="فقط نقدی",
        )
        message = SimpleNamespace(text="ف سکه 12تا 123456 7 5", answer=AsyncMock())
        state = SimpleNamespace(get_state=AsyncMock(return_value=None), update_data=AsyncMock(), set_state=AsyncMock())
        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=True)), patch(
            "bot.utils.offer_parser.parse_offer_text", new=AsyncMock(return_value=(parsed, None))
        ), patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(max_active_offers=3)), patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            return_value=FakeSessionContext(FakeSession([0])),
        ):
            await handle_text_offer(message, state, user=user, bot=SimpleNamespace())
        state.update_data.assert_awaited_once_with(
            trade_type="sell",
            commodity_id=7,
            commodity_name="سکه",
            quantity=12,
            price=123456,
            is_wholesale=False,
            lot_sizes=[7, 5],
            notes="فقط نقدی",
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_text_confirm)
        preview_text = message.answer.await_args.args[0]
        self.assertIn("پیش\u200cنمایش لفظ", preview_text)
        self.assertIn("خُرد [7, 5]", preview_text)


if __name__ == "__main__":
    unittest.main()
