import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.trade_create import Trade, handle_lot_split, handle_lot_wholesale


class BotTradeCreateLotTypeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_lot_wholesale_and_split_update_state_and_prompt_next_step(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 25}))

        await handle_lot_wholesale(callback, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(is_wholesale=True, lot_sizes=None)
        state.set_state.assert_awaited_once_with(Trade.awaiting_price)
        self.assertIn("قیمت را وارد کنید", callback.message.edit_text.await_args.args[0])

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 25}))
        await handle_lot_split(callback, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(is_wholesale=False)
        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_sizes)
        self.assertIn("جمع باید برابر 25 باشد", callback.message.edit_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()