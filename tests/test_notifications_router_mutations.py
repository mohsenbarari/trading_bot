import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.notifications import (
    delete_all_notifications,
    delete_notification,
    mark_all_notifications_read,
    mark_notification_read,
)
from core.enums import NotificationCategory, NotificationLevel


class FakeExecuteResult:
    def __init__(self, *, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.commits = 0
        self.deleted = []

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def commit(self):
        self.commits += 1

    async def delete(self, obj):
        self.deleted.append(obj)


def make_notification(notification_id, **overrides):
    data = {
        "id": notification_id,
        "message": f"msg-{notification_id}",
        "is_read": False,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "level": NotificationLevel.INFO,
        "category": NotificationCategory.SYSTEM,
        "user_id": 5,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class NotificationsRouterMutationTests(unittest.IsolatedAsyncioTestCase):
    async def test_mark_notification_read_noops_for_missing_or_already_read(self):
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(value=None)])
            result = await mark_notification_read(1, current_user=current_user, db=db, redis=SimpleNamespace())
            self.assertIsNone(result)
            self.assertEqual(db.commits, 0)
            sync_mock.assert_not_awaited()

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(value=make_notification(2, is_read=True))])
            result = await mark_notification_read(2, current_user=current_user, db=db, redis=SimpleNamespace())
            self.assertIsNone(result)
            self.assertEqual(db.commits, 0)
            sync_mock.assert_not_awaited()

    async def test_mark_notification_read_marks_unread_notification_and_syncs_counter(self):
        current_user = SimpleNamespace(id=5)
        notification = make_notification(3, is_read=False)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(value=notification)])
            result = await mark_notification_read(3, current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertTrue(notification.is_read)
        self.assertEqual(db.commits, 1)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)

    async def test_mark_all_notifications_read_commits_only_when_needed_and_always_syncs(self):
        current_user = SimpleNamespace(id=5)
        first = make_notification(1, is_read=False)
        second = make_notification(2, is_read=False)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(values=[first, second])])
            result = await mark_all_notifications_read(current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertTrue(first.is_read)
        self.assertTrue(second.is_read)
        self.assertEqual(db.commits, 1)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(values=[])])
            result = await mark_all_notifications_read(current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertEqual(db.commits, 0)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)

    async def test_delete_all_notifications_and_single_delete_cover_success_and_not_found(self):
        current_user = SimpleNamespace(id=5)
        first = make_notification(10)
        second = make_notification(11)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(values=[first, second])])
            result = await delete_all_notifications(current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertEqual(db.deleted, [first, second])
        self.assertEqual(db.commits, 1)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)

        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(values=[])])
            result = await delete_all_notifications(current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertEqual(db.deleted, [])
        self.assertEqual(db.commits, 0)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)

        with self.assertRaises(HTTPException) as exc_info:
            await delete_notification(
                44,
                current_user=current_user,
                db=FakeDB([FakeExecuteResult(value=None)]),
                redis=SimpleNamespace(),
            )
        self.assertEqual(exc_info.exception.status_code, 404)

        victim = make_notification(45)
        with patch("api.routers.notifications.sync_unread_count", new=AsyncMock()) as sync_mock:
            db = FakeDB([FakeExecuteResult(value=victim)])
            result = await delete_notification(45, current_user=current_user, db=db, redis=SimpleNamespace())

        self.assertIsNone(result)
        self.assertEqual(db.deleted, [victim])
        self.assertEqual(db.commits, 1)
        sync_mock.assert_awaited_once_with(db, unittest.mock.ANY, 5)


if __name__ == "__main__":
    unittest.main()