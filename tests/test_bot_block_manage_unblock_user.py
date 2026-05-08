import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import handle_unblock_user


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())


class BotBlockManageUnblockUserTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_unblock_user_refreshes_list_or_menu(self):
        callback = make_callback()
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.unblock_user", new=AsyncMock(return_value=(True, "ok"))
        ), patch("bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1"}])), patch(
            "bot.handlers.block_manage.safe_edit_text", new=AsyncMock()
        ) as safe_edit:
            await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        self.assertIn("کاربران مسدود شده", safe_edit.await_args.args[1])

        callback = make_callback()
        status = {"max_blocked": 3, "can_block": True, "current_blocked": 0, "remaining": 3}
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.unblock_user", new=AsyncMock(return_value=(True, "ok"))
        ), patch("bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[])), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        self.assertIn("لیست خالی است", safe_edit.await_args.args[1])


if __name__ == "__main__":
    unittest.main()