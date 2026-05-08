import unittest
from datetime import datetime
from types import SimpleNamespace

from api.routers.notifications import (
    get_all_notifications,
    get_unread_count,
    get_unread_notifications,
    sync_unread_count,
)
from core.enums import NotificationCategory, NotificationLevel


class FakeExecuteResult:
    def __init__(self, *, scalar_value=None, values=None):
        self._scalar_value = scalar_value
        self._values = list(values or [])

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.set_calls.append((key, value))
        self.values[key] = value


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


class NotificationsRouterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_unread_count_counts_db_rows_and_updates_redis(self):
        redis = FakeRedis()
        db = FakeDB([FakeExecuteResult(scalar_value=3)])

        result = await sync_unread_count(db, redis, user_id=9)

        self.assertEqual(result, 3)
        self.assertEqual(redis.set_calls, [("user:9:unread_count", 3)])

    async def test_get_unread_count_reads_directly_from_redis(self):
        current_user = SimpleNamespace(id=5)

        self.assertEqual(await get_unread_count(current_user=current_user, redis=FakeRedis()), 0)
        self.assertEqual(
            await get_unread_count(current_user=current_user, redis=FakeRedis({"user:5:unread_count": "7"})),
            7,
        )

    async def test_get_unread_notifications_and_get_all_notifications_return_db_rows(self):
        current_user = SimpleNamespace(id=5)
        unread_rows = [make_notification(1), make_notification(2)]
        all_rows = [make_notification(3, is_read=True)]

        unread = await get_unread_notifications(
            current_user=current_user,
            db=FakeDB([FakeExecuteResult(values=unread_rows)]),
        )
        all_notifications = await get_all_notifications(
            current_user=current_user,
            db=FakeDB([FakeExecuteResult(values=all_rows)]),
        )

        self.assertEqual(unread, unread_rows)
        self.assertEqual(all_notifications, all_rows)


if __name__ == "__main__":
    unittest.main()