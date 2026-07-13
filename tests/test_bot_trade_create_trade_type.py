import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import TradeTypeCallback
from bot.handlers.trade_create import Trade, handle_trade_type_selection


class BotTradeCreateTradeTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_trade_type_selection_updates_state_and_edits_message(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"wizard_return_to_review": False}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        with patch("bot.handlers.trade_create.get_settlement_type_keyboard", return_value="KB"):
            await handle_trade_type_selection(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=TradeTypeCallback(type="sell"),
            )

        state.update_data.assert_awaited_once_with(trade_type="sell", trade_type_fa="🔴 فروش")
        state.set_state.assert_awaited_once_with(Trade.awaiting_settlement_type)
        callback.message.edit_text.assert_awaited_once()
        self.assertIn("نوع تسویه", callback.message.edit_text.await_args.args[0])
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "KB")
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
