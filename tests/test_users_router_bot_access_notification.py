import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.users import send_bot_access_notification


class UsersRouterBotAccessNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_bot_access_notification_handles_grant_and_revoke(self):
        user = SimpleNamespace(id=5, telegram_id=999)

        with patch("api.routers.users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
            "api.routers.users.send_telegram_notification", new=AsyncMock(), create=True
        ) as telegram_mock:
            await send_bot_access_notification(SimpleNamespace(), user, True)
        self.assertIn("فعال شد", notify_mock.await_args.args[2])
        telegram_mock.assert_awaited_once()

        with patch("api.routers.users.create_user_notification", new=AsyncMock()) as notify_mock, patch(
            "api.routers.users.send_telegram_notification", new=AsyncMock(), create=True
        ):
            await send_bot_access_notification(SimpleNamespace(), user, False)
        self.assertIn("محدود شده", notify_mock.await_args.args[2])


if __name__ == "__main__":
    unittest.main()