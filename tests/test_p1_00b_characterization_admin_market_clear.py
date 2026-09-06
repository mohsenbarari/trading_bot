"""Hermetic direct characterization for clear_current_market_message endpoint.

Target: api.routers.admin_messages.clear_current_market_message

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
    AdminMarketMessageRead,
    clear_current_market_message,
)


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAdminMarketClearCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for clear_current_market_message."""

    async def test_clear_current_market_message_success_with_active_message(self) -> None:
        """Characterize non-None success path: user unused, deactivation, cache None, event None, serialized return."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 9, 30, 0)

        deactivated_message = SimpleNamespace(
            id=77,
            content="پیام بازار جهت پاکسازی",
            created_by_id=3,
            created_by=SimpleNamespace(account_name="origin_admin"),
            reused_from_id=11,
            is_active=False,
            notified_recipients_count=4,
            published_at=published_at,
            created_at=created_at,
        )

        call_order: list[str] = []

        async def _fake_deactivate(db):
            call_order.append("deactivate_current_market_management_message")
            return deactivated_message

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        def _fake_publish_event(event_name, payload):
            call_order.append("publish_event_sync")

        orig_serialize = admin_messages_module._serialize_market_message

        def _fake_serialize(message):
            call_order.append("_serialize_market_message")
            return orig_serialize(message)

        with mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_deactivate,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
            side_effect=_fake_publish_event,
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages._serialize_market_message",
            side_effect=_fake_serialize,
        ) as mock_serialize:
            result = await clear_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "deactivate_current_market_management_message",
                "set_cached_admin_market_current",
                "publish_event_sync",
                "_serialize_market_message",
            ],
        )

        mock_deactivate.assert_awaited_once_with(fake_db)
        mock_cache.assert_awaited_once_with(None)
        mock_publish.assert_called_once_with("market:admin_message_published", None)

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 77)
        self.assertEqual(result.content, "پیام بازار جهت پاکسازی")
        self.assertEqual(result.created_by_id, 3)
        self.assertEqual(result.created_by_name, "origin_admin")
        self.assertEqual(result.reused_from_id, 11)
        self.assertFalse(result.is_active)
        self.assertEqual(result.notified_recipients_count, 4)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

    async def test_clear_current_market_message_no_current_message_returns_none(self) -> None:
        """Characterize no-current-message path: returns None while cache and event still occur in order."""
        fake_db = FakeAsyncSession()
        unused_user = object()

        call_order: list[str] = []

        async def _fake_deactivate(db):
            call_order.append("deactivate_current_market_management_message")
            return None

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        def _fake_publish_event(event_name, payload):
            call_order.append("publish_event_sync")

        with mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_deactivate,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
            side_effect=_fake_publish_event,
        ) as mock_publish:
            result = await clear_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "deactivate_current_market_management_message",
                "set_cached_admin_market_current",
                "publish_event_sync",
            ],
        )

        mock_deactivate.assert_awaited_once_with(fake_db)
        mock_cache.assert_awaited_once_with(None)
        mock_publish.assert_called_once_with("market:admin_message_published", None)
        self.assertIsNone(result)

    async def test_failure_propagation_database_deactivation_halts_downstream(self) -> None:
        """Database deactivation failure propagates exactly and halts cache, event, and compensating calls."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated database failure during deactivation")

        with mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_create:
            with self.assertRaises(RuntimeError) as ctx:
                await clear_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_deactivate.assert_awaited_once_with(fake_db)
        mock_cache.assert_not_called()
        mock_publish.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_create.assert_not_called()

    async def test_failure_propagation_cache_write_halts_downstream(self) -> None:
        """Cache-write failure propagates after deactivation without publishing event or invented rollback."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated Redis failure on setting cache to None")
        deactivated_message = SimpleNamespace(
            id=78,
            content="پیام با خطای کش",
            created_by_id=5,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=False,
            notified_recipients_count=0,
            published_at=datetime(2026, 6, 1, 11, 0, 0),
            created_at=datetime(2026, 6, 1, 11, 0, 0),
        )

        with mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=deactivated_message,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_create:
            with self.assertRaises(RuntimeError) as ctx:
                await clear_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_deactivate.assert_awaited_once_with(fake_db)
        mock_cache.assert_awaited_once_with(None)
        mock_publish.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_create.assert_not_called()

    async def test_failure_propagation_event_publication_halts_downstream(self) -> None:
        """Synchronous event-publication failure propagates after deactivation and cache None without rollback."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated synchronous event bus crash on clear")
        deactivated_message = SimpleNamespace(
            id=79,
            content="پیام با خطای انتشار",
            created_by_id=5,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=False,
            notified_recipients_count=0,
            published_at=datetime(2026, 6, 1, 12, 0, 0),
            created_at=datetime(2026, 6, 1, 12, 0, 0),
        )

        with mock.patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=deactivated_message,
        ) as mock_deactivate, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as mock_cache, mock.patch(
            "api.routers.admin_messages.publish_event_sync",
            side_effect=expected_error,
        ) as mock_publish, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate, mock.patch(
            "api.routers.admin_messages.create_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_create:
            with self.assertRaises(RuntimeError) as ctx:
                await clear_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_deactivate.assert_awaited_once_with(fake_db)
        mock_cache.assert_awaited_once_with(None)
        mock_publish.assert_called_once_with("market:admin_message_published", None)
        mock_invalidate.assert_not_called()
        mock_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
