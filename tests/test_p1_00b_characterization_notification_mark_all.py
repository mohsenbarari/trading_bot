"""Hermetic characterization tests for mark_all_notifications_read and sync_unread_count.

Seed ID: API-NOTIFY-POST-NOTIFICATIONS-MARK-ALL-NOTIFICATIONS-READ-L318
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

# Install network fence before application imports
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers.notifications import mark_all_notifications_read, sync_unread_count
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class AsyncSessionAdapter:
    """Minimal async execute/commit adapter wrapping a synchronous SQLAlchemy Session."""

    def __init__(self, sync_session: Session, fail_commit: bool = False) -> None:
        self.sync_session = sync_session
        self.commit_count = 0
        self.fail_commit = fail_commit

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self.sync_session.execute(statement, *args, **kwargs)

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("Injected commit failure before underlying Session.commit")
        self.commit_count += 1
        self.sync_session.commit()

    async def rollback(self) -> None:
        self.sync_session.rollback()

    async def close(self) -> None:
        self.sync_session.close()


class NotificationMarkAllCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for mark_all_notifications_read seed handler.

    Seed ID: API-NOTIFY-POST-NOTIFICATIONS-MARK-ALL-NOTIFICATIONS-READ-L318
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="p1_00b_notif_")
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
        """Seed two unread and one read notification for user 5, and one unread for user 9."""
        session = self._make_session()
        session.add_all(
            [
                Notification(
                    id=1,
                    user_id=5,
                    message="User 5 unread notification 1",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=2,
                    user_id=5,
                    message="User 5 unread notification 2",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=3,
                    user_id=5,
                    message="User 5 read notification 3",
                    is_read=True,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
                Notification(
                    id=4,
                    user_id=9,
                    message="User 9 unread notification 4",
                    is_read=False,
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                ),
            ]
        )
        session.commit()
        session.close()

    def _fetch_notification_states(self) -> list[tuple[int, int, int]]:
        """Read durable rows directly from separate SQLite connection."""
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, user_id, is_read FROM notifications ORDER BY id")
            return cursor.fetchall()

    async def test_success_mark_all_reads_and_syncs_counter(self) -> None:
        """Success: user 5 rows marked read, durably committed before Redis set, user 9 unread."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session)
        user5 = types.SimpleNamespace(id=5)

        redis_set_observed_committed_state: list[list[tuple[int, int, int]]] = []

        fake_redis = AsyncMock()

        async def fake_set(key: str, value: Any) -> bool:
            with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, user_id, is_read FROM notifications ORDER BY id"
                )
                rows = cur.fetchall()
                redis_set_observed_committed_state.append(rows)
            return True

        fake_redis.set.side_effect = fake_set

        result = await mark_all_notifications_read(
            current_user=user5,
            db=adapter,
            redis=fake_redis,
        )

        self.assertIsNone(result)
        self.assertEqual(adapter.commit_count, 1)

        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 0)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertTrue(type(counter_arg) is int)

        self.assertEqual(len(redis_set_observed_committed_state), 1)
        callback_rows = redis_set_observed_committed_state[0]
        user5_callback_reads = [
            is_read for _, u_id, is_read in callback_rows if u_id == 5
        ]
        user9_callback_reads = [
            is_read for _, u_id, is_read in callback_rows if u_id == 9
        ]
        self.assertEqual(user5_callback_reads, [1, 1, 1])
        self.assertEqual(user9_callback_reads, [0])

        current_rows = self._fetch_notification_states()
        user5_current = [is_read for _, u_id, is_read in current_rows if u_id == 5]
        user9_current = [is_read for _, u_id, is_read in current_rows if u_id == 9]
        self.assertEqual(user5_current, [1, 1, 1])
        self.assertEqual(user9_current, [0])

    async def test_empty_notifications_causes_no_commit_and_writes_zero(self) -> None:
        """Empty: user with no notifications causes no commit and writes 0 counter."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session)
        user_empty = types.SimpleNamespace(id=99)
        fake_redis = AsyncMock()

        rows_before = self._fetch_notification_states()

        result = await mark_all_notifications_read(
            current_user=user_empty,
            db=adapter,
            redis=fake_redis,
        )

        self.assertIsNone(result)
        self.assertEqual(adapter.commit_count, 0)
        fake_redis.set.assert_awaited_once_with("user:99:unread_count", 0)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertTrue(type(counter_arg) is int)

        rows_after = self._fetch_notification_states()
        self.assertEqual(rows_before, rows_after)

    async def test_repeat_invocation_in_fresh_session_causes_no_commit_and_writes_zero(
        self,
    ) -> None:
        """Retry: repeat invocation in fresh session causes no commit and writes 0 counter."""
        self._seed_notifications()

        session1 = self._make_session()
        adapter1 = AsyncSessionAdapter(session1)
        fake_redis1 = AsyncMock()
        user5 = types.SimpleNamespace(id=5)

        await mark_all_notifications_read(
            current_user=user5,
            db=adapter1,
            redis=fake_redis1,
        )
        self.assertEqual(adapter1.commit_count, 1)
        fake_redis1.set.assert_awaited_once_with("user:5:unread_count", 0)
        counter_arg1 = fake_redis1.set.await_args.args[1]
        self.assertIs(type(counter_arg1), int)
        self.assertTrue(type(counter_arg1) is int)

        durable_after_first = self._fetch_notification_states()

        session2 = self._make_session()
        adapter2 = AsyncSessionAdapter(session2)
        fake_redis2 = AsyncMock()

        result2 = await mark_all_notifications_read(
            current_user=user5,
            db=adapter2,
            redis=fake_redis2,
        )

        self.assertIsNone(result2)
        self.assertEqual(adapter2.commit_count, 0)
        fake_redis2.set.assert_awaited_once_with("user:5:unread_count", 0)
        counter_arg2 = fake_redis2.set.await_args.args[1]
        self.assertIs(type(counter_arg2), int)
        self.assertTrue(type(counter_arg2) is int)

        durable_after_second = self._fetch_notification_states()
        self.assertEqual(durable_after_first, durable_after_second)

    async def test_failure_before_commit_propagates_and_leaves_rows_unread(self) -> None:
        """Failure before commit: injected commit failure propagates, redis.set not awaited, unread rows persist."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session, fail_commit=True)
        user5 = types.SimpleNamespace(id=5)
        fake_redis = AsyncMock()

        with self.assertRaises(RuntimeError) as ctx:
            await mark_all_notifications_read(
                current_user=user5,
                db=adapter,
                redis=fake_redis,
            )

        self.assertIn("Injected commit failure", str(ctx.exception))
        fake_redis.set.assert_not_awaited()

        session.rollback()
        session.close()

        rows = self._fetch_notification_states()
        user5_rows = [(row_id, is_read) for row_id, u_id, is_read in rows if u_id == 5]
        user9_rows = [(row_id, is_read) for row_id, u_id, is_read in rows if u_id == 9]
        self.assertEqual(user5_rows, [(1, 0), (2, 0), (3, 1)])
        self.assertEqual(user9_rows, [(4, 0)])

    async def test_failure_after_commit_leaves_rows_committed_and_retry_commits_zero(
        self,
    ) -> None:
        """Failure after commit: redis.set failure propagates, rows remain committed, retry commits zero."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session)
        user5 = types.SimpleNamespace(id=5)

        failing_redis = AsyncMock()
        failing_redis.set.side_effect = RuntimeError("Simulated Redis failure")

        with self.assertRaises(RuntimeError) as ctx:
            await mark_all_notifications_read(
                current_user=user5,
                db=adapter,
                redis=failing_redis,
            )

        self.assertIn("Simulated Redis failure", str(ctx.exception))
        self.assertEqual(adapter.commit_count, 1)

        failing_redis.set.assert_awaited_once_with("user:5:unread_count", 0)
        counter_arg_failing = failing_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg_failing), int)
        self.assertTrue(type(counter_arg_failing) is int)

        session.close()

        rows = self._fetch_notification_states()
        user5_rows = [(row_id, is_read) for row_id, u_id, is_read in rows if u_id == 5]
        user9_rows = [(row_id, is_read) for row_id, u_id, is_read in rows if u_id == 9]
        self.assertEqual(user5_rows, [(1, 1), (2, 1), (3, 1)])
        self.assertEqual(user9_rows, [(4, 0)])

        retry_session = self._make_session()
        retry_adapter = AsyncSessionAdapter(retry_session)
        healthy_redis = AsyncMock()

        retry_result = await mark_all_notifications_read(
            current_user=user5,
            db=retry_adapter,
            redis=healthy_redis,
        )

        self.assertIsNone(retry_result)
        self.assertEqual(retry_adapter.commit_count, 0)
        healthy_redis.set.assert_awaited_once_with("user:5:unread_count", 0)
        counter_arg_healthy = healthy_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg_healthy), int)
        self.assertTrue(type(counter_arg_healthy) is int)

        retry_session.close()

    async def test_actual_sync_unread_count_direct(self) -> None:
        """Direct characterization of actual sync_unread_count helper."""
        self._seed_notifications()

        session = self._make_session()
        adapter = AsyncSessionAdapter(session)
        fake_redis = AsyncMock()

        count5 = await sync_unread_count(adapter, fake_redis, 5)
        self.assertEqual(count5, 2)
        self.assertIs(type(count5), int)
        self.assertTrue(type(count5) is int)
        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 2)
        counter_arg5 = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg5), int)
        self.assertTrue(type(counter_arg5) is int)

        fake_redis_empty = AsyncMock()
        count_empty = await sync_unread_count(adapter, fake_redis_empty, 99)
        self.assertEqual(count_empty, 0)
        self.assertIs(type(count_empty), int)
        self.assertTrue(type(count_empty) is int)
        fake_redis_empty.set.assert_awaited_once_with("user:99:unread_count", 0)
        counter_arg_empty = fake_redis_empty.set.await_args.args[1]
        self.assertIs(type(counter_arg_empty), int)
        self.assertTrue(type(counter_arg_empty) is int)


if __name__ == "__main__":
    unittest.main()
