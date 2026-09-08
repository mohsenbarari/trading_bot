"""Characterization test for notifications.delete_notification SQL success path.

Seed ID: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-NOTIFICATION-L362

This module characterizes direct handler SQL success for seed
API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-NOTIFICATION-L362, verifying durable
SQL deletion effects and real unread-count synchronization. It characterizes
direct handler SQL success, not HTTP routing, process reload, concurrency,
production parity or Stage completion.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import tempfile
import types
import unittest
from typing import Any
from unittest.mock import AsyncMock

# Select test env file and install network fence before application imports
os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.notifications import delete_notification
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class AsyncSessionAdapter:
    """Minimal async session adapter delegating execute/delete/commit to a sync Session."""

    def __init__(self, sync_session: Session) -> None:
        self.sync_session = sync_session
        self.delete_calls: list[Any] = []
        self.commit_count: int = 0
        self.execute_calls: list[Any] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.execute_calls.append(statement)
        return self.sync_session.execute(statement, *args, **kwargs)

    async def delete(self, instance: Any) -> None:
        self.delete_calls.append(instance)
        self.sync_session.delete(instance)

    async def commit(self) -> None:
        self.commit_count += 1
        self.sync_session.commit()

    async def rollback(self) -> None:
        self.sync_session.rollback()

    async def close(self) -> None:
        self.sync_session.close()


class NotificationDeleteSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for notifications.delete_notification SQL success path."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="p1_00b_notif_del_")
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)

        self.db_path = os.path.join(self.temp_dir, "test_characterization.db")
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.addCleanup(self.engine.dispose)

        self._init_sqlite_schema()

    def _init_sqlite_schema(self) -> None:
        """Create minimal raw SQL notifications schema matching all mapped columns."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    is_read BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    level VARCHAR(32) NOT NULL DEFAULT 'INFO',
                    category VARCHAR(32) NOT NULL DEFAULT 'SYSTEM',
                    dedupe_key VARCHAR(180),
                    extra_payload TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX ix_notifications_user_unread ON notifications (user_id, is_read);"
            )
            conn.commit()

    def _make_session(self) -> Session:
        session = Session(self.engine)
        self.addCleanup(session.close)
        return session

    def _seed_notifications(self) -> None:
        """Seed four synthetic rows: target unread for user 5, unread for user 5, read for user 5, unread for user 9."""
        session = self._make_session()
        session.add_all(
            [
                Notification(
                    id=1,
                    user_id=5,
                    message="Target unread notification for user 5",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=2,
                    user_id=5,
                    message="Remaining unread notification for user 5",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=3,
                    user_id=5,
                    message="Remaining read notification for user 5",
                    is_read=True,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=4,
                    user_id=9,
                    message="Unread notification for user 9",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
            ]
        )
        session.commit()
        session.close()

    def _fetch_durable_notifications(self) -> list[tuple[int, int, str, int]]:
        """Read durable rows directly from separate SQLite connection."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, message, is_read FROM notifications ORDER BY id"
            )
            return cursor.fetchall()

    async def test_delete_notification_success_and_syncs_unread_count(self) -> None:
        """Seed API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-NOTIFICATION-L362 SQL success path.

        Verifies:
        - Handler returns None
        - Exactly target ID is passed to adapter delete
        - Exactly one commit is made
        - Observation callback during redis.set await proves durable deletion before Redis write
        - Redis unread counter is set to 1 for user 5
        - Durable rows remain intact after handler session closes
        """
        self._seed_notifications()

        caller_user = types.SimpleNamespace(id=5)
        handler_session = self._make_session()
        adapter = AsyncSessionAdapter(handler_session)

        observed_committed_state_during_redis_set: list[
            list[tuple[int, int, str, int]]
        ] = []

        fake_redis = AsyncMock()

        async def fake_set(key: str, value: Any) -> bool:
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, user_id, message, is_read FROM notifications ORDER BY id"
                )
                rows = cursor.fetchall()
                observed_committed_state_during_redis_set.append(rows)
            return True

        fake_redis.set.side_effect = fake_set

        result = await delete_notification(
            notification_id=1,
            current_user=caller_user,
            db=adapter,
            redis=fake_redis,
        )

        # Assert handler returns None
        self.assertIsNone(result)

        # Assert exactly the target ID is passed to adapter delete
        self.assertEqual(len(adapter.delete_calls), 1)
        deleted_instance = adapter.delete_calls[0]
        self.assertIsInstance(deleted_instance, Notification)
        self.assertEqual(deleted_instance.id, 1)

        # Assert exactly one commit is made
        self.assertEqual(adapter.commit_count, 1)

        # Assert Redis.set was called and awaited exactly once with user:5:unread_count and 1
        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 1)
        self.assertEqual(fake_redis.set.call_count, 1)
        self.assertEqual(fake_redis.set.await_count, 1)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertEqual(counter_arg, 1)

        # Expected durable remaining rows
        expected_remaining = [
            (2, 5, "Remaining unread notification for user 5", 0),
            (3, 5, "Remaining read notification for user 5", 1),
            (4, 9, "Unread notification for user 9", 0),
        ]

        # Verify observation callback during redis.set await proved durable deletion before Redis write
        self.assertEqual(len(observed_committed_state_during_redis_set), 1)
        observed_rows = observed_committed_state_during_redis_set[0]
        self.assertEqual(observed_rows, expected_remaining)
        self.assertEqual([r[0] for r in observed_rows], [2, 3, 4])
        self.assertEqual([r[1] for r in observed_rows], [5, 5, 9])
        self.assertEqual(
            [r[2] for r in observed_rows],
            [
                "Remaining unread notification for user 5",
                "Remaining read notification for user 5",
                "Unread notification for user 9",
            ],
        )
        self.assertEqual([r[3] for r in observed_rows], [0, 1, 0])
        self.assertNotIn(1, [r[0] for r in observed_rows])

        # Recheck durable rows after handler session closes
        handler_session.close()

        durable_after_close = self._fetch_durable_notifications()
        self.assertEqual(len(durable_after_close), 3)
        self.assertEqual(durable_after_close, expected_remaining)
        self.assertEqual([r[0] for r in durable_after_close], [2, 3, 4])
        self.assertEqual([r[1] for r in durable_after_close], [5, 5, 9])
        self.assertEqual(
            [r[2] for r in durable_after_close],
            [
                "Remaining unread notification for user 5",
                "Remaining read notification for user 5",
                "Unread notification for user 9",
            ],
        )
        self.assertEqual([r[3] for r in durable_after_close], [0, 1, 0])
        self.assertNotIn(1, [r[0] for r in durable_after_close])


if __name__ == "__main__":
    unittest.main()
