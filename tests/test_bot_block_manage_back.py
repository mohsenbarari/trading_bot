import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import handle_back


class FakeState:
    def __init__(self):
        self.cleared = 0

    async def clear(self):
        self.cleared += 1


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotBlockManageBackTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_back_requires_user_and_renders_menu(self):
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        await handle_back(callback, state, user=None)
        callback.answer.assert_awaited_once_with()

        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())
        state = FakeState()
        status = {"can_block": False, "current_blocked": 0, "max_blocked": 3, "remaining": 3}
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await handle_back(callback, state, user=SimpleNamespace(id=5))

        self.assertEqual(state.cleared, 1)
        self.assertIn("قابلیت مسدود کردن برای شما غیرفعال است", safe_edit.await_args.args[1])
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()