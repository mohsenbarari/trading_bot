import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.realtime import (
    REALTIME_SOURCE_SYNC_APPLY,
    publish_event,
    publish_user_event,
    realtime_publish_writes_outbound_sync,
)


class FakeRedisClient:
    def __init__(self, publish_error=None, publish_result=0):
        self.publish_calls = []
        self.publish_error = publish_error
        self.publish_result = publish_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def publish(self, channel, payload):
        self.publish_calls.append((channel, payload))
        if self.publish_error:
            raise self.publish_error
        return self.publish_result


class RealtimeRouterPublishEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_event_uses_redis_as_the_only_healthy_transport_with_zero_subscribers(self):
        redis_client = FakeRedisClient(publish_result=0)
        data = {"id": 1, "status": "active", "mobile_number": "0912", "home_server": "foreign"}
        public_data = {"id": 1, "status": "active"}

        with patch("api.routers.realtime.uuid.uuid4", return_value=SimpleNamespace(hex="event-1")), patch(
            "api.routers.realtime.redis.Redis", return_value=redis_client
        ), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock()
        ) as broadcast_mock:
            event_id = await publish_event("offer:created", data)

        self.assertEqual(event_id, "event-1")
        self.assertEqual(
            redis_client.publish_calls,
            [(
                "events:offer:created",
                json.dumps(
                    {**public_data, "_realtime_event_id": "event-1"},
                    ensure_ascii=False,
                    default=str,
                ),
            )],
        )
        broadcast_mock.assert_not_awaited()

    async def test_publish_event_tolerates_redis_and_broadcast_failures(self):
        redis_client = FakeRedisClient(publish_error=RuntimeError("redis down"))

        with patch("api.routers.realtime.uuid.uuid4", return_value=SimpleNamespace(hex="event-fallback")), patch(
            "api.routers.realtime.redis.Redis", return_value=redis_client
        ), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock(side_effect=RuntimeError("ws down"))
        ) as broadcast_mock:
            await publish_event("offer:created", {"id": 1})
        broadcast_mock.assert_awaited_once_with({
            "type": "offer:created",
            "data": {"id": 1},
            "event_id": "event-fallback",
        })

    async def test_publish_event_blocks_private_and_unknown_event_types(self):
        redis_client = FakeRedisClient()

        with patch("api.routers.realtime.redis.Redis", return_value=redis_client), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock()
        ) as broadcast_mock:
            await publish_event("trade:created", {"id": 10, "price": 50000})
            await publish_event("internal:unexpected", {"secret": "value"})

        self.assertEqual(redis_client.publish_calls, [])
        broadcast_mock.assert_not_awaited()

    async def test_sync_apply_realtime_source_is_local_fanout_only(self):
        redis_client = FakeRedisClient()

        with patch("api.routers.realtime.uuid.uuid4", return_value=SimpleNamespace(hex="event-sync")), patch(
            "api.routers.realtime.redis.Redis", return_value=redis_client
        ), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock()
        ) as broadcast_mock:
            await publish_event("offer:updated", {"id": 5}, source=REALTIME_SOURCE_SYNC_APPLY)

        self.assertFalse(realtime_publish_writes_outbound_sync(REALTIME_SOURCE_SYNC_APPLY))
        self.assertEqual(
            redis_client.publish_calls,
            [(
                "events:offer:updated",
                json.dumps(
                    {"id": 5, "_realtime_event_id": "event-sync"},
                    ensure_ascii=False,
                    default=str,
                ),
            )],
        )
        broadcast_mock.assert_not_awaited()

    async def test_publish_user_event_publishes_only_to_notification_channel(self):
        redis_client = FakeRedisClient()
        data = {"safe": 1, "mobile_number": "0912"}

        with patch("api.routers.realtime.redis.Redis", return_value=redis_client), patch(
            "api.routers.realtime.manager.broadcast", new=AsyncMock()
        ) as broadcast_mock:
            await publish_user_event(7, "trade:created", data)

        self.assertEqual(
            redis_client.publish_calls,
            [(
                "notifications:7",
                json.dumps({"event": "trade:created", "data": data}, ensure_ascii=False, default=str),
            )],
        )
        broadcast_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
