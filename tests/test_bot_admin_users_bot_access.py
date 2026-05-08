import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_toggle_bot
from core.enums import NotificationLevel, NotificationCategory, UserRole


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


class BotAdminUsersBotAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_toggle_bot_handles_both_directions_and_missing_user(self):
        for initial_access, expected_status, expected_level in [
            (True, "غیرفعال", NotificationLevel.INFO),
            (False, "فعال", NotificationLevel.SUCCESS),
        ]:
            target_user = SimpleNamespace(
                id=9,
                telegram_id=123,
                has_bot_access=initial_access,
                trading_restricted_until=datetime(2100, 1, 1, 0, 0, 0),
                max_daily_trades=1,
                max_active_commodities=None,
                max_daily_requests=None,
            )
            session = FakeSession(target_user)
            callback = SimpleNamespace(
                data="user_toggle_bot_9",
                message=SimpleNamespace(edit_text=AsyncMock()),
                answer=AsyncMock(),
            )
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
                "bot.handlers.admin_users.create_user_notification", new=AsyncMock()
            ) as notify_mock, patch("bot.handlers.admin_users.send_telegram_notification", new=AsyncMock()) as telegram_mock, patch(
                "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
            ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock:
                await handle_user_toggle_bot(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            self.assertEqual(target_user.has_bot_access, not initial_access)
            session.commit.assert_awaited_once()
            self.assertEqual(notify_mock.await_args.kwargs["level"], expected_level)
            self.assertEqual(notify_mock.await_args.kwargs["category"], NotificationCategory.SYSTEM)
            telegram_mock.assert_awaited_once()
            keyboard_mock.assert_called_once_with(user_id=9, is_restricted=True, has_limitations=True)
            callback.answer.assert_awaited_once_with(f"✅ دسترسی بات {expected_status} شد.", show_alert=True)

        callback = SimpleNamespace(data="user_toggle_bot_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_toggle_bot(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()