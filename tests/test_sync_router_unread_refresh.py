import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import _refresh_notification_unread_counts


class FakeExecuteResult:
    def __init__(self, scalar_value):
        self._scalar_value = scalar_value

    def scalar(self):
        return self._scalar_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class FakeRedisClient:
    def __init__(self):
        self.set_calls = []

    async def set(self, key, value):
        self.set_calls.append((key, value))


class SyncRouterUnreadRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_notification_unread_counts_updates_each_user(self):
        redis_client = FakeRedisClient()
        db = FakeDB([FakeExecuteResult(3), FakeExecuteResult(0)])

        with patch("core.redis.get_redis_client", return_value=redis_client):
            await _refresh_notification_unread_counts(db, {5, 9})

        self.assertEqual({key for key, _ in redis_client.set_calls}, {"user:5:unread_count", "user:9:unread_count"})
        self.assertEqual(sorted(value for _, value in redis_client.set_calls), [0, 3])

    async def test_refresh_notification_unread_counts_ignores_empty_or_redis_init_failures(self):
        await _refresh_notification_unread_counts(FakeDB(), set())

        with patch("core.redis.get_redis_client", side_effect=RuntimeError("redis down")):
            await _refresh_notification_unread_counts(FakeDB(), {5})

        failing_db = FakeDB([RuntimeError("db down")])
        with patch("core.redis.get_redis_client", return_value=FakeRedisClient()):
            await _refresh_notification_unread_counts(failing_db, {5})


if __name__ == "__main__":
    unittest.main()