import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.users import send_delayed_removal_notification_api


def factory_for(user):
    async def _factory():
        yield SimpleNamespace(get=AsyncMock(return_value=user))
    return _factory


class UsersRouterDelayedRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_delayed_removal_notification_skips_when_restriction_or_limits_still_exist(self):
        blocked_user = SimpleNamespace(
            trading_restricted_until=datetime.utcnow() + timedelta(minutes=5),
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        with patch("api.routers.users.asyncio.sleep", new=AsyncMock()), patch(
            "api.routers.users.create_user_notification", new=AsyncMock()
        ) as notify_mock, patch("api.routers.users.send_telegram_notification", new=AsyncMock(), create=True) as telegram_mock:
            await send_delayed_removal_notification_api(factory_for(blocked_user), 5, 999, is_block=True, delay_seconds=0)
        notify_mock.assert_not_awaited()
        telegram_mock.assert_not_awaited()

        limited_user = SimpleNamespace(
            trading_restricted_until=None,
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        with patch("api.routers.users.asyncio.sleep", new=AsyncMock()), patch(
            "api.routers.users.create_user_notification", new=AsyncMock()
        ) as notify_mock, patch("api.routers.users.send_telegram_notification", new=AsyncMock(), create=True) as telegram_mock:
            await send_delayed_removal_notification_api(factory_for(limited_user), 5, 999, is_block=False, delay_seconds=0)
        notify_mock.assert_not_awaited()
        telegram_mock.assert_not_awaited()

    async def test_send_delayed_removal_notification_sends_message_when_removal_still_valid(self):
        user = SimpleNamespace(
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        with patch("api.routers.users.asyncio.sleep", new=AsyncMock()), patch(
            "api.routers.users.create_user_notification", new=AsyncMock()
        ) as notify_mock, patch("api.routers.users.send_telegram_notification", new=AsyncMock(), create=True) as telegram_mock:
            await send_delayed_removal_notification_api(factory_for(user), 5, 999, is_block=False, delay_seconds=0)

        self.assertIn("رفع محدودیت", notify_mock.await_args.args[2])
        telegram_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()