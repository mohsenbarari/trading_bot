import asyncio
from types import SimpleNamespace
import unittest

from fastapi.responses import StreamingResponse

from api.routers.notifications import stream_notifications


class FakePubSub:
    def __init__(self, messages):
        self.messages = list(messages)
        self.subscribed = []
        self.unsubscribed = []

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def listen(self):
        for message in self.messages:
            yield message
        raise asyncio.CancelledError()


class FakeRedis:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub


class NotificationStreamCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_event_preserves_channel_response_metadata_chunk_order_and_cancellation_cleanup(self):
        raw_payload = '{"event": "trade_status", "data": {"trade_id": 4101, "state": "confirmed"}}'
        pubsub = FakePubSub([
            {"type": "message", "data": raw_payload},
        ])
        user = SimpleNamespace(id=41)
        redis = FakeRedis(pubsub)

        response = await stream_notifications(
            current_user=user,
            redis=redis,
        )

        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "text/event-stream")

        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(pubsub.subscribed, ["notifications:41"])
        self.assertEqual(pubsub.unsubscribed, ["notifications:41"])
        self.assertEqual(
            chunks,
            [
                "event: trade_status\n",
                'data: {"trade_id": 4101, "state": "confirmed"}\n\n',
            ],
        )

    async def test_valid_json_without_event_is_emitted_as_original_raw_data(self):
        raw_payload = '{"notification_id": 88, "body": "Order filled", "unread_count": 1}'
        pubsub = FakePubSub([
            {"type": "message", "data": raw_payload},
        ])
        user = SimpleNamespace(id=52)
        redis = FakeRedis(pubsub)

        response = await stream_notifications(
            current_user=user,
            redis=redis,
        )

        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "text/event-stream")

        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(pubsub.subscribed, ["notifications:52"])
        self.assertEqual(pubsub.unsubscribed, ["notifications:52"])
        self.assertEqual(
            chunks,
            [
                'data: {"notification_id": 88, "body": "Order filled", "unread_count": 1}\n\n',
            ],
        )

    async def test_missing_event_data_falls_back_to_raw_and_non_message_is_ignored(self):
        raw_event_payload = '{"event": "system_maintenance"}'
        pubsub = FakePubSub([
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": raw_event_payload},
        ])
        user = SimpleNamespace(id=63)
        redis = FakeRedis(pubsub)

        response = await stream_notifications(
            current_user=user,
            redis=redis,
        )

        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "text/event-stream")

        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(pubsub.subscribed, ["notifications:63"])
        self.assertEqual(pubsub.unsubscribed, ["notifications:63"])
        self.assertEqual(
            chunks,
            [
                "event: system_maintenance\n",
                'data: {"event": "system_maintenance"}\n\n',
            ],
        )


if __name__ == "__main__":
    unittest.main()
