"""Characterization tests for direct notification read handlers under Stage P1-00b.

Evidences handler behaviors for blocker P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE:
- API-NOTIFY-GET-NOTIFICATIONS-GET-UNREAD-COUNT-L120: get_unread_count
- API-NOTIFY-GET-NOTIFICATIONS-GET-UNREAD-NOTIFICATIONS-L251: get_unread_notifications
- API-NOTIFY-GET-NOTIFICATIONS-GET-ALL-NOTIFICATIONS-L273: get_all_notifications

This characterizes direct handler logic against real isolated SQLite storage and
a fake read-only Redis interface; it does not evidence HTTP routing/dependency/Query
validation, real Redis, production/schema parity, concurrency, or Stage completion.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.routers.notifications import (
    get_all_notifications,
    get_unread_count,
    get_unread_notifications,
)
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification


class FakeReadOnlyRedis:
    """Fake read-only Redis client asserting read keys and prohibiting writes."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}
        self.get_calls: list[str] = []
        self.write_calls: list[str] = []

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        return self._data.get(key)

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        self.write_calls.append("set")
        raise AssertionError("FakeReadOnlyRedis.set must not be called")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        self.write_calls.append("delete")
        raise AssertionError("FakeReadOnlyRedis.delete must not be called")


class StrictAsyncReadOnlySessionAdapter:
    """Minimal async session adapter delegating read execution to a sync Session."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.execute_calls: list[Any] = []
        self.execute_count: int = 0
        self.commit_calls: int = 0
        self.add_calls: int = 0
        self.delete_calls: int = 0
        self.flush_calls: int = 0
        self.refresh_calls: int = 0

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.execute_count += 1
        self.execute_calls.append(statement)
        return self._session.execute(statement, *args, **kwargs)

    async def commit(self) -> None:
        self.commit_calls += 1
        raise AssertionError("StrictAsyncReadOnlySessionAdapter.commit must not be called")

    def add(self, instance: Any, _warn: bool = True) -> None:
        self.add_calls += 1
        raise AssertionError("StrictAsyncReadOnlySessionAdapter.add must not be called")

    def delete(self, instance: Any) -> None:
        self.delete_calls += 1
        raise AssertionError("StrictAsyncReadOnlySessionAdapter.delete must not be called")

    async def flush(self, objects: Any = None) -> None:
        self.flush_calls += 1
        raise AssertionError("StrictAsyncReadOnlySessionAdapter.flush must not be called")

    async def refresh(self, instance: Any, *args: Any, **kwargs: Any) -> None:
        self.refresh_calls += 1
        raise AssertionError("StrictAsyncReadOnlySessionAdapter.refresh must not be called")

    async def close(self) -> None:
        self._session.close()


def _snapshot_notification(item: Notification) -> dict[str, Any]:
    """Capture mapped notification column values for exact equality assertion."""
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


class NotificationReadsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for direct notification read handlers."""

    async def test_get_unread_count_reads_redis_key_and_converts_types(self) -> None:
        """API-NOTIFY-GET-NOTIFICATIONS-GET-UNREAD-COUNT-L120: get_unread_count."""
        current_user = SimpleNamespace(id=5)

        # 1. Integer conversion from byte string b'7'
        redis_populated = FakeReadOnlyRedis({"user:5:unread_count": b"7"})
        count_populated = await get_unread_count(
            current_user=current_user,
            redis=redis_populated,  # type: ignore[arg-type]
        )
        self.assertEqual(count_populated, 7)
        self.assertEqual(redis_populated.get_calls, ["user:5:unread_count"])
        self.assertEqual(redis_populated.write_calls, [])

        # 2. Absent-value zero fallback
        redis_empty = FakeReadOnlyRedis()
        count_empty = await get_unread_count(
            current_user=current_user,
            redis=redis_empty,  # type: ignore[arg-type]
        )
        self.assertEqual(count_empty, 0)
        self.assertEqual(redis_empty.get_calls, ["user:5:unread_count"])
        self.assertEqual(redis_empty.write_calls, [])

    def _seed_db(self, session_factory: sessionmaker) -> None:
        with session_factory() as seed_session:
            rows = [
                Notification(
                    id=101,
                    user_id=5,
                    message="Owner unread 101",
                    is_read=False,
                    created_at=datetime(2026, 9, 14, 12, 4, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-owner-101",
                    extra_payload={"scope": "owner_unread_101"},
                ),
                Notification(
                    id=102,
                    user_id=5,
                    message="Owner read 102",
                    is_read=True,
                    created_at=datetime(2026, 9, 14, 12, 3, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-owner-102",
                    extra_payload={"scope": "owner_read_102"},
                ),
                Notification(
                    id=103,
                    user_id=5,
                    message="Owner unread 103",
                    is_read=False,
                    created_at=datetime(2026, 9, 14, 12, 2, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-owner-103",
                    extra_payload={"scope": "owner_unread_103"},
                ),
                Notification(
                    id=104,
                    user_id=5,
                    message="Owner unread 104",
                    is_read=False,
                    created_at=datetime(2026, 9, 14, 12, 1, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-owner-104",
                    extra_payload={"scope": "owner_unread_104"},
                ),
                Notification(
                    id=105,
                    user_id=9,
                    message="Other user unread 105",
                    is_read=False,
                    created_at=datetime(2026, 9, 14, 12, 5, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-other-105",
                    extra_payload={"scope": "other_unread_105"},
                ),
                Notification(
                    id=106,
                    user_id=5,
                    message="Owner read 106",
                    is_read=True,
                    created_at=datetime(2026, 9, 14, 12, 0, 0),
                    level=NotificationLevel.INFO,
                    category=NotificationCategory.SYSTEM,
                    dedupe_key="seed-owner-106",
                    extra_payload={"scope": "owner_read_106"},
                ),
            ]
            seed_session.add_all(rows)
            seed_session.commit()

    async def test_get_unread_notifications_filters_owner_unread_order_pagination(self) -> None:
        """API-NOTIFY-GET-NOTIFICATIONS-GET-UNREAD-NOTIFICATIONS-L251: get_unread_notifications."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_unread_reads.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Notification.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            self._seed_db(session_factory)

            with session_factory() as baseline_session:
                baseline_rows = {
                    item.id: _snapshot_notification(item)
                    for item in baseline_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                }

            self.assertEqual(set(baseline_rows.keys()), {101, 102, 103, 104, 105, 106})

            current_user = SimpleNamespace(id=5)
            handler_session = session_factory()
            adapter = StrictAsyncReadOnlySessionAdapter(handler_session)

            try:
                results = await get_unread_notifications(
                    limit=2,
                    offset=1,
                    current_user=current_user,
                    db=adapter,
                )
            finally:
                await adapter.close()

            # Independent assertion of exact ids [103, 104]
            self.assertEqual([n.id for n in results], [103, 104])
            # Verify owner-only filtering (id 105 excluded)
            self.assertTrue(all(n.user_id == 5 for n in results))
            self.assertNotIn(105, [n.id for n in results])
            # Verify unread-only filtering (read rows 102, 106 excluded)
            self.assertTrue(all(n.is_read is False for n in results))
            self.assertNotIn(102, [n.id for n in results])
            self.assertNotIn(106, [n.id for n in results])
            # Verify offset skipped newest owner unread 101
            self.assertNotIn(101, [n.id for n in results])

            # Verify execution contract
            self.assertEqual(adapter.execute_count, 1)
            self.assertEqual(len(adapter.execute_calls), 1)
            self.assertEqual(adapter.commit_calls, 0)
            self.assertEqual(adapter.add_calls, 0)
            self.assertEqual(adapter.delete_calls, 0)
            self.assertEqual(adapter.flush_calls, 0)
            self.assertEqual(adapter.refresh_calls, 0)

            # Compare fresh-session snapshot after handler call
            with session_factory() as verify_session:
                persisted_rows = {
                    item.id: _snapshot_notification(item)
                    for item in verify_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                }
                self.assertEqual(persisted_rows, baseline_rows)
        finally:
            engine.dispose()
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def test_get_all_notifications_filters_owner_all_order_pagination(self) -> None:
        """API-NOTIFY-GET-NOTIFICATIONS-GET-ALL-NOTIFICATIONS-L273: get_all_notifications."""
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_all_reads.db")
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Notification.__table__.create(engine)
            session_factory = sessionmaker(bind=engine)
            self._seed_db(session_factory)

            with session_factory() as baseline_session:
                baseline_rows = {
                    item.id: _snapshot_notification(item)
                    for item in baseline_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                }

            self.assertEqual(set(baseline_rows.keys()), {101, 102, 103, 104, 105, 106})

            current_user = SimpleNamespace(id=5)
            handler_session = session_factory()
            adapter = StrictAsyncReadOnlySessionAdapter(handler_session)

            try:
                results = await get_all_notifications(
                    limit=3,
                    offset=1,
                    current_user=current_user,
                    db=adapter,
                )
            finally:
                await adapter.close()

            # Independent assertion of exact ids [102, 103, 104]
            self.assertEqual([n.id for n in results], [102, 103, 104])
            # Verify owner-only filtering (id 105 excluded despite newest timestamp)
            self.assertTrue(all(n.user_id == 5 for n in results))
            self.assertNotIn(105, [n.id for n in results])
            # Verify inclusion of both read states (102 is read, 103 and 104 are unread)
            read_states = {n.id: n.is_read for n in results}
            self.assertTrue(read_states[102])
            self.assertFalse(read_states[103])
            self.assertFalse(read_states[104])
            # Verify offset skipped newest owner row 101 and limit excluded 106
            self.assertNotIn(101, [n.id for n in results])
            self.assertNotIn(106, [n.id for n in results])

            # Verify execution contract
            self.assertEqual(adapter.execute_count, 1)
            self.assertEqual(len(adapter.execute_calls), 1)
            self.assertEqual(adapter.commit_calls, 0)
            self.assertEqual(adapter.add_calls, 0)
            self.assertEqual(adapter.delete_calls, 0)
            self.assertEqual(adapter.flush_calls, 0)
            self.assertEqual(adapter.refresh_calls, 0)

            # Compare fresh-session snapshot after handler call
            with session_factory() as verify_session:
                persisted_rows = {
                    item.id: _snapshot_notification(item)
                    for item in verify_session.execute(
                        select(Notification).order_by(Notification.id)
                    ).scalars().all()
                }
                self.assertEqual(persisted_rows, baseline_rows)
        finally:
            engine.dispose()
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
