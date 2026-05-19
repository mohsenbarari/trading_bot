import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_block_actions
from core.enums import UserAccountStatus, UserRole


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


class BotAdminUsersBlockActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_block_actions_rejects_protected_targets_and_ignores_non_admins(self):
        callback = SimpleNamespace(data="user_block_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_user_block_actions(callback, user=None)
        callback.answer.assert_not_awaited()

        protected_user = SimpleNamespace(id=9, role=UserRole.SUPER_ADMIN, account_status=UserAccountStatus.ACTIVE)
        callback = SimpleNamespace(data="user_block_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        callback = SimpleNamespace(data="user_block_apply_9_15", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

    async def test_handle_user_block_actions_shows_duration_menu(self):
        callback = SimpleNamespace(data="user_block_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(id=9, role=UserRole.STANDARD, account_status=UserAccountStatus.ACTIVE))), patch(
            "bot.handlers.admin_users.get_block_duration_keyboard", return_value="KB"
        ) as keyboard_mock:
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        keyboard_mock.assert_called_once_with(9)
        callback.message.edit_text.assert_awaited_once_with(
            "⏳ **مدت زمان مسدودیت را انتخاب کنید:**",
            reply_markup="KB",
            parse_mode="Markdown",
        )
        callback.answer.assert_awaited_once()

    async def test_handle_user_block_actions_applies_temporary_and_permanent_block(self):
        for data, expected_answer in [
            ("user_block_apply_9_15", "15 دقیقه"),
            ("user_block_apply_9_0", "دائم"),
        ]:
            target_user = SimpleNamespace(
                id=9,
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                telegram_id=123,
                trading_restricted_until=None,
                max_daily_trades=None,
                max_active_commodities=None,
                max_daily_requests=None,
            )
            session = FakeSession(target_user)
            callback = SimpleNamespace(data=data, message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
                "bot.handlers.admin_users.to_jalali_str", return_value="jalali"
            ), patch("bot.handlers.admin_users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
                "bot.handlers.admin_users.send_telegram_notification", new=AsyncMock()
            ) as telegram_mock, patch("bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")), patch(
                "bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB"
            ) as keyboard_mock, patch("bot.handlers.admin_users.datetime") as datetime_mock:
                datetime_mock.utcnow.return_value = datetime(2026, 1, 1, 12, 0, 0)
                await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            session.commit.assert_awaited_once()
            notify_mock.assert_awaited_once()
            telegram_mock.assert_awaited_once()
            keyboard_mock.assert_called_once_with(9, account_status=UserAccountStatus.ACTIVE, is_restricted=True, has_limitations=False, can_edit_role=True)
            callback.message.edit_text.assert_awaited_once_with("PROFILE", reply_markup="KB", parse_mode="Markdown")
            self.assertIn(expected_answer, callback.answer.await_args.args[0])

    async def test_handle_user_block_actions_handles_missing_user(self):
        callback = SimpleNamespace(data="user_block_apply_9_15", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        callback = SimpleNamespace(data="user_block_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()