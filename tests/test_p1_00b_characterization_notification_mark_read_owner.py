"""Characterization test for notifications.mark_notification_read cross-user no-op boundary.

This characterizes the real direct handler SQL cross-user no-op boundary only;
it does not prove HTTP dependency/routing behavior, process restart, concurrency,
production parity, or Stage completion.
"""

from __future__ import annotations

import os
import shutil
import tempfile
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

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.routers.notifications import mark_notification_read
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class AsyncSessionAdapter:
    """Minimal async session adapter recording execute and commit counts."""

    def __init__(self, session: Session):
        self._session = session
        self.execute_calls: list[Any] = []
        self.execute_count: int = 0
        self.commit_calls: int = 0

    async def execute(self, statement, *args, **kwargs):
        self.execute_count += 1
        self.execute_calls.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    async def commit(self):
        self.commit_calls += 1
        return self._session.commit()

    async def close(self):
        self._session.close()


def _snapshot_notification(item: Notification) -> dict[str, Any]:
    """Capture all mapped notification column values for equality assertion."""
    return {
        "id": item.id,
        "user_id": item.user_id,
        "message": item.message,
        "is_read": item.is_read,
        "created_at": item.created_at,
        "level": item.level,
        "category": item.category,
        "dedupe_key": item.dedupe_key,
        "extra_payload": item.extra_payload,
    }


class NotificationMarkReadOwnerCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes notifications.mark_notification_read cross-user no-op boundary."""

    async def test_mark_notification_read_cross_user_noop_boundary(self):
        """Assert caller 5 cannot mark unread notification owned by user 20 as read."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_mark_read_owner.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Notification.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)

            with session_factory() as seed_session:
                victim_notification = Notification(
                    id=10,
                    user_id=20,
                    message="Notification for user 20",
                    is_read=False,
                    created_at=datetime(2026, 9, 8, 12, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-victim-10",
                    extra_payload={"scope": "victim"},
                )
                caller_notification = Notification(
                    id=11,
                    user_id=5,
                    message="Notification for caller 5",
                    is_read=False,
                    created_at=datetime(2026, 9, 8, 12, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-caller-11",
                    extra_payload={"scope": "caller"},
                )
                seed_session.add_all([victim_notification, caller_notification])
                seed_session.commit()

            with session_factory() as baseline_session:
                baseline_rows = {
                    item.id: _snapshot_notification(item)
                    for item in baseline_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                }

            self.assertEqual(set(baseline_rows.keys()), {10, 11})

            caller_user = SimpleNamespace(id=5)
            fake_redis = AsyncMock()
            handler_session = session_factory()
            adapter = AsyncSessionAdapter(handler_session)

            try:
                result = await mark_notification_read(
                    notification_id=10,
                    current_user=caller_user,
                    db=adapter,
                    redis=fake_redis,
                )
            finally:
                handler_session.close()

            self.assertIsNone(result)
            self.assertEqual(adapter.execute_count, 1)
            self.assertEqual(len(adapter.execute_calls), 1)
            self.assertEqual(adapter.commit_calls, 0)
            self.assertEqual(fake_redis.mock_calls, [])
            fake_redis.set.assert_not_called()
            fake_redis.set.assert_not_awaited()
            self.assertEqual(fake_redis.set.call_count, 0)
            self.assertEqual(fake_redis.set.await_count, 0)

            with session_factory() as verify_session:
                persisted_items = (
                    verify_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                )
                self.assertEqual(len(persisted_items), 2)
                current_rows = {
                    item.id: _snapshot_notification(item)
                    for item in persisted_items
                }
                self.assertEqual(current_rows, baseline_rows)
                self.assertFalse(current_rows[10]["is_read"])
                self.assertFalse(current_rows[11]["is_read"])
        finally:
            engine.dispose()
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
