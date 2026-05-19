import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from bot.handlers.admin_users import handle_set_user_role, handle_user_edit_role
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


class BotAdminUsersRoleActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_handlers_ignore_non_super_admin_and_swallow_edit_failures(self):
        callback = SimpleNamespace(
            data="user_edit_role_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        await handle_user_edit_role(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.message.edit_text.assert_not_awaited()
        callback.answer.assert_not_awaited()

        target_user = SimpleNamespace(
            id=9,
            role=UserRole.STANDARD,
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        failing_callback = SimpleNamespace(
            data="set_user_role_9_STANDARD",
            message=SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method='editMessageText', message='unchanged'))),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="KB"):
            await handle_set_user_role(failing_callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))

        failing_callback.answer.assert_awaited_once_with("✅ نقش کاربر تغییر کرد.")

    async def test_handle_user_edit_role_shows_prompt(self):
        callback = SimpleNamespace(
            data="user_edit_role_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.get_user_role_edit_keyboard", return_value="KB") as keyboard_mock:
            await handle_user_edit_role(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9)
        callback.message.edit_text.assert_awaited_once_with("🎭 لطفاً نقش جدید کاربر را انتخاب کنید:", reply_markup="KB")
        callback.answer.assert_awaited_once()

    async def test_handle_set_user_role_handles_success_and_missing_user(self):
        target_user = SimpleNamespace(
            id=9,
            role=UserRole.STANDARD,
            trading_restricted_until=datetime(2100, 1, 1, 0, 0, 0),
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        session = FakeSession(target_user)
        callback = SimpleNamespace(
            data="set_user_role_9_MIDDLE_MANAGER",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="KB") as keyboard_mock:
            await handle_set_user_role(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(target_user.role, UserRole.MIDDLE_MANAGER)
        session.commit.assert_awaited_once()
        keyboard_mock.assert_called_once_with(user_id=9, is_restricted=True, has_limitations=True)
        callback.message.edit_text.assert_awaited_once_with("PROFILE", reply_markup="KB", parse_mode="Markdown")
        callback.answer.assert_awaited_once_with("✅ نقش کاربر تغییر کرد.")

        callback = SimpleNamespace(data="set_user_role_9_STANDARD", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_set_user_role(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()