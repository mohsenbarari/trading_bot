import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.realtime import publish_event


class FakeRedisClient:
    def __init__(self, publish_error=None):
        self.publish_calls = []
        self.publish_error = publish_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def publish(self, channel, payload):
        self.publish_calls.append((channel, payload))
        if self.publish_error:
            raise self.publish_error


class RealtimeRouterPublishEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_event_publishes_to_redis_and_broadcasts_sanitized_payload(self):
        redis_client = FakeRedisClient()
        data = {"safe": 1, "mobile_number": "0912"}

        with patch("api.routers.realtime.redis.Redis", return_value=redis_client), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock()
        ) as broadcast_mock:
            await publish_event("offer:created", data)

        self.assertEqual(redis_client.publish_calls, [("events:offer:created", json.dumps(data, ensure_ascii=False, default=str))])
        broadcast_mock.assert_awaited_once_with({"type": "offer:created", "data": {"safe": 1}})

    async def test_publish_event_tolerates_redis_and_broadcast_failures(self):
        redis_client = FakeRedisClient(publish_error=RuntimeError("redis down"))

        with patch("api.routers.realtime.redis.Redis", return_value=redis_client), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock(side_effect=RuntimeError("ws down"))
        ):
            await publish_event("offer:created", {"safe": 1})


if __name__ == "__main__":
    unittest.main()