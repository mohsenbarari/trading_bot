import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import AcceptLotsCallback
from bot.handlers.trade_create import Trade, handle_lot_sizes_input


class BotTradeCreateLotSizesInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_lot_sizes_input_handles_parse_error_invalid_suggestion_and_success(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 30}), update_data=AsyncMock(), set_state=AsyncMock())

        message = SimpleNamespace(text="x y", answer=AsyncMock())
        await handle_lot_sizes_input(message, state, user=SimpleNamespace(id=1))
        self.assertIn("اعداد را با فاصله", message.answer.await_args.args[0])

        message = SimpleNamespace(text="10 10", answer=AsyncMock())
        with patch("bot.handlers.trade_create.validate_lot_sizes", return_value=(False, "خطای لات", [15, 15])):
            await handle_lot_sizes_input(message, state, user=SimpleNamespace(id=1))
        self.assertEqual(message.answer.await_args.args[0], "خطای لات")
        suggested_keyboard = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(
            suggested_keyboard.inline_keyboard[0][0].callback_data,
            AcceptLotsCallback(lots="15_15").pack(),
        )

        message = SimpleNamespace(text="5 20 10", answer=AsyncMock())
        with patch("bot.handlers.trade_create.validate_lot_sizes", return_value=(True, None, None)):
            await handle_lot_sizes_input(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(lot_sizes=[20, 10, 5])
        state.set_state.assert_awaited_once_with(Trade.awaiting_price)
        self.assertIn("قیمت را وارد کنید", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()