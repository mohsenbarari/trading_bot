import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_history import show_trade_history


class FakeState:
    def __init__(self):
        self.updated = []

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))


class BotTradeHistoryShowTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_trade_history_requires_user_and_handles_missing_target(self):
        callback = make_callback()
        await show_trade_history(callback, SimpleNamespace(target_user_id=5), FakeState(), user=None)
        callback.answer.assert_awaited_once()
        self.assertIn("لطفاً ابتدا ثبت", callback.answer.await_args.args[0])
        self.assertEqual(callback.answer.await_args.kwargs, {"show_alert": True})

        callback = make_callback()
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(None, []))):
            await show_trade_history(callback, SimpleNamespace(target_user_id=5), FakeState(), user=SimpleNamespace(id=2))
        callback.answer.assert_awaited_once_with("کاربر یافت نشد!", show_alert=True)

    async def test_show_trade_history_updates_state_and_edits_message(self):
        callback = make_callback()
        state = FakeState()
        target_user = SimpleNamespace(account_name="target")
        trades = [SimpleNamespace(id=1)]

        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(target_user, trades))), patch(
            "bot.handlers.trade_history.format_trade_history", return_value="TEXT"
        ), patch("bot.handlers.trade_history.get_trade_history_keyboard", return_value="KB"):
            await show_trade_history(callback, SimpleNamespace(target_user_id=5), state, user=SimpleNamespace(id=2))

        self.assertEqual(state.updated, [{"history_months": 3, "history_target_id": 5}])
        callback.message.edit_text.assert_awaited_once_with("TEXT", reply_markup="KB")
        callback.answer.assert_awaited()


if __name__ == "__main__":
    unittest.main()