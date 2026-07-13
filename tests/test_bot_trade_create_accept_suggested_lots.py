import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import AcceptLotsCallback
from bot.handlers.trade_create import Trade, handle_accept_suggested_lots


class BotTradeCreateAcceptSuggestedLotsTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_accept_suggested_lots_handles_missing_user_and_success(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"quantity": 30, "wizard_return_to_review": False}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        await handle_accept_suggested_lots(callback, state, user=None, callback_data=AcceptLotsCallback(lots="10_20"))
        callback.answer.assert_awaited_once_with()
        state.update_data.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"quantity": 30, "wizard_return_to_review": False}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        with patch("bot.handlers.trade_create.validate_lot_sizes", return_value=(True, None, None)):
            await handle_accept_suggested_lots(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=AcceptLotsCallback(lots="10_20"),
            )
        state.update_data.assert_awaited_once_with(lot_sizes=[10, 20])
        state.set_state.assert_awaited_once_with(Trade.awaiting_price)
        self.assertIn("قیمت را وارد کنید", callback.message.edit_text.await_args.args[0])
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
