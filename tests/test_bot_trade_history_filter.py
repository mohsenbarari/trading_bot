import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_history import filter_trade_history


class FakeState:
    def __init__(self):
        self.updated = []

    async def update_data(self, **kwargs):
        self.updated.append(kwargs)


def make_callback(side_effect=None):
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(side_effect=side_effect)),
    )


class FakeBadRequest(Exception):
    pass


class BotTradeHistoryFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_trade_history_handles_missing_target(self):
        callback = make_callback()
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(None, []))):
            await filter_trade_history(callback, SimpleNamespace(months=6, target_user_id=5), FakeState(), user=SimpleNamespace(id=2))

        callback.answer.assert_awaited_once_with("کاربر یافت نشد!", show_alert=True)

    async def test_filter_trade_history_ignores_telegram_bad_request(self):
        callback = make_callback(side_effect=FakeBadRequest())
        state = FakeState()
        with patch("bot.handlers.trade_history.get_trade_history", new=AsyncMock(return_value=(SimpleNamespace(account_name="t"), [1]))), patch(
            "bot.handlers.trade_history.format_trade_history", return_value="TEXT"
        ), patch("bot.handlers.trade_history.get_trade_history_keyboard", return_value="KB"), patch(
            "bot.handlers.trade_history.TelegramBadRequest", FakeBadRequest
        ):
            await filter_trade_history(callback, SimpleNamespace(months=6, target_user_id=5), state, user=SimpleNamespace(id=2))

        self.assertEqual(state.updated, [{"history_months": 6, "history_target_id": 5}])
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()