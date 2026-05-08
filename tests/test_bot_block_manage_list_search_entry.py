import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import show_blocked_list, start_search, BlockStates


class FakeState:
    def __init__(self):
        self.states = []

    async def set_state(self, value):
        self.states.append(value)


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageListSearchEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_blocked_list_handles_empty_and_renders_list(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[])
        ):
            await show_blocked_list(callback, user=SimpleNamespace(id=5))
        callback.answer.assert_awaited_once_with("لیست خالی است", show_alert=True)

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1"}])
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await show_blocked_list(callback, user=SimpleNamespace(id=5))
        self.assertIn("کاربران مسدود شده", safe_edit.await_args.args[1])

    async def test_start_search_moves_to_search_state(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        with patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await start_search(callback, state, user=SimpleNamespace(id=5))

        self.assertEqual(state.states, [BlockStates.searching])
        self.assertIn("جستجوی کاربر", safe_edit.await_args.args[1])


if __name__ == "__main__":
    unittest.main()