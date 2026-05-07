import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.services.chat_room_service import (
    build_channel_message_event_payload,
    build_group_message_event_payload,
    publish_channel_message_event,
    publish_channel_reaction_event,
    publish_channel_read_event,
    publish_group_message_event,
    publish_group_reaction_event,
    publish_group_read_event,
)


class ChatRoomServiceEventTests(unittest.IsolatedAsyncioTestCase):
    def test_build_channel_message_event_payload_adds_room_metadata_and_fallback_title(self):
        message = SimpleNamespace(id=5, content="hello")
        chat = SimpleNamespace(id=12, title=None)

        payload = build_channel_message_event_payload(
            message,
            chat=chat,
            serializer=lambda msg: {"id": msg.id, "content": msg.content},
        )

        self.assertEqual(payload["id"], 5)
        self.assertEqual(payload["content"], "hello")
        self.assertEqual(payload["room_kind"], "channel")
        self.assertEqual(payload["chat_id"], 12)
        self.assertEqual(payload["conversation_id"], -12)
        self.assertEqual(payload["conversation_other_user_id"], -12)
        self.assertEqual(payload["conversation_title"], "کانال 12")
        self.assertIsNone(payload["can_send"])

    def test_build_group_message_event_payload_adds_room_metadata_and_title(self):
        message = SimpleNamespace(id=7, content="group")
        chat = SimpleNamespace(id=18, title="اتاق اصلی")

        payload = build_group_message_event_payload(
            message,
            chat=chat,
            serializer=lambda msg: {"id": msg.id, "content": msg.content},
        )

        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["content"], "group")
        self.assertEqual(payload["room_kind"], "group")
        self.assertEqual(payload["chat_id"], 18)
        self.assertEqual(payload["conversation_id"], -18)
        self.assertEqual(payload["conversation_other_user_id"], -18)
        self.assertEqual(payload["conversation_title"], "اتاق اصلی")
        self.assertTrue(payload["can_send"])

    async def test_publish_channel_message_event_skips_sender(self):
        publisher = AsyncMock()
        message = SimpleNamespace(id=5, content="hello", sender_id=2)
        chat = SimpleNamespace(id=12, title=None)

        await publish_channel_message_event(
            chat=chat,
            message=message,
            member_user_ids=[1, 2, 3],
            serializer=lambda msg: {"id": msg.id},
            publisher=publisher,
        )

        self.assertEqual(publisher.await_count, 2)
        published_user_ids = [call.args[0] for call in publisher.await_args_list]
        self.assertEqual(published_user_ids, [1, 3])
        self.assertTrue(all(call.args[1] == "chat:message" for call in publisher.await_args_list))

    async def test_publish_group_message_event_skips_sender(self):
        publisher = AsyncMock()
        message = SimpleNamespace(id=8, content="hello", sender_id=1)
        chat = SimpleNamespace(id=22, title="group")

        await publish_group_message_event(
            chat=chat,
            message=message,
            member_user_ids=[1, 2, 3],
            serializer=lambda msg: {"id": msg.id},
            publisher=publisher,
        )

        self.assertEqual([call.args[0] for call in publisher.await_args_list], [2, 3])
        self.assertTrue(all(call.args[1] == "chat:message" for call in publisher.await_args_list))

    async def test_publish_channel_and_group_read_events_skip_reader(self):
        channel_publisher = AsyncMock()
        group_publisher = AsyncMock()

        await publish_channel_read_event(
            chat_id=12,
            reader_id=2,
            member_user_ids=[1, 2, 3],
            publisher=channel_publisher,
        )
        await publish_group_read_event(
            chat_id=18,
            reader_id=3,
            member_user_ids=[1, 2, 3],
            publisher=group_publisher,
        )

        self.assertEqual([call.args[0] for call in channel_publisher.await_args_list], [1, 3])
        self.assertEqual([call.args[0] for call in group_publisher.await_args_list], [1, 2])
        self.assertTrue(all(call.args[1] == "chat:read" for call in channel_publisher.await_args_list))
        self.assertTrue(all(call.args[1] == "chat:read" for call in group_publisher.await_args_list))

    async def test_publish_reaction_events_broadcast_to_all_members(self):
        channel_publisher = AsyncMock()
        group_publisher = AsyncMock()
        message = SimpleNamespace(id=5, content="hello", sender_id=2)
        channel_chat = SimpleNamespace(id=12, title=None)
        group_chat = SimpleNamespace(id=18, title="team")

        await publish_channel_reaction_event(
            chat=channel_chat,
            message=message,
            member_user_ids=[1, 2, 3],
            serializer=lambda msg: {"id": msg.id},
            publisher=channel_publisher,
        )
        await publish_group_reaction_event(
            chat=group_chat,
            message=message,
            member_user_ids=[1, 2, 3],
            serializer=lambda msg: {"id": msg.id},
            publisher=group_publisher,
        )

        self.assertEqual([call.args[0] for call in channel_publisher.await_args_list], [1, 2, 3])
        self.assertEqual([call.args[0] for call in group_publisher.await_args_list], [1, 2, 3])
        self.assertTrue(all(call.args[1] == "chat:reaction" for call in channel_publisher.await_args_list))
        self.assertTrue(all(call.args[1] == "chat:reaction" for call in group_publisher.await_args_list))


if __name__ == "__main__":
    unittest.main()