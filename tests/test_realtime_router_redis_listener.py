import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.routers.realtime import listen_redis_events


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class FailingWebSocket(FakeWebSocket):
    async def send_json(self, payload):
        raise RuntimeError("send failed")


class FakePubSub:
    def __init__(self, messages):
        self.messages = list(messages)
        self.subscribed = None

    async def subscribe(self, *channels):
        self.subscribed = channels

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self.messages:
            next_item = self.messages.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item
        raise asyncio.CancelledError()


class FakeRedisClient:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def pubsub(self):
        return self._pubsub


class RealtimeRouterRedisListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_listen_redis_events_formats_notification_and_general_channels(self):
        messages = [
            {"type": "message", "channel": b"notifications:5", "data": b'{"event":"message","data":{"safe":1,"mobile_number":"0912"}}'},
            {"type": "message", "channel": b"events:offer:created", "data": b'{"safe":2,"mobile_number":"0935"}'},
        ]
        pubsub = FakePubSub(messages)
        websocket = FakeWebSocket()

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)), patch(
            "api.routers.realtime.asyncio.sleep", new=asyncio.sleep
        ):
            await listen_redis_events(websocket, user_id=5)

        self.assertIn("notifications:5", pubsub.subscribed)
        self.assertEqual(
            websocket.sent,
            [
                {"type": "message", "data": {"safe": 1}},
                {"type": "offer:created", "data": {"safe": 2}},
            ],
        )

    async def test_listen_redis_events_handles_send_failures_timeout_loop_errors_and_critical_errors(self):
        failing_pubsub = FakePubSub([
            {"type": "message", "channel": b"events:offer:created", "data": b'{"safe": 1}'},
        ])
        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(failing_pubsub)), patch(
            "api.routers.realtime.asyncio.sleep", new=asyncio.sleep
        ), patch("api.routers.realtime.logging.error") as error_mock:
            await listen_redis_events(FailingWebSocket(), user_id=None)
        self.assertNotIn("notifications:None", failing_pubsub.subscribed)
        self.assertTrue(error_mock.called)

        timeout_pubsub = FakePubSub([asyncio.TimeoutError()])
        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(timeout_pubsub)), patch(
            "api.routers.realtime.asyncio.sleep", new=asyncio.sleep
        ):
            await listen_redis_events(FakeWebSocket(), user_id=None)

        loop_error_pubsub = FakePubSub([RuntimeError("loop failed")])
        sleep_calls = []

        async def fake_sleep(delay):
            sleep_calls.append(delay)

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(loop_error_pubsub)), patch(
            "api.routers.realtime.asyncio.sleep", side_effect=fake_sleep
        ), patch("api.routers.realtime.logging.error"):
            await listen_redis_events(FakeWebSocket(), user_id=None)
        self.assertIn(1, sleep_calls)

        with patch("api.routers.realtime.redis.Redis", side_effect=RuntimeError("redis down")), patch(
            "api.routers.realtime.logging.error"
        ) as error_mock:
            await listen_redis_events(FakeWebSocket(), user_id=None)
        error_mock.assert_called()


if __name__ == "__main__":
    unittest.main()