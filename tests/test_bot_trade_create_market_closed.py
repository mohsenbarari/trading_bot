import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_text_offer_confirm, handle_trade_confirm


class BotTradeCreateMarketClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_wizard_and_text_confirm_paths_stop_when_market_is_closed(self):
        callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock())

        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)):
            await handle_trade_confirm(callback, state, user=SimpleNamespace(id=1), bot=SimpleNamespace())

        callback.message.edit_text.assert_awaited_once_with(
            "بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
        )
        state.clear.assert_awaited_once()
        callback.answer.assert_awaited_once_with()

        text_callback = SimpleNamespace(
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        text_state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock())

        with patch("bot.handlers.trade_create._bot_market_is_open", new=AsyncMock(return_value=False)):
            await handle_text_offer_confirm(text_callback, text_state, user=SimpleNamespace(id=1), bot=SimpleNamespace())

        text_callback.message.edit_text.assert_awaited_once_with(
            "بعلت بسته بودن بازار درخواست شما ثبت نشد\nلطفا در زمان فعال بودن بازار اقدام به ثبت درخواست کنید."
        )
        text_state.clear.assert_awaited_once()
        text_callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()