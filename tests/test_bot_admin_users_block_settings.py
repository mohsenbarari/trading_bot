import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import (
    handle_admin_max_block_set,
    handle_admin_set_max_block,
    handle_admin_toggle_block,
    handle_user_block_settings,
)
from core.enums import UserRole


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersBlockSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_block_settings_and_toggle_and_max_set(self):
        target_user = SimpleNamespace(id=9, account_name="ali", can_block_users=True, max_blocked_users=5)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="user_block_settings_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9, True, 5)
        callback.answer.assert_awaited_once()

        callback = SimpleNamespace(answer=AsyncMock(), data="user_block_settings_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        target_user = SimpleNamespace(id=9, account_name="ali", can_block_users=False, max_blocked_users=5)
        session = FakeSession(target_user)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_toggle_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_admin_toggle_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertTrue(target_user.can_block_users)
        session.commit.assert_awaited_once()
        keyboard_mock.assert_called_once_with(9, True, 5)
        callback.answer.assert_awaited_once_with("✅ قابلیت بلاک فعال شد.", show_alert=True)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_set_max_block_9")
        with patch("bot.handlers.admin_users.get_max_block_options_keyboard", return_value="KB") as keyboard_mock:
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9)
        self.assertIn("سقف بلاک", callback.message.edit_text.await_args.args[0])

        target_user = SimpleNamespace(id=9, account_name="ali", can_block_users=True, max_blocked_users=5)
        session = FakeSession(target_user)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_max_block_set_9_12")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(target_user.max_blocked_users, 12)
        session.commit.assert_awaited_once()
        keyboard_mock.assert_called_once_with(9, True, 12)
        callback.answer.assert_awaited_once_with("✅ سقف بلاک به 12 تغییر کرد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()