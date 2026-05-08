import asyncio
import unittest
from types import SimpleNamespace

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


class NotificationsRouterStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_notifications_emits_named_event_payload(self):
        pubsub = FakePubSub([
            {"type": "message", "data": '{"event":"message","data":{"id":1,"text":"hi"}}'},
        ])
        response = await stream_notifications(
            current_user=SimpleNamespace(id=7),
            redis=FakeRedis(pubsub),
        )

        self.assertIsInstance(response, StreamingResponse)
        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(pubsub.subscribed, ["notifications:7"])
        self.assertEqual(pubsub.unsubscribed, ["notifications:7"])
        self.assertEqual(
            chunks,
            [
                "event: message\n",
                'data: {"id": 1, "text": "hi"}\n\n',
            ],
        )

    async def test_stream_notifications_falls_back_to_raw_data_for_non_json(self):
        pubsub = FakePubSub([
            {"type": "message", "data": "plain-text"},
            {"type": "subscribe", "data": 1},
        ])
        response = await stream_notifications(
            current_user=SimpleNamespace(id=9),
            redis=FakeRedis(pubsub),
        )

        chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(chunks, ["data: plain-text\n\n"])
        self.assertEqual(pubsub.unsubscribed, ["notifications:9"])


if __name__ == "__main__":
    unittest.main()