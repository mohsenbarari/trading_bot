"""Hermetic characterization test for notifications.mark_notification_read owner HTTP 204 path.

Stage: P1-00b
Assignment: WA-P1-00B-NORMAL-RETURN-20261004-277
Scenario: P1-00B.NOTIFICATIONS.MARK-READ-OWNER-HTTP204
Seed ID: API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296

Scope and Bounded Single-Unit Reason:
- Characterizes live HTTP PATCH /api/notifications/{notification_id}/read routing and handler
  behavior when an owner caller (id=5) marks an unread notification (id=1) as read.
- Bounded single unit because custom Session adapter, isolated SQLite table lifecycle, and durable
  persisted outcome require dedicated execution; batching is forbidden.
- Real SQLite-backed Session execution via async adapter delegating execute to sync Session.
- Live mark_notification_read and sync_unread_count execution without fake result queues or fallbacks.
- FastAPI dependency overrides replace get_current_user, get_db, and get_redis.
- Proves durable SQL commit occurs before Redis write by inspecting a separate raw SQLite connection
  inside the fake Redis.set await callback.

Exclusions:
- Excludes already accepted cross-user HTTP204, missing-ID mark-read, all delete404/deferred scenarios,
  authentication internals, token parsing/verification, user account status/locking, concurrency,
  retry, reload, production parity, external action, origin/seed/Stage closure, and ledger edits.
- Preserves deferrals normal273-notification-mark-read-missing-id-http-20261004-c002,
  normal273-notification-delete-missing-id-http-20261004-c001, normal271-notification-delete-cross-user-http-20261004-c002,
  normal265-canonical-five-closeout-20261004-c001, and all older normal169 deferrals.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import contextlib
import sqlite3
import types
import unittest
from typing import Any
from unittest.mock import AsyncMock, call

from fastapi import FastAPI
import httpx
from sqlalchemy.orm import Session

import tests.test_p1_00b_characterization_notification_delete_success as fixture_module
from api.deps import get_current_user
from api.routers.notifications import router as notifications_router
from core.db import get_db
from core.redis import get_redis


class HttpAsyncSessionAdapter(fixture_module.AsyncSessionAdapter):
    """AsyncSession adapter tracking execute, commit, delete, flush, add, refresh, and rollback operations."""

    def __init__(self, sync_session: Session) -> None:
        super().__init__(sync_session)
        self.flush_count: int = 0
        self.add_calls: list[Any] = []
        self.refresh_calls: list[Any] = []
        self.rollback_count: int = 0

    async def flush(self, *args: Any, **kwargs: Any) -> None:
        self.flush_count += 1
        self.sync_session.flush(*args, **kwargs)

    def add(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.add_calls.append(instance)
        self.sync_session.add(instance, *args, **kwargs)

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append(instance)
        self.sync_session.refresh(instance, *args, **kwargs)

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.sync_session.rollback()


class NotificationMarkReadOwnerHttpCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for notifications.mark_notification_read owner HTTP 204 path."""

    def setUp(self) -> None:
        super().setUp()
        self.fence = install_p1_00_network_fence()
        self.denied_before = len(self.fence["denied_connects"])
        self.constructs_before = len(self.fence["live_redis_constructs"])

        self.fixture = fixture_module.NotificationDeleteSuccessCharacterizationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _fetch_nine_column_snapshot(self) -> list[tuple[Any, ...]]:
        """Read all 9 persisted columns directly from raw SQLite ordered by id."""
        with contextlib.closing(sqlite3.connect(self.fixture.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, message, is_read, created_at, level, category, dedupe_key, extra_payload
                FROM notifications
                ORDER BY id
                """
            )
            return cursor.fetchall()

    def _fetch_owner_unread_count(self, user_id: int = 5) -> int:
        """Read raw SQLite unread count for user_id."""
        with contextlib.closing(sqlite3.connect(self.fixture.db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(id) FROM notifications WHERE user_id = ? AND is_read = 0",
                (user_id,),
            )
            return int(cursor.fetchone()[0])

    async def test_owner_unread_mark_read_returns_http_204_and_persists_before_redis(self) -> None:
        """Seed API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296 owner success path.

        Scenario: P1-00B.NOTIFICATIONS.MARK-READ-OWNER-HTTP204
        Caller id=5 issues PATCH /api/notifications/1/read on unread notification id=1.
        """
        self.fixture._seed_notifications()

        # Independently prove rows 1..4 existence, owners, and read states before execution
        snapshot_before = self._fetch_nine_column_snapshot()
        self.assertEqual(len(snapshot_before), 4)

        # Row 1: id=1, user_id=5, unread (target owner row)
        self.assertEqual(snapshot_before[0][0], 1)
        self.assertEqual(snapshot_before[0][1], 5)
        self.assertEqual(snapshot_before[0][3], 0)

        # Row 2: id=2, user_id=5, unread
        self.assertEqual(snapshot_before[1][0], 2)
        self.assertEqual(snapshot_before[1][1], 5)
        self.assertEqual(snapshot_before[1][3], 0)

        # Row 3: id=3, user_id=5, read
        self.assertEqual(snapshot_before[2][0], 3)
        self.assertEqual(snapshot_before[2][1], 5)
        self.assertEqual(snapshot_before[2][3], 1)

        # Row 4: id=4, user_id=9, unread
        self.assertEqual(snapshot_before[3][0], 4)
        self.assertEqual(snapshot_before[3][1], 9)
        self.assertEqual(snapshot_before[3][3], 0)

        # Assert caller initial unread count is 2
        self.assertEqual(self._fetch_owner_unread_count(5), 2)

        # Expected final snapshot: row 1 is_read becomes 1; all other 8 columns and rows 2..4 invariant
        expected_snapshot = [
            (
                snapshot_before[0][0],
                snapshot_before[0][1],
                snapshot_before[0][2],
                1,
                snapshot_before[0][4],
                snapshot_before[0][5],
                snapshot_before[0][6],
                snapshot_before[0][7],
                snapshot_before[0][8],
            ),
            snapshot_before[1],
            snapshot_before[2],
            snapshot_before[3],
        ]

        caller_user = types.SimpleNamespace(id=5)
        handler_session = self.fixture._make_session()
        self.addCleanup(handler_session.close)
        adapter = HttpAsyncSessionAdapter(handler_session)

        observed_committed_state_during_redis_set: list[list[tuple[Any, ...]]] = []
        observed_unread_count_during_redis_set: list[int] = []
        observed_commit_count_during_redis_set: list[int] = []

        fake_redis = AsyncMock()

        async def fake_set(key: str, value: Any) -> bool:
            with contextlib.closing(sqlite3.connect(self.fixture.db_path)) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, user_id, message, is_read, created_at, level, category, dedupe_key, extra_payload
                    FROM notifications
                    ORDER BY id
                    """
                )
                observed_rows = cursor.fetchall()
                observed_committed_state_during_redis_set.append(observed_rows)
                cursor.execute(
                    "SELECT count(id) FROM notifications WHERE user_id = ? AND is_read = 0",
                    (5,),
                )
                observed_count = int(cursor.fetchone()[0])
                observed_unread_count_during_redis_set.append(observed_count)
            observed_commit_count_during_redis_set.append(adapter.commit_count)
            return True

        fake_redis.set.side_effect = fake_set

        user_override_count = 0
        db_override_count = 0
        redis_override_count = 0

        def override_get_current_user() -> Any:
            nonlocal user_override_count
            user_override_count += 1
            return caller_user

        async def override_get_db() -> Any:
            nonlocal db_override_count
            db_override_count += 1
            yield adapter

        async def override_get_redis() -> Any:
            nonlocal redis_override_count
            redis_override_count += 1
            yield fake_redis

        app = FastAPI()
        app.include_router(notifications_router, prefix="/api/notifications")
        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_redis] = override_get_redis

        try:
            if hasattr(httpx, "ASGITransport"):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    response = await client.patch("/api/notifications/1/read")
            else:
                async with httpx.AsyncClient(
                    app=app, base_url="http://testserver"
                ) as client:
                    response = await client.patch("/api/notifications/1/read")
        finally:
            app.dependency_overrides.clear()

        # Assert each dependency override entered exactly once
        self.assertEqual(user_override_count, 1)
        self.assertEqual(db_override_count, 1)
        self.assertEqual(redis_override_count, 1)

        # Assert status 204 and empty body
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

        # Assert exactly two ordered adapter execute calls and one commit
        self.assertEqual(len(adapter.execute_calls), 2)
        self.assertEqual(adapter.commit_count, 1)

        # First execute: SELECT Notification WHERE id == 1 AND user_id == 5
        captured_stmt_1 = adapter.execute_calls[0]
        compiled_1 = captured_stmt_1.compile()
        normalized_actual_sql_1 = " ".join(str(compiled_1).split())
        expected_sql_1 = (
            "SELECT notifications.id, notifications.user_id, notifications.message, "
            "notifications.is_read, notifications.created_at, notifications.level, "
            "notifications.category, notifications.dedupe_key, notifications.extra_payload "
            "FROM notifications "
            "WHERE notifications.id = :id_1 AND notifications.user_id = :user_id_1"
        )
        self.assertEqual(normalized_actual_sql_1, expected_sql_1)
        self.assertEqual(compiled_1.params, {"id_1": 1, "user_id_1": 5})

        # Second execute: SELECT count(Notification.id) WHERE user_id == 5 AND is_read == False
        captured_stmt_2 = adapter.execute_calls[1]
        compiled_2 = captured_stmt_2.compile()
        normalized_actual_sql_2 = " ".join(str(compiled_2).split())
        expected_sql_2 = (
            "SELECT count(notifications.id) AS count_1 "
            "FROM notifications "
            "WHERE notifications.user_id = :user_id_1 AND notifications.is_read = false"
        )
        self.assertEqual(normalized_actual_sql_2, expected_sql_2)
        self.assertEqual(compiled_2.params, {"user_id_1": 5})

        # Assert zero explicit delete/add/flush/refresh/rollback operations
        self.assertEqual(len(adapter.delete_calls), 0)
        self.assertEqual(len(adapter.add_calls), 0)
        self.assertEqual(adapter.flush_count, 0)
        self.assertEqual(len(adapter.refresh_calls), 0)
        self.assertEqual(adapter.rollback_count, 0)

        # Assert no pending new/dirty/deleted session objects at completion
        self.assertEqual(len(handler_session.new), 0)
        self.assertEqual(len(handler_session.dirty), 0)
        self.assertEqual(len(handler_session.deleted), 0)

        # Prove durable transition BEFORE Redis write via callback observations
        self.assertEqual(len(observed_committed_state_during_redis_set), 1)
        self.assertEqual(observed_committed_state_during_redis_set[0], expected_snapshot)
        self.assertEqual(observed_unread_count_during_redis_set[0], 1)
        self.assertEqual(observed_commit_count_during_redis_set[0], 1)

        # Assert Redis.set was called and awaited exactly once with exact key/integer and no other interactions
        fake_redis.set.assert_awaited_once_with("user:5:unread_count", 1)
        self.assertEqual(fake_redis.set.call_count, 1)
        self.assertEqual(fake_redis.set.await_count, 1)
        counter_arg = fake_redis.set.await_args.args[1]
        self.assertIs(type(counter_arg), int)
        self.assertEqual(counter_arg, 1)
        self.assertEqual(fake_redis.mock_calls, [call.set("user:5:unread_count", 1)])

        # Snapshot check after HTTP request (before handler Session closure)
        snapshot_after_http = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_http, expected_snapshot)

        # Explicit handler Session closure
        handler_session.close()

        # Snapshot check after handler Session closure
        snapshot_after_close = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_close, expected_snapshot)

        # Validate column-level row integrity and preservation across all rows
        for row in snapshot_after_close:
            self.assertIsNotNone(row[0])  # id
            self.assertIn(row[1], (5, 9))  # user_id
            self.assertIsNotNone(row[2])  # message
            self.assertIn(row[3], (0, 1))  # is_read
            self.assertIsNotNone(row[4])  # created_at
            self.assertIn(row[5], ("INFO", "SUCCESS", "WARNING", "ERROR"))  # level
            self.assertIn(row[6], ("SYSTEM", "USER", "TRADE"))  # category
            self.assertIsNone(row[7])  # dedupe_key (null preserved)
            self.assertIsNone(row[8])  # extra_payload (null preserved)

        # Row 1 is_read is 1, other 8 columns invariant
        self.assertEqual(snapshot_after_close[0][3], 1)
        self.assertEqual(snapshot_after_close[0][0], snapshot_before[0][0])
        self.assertEqual(snapshot_after_close[0][1], snapshot_before[0][1])
        self.assertEqual(snapshot_after_close[0][2], snapshot_before[0][2])
        self.assertEqual(snapshot_after_close[0][4], snapshot_before[0][4])
        self.assertEqual(snapshot_after_close[0][5], snapshot_before[0][5])
        self.assertEqual(snapshot_after_close[0][6], snapshot_before[0][6])
        self.assertEqual(snapshot_after_close[0][7], snapshot_before[0][7])
        self.assertEqual(snapshot_after_close[0][8], snapshot_before[0][8])

        # Rows 2..4 remain completely invariant
        self.assertEqual(snapshot_after_close[1:], snapshot_before[1:])

        # Final raw unread count check
        self.assertEqual(self._fetch_owner_unread_count(5), 1)

        # Network fence invariants
        self.assertEqual(len(self.fence["denied_connects"]), self.denied_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.constructs_before)


if __name__ == "__main__":
    unittest.main()
