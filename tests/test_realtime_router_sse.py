import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport

from api.routers.realtime import event_generator, router, sse_stream


class FakePubSub:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.subscribed = None
        self.unsubscribe_calls = 0

    async def subscribe(self, *channels):
        self.subscribed = channels

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self.messages:
            next_item = self.messages.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item
        raise asyncio.CancelledError()

    async def unsubscribe(self):
        self.unsubscribe_calls += 1


class FakeRedisClient:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def pubsub(self):
        return self._pubsub


class RealtimeRouterSseTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_stream_rejects_anonymous_requests(self):
        app = FastAPI()
        app.include_router(router)
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/stream")

        self.assertEqual(response.status_code, 401)

    async def test_event_generator_sanitizes_payload_and_unsubscribes_on_cancel(self):
        pubsub = FakePubSub([
            {
                "type": "message",
                "channel": b"events:offer:created",
                "data": b'{"id": 1, "status": "active", "mobile_number": "0912", "home_server": "foreign", "_realtime_event_id": "event-sse"}',
            },
        ])
        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)):
            generator = event_generator(user_id=5)
            first = await generator.__anext__()
            with self.assertRaises(asyncio.CancelledError):
                await generator.__anext__()

        self.assertIn("event: offer:created", first)
        self.assertIn("id: event-sse", first)
        self.assertIn('"id": 1', first)
        self.assertIn('"status": "active"', first)
        self.assertNotIn("mobile_number", first)
        self.assertNotIn("home_server", first)
        self.assertIn("notifications:5", pubsub.subscribed)
        self.assertNotIn("events:trade:created", pubsub.subscribed)
        self.assertEqual(pubsub.unsubscribe_calls, 2)

    async def test_event_generator_formats_notification_channel_events(self):
        pubsub = FakePubSub([
            {"type": "message", "channel": b"notifications:5", "data": b'{"event":"trade:created","data":{"safe": 9, "mobile_number": "0912"}}'},
        ])

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)), patch(
            "api.routers.realtime._websocket_access_denial",
            new=AsyncMock(return_value=None),
        ):
            generator = event_generator(user_id=5)
            first = await generator.__anext__()
            with self.assertRaises(asyncio.CancelledError):
                await generator.__anext__()

        self.assertIn("event: trade:created", first)
        self.assertIn('"safe": 9', first)
        self.assertNotIn("mobile_number", first)

    async def test_event_generator_stops_private_delivery_after_user_is_locked(self):
        pubsub = FakePubSub([
            {
                "type": "message",
                "channel": b"notifications:5",
                "data": b'{"event":"trade:created","data":{"safe":9}}',
            },
        ])

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)), patch(
            "api.routers.realtime._websocket_access_denial",
            new=AsyncMock(return_value=(4003, "User is inactive")),
        ):
            generator = event_generator(user_id=5)
            with self.assertRaises(StopAsyncIteration):
                await generator.__anext__()

        self.assertEqual(pubsub.unsubscribe_calls, 1)

    async def test_event_generator_stops_private_delivery_after_session_revocation(self):
        session_id = "85f737c9-477b-47ca-a2b2-d069fdc6d094"
        pubsub = FakePubSub([
            {
                "type": "message",
                "channel": b"notifications:5",
                "data": b'{"event":"trade:created","data":{"safe":9}}',
            },
        ])

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)), patch(
            "api.routers.realtime.is_session_blacklisted",
            new=AsyncMock(return_value=True),
        ) as blacklist_mock:
            generator = event_generator(user_id=5, session_id=session_id)
            with self.assertRaises(StopAsyncIteration):
                await generator.__anext__()

        blacklist_mock.assert_awaited_once_with(session_id)
        self.assertEqual(pubsub.unsubscribe_calls, 1)

    async def test_sse_stream_wraps_generator_with_expected_headers(self):
        session_id = "85f737c9-477b-47ca-a2b2-d069fdc6d094"

        async def stream():
            if False:
                yield ""

        with patch(
            "api.routers.realtime.verify_ws_token",
            return_value=(7, session_id),
        ), patch(
            "api.routers.realtime.event_generator",
            return_value=stream(),
        ) as generator_mock:
            response = await sse_stream(
                request=SimpleNamespace(),
                current_user=SimpleNamespace(id=7),
                token="signed-token",
            )
        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertEqual(response.headers["X-Accel-Buffering"], "no")
        generator_mock.assert_called_once_with(7, session_id=session_id)

    async def test_event_generator_tolerates_invalid_json_and_emits_heartbeat(self):
        pubsub = FakePubSub([
            {"type": "message", "channel": b"events:offer:updated", "data": b"not-json"},
        ])

        class FakeLoop:
            def __init__(self):
                self.values = iter([0, 20])

            def time(self):
                return next(self.values)

        async def fake_sleep(_delay):
            raise asyncio.CancelledError()

        with patch("api.routers.realtime.redis.Redis", return_value=FakeRedisClient(pubsub)), patch(
            "api.routers.realtime.asyncio.get_event_loop", return_value=FakeLoop()
        ), patch("api.routers.realtime.asyncio.sleep", side_effect=fake_sleep):
            generator = event_generator(user_id=5)
            first = await generator.__anext__()
            second = await generator.__anext__()
            with self.assertRaises(asyncio.CancelledError):
                await generator.__anext__()

        self.assertIn("event: offer:updated", first)
        self.assertIn("data: {}", first)
        self.assertEqual(second, "event: heartbeat\ndata: {}\n\n")


if __name__ == "__main__":
    unittest.main()
