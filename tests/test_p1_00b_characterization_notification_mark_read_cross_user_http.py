"""Hermetic characterization test for notifications.mark_notification_read cross-user HTTP 204 path.

Stage: P1-00b
Assignment: WA-P1-00B-NORMAL-RETURN-20261004-273
Scenario: P1-00B.NOTIFICATIONS.MARK-READ-CROSS-USER-HTTP204
Seed ID: API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296

Scope and Exclusions:
- Characterizes live HTTP PATCH /api/notifications/{notification_id}/read routing and handler
  behavior when a caller (id=5) attempts to mark as read a notification (id=4) owned by user 9.
- Real SQLite-backed Session execution via async adapter delegating execute to sync Session.
- FastAPI dependency overrides replace get_current_user, get_db, and get_redis.
- Excluded: HTTP authentication internals, token parsing/verification, user account status/locking,
  real Redis networking/connection pools, background push notifications, owner-success mutation paths,
  missing-ID 404 paths, deletion outcomes, concurrency, process reload, and Stage P1-00b closure.
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
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
from sqlalchemy.orm import Session

import tests.test_p1_00b_characterization_notification_delete_success as fixture_module
from api.deps import get_current_user
from api.routers.notifications import router as notifications_router
from core.db import get_db
from core.redis import get_redis


class HttpAsyncSessionAdapter(fixture_module.AsyncSessionAdapter):
    """AsyncSession adapter tracking execute, commit, delete, flush, add, and refresh operations."""

    def __init__(self, sync_session: Session) -> None:
        super().__init__(sync_session)
        self.flush_count: int = 0
        self.add_calls: list[Any] = []
        self.refresh_calls: list[Any] = []

    async def flush(self, *args: Any, **kwargs: Any) -> None:
        self.flush_count += 1
        self.sync_session.flush(*args, **kwargs)

    def add(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.add_calls.append(instance)
        self.sync_session.add(instance, *args, **kwargs)

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls.append(instance)
        self.sync_session.refresh(instance, *args, **kwargs)


class NotificationMarkReadCrossUserHttpCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for notifications.mark_notification_read cross-user HTTP 204 path."""

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

    async def test_cross_user_mark_read_returns_http_204_without_mutation(self) -> None:
        """Seed API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296 cross-user path.

        Scenario: P1-00B.NOTIFICATIONS.MARK-READ-CROSS-USER-HTTP204
        Caller id=5 attempts PATCH /api/notifications/4/read, target notification id=4 belongs to user 9.
        """
        self.fixture._seed_notifications()

        # Independently prove rows 1..4 existence, owners, and read states before execution
        snapshot_before = self._fetch_nine_column_snapshot()
        self.assertEqual(len(snapshot_before), 4)

        # Row 1: id=1, user_id=5, unread
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

        # Row 4: id=4, user_id=9, unread (target cross-user row)
        self.assertEqual(snapshot_before[3][0], 4)
        self.assertEqual(snapshot_before[3][1], 9)
        self.assertEqual(snapshot_before[3][3], 0)

        caller_user = types.SimpleNamespace(id=5)
        handler_session = self.fixture._make_session()
        self.addCleanup(handler_session.close)
        adapter = HttpAsyncSessionAdapter(handler_session)
        fake_redis = AsyncMock()

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
                    response = await client.patch("/api/notifications/4/read")
            else:
                async with httpx.AsyncClient(
                    app=app, base_url="http://testserver"
                ) as client:
                    response = await client.patch("/api/notifications/4/read")
        finally:
            app.dependency_overrides.clear()

        # Assert each dependency override entered exactly once
        self.assertEqual(user_override_count, 1)
        self.assertEqual(db_override_count, 1)
        self.assertEqual(redis_override_count, 1)

        # Assert status 204 and empty body
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

        # Assert exactly one SQL execute occurred, with independently written compiled SELECT
        self.assertEqual(len(adapter.execute_calls), 1)
        captured_stmt = adapter.execute_calls[0]
        compiled = captured_stmt.compile()
        normalized_actual_sql = " ".join(str(compiled).split())
        expected_sql = (
            "SELECT notifications.id, notifications.user_id, notifications.message, "
            "notifications.is_read, notifications.created_at, notifications.level, "
            "notifications.category, notifications.dedupe_key, notifications.extra_payload "
            "FROM notifications "
            "WHERE notifications.id = :id_1 AND notifications.user_id = :user_id_1"
        )
        self.assertEqual(normalized_actual_sql, expected_sql)
        self.assertEqual(compiled.params, {"id_1": 4, "user_id_1": 5})

        # Assert zero mutations on adapter
        self.assertEqual(adapter.commit_count, 0)
        self.assertEqual(len(adapter.delete_calls), 0)
        self.assertEqual(adapter.flush_count, 0)
        self.assertEqual(len(adapter.add_calls), 0)
        self.assertEqual(len(adapter.refresh_calls), 0)

        # Assert empty pending session states
        self.assertEqual(len(handler_session.new), 0)
        self.assertEqual(len(handler_session.dirty), 0)
        self.assertEqual(len(handler_session.deleted), 0)

        # Assert zero Redis interactions (sync_unread_count branch was not entered)
        self.assertEqual(fake_redis.mock_calls, [])
        fake_redis.set.assert_not_called()
        fake_redis.set.assert_not_awaited()

        # Snapshot check after HTTP request (before handler Session closure)
        snapshot_after_http = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_http, snapshot_before)

        # Explicit handler Session closure
        handler_session.close()

        # Snapshot check after handler Session closure
        snapshot_after_close = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_close, snapshot_before)

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

        # Row 4 remains intact and unread for owner 9
        self.assertEqual(snapshot_after_close[3][0], 4)
        self.assertEqual(snapshot_after_close[3][1], 9)
        self.assertEqual(snapshot_after_close[3][3], 0)

        # Network fence invariants
        self.assertEqual(len(self.fence["denied_connects"]), self.denied_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.constructs_before)


if __name__ == "__main__":
    unittest.main()
