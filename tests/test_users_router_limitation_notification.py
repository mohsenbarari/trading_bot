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

    async def test_queue_mode_persists_limitation_snapshot_without_direct_send(self):
        user = SimpleNamespace(
            id=5,
            telegram_id=999,
            sync_version=8,
            limitations_expire_at=None,
            max_daily_trades=4,
            max_active_commodities=None,
            max_daily_requests=8,
        )
        db = SimpleNamespace()
        call_order = []

        async def record_web_notification(*args, **kwargs):
            call_order.append("web_notification_commit_owner")

        async def record_telegram_intent(*args, **kwargs):
            call_order.append("telegram_intent")

        with patch(
            "api.routers.users.create_user_notification",
            new=AsyncMock(side_effect=record_web_notification),
        ), patch(
            "api.routers.users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="queue-v1"),
        ), patch(
            "api.routers.users.enqueue_account_restriction_telegram_notification_once",
            new=AsyncMock(side_effect=record_telegram_intent),
        ) as enqueue, patch(
            "api.routers.users.send_telegram_notification",
            new=AsyncMock(),
        ) as direct_send:
            await send_limitation_notification(db, user, ["A: 1", "B: 2"])

        enqueue.assert_awaited_once()
        self.assertEqual(
            enqueue.await_args.kwargs["restriction_kind"],
            "limitations",
        )
        self.assertEqual(enqueue.await_args.kwargs["user_sync_version"], 8)
        direct_send.assert_not_awaited()
        self.assertEqual(
            call_order,
            ["telegram_intent", "web_notification_commit_owner"],
        )


if __name__ == "__main__":
    unittest.main()
