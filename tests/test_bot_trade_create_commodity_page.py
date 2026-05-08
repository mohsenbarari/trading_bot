import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import PageCallback
from bot.handlers.trade_create import handle_commodity_page


class BotTradeCreateCommodityPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_commodity_page_uses_requested_page_and_edits_message(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید"}))

        with patch("bot.handlers.trade_create.get_commodities_keyboard", new=AsyncMock(return_value="KB")) as keyboard_mock:
            await handle_commodity_page(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=PageCallback(trade_type="buy", page=2),
            )

        keyboard_mock.assert_awaited_once_with("buy", page=2)
        callback.message.edit_text.assert_awaited_once()
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "KB")
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()