"""Hermetic direct characterization for create_market_message endpoint.

Target: api.routers.admin_messages.create_market_message

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
    AdminMarketMessageCreate,
    AdminMarketMessageRead,
    create_market_message,
)
from core.enums import NotificationCategory, NotificationLevel
from core.services.admin_message_service import MANAGEMENT_MESSAGE_TITLE


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAdminMarketCreateCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for create_market_message."""

    async def test_create_market_message_success_call_order_and_boundaries(self) -> None:
        """Characterize success boundary and exact call ordering across collaborators."""
        fake_db = FakeAsyncSession()
        actor_id = 7
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 10, 0, 0)
        recipient_ids = [101, 102]
        payload_in = AdminMarketMessageCreate(
            content="پیام جدید بازار جهت تست",
            reused_from_id=None,
        )

        market_message = SimpleNamespace(
            id=55,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_operator"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=len(recipient_ids),
            published_at=published_at,
            created_at=created_at,
        )

        call_order: list[str] = []

        async def _fake_list_recipients(db, exclude_user_ids=None):
            call_order.append("list_market_management_recipient_user_ids")
            return recipient_ids

        async def _fake_create_message(db, actor=None, content=None, reused_from_id=None, notified_recipients_count=None):
            call_order.append("create_market_management_message")
            return market_message

        orig_serialize = admin_messages_module._serialize_market_message

        def _fake_serialize(message):
            call_order.append("_serialize_market_message")
            return orig_serialize(message)

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        def _fake_publish_event(event_name, payload):
            call_order.append("publish_event_sync")

        async def _fake_create_notification(db, user_id, notification_text, level=None, category=None, extra_payload=None):
            call_order.append(f"create_user_notification:{user_id}")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            side_effect=_fake_list_recipients,
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_create_message,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages._serialize_market_message",
            side_effect=_fake_serialize,
        ) as mock_serialize, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
            side_effect=_fake_publish_event,
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
            side_effect=_fake_create_notification,
        ) as mock_notification:
            result = await create_market_message(
                data=payload_in,
                current_user=current_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "list_market_management_recipient_user_ids",
                "create_market_management_message",
                "_serialize_market_message",
                "set_cached_admin_market_current",
                "publish_event_sync",
                "create_user_notification:101",
                "create_user_notification:102",
                "_serialize_market_message",
            ],
        )

        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once_with(
            fake_db,
            actor=current_user,
            content=payload_in.content,
            reused_from_id=None,
            notified_recipients_count=2,
        )

        expected_cached_payload = {
            "id": 55,
            "content": payload_in.content,
            "created_by_id": actor_id,
            "created_by_name": "admin_operator",
            "reused_from_id": None,
            "is_active": True,
            "notified_recipients_count": 2,
            "published_at": "2026-06-01T10:00:00",
            "created_at": "2026-06-01T10:00:00",
        }
        mock_cache.assert_awaited_once_with(expected_cached_payload)
        mock_publish.assert_called_once_with("market:admin_message_published", expected_cached_payload)

        self.assertEqual(mock_notification.await_count, 2)
        expected_notification_text = f"{MANAGEMENT_MESSAGE_TITLE}\n{payload_in.content}"
        expected_extra_payload = {
            "route": "/market",
            "source": "admin_message",
            "admin_message_type": "market",
            "admin_message_id": 55,
        }
        mock_notification.assert_has_awaits(
            [
                mock.call(
                    fake_db,
                    101,
                    expected_notification_text,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    extra_payload=expected_extra_payload,
                ),
                mock.call(
                    fake_db,
                    102,
                    expected_notification_text,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    extra_payload=expected_extra_payload,
                ),
            ]
        )

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 55)
        self.assertEqual(result.content, payload_in.content)
        self.assertEqual(result.created_by_id, actor_id)
        self.assertEqual(result.created_by_name, "admin_operator")
        self.assertIsNone(result.reused_from_id)
        self.assertTrue(result.is_active)
        self.assertEqual(result.notified_recipients_count, 2)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

    async def test_create_market_message_zero_recipients_success(self) -> None:
        """Cover zero-recipient success behavior: notified_recipients_count 0 and notification skipped."""
        fake_db = FakeAsyncSession()
        actor_id = 9
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        published_at = datetime(2026, 6, 1, 11, 0, 0)
        created_at = datetime(2026, 6, 1, 11, 0, 0)
        payload_in = AdminMarketMessageCreate(
            content="پیام بدون دریافتکننده",
            reused_from_id=12,
        )

        market_message = SimpleNamespace(
            id=56,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin_zero"),
            reused_from_id=12,
            is_active=True,
            notified_recipients_count=0,
            published_at=published_at,
            created_at=created_at,
        )

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=market_message,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification:
            result = await create_market_message(
                data=payload_in,
                current_user=current_user,
                db=fake_db,
            )

        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once_with(
            fake_db,
            actor=current_user,
            content=payload_in.content,
            reused_from_id=12,
            notified_recipients_count=0,
        )
        mock_cache.assert_awaited_once()
        mock_publish.assert_called_once()
        mock_notification.assert_not_called()

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 56)
        self.assertEqual(result.notified_recipients_count, 0)
        self.assertEqual(result.reused_from_id, 12)

    async def test_failure_propagation_message_creation_halts_downstream(self) -> None:
        """Message creation failure propagates and halts cache, event, and notification work."""
        fake_db = FakeAsyncSession()
        actor_id = 15
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminMarketMessageCreate(content="پیام با خطای دیتابیس")
        expected_error = RuntimeError("Simulated database failure creating market message")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[201],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification:
            with self.assertRaises(RuntimeError) as ctx:
                await create_market_message(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Simulated database failure creating market message")
        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once()
        mock_cache.assert_not_called()
        mock_publish.assert_not_called()
        mock_notification.assert_not_called()

    async def test_failure_propagation_cache_update_halts_downstream(self) -> None:
        """Cache update failure propagates after creation and halts event and notification work."""
        fake_db = FakeAsyncSession()
        actor_id = 16
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminMarketMessageCreate(content="پیام با خطای کش")
        published_at = datetime(2026, 6, 1, 12, 0, 0)
        created_at = datetime(2026, 6, 1, 12, 0, 0)
        market_message = SimpleNamespace(
            id=57,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=1,
            published_at=published_at,
            created_at=created_at,
        )
        expected_error = RuntimeError("Simulated Redis cache failure")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[201],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=market_message,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification:
            with self.assertRaises(RuntimeError) as ctx:
                await create_market_message(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Simulated Redis cache failure")
        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once()
        mock_cache.assert_awaited_once()
        mock_publish.assert_not_called()
        mock_notification.assert_not_called()

    async def test_failure_propagation_event_publication_halts_downstream(self) -> None:
        """Event publication failure propagates after cache update and halts notification work."""
        fake_db = FakeAsyncSession()
        actor_id = 17
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminMarketMessageCreate(content="پیام با خطای انتشار رویداد")
        published_at = datetime(2026, 6, 1, 13, 0, 0)
        created_at = datetime(2026, 6, 1, 13, 0, 0)
        market_message = SimpleNamespace(
            id=58,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=1,
            published_at=published_at,
            created_at=created_at,
        )
        expected_error = RuntimeError("Simulated synchronous event bus crash")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=[201],
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=market_message,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
            side_effect=expected_error,
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
        ) as mock_notification:
            with self.assertRaises(RuntimeError) as ctx:
                await create_market_message(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Simulated synchronous event bus crash")
        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once()
        mock_cache.assert_awaited_once()
        mock_publish.assert_called_once()
        mock_notification.assert_not_called()

    async def test_notification_failure_propagates_without_retry_or_invented_rollback(self) -> None:
        """Notification failure propagates after earlier steps without retry, false delivery claims, or rollback."""
        fake_db = FakeAsyncSession()
        actor_id = 18
        current_user = SimpleNamespace(id=actor_id, role="super_admin")
        payload_in = AdminMarketMessageCreate(content="پیام با خطای اعلان")
        published_at = datetime(2026, 6, 1, 14, 0, 0)
        created_at = datetime(2026, 6, 1, 14, 0, 0)
        recipient_ids = [301, 302, 303]
        market_message = SimpleNamespace(
            id=59,
            content=payload_in.content,
            created_by_id=actor_id,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=len(recipient_ids),
            published_at=published_at,
            created_at=created_at,
        )
        expected_error = RuntimeError("Notification delivery failed on second recipient")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_recipient_user_ids",
            new_callable=mock.AsyncMock,
            return_value=recipient_ids,
        ) as mock_recipients, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=market_message,
        ) as mock_create, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.create_user_notification",
            new_callable=mock.AsyncMock,
            side_effect=[None, expected_error, None],
        ) as mock_notification, mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate:
            with self.assertRaises(RuntimeError) as ctx:
                await create_market_message(
                    data=payload_in,
                    current_user=current_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        self.assertEqual(str(ctx.exception), "Notification delivery failed on second recipient")

        mock_recipients.assert_awaited_once_with(fake_db, exclude_user_ids=[actor_id])
        mock_create.assert_awaited_once()
        mock_cache.assert_awaited_once()
        mock_publish.assert_called_once()

        self.assertEqual(mock_notification.await_count, 2)
        self.assertEqual(mock_notification.await_args_list[0].args[1], 301)
        self.assertEqual(mock_notification.await_args_list[1].args[1], 302)

        mock_deactivate.assert_not_called()
        mock_invalidate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
