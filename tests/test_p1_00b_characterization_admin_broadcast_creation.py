"""Hermetic direct characterization for create_broadcast endpoint.

Target: api.routers.admin_messages.create_broadcast

Note: This module provides direct characterization only and does not update
the executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete; evidence promotion is a separate unit.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import api.routers.admin_messages as admin_messages_module
from api.routers.admin_messages import (
    AdminBroadcastCreate,
    AdminBroadcastCreateResponse,
    create_broadcast,
)
from core.enums import NotificationCategory, NotificationLevel
from core.services.admin_message_service import MANAGEMENT_MESSAGE_TITLE


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAdminBroadcastCreateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for create_broadcast."""

    async def test_create_broadcast_success_call_order_and_boundaries(self) -> None:
        """Characterize success boundary, preserved target group order, and call ordering."""
        fake_db = FakeAsyncSession()
        actor_id = 7
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 10, 0, 0)
        recipient_ids = [101, 102]
        payload_in = AdminBroadcastCreate(
            content="اعلان همگانی سیستم برای تست",
            target_groups=["customers", "managers"],
        )

        broadcast_message = SimpleNamespace(
            id=88,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_operator"),
            target_groups=["customers", "managers"],
            recipient_count=len(recipient_ids),
            published_at=published_at,
            created_at=created_at,
        )

        msg1 = SimpleNamespace(
            id=1001,
            sender_id=actor_id,
            receiver_id=101,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        msg2 = SimpleNamespace(
            id=1002,
            sender_id=actor_id,
            receiver_id=102,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        created_messages = [
            SimpleNamespace(recipient_user_id=101, chat=SimpleNamespace(id=501), message=msg1),
            SimpleNamespace(recipient_user_id=102, chat=SimpleNamespace(id=502), message=msg2),
        ]

        call_order: list[str] = []

        async def _fake_list_recipients(db, target_groups=None, exclude_user_ids=None):
            call_order.append("list_broadcast_recipient_user_ids")
            return recipient_ids

        async def _fake_create_broadcast(db, actor=None, content=None, target_groups=None, recipient_user_ids=None):
            call_order.append("create_management_broadcast")
            return broadcast_message, created_messages

        async def _fake_publish_user_event(user_id, event_name, payload):
            call_order.append(f"publish_user_event:{user_id}")

        async def _fake_create_notification(db, user_id, notification_text, level=None, category=None, extra_payload=None):
            call_order.append(f"create_user_notification:{user_id}")

        def _fake_audit_log(*args, **kwargs):
            call_order.append("audit_log")

        orig_serialize = admin_messages_module._serialize_broadcast

        def _fake_serialize(message):
            call_order.append("_serialize_broadcast")
            return orig_serialize(message)

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            side_effect=_fake_list_recipients,
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            side_effect=_fake_create_broadcast,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
            side_effect=_fake_publish_user_event,
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
            side_effect=_fake_create_notification,
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
            side_effect=_fake_audit_log,
        ) as mock_audit, mock.patch(
            "api.routers.admin_messages._serialize_broadcast",
            side_effect=_fake_serialize,
        ):
            result = await create_broadcast(
                data=payload_in,
                current_user=current_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "list_broadcast_recipient_user_ids",
                "create_management_broadcast",
                "publish_user_event:101",
                "create_user_notification:101",
                "publish_user_event:102",
                "create_user_notification:102",
                "audit_log",
                "_serialize_broadcast",
            ],
        )

        mock_recipients.assert_awaited_once_with(
            fake_db,
            target_groups=["customers", "managers"],
            exclude_user_ids=[actor_id],
        )
        mock_create.assert_awaited_once_with(
            fake_db,
            actor=current_user,
            content=payload_in.content,
            target_groups=["customers", "managers"],
            recipient_user_ids=recipient_ids,
        )

        expected_event_1 = admin_messages_module._serialize_broadcast_message_event(msg1, chat_id=501)
        expected_event_2 = admin_messages_module._serialize_broadcast_message_event(msg2, chat_id=502)
        self.assertEqual(expected_event_1["chat_id"], 501)
        self.assertEqual(expected_event_1["conversation_id"], -501)
        self.assertEqual(expected_event_1["conversation_other_user_id"], -501)
        self.assertEqual(expected_event_1["room_kind"], "group")
        self.assertFalse(expected_event_1["can_send"])
        self.assertTrue(expected_event_1["is_system"])
        self.assertEqual(expected_event_1["sender_name"], MANAGEMENT_MESSAGE_TITLE)
        self.assertEqual(expected_event_2["conversation_id"], -502)
        self.assertEqual(expected_event_2["conversation_other_user_id"], -502)

        mock_publish_event.assert_has_awaits(
            [
                mock.call(101, "chat:message", expected_event_1),
                mock.call(102, "chat:message", expected_event_2),
            ]
        )

        expected_notification_text = f"{MANAGEMENT_MESSAGE_TITLE}\n{payload_in.content}"
        mock_notification.assert_has_awaits(
            [
                mock.call(
                    fake_db,
                    101,
                    expected_notification_text,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    extra_payload={
                        "route": "/chat?user_id=-501",
                        "source": "admin_message",
                        "admin_message_type": "broadcast",
                        "admin_broadcast_id": 88,
                        "chat_id": 501,
                    },
                ),
                mock.call(
                    fake_db,
                    102,
                    expected_notification_text,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    extra_payload={
                        "route": "/chat?user_id=-502",
                        "source": "admin_message",
                        "admin_message_type": "broadcast",
                        "admin_broadcast_id": 88,
                        "chat_id": 502,
                    },
                ),
            ]
        )

        mock_audit.assert_called_once_with(
            "admin.broadcast",
            target_type="admin_broadcast",
            target_id=88,
            actor_id=actor_id,
            actor_role="super_admin",
            after_summary={
                "target_groups": ["customers", "managers"],
                "recipient_count": 2,
            },
        )

        self.assertIsInstance(result, AdminBroadcastCreateResponse)
        self.assertEqual(result.id, 88)
        self.assertEqual(result.content, payload_in.content)
        self.assertEqual(result.created_by_id, actor_id)
        self.assertEqual(result.created_by_name, "admin_operator")
        self.assertEqual(result.target_groups, ["customers", "managers"])
        self.assertEqual(result.recipient_count, 2)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)
        self.assertEqual(result.delivered_user_ids, recipient_ids)
        fake_db.execute.assert_not_called()

    async def test_create_broadcast_zero_recipients_success(self) -> None:
        """Cover zero-recipient success path: no messages, publication, or notification."""
        fake_db = FakeAsyncSession()
        actor_id = 9
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 11, 0, 0)
        created_at = datetime(2026, 6, 1, 11, 0, 0)
        payload_in = AdminBroadcastCreate(
            content="اعلان عمومی بدون گیرنده",
            target_groups=["users"],
        )

        broadcast_message = SimpleNamespace(
            id=89,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_zero"),
            target_groups=["users"],
            recipient_count=0,
            published_at=published_at,
            created_at=created_at,
        )

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            return_value=(broadcast_message, []),
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
        ) as mock_audit:
            result = await create_broadcast(
                data=payload_in,
                current_user=current_user,
                db=fake_db,
            )

        mock_recipients.assert_awaited_once_with(
            fake_db,
            target_groups=["users"],
            exclude_user_ids=[actor_id],
        )
        mock_create.assert_awaited_once_with(
            fake_db,
            actor=current_user,
            content=payload_in.content,
            target_groups=["users"],
            recipient_user_ids=[],
        )
        mock_publish_event.assert_not_called()
        mock_notification.assert_not_called()
        mock_audit.assert_called_once_with(
            "admin.broadcast",
            target_type="admin_broadcast",
            target_id=89,
            actor_id=actor_id,
            actor_role="super_admin",
            after_summary={
                "target_groups": ["users"],
                "recipient_count": 0,
            },
        )

        self.assertIsInstance(result, AdminBroadcastCreateResponse)
        self.assertEqual(result.id, 89)
        self.assertEqual(result.recipient_count, 0)
        self.assertEqual(result.delivered_user_ids, [])
        fake_db.execute.assert_not_called()

    async def test_create_broadcast_fallback_serialization_fields(self) -> None:
        """Verify serialization fallbacks for optional or missing broadcast fields."""
        fake_db = FakeAsyncSession()
        actor_id = 12
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 11, 30, 0)
        created_at = datetime(2026, 6, 1, 11, 30, 0)
        payload_in = AdminBroadcastCreate(
            content="پیام برای بررسی مقادیر فال‌بک",
            target_groups=["accountants"],
        )

        broadcast_message = SimpleNamespace(
            id=90,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=None,
            target_groups=None,
            recipient_count=None,
            published_at=published_at,
            created_at=created_at,
        )

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[205],
        ), mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            return_value=(broadcast_message, []),
        ), mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
        ), mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ), mock.patch(
            "api.routers.admin_messages.audit_log",
        ):
            result = await create_broadcast(
                data=payload_in,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIsInstance(result, AdminBroadcastCreateResponse)
        self.assertEqual(result.id, 90)
        self.assertIsNone(result.created_by_name)
        self.assertEqual(result.target_groups, [])
        self.assertEqual(result.recipient_count, 0)
        self.assertEqual(result.delivered_user_ids, [205])
        fake_db.execute.assert_not_called()

    async def test_failure_propagation_recipient_lookup_halts_downstream(self) -> None:
        """Recipient lookup failure propagates and halts broadcast creation, events, and audit."""
        fake_db = FakeAsyncSession()
        actor_id = 21
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminBroadcastCreate(content="خطا در دریافت گیرندگان", target_groups=["users"])
        expected_error = RuntimeError("Database error during broadcast recipient lookup")

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
        ) as mock_audit:
            with self.assertRaises(RuntimeError) as ctx:
                await create_broadcast(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Database error during broadcast recipient lookup")
        mock_recipients.assert_awaited_once_with(
            fake_db,
            target_groups=["users"],
            exclude_user_ids=[actor_id],
        )
        mock_create.assert_not_called()
        mock_publish_event.assert_not_called()
        mock_notification.assert_not_called()
        mock_audit.assert_not_called()
        fake_db.execute.assert_not_called()

    async def test_failure_propagation_broadcast_creation_halts_downstream(self) -> None:
        """Broadcast creation failure propagates and halts message delivery and audit."""
        fake_db = FakeAsyncSession()
        actor_id = 22
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminBroadcastCreate(content="خطا در ایجاد رکورد برودکست", target_groups=["users"])
        expected_error = RuntimeError("Database error creating broadcast record")

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[101, 102],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
        ) as mock_audit:
            with self.assertRaises(RuntimeError) as ctx:
                await create_broadcast(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Database error creating broadcast record")
        mock_recipients.assert_awaited_once_with(
            fake_db,
            target_groups=["users"],
            exclude_user_ids=[actor_id],
        )
        mock_create.assert_awaited_once_with(
            fake_db,
            actor=current_user,
            content=payload_in.content,
            target_groups=["users"],
            recipient_user_ids=[101, 102],
        )
        mock_publish_event.assert_not_called()
        mock_notification.assert_not_called()
        mock_audit.assert_not_called()
        fake_db.execute.assert_not_called()

    async def test_failure_propagation_delivery_publish_event_halts_downstream(self) -> None:
        """Delivery event publication failure preserves exact completed prefix and prevents audit."""
        fake_db = FakeAsyncSession()
        actor_id = 23
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 12, 0, 0)
        created_at = datetime(2026, 6, 1, 12, 0, 0)
        payload_in = AdminBroadcastCreate(content="خطا حین انتشار پیام دوم", target_groups=["users"])

        broadcast_message = SimpleNamespace(
            id=91,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_operator"),
            target_groups=["users"],
            recipient_count=3,
            published_at=published_at,
            created_at=created_at,
        )

        msg1 = SimpleNamespace(
            id=2001,
            sender_id=actor_id,
            receiver_id=401,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        msg2 = SimpleNamespace(
            id=2002,
            sender_id=actor_id,
            receiver_id=402,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        msg3 = SimpleNamespace(
            id=2003,
            sender_id=actor_id,
            receiver_id=403,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        created_messages = [
            SimpleNamespace(recipient_user_id=401, chat=SimpleNamespace(id=601), message=msg1),
            SimpleNamespace(recipient_user_id=402, chat=SimpleNamespace(id=602), message=msg2),
            SimpleNamespace(recipient_user_id=403, chat=SimpleNamespace(id=603), message=msg3),
        ]
        expected_error = RuntimeError("Simulated websocket broker failure for recipient 402")

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[401, 402, 403],
        ), mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            return_value=(broadcast_message, created_messages),
        ), mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
            side_effect=[None, expected_error, None],
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
        ) as mock_audit:
            with self.assertRaises(RuntimeError) as ctx:
                await create_broadcast(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Simulated websocket broker failure for recipient 402")
        self.assertEqual(mock_publish_event.await_count, 2)
        self.assertEqual(mock_publish_event.await_args_list[0].args[0], 401)
        self.assertEqual(mock_publish_event.await_args_list[1].args[0], 402)
        self.assertEqual(mock_notification.await_count, 1)
        self.assertEqual(mock_notification.await_args_list[0].args[1], 401)
        mock_audit.assert_not_called()
        fake_db.execute.assert_not_called()

    async def test_failure_propagation_delivery_notification_halts_downstream(self) -> None:
        """Delivery notification failure preserves exact completed prefix and prevents audit."""
        fake_db = FakeAsyncSession()
        actor_id = 24
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 13, 0, 0)
        created_at = datetime(2026, 6, 1, 13, 0, 0)
        payload_in = AdminBroadcastCreate(content="خطا حین ارسال اعلان دوم", target_groups=["users"])

        broadcast_message = SimpleNamespace(
            id=92,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_operator"),
            target_groups=["users"],
            recipient_count=3,
            published_at=published_at,
            created_at=created_at,
        )

        msg1 = SimpleNamespace(
            id=3001,
            sender_id=actor_id,
            receiver_id=501,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        msg2 = SimpleNamespace(
            id=3002,
            sender_id=actor_id,
            receiver_id=502,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        msg3 = SimpleNamespace(
            id=3003,
            sender_id=actor_id,
            receiver_id=503,
            content=payload_in.content,
            message_type="text",
            is_read=False,
            is_deleted=False,
            updated_at=published_at,
            created_at=created_at,
            forwarded_from_id=None,
        )
        created_messages = [
            SimpleNamespace(recipient_user_id=501, chat=SimpleNamespace(id=701), message=msg1),
            SimpleNamespace(recipient_user_id=502, chat=SimpleNamespace(id=702), message=msg2),
            SimpleNamespace(recipient_user_id=503, chat=SimpleNamespace(id=703), message=msg3),
        ]
        expected_error = RuntimeError("Notification push failed for recipient 502")

        with mock.patch(
            "api.routers.admin_messages.list_broadcast_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[501, 502, 503],
        ), mock.patch(
            "api.routers.admin_messages.create_management_broadcast",
            new_callable=mock.AsyncMock,
            return_value=(broadcast_message, created_messages),
        ), mock.patch(
            "api.routers.admin_messages.publish_user_event",
            new_callable=mock.AsyncMock,
        ) as mock_publish_event, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
            side_effect=[None, expected_error, None],
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.audit_log",
        ) as mock_audit:
            with self.assertRaises(RuntimeError) as ctx:
                await create_broadcast(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Notification push failed for recipient 502")
        self.assertEqual(mock_publish_event.await_count, 2)
        self.assertEqual(mock_publish_event.await_args_list[0].args[0], 501)
        self.assertEqual(mock_publish_event.await_args_list[1].args[0], 502)
        self.assertEqual(mock_notification.await_count, 2)
        self.assertEqual(mock_notification.await_args_list[0].args[1], 501)
        self.assertEqual(mock_notification.await_args_list[1].args[1], 502)
        mock_audit.assert_not_called()
        fake_db.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
