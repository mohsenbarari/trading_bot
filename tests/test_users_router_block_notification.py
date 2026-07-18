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

    async def test_queue_mode_persists_block_snapshot_without_direct_send(self):
        restricted_until = datetime(2026, 1, 1, 12, 0, 0)
        user = SimpleNamespace(
            id=5,
            telegram_id=999,
            sync_version=7,
            trading_restricted_until=restricted_until,
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
            await send_block_notification(db, user, restricted_until)

        enqueue.assert_awaited_once()
        self.assertEqual(enqueue.await_args.kwargs["restriction_kind"], "block")
        self.assertEqual(enqueue.await_args.kwargs["user_sync_version"], 7)
        direct_send.assert_not_awaited()
        self.assertEqual(
            call_order,
            ["telegram_intent", "web_notification_commit_owner"],
        )


if __name__ == "__main__":
    unittest.main()
