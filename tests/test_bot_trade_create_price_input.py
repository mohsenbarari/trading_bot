import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.trade_create import Trade, handle_price_input


class BotTradeCreatePriceInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_price_input_handles_missing_user_invalid_price_and_success(self):
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

        message = SimpleNamespace(text="12345", answer=AsyncMock())
        await handle_price_input(message, state, user=None, bot=SimpleNamespace())
        message.answer.assert_not_awaited()

        message = SimpleNamespace(text="12ab", answer=AsyncMock())
        await handle_price_input(message, state, user=SimpleNamespace(id=1), bot=SimpleNamespace())
        self.assertIn("عدد صحیح", message.answer.await_args.args[0])

        message = SimpleNamespace(text="1234", answer=AsyncMock())
        await handle_price_input(message, state, user=SimpleNamespace(id=1), bot=SimpleNamespace())
        self.assertIn("5 یا 6 رقم", message.answer.await_args.args[0])

        message = SimpleNamespace(text="12345", answer=AsyncMock())
        await handle_price_input(message, state, user=SimpleNamespace(id=1), bot=SimpleNamespace())
        state.update_data.assert_awaited_once_with(price=12345)
        state.set_state.assert_awaited_once_with(Trade.awaiting_notes)
        self.assertIn("توضیحات یا شرایط", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()