import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import handle_block_user


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())


class BotBlockManageBlockUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_block_user_answers_and_refreshes_menu_on_success(self):
        callback = make_callback()
        await handle_block_user(callback, SimpleNamespace(user_id=7), user=None)
        callback.answer.assert_awaited_once_with()

        callback = make_callback()
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.block_user", new=AsyncMock(return_value=(False, "no"))
        ):
            await handle_block_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        callback.answer.assert_awaited_once_with("no", show_alert=True)

        callback = make_callback()
        status = {"can_block": True, "current_blocked": 1, "max_blocked": 3, "remaining": 2}
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.block_user", new=AsyncMock(return_value=(True, "ok"))
        ), patch("bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)), patch(
            "bot.handlers.block_manage.safe_edit_text", new=AsyncMock()
        ) as safe_edit:
            await handle_block_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        self.assertIn("مدیریت کاربران مسدود", safe_edit.await_args.args[1])


if __name__ == "__main__":
    unittest.main()