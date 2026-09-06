"""Hermetic direct characterization for get_market_message_history endpoint.

Target: api.routers.admin_messages.get_market_message_history

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

from api.routers.admin_messages import (
    AdminMarketMessageRead,
    get_market_message_history,
)


class FakeAsyncSession:
    """Minimal fake AsyncSession for hermetic characterization testing."""

    def __init__(self) -> None:
        self.execute = mock.AsyncMock()


class P100bAdminMarketHistoryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct characterization tests for get_market_message_history."""

    async def test_get_market_message_history_non_empty_success(self) -> None:
        """Characterize non-empty history serialization, serializer mapping, order, and limit."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        pub1 = datetime(2026, 6, 1, 10, 0, 0)
        created1 = datetime(2026, 6, 1, 9, 30, 0)
        pub2 = datetime(2026, 6, 2, 11, 0, 0)
        created2 = datetime(2026, 6, 2, 10, 30, 0)
        pub3 = datetime(2026, 6, 3, 12, 0, 0)
        created3 = datetime(2026, 6, 3, 11, 30, 0)

        raw_messages = [
            SimpleNamespace(
                id=101,
                content="پیام اول بازار",
                created_by_id=10,
                created_by=SimpleNamespace(account_name="author_alice"),
                reused_from_id=42,
                is_active=1,
                notified_recipients_count=17,
                published_at=pub1,
                created_at=created1,
            ),
            SimpleNamespace(
                id=102,
                content="پیام دوم بازار",
                created_by_id=11,
                created_by=None,
                reused_from_id=None,
                is_active=0,
                notified_recipients_count=None,
                published_at=pub2,
                created_at=created2,
            ),
            SimpleNamespace(
                id=103,
                content="پیام سوم بازار",
                created_by_id=12,
                created_by=SimpleNamespace(),
                reused_from_id=99,
                is_active=False,
                notified_recipients_count=0,
                published_at=pub3,
                created_at=created3,
            ),
        ]

        with mock.patch(
            "api.routers.admin_messages.list_market_management_history",
            new_callable=mock.AsyncMock,
            return_value=raw_messages,
        ) as mock_list_history:
            result = await get_market_message_history(
                limit=15,
                current_user=unused_user,
                db=fake_db,
            )

        mock_list_history.assert_awaited_once_with(fake_db, limit=15)
        fake_db.execute.assert_not_called()

        self.assertEqual(len(result), 3)
        for item in result:
            self.assertIsInstance(item, AdminMarketMessageRead)

        self.assertEqual([item.id for item in result], [101, 102, 103])

        self.assertEqual(result[0].id, 101)
        self.assertEqual(result[0].content, "پیام اول بازار")
        self.assertEqual(result[0].created_by_id, 10)
        self.assertEqual(result[0].created_by_name, "author_alice")
        self.assertEqual(result[0].reused_from_id, 42)
        self.assertIs(result[0].is_active, True)
        self.assertEqual(result[0].notified_recipients_count, 17)
        self.assertEqual(result[0].published_at, pub1)
        self.assertEqual(result[0].created_at, created1)

        self.assertEqual(result[1].id, 102)
        self.assertEqual(result[1].content, "پیام دوم بازار")
        self.assertEqual(result[1].created_by_id, 11)
        self.assertIsNone(result[1].created_by_name)
        self.assertIsNone(result[1].reused_from_id)
        self.assertIs(result[1].is_active, False)
        self.assertEqual(result[1].notified_recipients_count, 0)
        self.assertEqual(result[1].published_at, pub2)
        self.assertEqual(result[1].created_at, created2)

        self.assertEqual(result[2].id, 103)
        self.assertEqual(result[2].content, "پیام سوم بازار")
        self.assertEqual(result[2].created_by_id, 12)
        self.assertIsNone(result[2].created_by_name)
        self.assertEqual(result[2].reused_from_id, 99)
        self.assertIs(result[2].is_active, False)
        self.assertEqual(result[2].notified_recipients_count, 0)
        self.assertEqual(result[2].published_at, pub3)
        self.assertEqual(result[2].created_at, created3)

    async def test_get_market_message_history_default_limit_and_empty_result(self) -> None:
        """Characterize omitting limit default (50) and returning an empty list."""
        fake_db = FakeAsyncSession()
        unused_user = object()

        with mock.patch(
            "api.routers.admin_messages.list_market_management_history",
            new_callable=mock.AsyncMock,
            return_value=[],
        ) as mock_list_history:
            result = await get_market_message_history(
                current_user=unused_user,
                db=fake_db,
            )

        mock_list_history.assert_awaited_once_with(fake_db, limit=50)
        fake_db.execute.assert_not_called()
        self.assertEqual(result, [])

    async def test_failure_propagation_list_market_management_history_exception(self) -> None:
        """Characterize service exception propagating unchanged from list_market_management_history."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        expected_error = RuntimeError("Database query failed")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_history",
            new_callable=mock.AsyncMock,
            side_effect=expected_error,
        ) as mock_list_history:
            with self.assertRaises(RuntimeError) as ctx:
                await get_market_message_history(
                    limit=20,
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_list_history.assert_awaited_once_with(fake_db, limit=20)
        fake_db.execute.assert_not_called()

    async def test_failure_propagation_item_serialization_exception(self) -> None:
        """Characterize item serialization failure propagating and preventing serialization of later items."""
        fake_db = FakeAsyncSession()
        unused_user = object()
        msg1 = SimpleNamespace(id=1)
        msg2 = SimpleNamespace(id=2)
        msg3 = SimpleNamespace(id=3)
        expected_error = ValueError("Serialization failed for item 2")

        with mock.patch(
            "api.routers.admin_messages.list_market_management_history",
            new_callable=mock.AsyncMock,
            return_value=[msg1, msg2, msg3],
        ) as mock_list_history, mock.patch(
            "api.routers.admin_messages._serialize_market_message",
            side_effect=[mock.Mock(), expected_error, mock.Mock()],
        ) as mock_serialize:
            with self.assertRaises(ValueError) as ctx:
                await get_market_message_history(
                    limit=30,
                    current_user=unused_user,
                    db=fake_db,
                )

        self.assertIs(ctx.exception, expected_error)
        mock_list_history.assert_awaited_once_with(fake_db, limit=30)
        fake_db.execute.assert_not_called()
        self.assertEqual(mock_serialize.call_count, 2)
        mock_serialize.assert_has_calls([mock.call(msg1), mock.call(msg2)])


if __name__ == "__main__":
    unittest.main()
