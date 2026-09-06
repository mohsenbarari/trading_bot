"""Hermetic direct characterization for get_current_market_message endpoint.

Target: api.routers.admin_messages.get_current_market_message

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
    get_current_market_message,
)


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAdminMarketCurrentCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for get_current_market_message."""

    async def test_get_current_market_message_valid_cache_hit(self) -> None:
        """Characterize valid non-null cache hit without database query or cache mutation."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 9, 30, 0)
        cached_payload = {
            "id": 42,
            "content": "پیام موجود در کش",
            "created_by_id": 2,
            "created_by_name": "market_admin",
            "reused_from_id": 5,
            "is_active": True,
            "notified_recipients_count": 10,
            "published_at": "2026-06-01T10:00:00",
            "created_at": "2026-06-01T09:30:00",
        }

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=(True, cached_payload),
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            result = await get_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_not_called()
        mock_set_cache.assert_not_called()
        mock_invalidate_cache.assert_not_called()

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 42)
        self.assertEqual(result.content, "پیام موجود در کش")
        self.assertEqual(result.created_by_id, 2)
        self.assertEqual(result.created_by_name, "market_admin")
        self.assertEqual(result.reused_from_id, 5)
        self.assertTrue(result.is_active)
        self.assertEqual(result.notified_recipients_count, 10)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

    async def test_get_current_market_message_cached_null_hit(self) -> None:
        """Characterize cached-null hit returning None without database query or cache mutation."""
        fake_db = FakeAsyncSession()
        unused_user = object()

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=(True, None),
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            result = await get_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_not_called()
        mock_set_cache.assert_not_called()
        mock_invalidate_cache.assert_not_called()
        self.assertIsNone(result)

    async def test_get_current_market_message_cache_miss_with_database_message(self) -> None:
        """Characterize cache miss with database message: queries db, sets JSON cache, returns message."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 9, 30, 0)

        db_message = SimpleNamespace(
            id=51,
            content="پیام دریافتی از پایگاه داده",
            created_by_id=3,
            created_by=SimpleNamespace(account_name="author_admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=5,
            published_at=published_at,
            created_at=created_at,
        )

        call_order: list[str] = []

        async def _fake_get_cache():
            call_order.append("get_cached_admin_market_current")
            return False, None

        async def _fake_get_db(db):
            call_order.append("get_current_market_management_message")
            return db_message

        orig_serialize = admin_messages_module._serialize_market_message

        def _fake_serialize(message):
            call_order.append("_serialize_market_message")
            return orig_serialize(message)

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_cache,
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_db,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages._serialize_market_message",
            side_effect=_fake_serialize,
        ) as mock_serialize, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            result = await get_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "get_cached_admin_market_current",
                "get_current_market_management_message",
                "_serialize_market_message",
                "set_cached_admin_market_current",
            ],
        )

        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_awaited_once_with(fake_db)
        expected_cache_payload = {
            "id": 51,
            "content": "پیام دریافتی از پایگاه داده",
            "created_by_id": 3,
            "created_by_name": "author_admin",
            "reused_from_id": None,
            "is_active": True,
            "notified_recipients_count": 5,
            "published_at": "2026-06-01T10:00:00",
            "created_at": "2026-06-01T09:30:00",
        }
        mock_set_cache.assert_awaited_once_with(expected_cache_payload)
        mock_invalidate_cache.assert_not_called()

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 51)
        self.assertEqual(result.content, "پیام دریافتی از پایگاه داده")
        self.assertEqual(result.created_by_id, 3)
        self.assertEqual(result.created_by_name, "author_admin")
        self.assertIsNone(result.reused_from_id)
        self.assertTrue(result.is_active)
        self.assertEqual(result.notified_recipients_count, 5)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

    async def test_get_current_market_message_cache_miss_without_database_message(self) -> None:
        """Characterize cache miss with no database message: queries db, caches None, returns None."""
        fake_db = FakeAsyncSession()
        unused_user = object()

        call_order: list[str] = []

        async def _fake_get_cache():
            call_order.append("get_cached_admin_market_current")
            return False, None

        async def _fake_get_db(db):
            call_order.append("get_current_market_management_message")
            return None

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_cache,
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_db,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            result = await get_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "get_cached_admin_market_current",
                "get_current_market_management_message",
                "set_cached_admin_market_current",
            ],
        )

        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_awaited_once_with(fake_db)
        mock_set_cache.assert_awaited_once_with(None)
        mock_invalidate_cache.assert_not_called()
        self.assertIsNone(result)

    async def test_get_current_market_message_invalid_cached_payload_fallbacks_and_refreshes(self) -> None:
        """Characterize invalid cache fail-open: invalidates cache, queries db, sets refreshed cache."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        invalid_cached_payload = {"id": "not-an-int-and-missing-fields"}
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 9, 30, 0)

        db_message = SimpleNamespace(
            id=60,
            content="پیام جایگزین پس از کش نامعتبر",
            created_by_id=4,
            created_by=SimpleNamespace(account_name="recovering_admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=8,
            published_at=published_at,
            created_at=created_at,
        )

        call_order: list[str] = []

        async def _fake_get_cache():
            call_order.append("get_cached_admin_market_current")
            return True, invalid_cached_payload

        async def _fake_invalidate():
            call_order.append("invalidate_admin_market_current_cache")
            return True

        async def _fake_get_db(db):
            call_order.append("get_current_market_management_message")
            return db_message

        orig_serialize = admin_messages_module._serialize_market_message

        def _fake_serialize(message):
            call_order.append("_serialize_market_message")
            return orig_serialize(message)

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_cache,
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
            side_effect=_fake_invalidate,
        ) as mock_invalidate_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=_fake_get_db,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages._serialize_market_message",
            side_effect=_fake_serialize,
        ) as mock_serialize, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=_fake_set_cache,
        ) as mock_set_cache:
            result = await get_current_market_message(
                current_user=unused_user,
                db=fake_db,
            )

        self.assertEqual(
            call_order,
            [
                "get_cached_admin_market_current",
                "invalidate_admin_market_current_cache",
                "get_current_market_management_message",
                "_serialize_market_message",
                "set_cached_admin_market_current",
            ],
        )

        mock_get_cache.assert_awaited_once_with()
        mock_invalidate_cache.assert_awaited_once_with()
        mock_db_service.assert_awaited_once_with(fake_db)
        expected_cache_payload = {
            "id": 60,
            "content": "پیام جایگزین پس از کش نامعتبر",
            "created_by_id": 4,
            "created_by_name": "recovering_admin",
            "reused_from_id": None,
            "is_active": True,
            "notified_recipients_count": 8,
            "published_at": "2026-06-01T10:00:00",
            "created_at": "2026-06-01T09:30:00",
        }
        mock_set_cache.assert_awaited_once_with(expected_cache_payload)

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 60)
        self.assertEqual(result.content, "پیام جایگزین پس از کش نامعتبر")
        self.assertEqual(result.created_by_id, 4)
        self.assertEqual(result.created_by_name, "recovering_admin")
        self.assertIsNone(result.reused_from_id)
        self.assertTrue(result.is_active)
        self.assertEqual(result.notified_recipients_count, 8)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

    async def test_failure_propagation_cache_read_halts_downstream(self) -> None:
        """Cache-read failure propagates immediately and halts database, invalidation, and cache writes."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated cache-read failure")

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache:
            with self.assertRaises(RuntimeError) as ctx:
                await get_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_get_cache.assert_awaited_once_with()
        mock_invalidate_cache.assert_not_called()
        mock_db_service.assert_not_called()
        mock_set_cache.assert_not_called()

    async def test_failure_propagation_invalid_cache_invalidation_halts_downstream(self) -> None:
        """Invalidation failure after invalid cache payload propagates immediately and halts database read."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated cache invalidation failure")

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=(True, {"corrupt": "payload"}),
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_invalidate_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache:
            with self.assertRaises(RuntimeError) as ctx:
                await get_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_get_cache.assert_awaited_once_with()
        mock_invalidate_cache.assert_awaited_once_with()
        mock_db_service.assert_not_called()
        mock_set_cache.assert_not_called()

    async def test_failure_propagation_database_read_halts_downstream(self) -> None:
        """Database read failure after cache miss propagates immediately and halts cache write and compensation."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated database read failure")

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=(False, None),
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            with self.assertRaises(RuntimeError) as ctx:
                await get_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_awaited_once_with(fake_db)
        mock_set_cache.assert_not_called()
        mock_invalidate_cache.assert_not_called()

    async def test_failure_propagation_refreshed_cache_write_halts_downstream(self) -> None:
        """Refreshed-cache write failure propagates immediately without retry or invented rollback."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Simulated cache write failure")
        db_message = SimpleNamespace(
            id=61,
            content="پیام پایگاه داده",
            created_by_id=1,
            created_by=SimpleNamespace(account_name="admin"),
            reused_from_id=None,
            is_active=True,
            notified_recipients_count=0,
            published_at=datetime(2026, 6, 1, 10, 0, 0),
            created_at=datetime(2026, 6, 1, 10, 0, 0),
        )

        with mock.patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            return_value=(False, None),
        ) as mock_get_cache, mock.patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new_callable=mock.AsyncMock,
            return_value=db_message,
        ) as mock_db_service, mock.patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_set_cache, mock.patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new_callable=mock.AsyncMock,
        ) as mock_invalidate_cache:
            with self.assertRaises(RuntimeError) as ctx:
                await get_current_market_message(
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_get_cache.assert_awaited_once_with()
        mock_db_service.assert_awaited_once_with(fake_db)
        mock_set_cache.assert_awaited_once()
        mock_invalidate_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
