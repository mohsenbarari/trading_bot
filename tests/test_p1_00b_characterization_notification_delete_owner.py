"""Characterization test for notifications.delete_notification cross-user rejection boundary.

Seed: API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-NOTIFICATION-L362
Boundary: Cross-user deletion rejection boundary where caller id 5 attempts to delete
an existing notification belonging to user id 20. Validates that the request raises
HTTPException(404, "Notification not found"), leaves adapter delete and commit call counts
at zero, invokes no Redis set activity, and preserves durable SQLite rows.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.routers.notifications import delete_notification
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class AsyncSessionAdapter:
    """Minimal async session adapter recording calls and delegating to a sync Session."""

    def __init__(self, session: Session):
        self._session = session
        self.delete_calls: list[Any] = []
        self.commit_calls: int = 0
        self.execute_calls: list[Any] = []

    async def execute(self, statement, *args, **kwargs):
        self.execute_calls.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    async def delete(self, instance):
        self.delete_calls.append(instance)
        return self._session.delete(instance)

    async def commit(self):
        self.commit_calls += 1
        return self._session.commit()

    async def close(self):
        self._session.close()


class NotificationDeleteOwnerCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes notifications.delete_notification cross-user delete rejection boundary."""

    async def test_delete_notification_cross_user_rejection_boundary(self):
        """Seed API-NOTIFY-DELETE-NOTIFICATIONS-DELETE-NOTIFICATION-L362 cross-user rejection.

        Assert caller 5 cannot delete notification owned by user 20:
        - HTTPException 404 with detail 'Notification not found'
        - Zero adapter delete calls and zero commit calls
        - Zero fake Redis set call or await activity
        - Durable state for both seeded notifications remains intact in a fresh session
        """
        engine = create_engine("sqlite:///:memory:")
        try:
            Notification.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)

            # Seed victim notification (owner 20) and caller notification (owner 5)
            with session_factory() as seed_session:
                victim_notification = Notification(
                    id=10,
                    user_id=20,
                    message="Notification for user 20",
                    is_read=False,
                    created_at=datetime(2026, 9, 8, 12, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
                caller_notification = Notification(
                    id=11,
                    user_id=5,
                    message="Notification for caller 5",
                    is_read=False,
                    created_at=datetime(2026, 9, 8, 12, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                )
                seed_session.add_all([victim_notification, caller_notification])
                seed_session.commit()

            caller_user = SimpleNamespace(id=5)
            fake_redis = AsyncMock()
            handler_session = session_factory()
            adapter = AsyncSessionAdapter(handler_session)

            try:
                with self.assertRaises(HTTPException) as exc_context:
                    await delete_notification(
                        notification_id=10,
                        current_user=caller_user,
                        db=adapter,
                        redis=fake_redis,
                    )
            finally:
                handler_session.close()

            # Verify 404 HTTPException response
            self.assertEqual(exc_context.exception.status_code, 404)
            self.assertEqual(exc_context.exception.detail, "Notification not found")

            # Verify zero delete, zero commit, and zero Redis set calls
            self.assertEqual(adapter.delete_calls, [])
            self.assertEqual(adapter.commit_calls, 0)
            self.assertEqual(len(adapter.execute_calls), 1)
            fake_redis.set.assert_not_called()
            fake_redis.set.assert_not_awaited()
            self.assertEqual(fake_redis.set.call_count, 0)
            self.assertEqual(fake_redis.set.await_count, 0)

            # Verify durable notification state in a separate fresh session
            with session_factory() as verify_session:
                all_notifications = (
                    verify_session.execute(select(Notification).order_by(Notification.id))
                    .scalars()
                    .all()
                )
                self.assertEqual(len(all_notifications), 2)

                persisted_victim = verify_session.get(Notification, 10)
                self.assertIsNotNone(persisted_victim)
                self.assertEqual(persisted_victim.user_id, 20)
                self.assertEqual(persisted_victim.message, "Notification for user 20")
                self.assertFalse(persisted_victim.is_read)
                self.assertEqual(persisted_victim.level, NotificationLevel.INFO)
                self.assertEqual(persisted_victim.category, NotificationCategory.SYSTEM)

                persisted_caller = verify_session.get(Notification, 11)
                self.assertIsNotNone(persisted_caller)
                self.assertEqual(persisted_caller.user_id, 5)
                self.assertEqual(persisted_caller.message, "Notification for caller 5")
                self.assertFalse(persisted_caller.is_read)
                self.assertEqual(persisted_caller.level, NotificationLevel.INFO)
                self.assertEqual(persisted_caller.category, NotificationCategory.SYSTEM)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
