import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_back_to_type, handle_trade_cancel


class BotTradeCreateBackCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_back_to_type_and_trade_cancel_clear_state_and_edit_message(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())

        with patch("bot.handlers.trade_create.get_trade_type_keyboard", return_value="KB"):
            await handle_back_to_type(callback, state, user=SimpleNamespace(id=1))
        state.clear.assert_awaited_once()
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "KB")
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await handle_trade_cancel(callback, state, user=SimpleNamespace(id=1))
        state.clear.assert_awaited_once()
        self.assertIn("لغو شد", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()