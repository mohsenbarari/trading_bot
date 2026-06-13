import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.admin_messages import (
    AdminBroadcastCreate,
    AdminMarketMessageCreate,
    clear_current_market_message,
    create_broadcast,
    create_market_message,
    get_current_market_message,
)
from core.enums import MessageType


class AdminMessagesRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_market_message_returns_cached_payload_without_db_read(self):
        cached_payload = {
            "id": 42,
            "content": "پیام کش‌شده بازار",
            "created_by_id": 1,
            "created_by_name": "admin",
            "reused_from_id": None,
            "is_active": True,
            "notified_recipients_count": 2,
            "published_at": "2026-05-29T08:00:00",
            "created_at": "2026-05-29T08:00:00",
        }

        with patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new=AsyncMock(return_value=(True, cached_payload)),
        ), patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new=AsyncMock(),
        ) as service_mock, patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(),
        ) as cache_set_mock:
            result = await get_current_market_message(current_user=SimpleNamespace(id=7), db=object())

        service_mock.assert_not_awaited()
        cache_set_mock.assert_not_awaited()
        self.assertEqual(result.id, 42)
        self.assertEqual(result.content, "پیام کش‌شده بازار")

    async def test_get_current_market_message_caches_db_fallback_and_empty_state(self):
        db = object()
        market_message = SimpleNamespace(
            id=43,
            content="پیام تازه بازار",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=5,
            published_at=datetime(2026, 5, 29, 8, 0, 0),
            created_at=datetime(2026, 5, 29, 8, 0, 0),
        )

        with patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new=AsyncMock(return_value=(False, None)),
        ), patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new=AsyncMock(return_value=market_message),
        ) as service_mock, patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(return_value=True),
        ) as cache_set_mock:
            result = await get_current_market_message(current_user=SimpleNamespace(id=7), db=db)

        service_mock.assert_awaited_once_with(db)
        cache_set_mock.assert_awaited_once()
        cached_arg = cache_set_mock.await_args.args[0]
        self.assertEqual(cached_arg["id"], 43)
        self.assertEqual(result.notified_recipients_count, 5)

        with patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new=AsyncMock(return_value=(False, None)),
        ), patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(return_value=True),
        ) as empty_cache_set_mock:
            empty_result = await get_current_market_message(current_user=SimpleNamespace(id=7), db=db)

        empty_cache_set_mock.assert_awaited_once_with(None)
        self.assertIsNone(empty_result)

    async def test_get_current_market_message_invalidates_malformed_cache_payload(self):
        db = object()
        market_message = SimpleNamespace(
            id=44,
            content="پیام سالم دیتابیس",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=1,
            published_at=datetime(2026, 5, 29, 8, 0, 0),
            created_at=datetime(2026, 5, 29, 8, 0, 0),
        )

        with patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new=AsyncMock(return_value=(True, {"id": "not-an-int"})),
        ), patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new=AsyncMock(return_value=True),
        ) as invalidate_mock, patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new=AsyncMock(return_value=market_message),
        ) as service_mock, patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(return_value=True),
        ):
            result = await get_current_market_message(current_user=SimpleNamespace(id=7), db=db)

        invalidate_mock.assert_awaited_once()
        service_mock.assert_awaited_once_with(db)
        self.assertEqual(result.id, 44)

    async def test_create_market_message_notifies_eligible_users_and_publishes_event(self):
        db = object()
        current_user = SimpleNamespace(id=1)
        market_message = SimpleNamespace(
            id=42,
            content="بازار امروز با مدیریت جدید کار می‌کند",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=2,
            published_at=datetime(2026, 5, 29, 8, 0, 0),
            created_at=datetime(2026, 5, 29, 8, 0, 0),
        )

        with patch("api.routers.admin_messages.create_market_management_message", new=AsyncMock(return_value=market_message)) as create_mock, patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new=AsyncMock(return_value=[7, 8]),
        ) as recipients_mock, patch(
            "api.routers.admin_messages.create_user_notification",
            new=AsyncMock(),
        ) as notification_mock, patch("api.routers.admin_messages.publish_event_sync") as publish_mock, patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(return_value=True),
        ) as cache_set_mock:
            result = await create_market_message(
                data=AdminMarketMessageCreate(content=market_message.content),
                current_user=current_user,
                db=db,
            )

        create_mock.assert_awaited_once_with(
            db,
            actor=current_user,
            content=market_message.content,
            reused_from_id=None,
            notified_recipients_count=2,
        )
        recipients_mock.assert_awaited_once_with(db, exclude_user_ids=[1])
        self.assertEqual(notification_mock.await_count, 2)
        cache_set_mock.assert_awaited_once()
        self.assertEqual(cache_set_mock.await_args.args[0]["id"], 42)
        publish_mock.assert_called_once()
        self.assertEqual(publish_mock.call_args.args[0], "market:admin_message_published")
        self.assertEqual(result.notified_recipients_count, 2)

    async def test_clear_current_market_message_deactivates_pin_and_publishes_empty_event(self):
        db = object()
        current_user = SimpleNamespace(id=1)
        deactivated_message = SimpleNamespace(
            id=42,
            content="پیام بازار قبلی",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=False,
            notified_recipients_count=2,
            published_at=datetime(2026, 5, 29, 8, 0, 0),
            created_at=datetime(2026, 5, 29, 8, 0, 0),
        )

        with patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new=AsyncMock(return_value=deactivated_message),
        ) as deactivate_mock, patch("api.routers.admin_messages.publish_event_sync") as publish_mock, patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=AsyncMock(return_value=True),
        ) as cache_set_mock:
            result = await clear_current_market_message(current_user=current_user, db=db)

        deactivate_mock.assert_awaited_once_with(db)
        cache_set_mock.assert_awaited_once_with(None)
        publish_mock.assert_called_once_with("market:admin_message_published", None)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_active)

    async def test_create_broadcast_creates_system_room_messages_and_management_events(self):
        db = object()
        current_user = SimpleNamespace(id=1, role=SimpleNamespace(value="مدیر ارشد"))
        published_at = datetime(2026, 5, 29, 8, 0, 0)
        broadcast = SimpleNamespace(
            id=9,
            content="جلسه امروز ساعت ۱۲ برگزار می‌شود",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            target_groups=["users", "customers"],
            recipient_count=2,
            published_at=published_at,
            created_at=published_at,
        )
        created_messages = [
            SimpleNamespace(
                recipient_user_id=7,
                chat=SimpleNamespace(id=70),
                message=SimpleNamespace(
                    id=701,
                    sender_id=1,
                    receiver_id=7,
                    content=broadcast.content,
                    message_type=MessageType.TEXT,
                    is_read=False,
                    is_deleted=False,
                    updated_at=None,
                    created_at=published_at,
                    forwarded_from_id=None,
                ),
            ),
            SimpleNamespace(
                recipient_user_id=8,
                chat=SimpleNamespace(id=80),
                message=SimpleNamespace(
                    id=801,
                    sender_id=1,
                    receiver_id=8,
                    content=broadcast.content,
                    message_type=MessageType.TEXT,
                    is_read=False,
                    is_deleted=False,
                    updated_at=None,
                    created_at=published_at,
                    forwarded_from_id=None,
                ),
            ),
        ]

        with patch("api.routers.admin_messages.list_broadcast_recipient_user_ids", new=AsyncMock(return_value=[7, 8])) as recipients_mock, patch(
            "api.routers.admin_messages.create_management_broadcast",
            new=AsyncMock(return_value=(broadcast, created_messages)),
        ) as create_mock, patch("api.routers.admin_messages.publish_user_event", new=AsyncMock()) as publish_mock, patch(
            "api.routers.admin_messages.create_user_notification",
            new=AsyncMock(),
        ) as notification_mock:
            result = await create_broadcast(
                data=AdminBroadcastCreate(content=broadcast.content, target_groups=["users", "customers"]),
                current_user=current_user,
                db=db,
            )

        recipients_mock.assert_awaited_once_with(db, target_groups=["users", "customers"], exclude_user_ids=[1])
        create_mock.assert_awaited_once_with(
            db,
            actor=current_user,
            content=broadcast.content,
            target_groups=["users", "customers"],
            recipient_user_ids=[7, 8],
        )
        self.assertEqual(publish_mock.await_count, 2)
        first_event = publish_mock.await_args_list[0].args[2]
        self.assertEqual(first_event["sender_name"], "پیام مدیریت")
        self.assertEqual(first_event["conversation_other_user_id"], -70)
        self.assertFalse(first_event["can_send"])
        self.assertTrue(first_event["is_system"])
        self.assertEqual(notification_mock.await_count, 2)
        self.assertEqual(result.recipient_count, 2)
        self.assertEqual(result.delivered_user_ids, [7, 8])


if __name__ == "__main__":
    unittest.main()
