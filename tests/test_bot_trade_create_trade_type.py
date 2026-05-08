import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import TradeTypeCallback
from bot.handlers.trade_create import handle_trade_type_selection


class BotTradeCreateTradeTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_type_selection_updates_state_and_edits_message(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(update_data=AsyncMock())

        with patch("bot.handlers.trade_create.get_commodities_keyboard", new=AsyncMock(return_value="KB")):
            await handle_trade_type_selection(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=TradeTypeCallback(type="sell"),
            )

        state.update_data.assert_awaited_once_with(trade_type="sell", trade_type_fa="🔴 فروش")
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("کالای مورد نظر", callback.message.edit_text.await_args.args[0])
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "KB")
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()