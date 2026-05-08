import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import show_block_menu


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_block_menu_handles_missing_user_and_renders_status(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        await show_block_menu(callback, user=None)
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        status = {"can_block": True, "current_blocked": 1, "max_blocked": 3, "remaining": 2}
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await show_block_menu(callback, user=SimpleNamespace(id=5))

        self.assertIn("مدیریت کاربران مسدود", safe_edit.await_args.args[1])
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()