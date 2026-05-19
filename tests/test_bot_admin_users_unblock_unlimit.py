import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_user_unblock, handle_user_unlimit
from core.enums import UserAccountStatus, UserRole


def consume_task(coro):
    coro.close()
    return None


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


class BotAdminUsersUnblockUnlimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_unblock_and_unlimit_ignore_unauthorized_and_protected_targets(self):
        callback = SimpleNamespace(data="user_unblock_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_user_unblock(callback, user=None)
        callback.answer.assert_not_awaited()

        protected_user = SimpleNamespace(
            id=9,
            role=UserRole.SUPER_ADMIN,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=123,
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        callback = SimpleNamespace(data="user_unblock_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_user_unblock(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        callback = SimpleNamespace(data="user_unlimit_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_user_unlimit(callback, user=None)
        callback.answer.assert_not_awaited()

        protected_limit_user = SimpleNamespace(
            id=9,
            role=UserRole.SUPER_ADMIN,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=123,
            trading_restricted_until=None,
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
            trades_count=0,
            commodities_traded_count=0,
            channel_messages_count=0,
        )
        callback = SimpleNamespace(data="user_unlimit_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_limit_user)):
            await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER))
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

    async def test_handle_user_unblock_handles_success_and_missing_user(self):
        target_user = SimpleNamespace(
            id=9,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=123,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        session = FakeSession(target_user)
        callback = SimpleNamespace(
            data="user_unblock_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task
        ) as create_task_mock, patch("bot.handlers.admin_users.send_delayed_removal_notification", new=AsyncMock()), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock:
            await handle_user_unblock(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIsNone(target_user.trading_restricted_until)
        session.commit.assert_awaited_once()
        create_task_mock.assert_called_once()
        keyboard_mock.assert_called_once_with(9, account_status=UserAccountStatus.ACTIVE, is_restricted=False, has_limitations=True, can_edit_role=True)
        callback.answer.assert_awaited_once_with("✅ رفع مسدودیت انجام شد.", show_alert=True)

        callback = SimpleNamespace(data="user_unblock_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_unblock(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

    async def test_handle_user_unlimit_resets_limits_and_handles_missing_user(self):
        target_user = SimpleNamespace(
            id=9,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=123,
            trading_restricted_until=datetime.utcnow() + timedelta(days=1),
            max_daily_trades=1,
            max_active_commodities=2,
            max_daily_requests=3,
            limitations_expire_at=datetime.utcnow() + timedelta(days=2),
            trades_count=7,
            commodities_traded_count=8,
            channel_messages_count=9,
        )
        session = FakeSession(target_user)
        callback = SimpleNamespace(
            data="user_unlimit_9",
            message=SimpleNamespace(edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=session), patch(
            "bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task
        ) as create_task_mock, patch("bot.handlers.admin_users.send_delayed_removal_notification", new=AsyncMock()), patch(
            "bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")
        ), patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock, patch(
            "bot.handlers.admin_users.datetime"
        ) as datetime_mock:
            datetime_mock.utcnow.return_value = datetime(2026, 1, 1, 12, 0, 0)
            await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIsNone(target_user.max_daily_trades)
        self.assertIsNone(target_user.max_active_commodities)
        self.assertIsNone(target_user.max_daily_requests)
        self.assertIsNone(target_user.limitations_expire_at)
        self.assertEqual(target_user.trades_count, 0)
        self.assertEqual(target_user.commodities_traded_count, 0)
        self.assertEqual(target_user.channel_messages_count, 0)
        session.commit.assert_awaited_once()
        create_task_mock.assert_called_once()
        keyboard_mock.assert_called_once_with(9, account_status=UserAccountStatus.ACTIVE, is_restricted=True, has_limitations=False, can_edit_role=True)
        callback.answer.assert_awaited_once_with("✅ محدودیت‌ها برداشته شد.", show_alert=True)

        callback = SimpleNamespace(data="user_unlimit_9", answer=AsyncMock())
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)


if __name__ == "__main__":
    unittest.main()