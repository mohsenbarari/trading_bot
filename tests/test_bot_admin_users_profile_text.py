import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from bot.handlers.admin_users import get_user_profile_text
from core.enums import UserRole


class BotAdminUsersProfileTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_profile_text_formats_restrictions_and_limitations(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        user = SimpleNamespace(
            account_name="ali",
            mobile_number="0912",
            role=UserRole.STANDARD,
            has_bot_access=True,
            created_at=now,
            trading_restricted_until=now + timedelta(days=1),
            max_daily_trades=2,
            max_active_commodities=3,
            max_daily_requests=4,
            limitations_expire_at=now + timedelta(days=2),
        )
        with patch("bot.handlers.admin_users.datetime") as datetime_mock, patch(
            "bot.handlers.admin_users.to_jalali_str", side_effect=["join", "restriction", "expire"]
        ):
            datetime_mock.utcnow.return_value = now
            text = await get_user_profile_text(user)
        self.assertIn("ali", text)
        self.assertIn("✅ فعال", text)
        self.assertIn("restriction", text)
        self.assertIn("معاملات روزانه: 2", text)
        self.assertIn("انقضا: expire", text)

    async def test_get_user_profile_text_marks_expired_restriction_as_free(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        user = SimpleNamespace(
            account_name=None,
            mobile_number=None,
            role=UserRole.STANDARD,
            has_bot_access=False,
            created_at=None,
            trading_restricted_until=now - timedelta(days=1),
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
        )
        with patch("bot.handlers.admin_users.datetime") as datetime_mock:
            datetime_mock.utcnow.return_value = now
            text = await get_user_profile_text(user)
        self.assertIn("آزاد (منقضی شده)", text)
        self.assertIn("❌ غیرفعال", text)


if __name__ == "__main__":
    unittest.main()