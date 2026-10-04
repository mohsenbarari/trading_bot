"""Hermetic characterization test for notifications.mark_notification_read already-read owner HTTP 204 path.

Stage: P1-00b
Assignment: WA-P1-00B-NORMAL-RETURN-20261004-277
Scenario: P1-00B.NOTIFICATIONS.MARK-READ-ALREADY-READ-OWNER-HTTP204
Seed ID: API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296

Scope and Bounded Single-Unit Reason:
- Characterizes live HTTP PATCH /api/notifications/{notification_id}/read routing and handler
  behavior when an owner caller (id=5) issues mark-read on an already read notification (id=3).
- Bounded single unit because custom Session adapter, isolated SQLite table lifecycle, and durable
  persisted invariance require dedicated execution; batching is forbidden.
- Real SQLite-backed Session execution via async adapter delegating execute to sync Session.
- Live mark_notification_read execution without fake result queues or fallbacks.
- FastAPI dependency overrides replace get_current_user, get_db, and get_redis.
- Proves no side effects, zero mutations, commit_count=0, and that sync_unread_count is not called.

Exclusions:
- Excludes unread-owner or cross-user replay, missing-ID mark-read, all delete404/deferred outcomes,
  authentication, concurrency, retries, reload, production parity, external action, origin/seed/blocker/Stage closure,
  and ledger edits.
- Preserves defer_prior_unit=normal273-notification-mark-read-missing-id-http-20261004-c002;
  defer_prior_unit=normal273-notification-delete-missing-id-http-20261004-c001;
  defer_prior_unit=normal271-notification-delete-cross-user-http-20261004-c002;
  defer_prior_unit=normal265-canonical-five-closeout-20261004-c001;
  all historical normal169 deferrals.
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

import tests.test_p1_00b_characterization_notification_delete_success as fixture_module
import tests.test_p1_00b_characterization_notification_mark_read_owner_http as owner_http_module
from api.deps import get_current_user
from api.routers.notifications import router as notifications_router
from core.db import get_db
from core.redis import get_redis


class NotificationMarkReadAlreadyReadHttpCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for notifications.mark_notification_read already-read owner HTTP 204 path."""

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

    async def test_owner_already_read_returns_http_204_without_mutation(self) -> None:
        """Seed API-NOTIFY-PATCH-NOTIFICATIONS-MARK-NOTIFICATION-READ-L296 already-read owner path.

        Scenario: P1-00B.NOTIFICATIONS.MARK-READ-ALREADY-READ-OWNER-HTTP204
        Caller id=5 issues PATCH /api/notifications/3/read where row 3 is already read.
        """
        self.fixture._seed_notifications()

        # Independently verify raw SQLite nine-column ordered pre-snapshot
        snapshot_before = self._fetch_nine_column_snapshot()
        self.assertEqual(len(snapshot_before), 4)

        # Ordered IDs: 1, 2, 3, 4
        self.assertEqual([row[0] for row in snapshot_before], [1, 2, 3, 4])
        # Ordered Owners: 5, 5, 5, 9
        self.assertEqual([row[1] for row in snapshot_before], [5, 5, 5, 9])
        # Ordered Read flags: 0, 0, 1, 0
        self.assertEqual([row[3] for row in snapshot_before], [0, 0, 1, 0])

        # Target 3 belongs to 5 and is_read=1
        self.assertEqual(snapshot_before[2][0], 3)
        self.assertEqual(snapshot_before[2][1], 5)
        self.assertEqual(snapshot_before[2][3], 1)

        # Owner 5 initial unread count is 2
        self.assertEqual(self._fetch_owner_unread_count(5), 2)

        caller_user = types.SimpleNamespace(id=5)
        handler_session = self.fixture._make_session()
        self.addCleanup(handler_session.close)
        adapter = owner_http_module.HttpAsyncSessionAdapter(handler_session)

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
                    response = await client.patch("/api/notifications/3/read")
            else:
                async with httpx.AsyncClient(
                    app=app, base_url="http://testserver"
                ) as client:
                    response = await client.patch("/api/notifications/3/read")
        finally:
            app.dependency_overrides.clear()

        # Assert each dependency override entered exactly once
        self.assertEqual(user_override_count, 1)
        self.assertEqual(db_override_count, 1)
        self.assertEqual(redis_override_count, 1)

        # Assert status 204 and empty body
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

        # Assert exactly one execute call (sync_unread_count is not called on this branch)
        self.assertEqual(len(adapter.execute_calls), 1)

        # Verify normalized query statement and exact compiled parameters
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
        self.assertEqual(compiled.params, {"id_1": 3, "user_id_1": 5})

        # Prove no side effects on session and adapter
        self.assertEqual(adapter.commit_count, 0)
        self.assertEqual(len(adapter.delete_calls), 0)
        self.assertEqual(len(adapter.add_calls), 0)
        self.assertEqual(adapter.flush_count, 0)
        self.assertEqual(len(adapter.refresh_calls), 0)
        self.assertEqual(adapter.rollback_count, 0)

        # Handler Session remains clean
        self.assertEqual(len(handler_session.new), 0)
        self.assertEqual(len(handler_session.dirty), 0)
        self.assertEqual(len(handler_session.deleted), 0)

        # Prove zero Redis interactions
        self.assertEqual(fake_redis.mock_calls, [])
        self.assertEqual(fake_redis.set.call_count, 0)
        self.assertEqual(fake_redis.set.await_count, 0)

        # Snapshot check after HTTP request while handler Session is open
        snapshot_after_http = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_http, snapshot_before)

        # Explicit handler Session closure
        handler_session.close()

        # Snapshot check after explicit handler Session closure
        snapshot_after_close = self._fetch_nine_column_snapshot()
        self.assertEqual(snapshot_after_close, snapshot_before)

        # Owner 5 unread count remains 2
        self.assertEqual(self._fetch_owner_unread_count(5), 2)

        # Verify every column invariant across all 4 rows including nullable fields
        for row in snapshot_after_close:
            self.assertIsNotNone(row[0])  # id
            self.assertIn(row[1], (5, 9))  # user_id
            self.assertIsNotNone(row[2])  # message
            self.assertIn(row[3], (0, 1))  # is_read
            self.assertIsNotNone(row[4])  # created_at
            self.assertIn(row[5], ("INFO", "SUCCESS", "WARNING", "ERROR"))  # level
            self.assertIn(row[6], ("SYSTEM", "USER", "TRADE"))  # category
            self.assertIsNone(row[7])  # dedupe_key
            self.assertIsNone(row[8])  # extra_payload

        self.assertEqual(snapshot_after_close[0], snapshot_before[0])
        self.assertEqual(snapshot_after_close[1], snapshot_before[1])
        self.assertEqual(snapshot_after_close[2], snapshot_before[2])
        self.assertEqual(snapshot_after_close[3], snapshot_before[3])

        # Network fence invariants
        self.assertEqual(len(self.fence["denied_connects"]), self.denied_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.constructs_before)


if __name__ == "__main__":
    unittest.main()
