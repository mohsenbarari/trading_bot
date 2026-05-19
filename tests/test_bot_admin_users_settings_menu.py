import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from bot.handlers.admin_users import handle_user_settings
from core.enums import UserAccountStatus, UserRole


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersSettingsMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_settings_ignores_non_admin_and_swallows_edit_failures(self):
        callback = SimpleNamespace(data="user_settings_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_user_settings(callback, user=None)
        callback.answer.assert_not_awaited()

        target_user = SimpleNamespace(
            id=9,
            account_status=UserAccountStatus.ACTIVE,
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=False,
            max_blocked_users=1,
        )
        failing_message = SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method='editMessageText', message='unchanged')))
        callback = SimpleNamespace(data="user_settings_9", message=failing_message, answer=AsyncMock())

        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB"):
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))

        callback.answer.assert_awaited_once()

    async def test_handle_user_settings_handles_missing_user_and_success(self):
        callback = SimpleNamespace(data="user_settings_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        target_user = SimpleNamespace(
            id=9,
            account_status=UserAccountStatus.ACTIVE,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            max_active_commodities=2,
            max_daily_requests=None,
            can_block_users=True,
            max_blocked_users=5,
        )
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(data="user_settings_9", message=message, answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock:
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9, account_status=UserAccountStatus.ACTIVE, is_restricted=True, has_limitations=True, can_block=True, max_blocked=5, can_edit_role=True)
        message.edit_text.assert_awaited_once_with("PROFILE", reply_markup="KB", parse_mode="Markdown")
        callback.answer.assert_awaited_once()

    async def test_middle_manager_cannot_open_settings_for_admin_target(self):
        target_user = SimpleNamespace(
            id=9,
            role=UserRole.MIDDLE_MANAGER,
            account_status=UserAccountStatus.ACTIVE,
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=True,
            max_blocked_users=5,
        )
        callback = SimpleNamespace(data="user_settings_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)):
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))

        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)
        callback.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()