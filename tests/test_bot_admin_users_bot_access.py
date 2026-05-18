import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_toggle_account_status
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


class BotAdminUsersAccountStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_toggle_account_status_handles_both_directions_and_missing_user(self):
        for initial_status, expected_status in [
            (UserAccountStatus.ACTIVE, "غیرفعال"),
            (UserAccountStatus.INACTIVE, "فعال"),
        ]:
            target_user = SimpleNamespace(
                id=9,
                telegram_id=123,
                account_status=initial_status,
                trading_restricted_until=datetime(2100, 1, 1, 0, 0, 0),
                max_daily_trades=1,
                max_active_commodities=None,
                max_daily_requests=None,
            )
            session = FakeSession(target_user)
            callback = SimpleNamespace(
                data="user_toggle_account_status_9",
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            async def transition_side_effect(_session, user_obj, target_status):
                user_obj.account_status = target_status

            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
                "bot.handlers.admin_users.transition_user_account_status", new=AsyncMock(side_effect=transition_side_effect)
            ) as transition_mock, patch(
                "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
            ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock:
                await handle_user_toggle_account_status(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertEqual(target_user.account_status, UserAccountStatus.ACTIVE if initial_status == UserAccountStatus.INACTIVE else UserAccountStatus.INACTIVE)
            session.commit.assert_awaited_once()
            transition_mock.assert_awaited_once()
            keyboard_mock.assert_called_once_with(user_id=9, account_status=target_user.account_status, is_restricted=True, has_limitations=True, can_edit_role=True)
            callback.answer.assert_awaited_once_with(f"✅ وضعیت حساب {expected_status} شد.", show_alert=True)

        callback = SimpleNamespace(data="user_toggle_account_status_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_toggle_account_status(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()