import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.users import send_limitation_notification


class UsersRouterLimitationNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_limitation_notification_includes_limits_and_expiry(self):
        user = SimpleNamespace(id=5, telegram_id=999, limitations_expire_at=object())

        with patch("api.routers.users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
            "api.routers.users.to_jalali_str", return_value="1404/02/02"
        ), patch("api.routers.users.send_telegram_notification", new=AsyncMock(), create=True) as telegram_mock:
            await send_limitation_notification(SimpleNamespace(), user, ["A: 1", "B: 2"])

        message = notify_mock.await_args.args[2]
        self.assertIn("A: 1", message)
        self.assertIn("B: 2", message)
        self.assertIn("1404/02/02", message)
        telegram_mock.assert_awaited_once_with(999, message)


if __name__ == "__main__":
    unittest.main()