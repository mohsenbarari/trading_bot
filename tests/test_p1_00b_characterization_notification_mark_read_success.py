"""Hermetic characterization test for mark_notification_read owner-unread success outcome.

Seed ID: API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Bounded Scope and Exclusions:
Characterizes only the direct-handler owner-unread success outcome where the caller-owned
unread notification is marked read, committed to the database, and remaining unread count
is synced to Redis.
This characterization unit does not claim or resolve:
- HTTP routing, method parsing, or middleware behavior
- Missing notification or non-owner isolation behavior
- Process reload or daemon crash recovery
- Concurrency or race conditions
- Complete seed coverage (the other 329 canonical seed assertions remain unresolved)
- Origin matrix acceptance (the 24 unresolved origin cells remain unresolved)
- DR-018
- Stage P1-00b completion
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

# Configure unit-test environment and network fence before application imports
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.notifications import mark_notification_read, sync_unread_count
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class AsyncSessionAdapter:
    """Minimal async execute/commit adapter wrapping a synchronous SQLAlchemy Session."""

    def __init__(self, sync_session: Session) -> None:
        self.sync_session = sync_session
        self.commit_count = 0

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self.sync_session.execute(statement, *args, **kwargs)

    async def commit(self) -> None:
        self.commit_count += 1
        self.sync_session.commit()

    async def rollback(self) -> None:
        self.sync_session.rollback()

    async def close(self) -> None:
        self.sync_session.close()


class NotificationMarkReadSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for mark_notification_read owner-unread success outcome.

    Seed ID: API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296
    Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="p1_00b_notif_read_")
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
                CREATE TABLE notifications (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    user_id INTEGER NOT NULL,\n                    message TEXT NOT NULL,\n                    is_read BOOLEAN NOT NULL DEFAULT 0,\n                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,\n                    level VARCHAR(32) NOT NULL DEFAULT 'INFO',\n                    category VARCHAR(32) NOT NULL DEFAULT 'SYSTEM',\n                    dedupe_key VARCHAR(180),\n                    extra_payload TEXT\n                );
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
        """Seed target unread (id=1) and another unread for user 5, plus one unread for user 9."""
        session = self._make_session()
        session.add_all(
            [
                Notification(
                    id=1,
                    user_id=5,
                    message="User 5 unread notification 1 (target)",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=2,
                    user_id=5,
                    message="User 5 unread notification 2 (other)",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=3,
                    user_id=9,
                    message="User 9 unread notification 3",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
            ]
        )
        session.commit()
        session.close()

    def _fetch_notification_states(self) -> list[tuple[int, int, int]]:
        """Read durable rows directly from a separate SQLite connection."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, user_id, is_read FROM notifications ORDER BY id")
            return cursor.fetchall()

    async def test_owner_unread_success_marks_read_and_syncs_remaining_count(self) -> None:
        """Verify owner-unread success: commit read state, observe transition during Redis set, write count 1."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session)
        user5 = types.SimpleNamespace(id=5)

        observed_states_during_redis_set: list[dict[int, int]] = []
        fake_redis = AsyncMock()

        async def fake_set(key: str, value: Any) -> bool:
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, is_read FROM notifications ORDER BY id")
                observed_states_during_redis_set.append(dict(cur.fetchall()))
            return True

        fake_redis.set.side_effect = fake_set

        result = await mark_notification_read(
            notification_id=1,
            current_user=user5,
            db=adapter,
            redis=fake_redis,
        )

        # 1. Return is None
        self.assertIsNone(result)

        # 2. Exactly one database commit
        self.assertEqual(adapter.commit_count, 1)

        # 3. During the awaited Redis set, target transition is already visible via separate connection
        self.assertEqual(len(observed_states_during_redis_set), 1)
        during_set = observed_states_during_redis_set[0]
        self.assertEqual(during_set[1], 1)
        self.assertEqual(during_set[2], 0)
        self.assertEqual(during_set[3], 0)

        # 4. Durable final state marks only id=1 read
        durable_rows = self._fetch_notification_states()
        durable_dict = {row_id: (u_id, is_read) for row_id, u_id, is_read in durable_rows}
        self.assertEqual(durable_dict[1], (5, 1))
        self.assertEqual(durable_dict[2], (5, 0))
        self.assertEqual(durable_dict[3], (9, 0))

        # 5. Redis is awaited exactly once with key user:5:unread_count and integer value 1
        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 1)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertEqual(counter_arg, 1)


if __name__ == "__main__":
    unittest.main()
