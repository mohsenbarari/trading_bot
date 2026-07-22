import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.block_service import BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED
from bot.handlers.block_manage import handle_unblock_user


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_callback():
    return SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace())


class BotBlockManageUnblockUserTests(unittest.IsolatedAsyncioTestCase):
    normal_status = {"can_block": True, "can_block_now": True, "current_blocked": 1, "max_blocked": 3, "remaining": 2}

    async def test_handle_unblock_user_refreshes_list_or_menu(self):
        callback = make_callback()
        await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=None)
        callback.answer.assert_awaited_once_with()

        callback = make_callback()
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=self.normal_status)
        ), patch(
            "bot.handlers.block_manage.unblock_user", new=AsyncMock(return_value=(True, "ok"))
        ), patch("bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[{"id": 1, "account_name": "u1"}])), patch(
            "bot.handlers.block_manage.safe_edit_text", new=AsyncMock()
        ) as safe_edit:
            await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        self.assertIn("کاربران مسدود شده", safe_edit.await_args.args[2])

        callback = make_callback()
        status = {"max_blocked": 3, "can_block": True, "current_blocked": 0, "remaining": 3}
        with patch("bot.handlers.block_manage.AsyncSessionLocal", side_effect=[FakeSessionContext(), FakeSessionContext(), FakeSessionContext(), FakeSessionContext()]), patch(
            "bot.handlers.block_manage.unblock_user", new=AsyncMock(return_value=(True, "ok"))
        ), patch("bot.handlers.block_manage.get_blocked_users", new=AsyncMock(return_value=[])), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.safe_edit_text", new=AsyncMock()) as safe_edit:
            await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))
        self.assertIn("لیست خالی است", safe_edit.await_args.args[2])

    async def test_handle_unblock_user_rejects_delegated_accounts(self):
        callback = make_callback()
        status = {
            "can_block": False,
            "reason_code": BLOCK_STATUS_REASON_ACCOUNTANT_DELEGATED,
            "reason_message": "قابلیت بلاک کاربران فقط در اختیار سرگروه است.",
        }
        with patch("bot.handlers.block_manage.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "bot.handlers.block_manage.get_block_status", new=AsyncMock(return_value=status)
        ), patch("bot.handlers.block_manage.unblock_user", new=AsyncMock()) as unblock_mock:
            await handle_unblock_user(callback, SimpleNamespace(user_id=7), user=SimpleNamespace(id=5))

        callback.answer.assert_awaited_once_with(status["reason_message"], show_alert=True)
        unblock_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
