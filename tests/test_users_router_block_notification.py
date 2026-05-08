import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.users import send_block_notification


class UsersRouterBlockNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_block_notification_formats_temporary_and_permanent_messages(self):
        user = SimpleNamespace(id=5, telegram_id=999)

        with patch("api.routers.users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
            "api.routers.users.to_jalali_str", return_value="1404/01/01"
        ), patch("api.routers.users.send_telegram_notification", new=AsyncMock(), create=True) as telegram_mock:
            await send_block_notification(SimpleNamespace(), user, datetime(2026, 1, 1, 12, 0, 0))

        args = notify_mock.await_args.args
        self.assertIn("1404/01/01", args[2])
        telegram_mock.assert_awaited_once_with(999, args[2])

        with patch("api.routers.users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
            "api.routers.users.send_telegram_notification", new=AsyncMock(), create=True
        ):
            await send_block_notification(SimpleNamespace(), user, datetime(2201, 1, 1, 0, 0, 0))

        self.assertIn("دائمی", notify_mock.await_args.args[2])


if __name__ == "__main__":
    unittest.main()