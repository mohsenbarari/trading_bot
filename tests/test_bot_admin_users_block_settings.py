import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

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
    async def test_block_setting_handlers_cover_guard_and_edit_failure_paths(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="user_block_settings_9")
        await handle_user_block_settings(callback, user=None)
        callback.answer.assert_not_awaited()

        protected_user = SimpleNamespace(id=9, role=UserRole.SUPER_ADMIN, account_name="chief", can_block_users=True, max_blocked_users=5)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="user_block_settings_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        failing_settings_message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method='editMessageText', message='unchanged')))
        callback = SimpleNamespace(message=failing_settings_message, answer=AsyncMock(), data="user_block_settings_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5))), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ):
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once()

        failing_message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method='editMessageText', message='unchanged')))
        callback = SimpleNamespace(message=failing_message, answer=AsyncMock(), data="admin_toggle_block_9")
        toggle_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5)
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(toggle_user)
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ):
            await handle_admin_toggle_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("✅ قابلیت بلاک غیرفعال شد.", show_alert=True)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_set_max_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_toggle_block_9")
        await handle_admin_toggle_block(callback, user=None)
        callback.answer.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_set_max_block_9")
        await handle_admin_set_max_block(callback, user=None)
        callback.answer.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_max_block_set_9_12")
        await handle_admin_max_block_set(callback, user=None)
        callback.answer.assert_not_awaited()

        failing_message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method='editMessageText', message='unchanged')))
        callback = SimpleNamespace(message=failing_message, answer=AsyncMock(), data="admin_max_block_set_9_12")
        max_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5)
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(max_user)
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ):
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("✅ سقف بلاک به 12 تغییر کرد.", show_alert=True)

    async def test_handle_user_block_settings_and_toggle_and_max_set(self):
        target_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5)
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

        target_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=False, max_blocked_users=5)
        session = FakeSession(target_user)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_toggle_block_9")
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_admin_toggle_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertTrue(target_user.can_block_users)
        session.commit.assert_awaited_once()
        keyboard_mock.assert_called_once_with(9, True, 5)
        callback.answer.assert_awaited_once_with("✅ قابلیت بلاک فعال شد.", show_alert=True)

        callback = SimpleNamespace(answer=AsyncMock(), data="admin_toggle_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_admin_toggle_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_set_max_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD))), patch(
            "bot.handlers.admin_users.get_max_block_options_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9)
        self.assertIn("سقف بلاک", callback.message.edit_text.await_args.args[0])

        callback = SimpleNamespace(answer=AsyncMock(), data="admin_set_max_block_9")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        target_user = SimpleNamespace(id=9, role=UserRole.STANDARD, account_name="ali", can_block_users=True, max_blocked_users=5)
        session = FakeSession(target_user)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_max_block_set_9_12")
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(target_user.max_blocked_users, 12)
        session.commit.assert_awaited_once()
        keyboard_mock.assert_called_once_with(9, True, 12)
        callback.answer.assert_awaited_once_with("✅ سقف بلاک به 12 تغییر کرد.", show_alert=True)

        denied_callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock(), data="admin_max_block_set_9_12")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.SUPER_ADMIN))):
            await handle_admin_max_block_set(denied_callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        denied_callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        callback = SimpleNamespace(answer=AsyncMock(), data="admin_max_block_set_9_12")
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
