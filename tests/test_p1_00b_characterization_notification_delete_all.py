"""Hermetic characterization test for delete_all_notifications and sync_unread_count.

Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed ID: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-ALL-NOTIFICATIONS-L343

This module directly characterizes the real api.routers.notifications.delete_all_notifications
handler and its real sync_unread_count dependency against durable isolated file-backed SQLite.
Included scenarios: populated-owner deletion and empty-owner no-op.
Evidenced exclusions: HTTP routing/dependency injection, live Redis behavior,
production or schema parity, failures, retry, concurrency, other notification seeds,
origin-matrix cells, full seed acceptance, and P1-00b/Stage closure.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
import json
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

from api.routers.notifications import delete_all_notifications, sync_unread_count
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


class NotificationDeleteAllCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for delete_all_notifications and sync_unread_count.

    Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Seed ID: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-ALL-NOTIFICATIONS-L343

    Directly characterizes the handler and sync_unread_count dependency.
    Exclusions: HTTP routing/dependency injection, live Redis behavior, production
    or schema parity, failures, retry, concurrency, other notification seeds,
    origin-matrix cells, full seed acceptance, and P1-00b/Stage closure.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="p1_00b_notif_del_all_")
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
                CREATE TABLE notifications (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    user_id INTEGER NOT NULL,\n                    message TEXT NOT NULL,\n                    is_read BOOLEAN NOT NULL DEFAULT 0,\n                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    level VARCHAR(32) NOT NULL DEFAULT 'INFO',\n                    category VARCHAR(32) NOT NULL DEFAULT 'SYSTEM',\n                    dedupe_key VARCHAR(180),\n                    extra_payload TEXT\n                );\n                """
            )
            conn.execute(
                "CREATE INDEX ix_notifications_user_unread ON notifications (user_id, is_read);"
            )
            conn.commit()

    def _make_session(self) -> Session:
        session = Session(self.engine)
        self.addCleanup(session.close)
        return session

    def _seed_populated_notifications(self) -> None:
        """Seed unread and read notifications for user 5, plus an unread notification for user 9."""
        session = self._make_session()
        session.add_all(
            [
                Notification(
                    id=1,
                    user_id=5,
                    message="User 5 unread notification 1",
                    is_read=False,
                    created_at=datetime(2026, 9, 1, 10, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="dedupe-5-1",
                    extra_payload={"type": "alert_1"},
                ),
                Notification(
                    id=2,
                    user_id=5,
                    message="User 5 read notification 2",
                    is_read=True,
                    created_at=datetime(2026, 9, 1, 10, 5, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="dedupe-5-2",
                    extra_payload={"type": "alert_2"},
                ),
                Notification(
                    id=3,
                    user_id=9,
                    message="User 9 unread notification 3",
                    is_read=False,
                    created_at=datetime(2026, 9, 1, 10, 10, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="dedupe-9-3",
                    extra_payload={"type": "alert_3"},
                ),
            ]
        )
        session.commit()
        session.close()

    def _fetch_durable_notifications(
        self,
    ) -> list[tuple[int, int, str, int, str, str, str, str | None, Any]]:
        """Read durable rows directly from separate SQLite connection."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, message, is_read, created_at, level, category, dedupe_key, extra_payload
                FROM notifications
                ORDER BY id
                """
            )
            rows = cursor.fetchall()
            result = []
            for r in rows:
                raw_payload = r[8]
                payload: Any
                if isinstance(raw_payload, str):
                    try:
                        payload = json.loads(raw_payload)
                    except Exception:
                        payload = raw_payload
                else:
                    payload = raw_payload
                result.append(
                    (
                        r[0],
                        r[1],
                        r[2],
                        r[3],
                        str(r[4]),
                        r[5],
                        r[6],
                        r[7],
                        payload,
                    )
                )
            return result

    async def test_delete_all_notifications_populated_owner_success(self) -> None:
        """Populated-owner deletion scenario for delete_all_notifications.

        Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
        Seed ID: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-ALL-NOTIFICATIONS-L343

        Directly characterizes populated-owner deletion with real handler and
        real sync_unread_count against isolated file-backed SQLite.
        Exclusions: HTTP routing/dependency injection, live Redis behavior,
        production or schema parity, failures, retry, concurrency, other
        notification seeds, origin-matrix cells, full seed acceptance, and
        P1-00b/Stage closure.
        """
        self._seed_populated_notifications()

        user5 = types.SimpleNamespace(id=5)
        handler_session = self._make_session()
        adapter = AsyncSessionAdapter(handler_session)

        observed_committed_state_during_redis_set: list[
            list[tuple[int, int, str, int, str, str, str, str | None, Any]]
        ] = []

        fake_redis = AsyncMock()

        async def fake_set(key: str, value: Any) -> bool:
            observed_committed_state_during_redis_set.append(
                self._fetch_durable_notifications()
            )
            return True

        fake_redis.set.side_effect = fake_set

        result = await delete_all_notifications(
            current_user=user5,
            db=adapter,
            redis=fake_redis,
        )

        # 1. Handler returns None
        self.assertIsNone(result)

        # 2. Exactly the two owner objects/IDs and no other-user object are passed to delete
        self.assertEqual(len(adapter.delete_calls), 2)
        deleted_ids = [d.id for d in adapter.delete_calls]
        self.assertEqual(sorted(deleted_ids), [1, 2])
        for deleted_instance in adapter.delete_calls:
            self.assertIsInstance(deleted_instance, Notification)
            self.assertEqual(deleted_instance.user_id, 5)
            self.assertNotEqual(deleted_instance.user_id, 9)

        # 3. Exactly one commit occurs
        self.assertEqual(adapter.commit_count, 1)

        # 4. Prove deletion was already committed before the unread-count write
        expected_other_user_row = (
            3,
            9,
            "User 9 unread notification 3",
            0,
            "2026-09-01 10:10:00.000000",
            "INFO",
            "SYSTEM",
            "dedupe-9-3",
            {"type": "alert_3"},
        )
        self.assertEqual(len(observed_committed_state_during_redis_set), 1)
        observed_during_set = observed_committed_state_during_redis_set[0]
        self.assertEqual(observed_during_set, [expected_other_user_row])
        self.assertNotIn(1, [r[0] for r in observed_during_set])
        self.assertNotIn(2, [r[0] for r in observed_during_set])
        self.assertTrue(all(r[1] != 5 for r in observed_during_set))

        # 5. Fake Redis set is awaited exactly once with key user:5:unread_count and integer 0
        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 0)
        self.assertEqual(fake_redis.set.call_count, 1)
        self.assertEqual(fake_redis.set.await_count, 1)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertEqual(counter_arg, 0)

        # 6. All owner rows are absent in fresh durable read while other-user row is unchanged
        handler_session.close()

        durable_after = self._fetch_durable_notifications()
        self.assertEqual(len(durable_after), 1)
        self.assertEqual(durable_after, [expected_other_user_row])
        self.assertNotIn(1, [r[0] for r in durable_after])
        self.assertNotIn(2, [r[0] for r in durable_after])
        self.assertTrue(all(r[1] != 5 for r in durable_after))

    async def test_delete_all_notifications_empty_owner_noop(self) -> None:
        """Empty-owner no-op scenario for delete_all_notifications.

        Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
        Seed ID: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-ALL-NOTIFICATIONS-L343

        Directly characterizes empty-owner no-op with real handler and
        real sync_unread_count against isolated file-backed SQLite.
        Exclusions: HTTP routing/dependency injection, live Redis behavior,
        production or schema parity, failures, retry, concurrency, other
        notification seeds, origin-matrix cells, full seed acceptance, and
        P1-00b/Stage closure.
        """
        # Seed rows belonging only to another user (user 9)
        session = self._make_session()
        session.add(
            Notification(
                id=3,
                user_id=9,
                message="User 9 unread notification 3",
                is_read=False,
                created_at=datetime(2026, 9, 1, 10, 10, 0),
                level=NotificationLevel.INFO,
                category=NotificationCategory.SYSTEM,
                dedupe_key="dedupe-9-3",
                extra_payload={"type": "alert_3"},
            )
        )
        session.commit()
        session.close()

        expected_other_user_row = (
            3,
            9,
            "User 9 unread notification 3",
            0,
            "2026-09-01 10:10:00.000000",
            "INFO",
            "SYSTEM",
            "dedupe-9-3",
            {"type": "alert_3"},
        )
        durable_before = self._fetch_durable_notifications()
        self.assertEqual(durable_before, [expected_other_user_row])

        user_empty = types.SimpleNamespace(id=99)
        handler_session = self._make_session()
        adapter = AsyncSessionAdapter(handler_session)
        fake_redis = AsyncMock()

        result = await delete_all_notifications(
            current_user=user_empty,
            db=adapter,
            redis=fake_redis,
        )

        # 1. Handler returns None
        self.assertIsNone(result)

        # 2. One selection execute in delete_all_notifications, plus count execute in sync_unread_count
        self.assertEqual(len(adapter.execute_calls), 2)
        # Verify first execute is selection for current_user's notifications
        self.assertIn("notifications", str(adapter.execute_calls[0]).lower())

        # 3. Zero delete calls
        self.assertEqual(len(adapter.delete_calls), 0)

        # 4. Zero commits
        self.assertEqual(adapter.commit_count, 0)

        # 5. Other user's durable row is byte/value-equivalent in fresh read
        durable_after = self._fetch_durable_notifications()
        self.assertEqual(durable_after, durable_before)
        self.assertEqual(durable_after, [expected_other_user_row])

        # 6. Exactly one awaited fake Redis set for user:99:unread_count with integer 0
        fake_redis.set.assert_awaited_once_with("user:99:unread_count", 0)
        self.assertEqual(fake_redis.set.call_count, 1)
        self.assertEqual(fake_redis.set.await_count, 1)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertEqual(counter_arg, 0)


if __name__ == "__main__":
    unittest.main()
