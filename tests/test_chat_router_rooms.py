import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatType
from api.routers.chat import (
    get_room_messages,
    mark_room_messages_read,
    send_room_activity_signal,
    send_room_message,
)


class ChatRouterRoomEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_room_messages_uses_group_branch_and_serializer(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=70, type=ChatType.GROUP)
        messages = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
        serialized = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)) as room_mock, patch(
            "api.routers.chat.get_active_group_member_or_403",
            new=AsyncMock(),
        ) as member_mock, patch(
            "api.routers.chat.list_group_messages",
            new=AsyncMock(return_value=messages),
        ) as list_mock, patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            return_value=serialized,
        ) as serialize_mock:
            result = await get_room_messages(
                chat_id=70,
                limit=25,
                before_id=90,
                around_id=100,
                current_user=current_user,
                db=db,
            )

        room_mock.assert_awaited_once_with(db, 70)
        member_mock.assert_awaited_once_with(db, chat=chat, user_id=5)
        list_mock.assert_awaited_once_with(db, chat=chat, limit=25, before_id=90, around_id=100)
        serialize_mock.assert_called_once()
        self.assertIs(result, serialized)

    async def test_get_room_messages_uses_channel_branch_and_serializer(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=71, type=ChatType.CHANNEL)
        messages = [SimpleNamespace(id=3)]
        serialized = [SimpleNamespace(id=3)]

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)), patch(
            "api.routers.chat.get_active_channel_member_or_403",
            new=AsyncMock(),
        ) as member_mock, patch(
            "api.routers.chat.list_channel_messages",
            new=AsyncMock(return_value=messages),
        ) as list_mock, patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            return_value=serialized,
        ) as serialize_mock:
            result = await get_room_messages(chat_id=71, current_user=current_user, db=db)

        member_mock.assert_awaited_once_with(db, chat=chat, user_id=5)
        list_mock.assert_awaited_once_with(db, chat=chat, limit=50, before_id=None, around_id=None)
        serialize_mock.assert_awaited_once_with(db, messages, viewer_user_id=5)
        self.assertIs(result, serialized)

    async def test_send_room_message_uses_group_branch_and_publishes(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=70, type=ChatType.GROUP)
        data = SimpleNamespace(
            content="hello",
            message_type="text",
            reply_to_message_id=10,
            forwarded_from_id=20,
        )
        message = SimpleNamespace(id=99)
        serialized = SimpleNamespace(id=99)

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)), patch(
            "api.routers.chat.send_group_message",
            new=AsyncMock(return_value=message),
        ) as send_mock, patch(
            "api.routers.chat._serialize_direct_message_with_accountant_contract",
            new=AsyncMock(return_value=serialized),
        ) as serialize_message_mock, patch(
            "api.routers.chat.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 6]),
        ) as users_mock, patch(
            "api.routers.chat.publish_group_message_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await send_room_message(chat_id=70, data=data, current_user=current_user, db=db)

        send_mock.assert_awaited_once_with(
            db,
            chat=chat,
            sender=current_user,
            content="hello",
            message_type="text",
            reply_to_message_id=10,
            forwarded_from_id=20,
        )
        users_mock.assert_awaited_once_with(db, chat_id=70)
        serialize_message_mock.assert_awaited_once_with(db, message, viewer_user_id=5)
        publish_mock.assert_awaited_once_with(
            chat=chat,
            message=message,
            member_user_ids=[5, 6],
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
        )
        self.assertIs(result, serialized)

    async def test_send_room_message_uses_channel_branch_and_publishes(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=71, type=ChatType.CHANNEL)
        data = SimpleNamespace(
            content="notice",
            message_type="text",
            reply_to_message_id=None,
            forwarded_from_id=None,
        )
        message = SimpleNamespace(id=101)
        serialized = SimpleNamespace(id=101)

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)), patch(
            "api.routers.chat.send_channel_message",
            new=AsyncMock(return_value=message),
        ) as send_mock, patch(
            "api.routers.chat._serialize_direct_message_with_accountant_contract",
            new=AsyncMock(return_value=serialized),
        ) as serialize_message_mock, patch(
            "api.routers.chat.load_accountant_chat_identity_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "api.routers.chat.list_active_channel_member_user_ids",
            new=AsyncMock(return_value=[5, 7]),
        ) as users_mock, patch(
            "api.routers.chat.publish_channel_message_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await send_room_message(chat_id=71, data=data, current_user=current_user, db=db)

        send_mock.assert_awaited_once_with(
            db,
            chat=chat,
            sender=current_user,
            content="notice",
            message_type="text",
            reply_to_message_id=None,
            forwarded_from_id=None,
        )
        users_mock.assert_awaited_once_with(db, chat_id=71)
        serialize_message_mock.assert_awaited_once_with(db, message, viewer_user_id=5)
        publish_mock.assert_awaited_once_with(
            chat=chat,
            message=message,
            member_user_ids=[5, 7],
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
        )
        self.assertIs(result, serialized)

    async def test_mark_room_messages_read_uses_group_branch_and_publishes(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=70, type=ChatType.GROUP)
        summary = SimpleNamespace(chat_id=70, reader_id=5, member_user_ids=[5, 6])

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)), patch(
            "api.routers.chat.mark_group_messages_read_with_broadcast",
            new=AsyncMock(return_value=summary),
        ) as mark_mock, patch(
            "api.routers.chat.publish_group_read_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await mark_room_messages_read(chat_id=70, current_user=current_user, db=db)

        mark_mock.assert_awaited_once_with(db, chat=chat, user_id=5)
        publish_mock.assert_awaited_once_with(chat_id=70, reader_id=5, member_user_ids=[5, 6], publisher=unittest.mock.ANY)
        self.assertIsNone(result)

    async def test_mark_room_messages_read_uses_channel_branch_and_publishes(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        chat = SimpleNamespace(id=71, type=ChatType.CHANNEL)
        summary = SimpleNamespace(chat_id=71, reader_id=5, member_user_ids=[5, 7])

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=chat)), patch(
            "api.routers.chat.mark_channel_messages_read_with_broadcast",
            new=AsyncMock(return_value=summary),
        ) as mark_mock, patch(
            "api.routers.chat.publish_channel_read_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await mark_room_messages_read(chat_id=71, current_user=current_user, db=db)

        mark_mock.assert_awaited_once_with(db, chat=chat, user_id=5)
        publish_mock.assert_awaited_once_with(chat_id=71, reader_id=5, member_user_ids=[5, 7], publisher=unittest.mock.ANY)
        self.assertIsNone(result)

    async def test_send_room_activity_signal_uses_group_and_channel_membership_branches(self):
        current_user = SimpleNamespace(id=5, account_name="room-user")
        db = object()
        group_chat = SimpleNamespace(id=70, type=ChatType.GROUP)
        channel_chat = SimpleNamespace(id=71, type=ChatType.CHANNEL)
        activity_data = SimpleNamespace(activity="typing", active=True)

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=group_chat)), patch(
            "api.routers.chat.get_active_group_member_or_403",
            new=AsyncMock(),
        ) as group_member_mock, patch(
            "api.routers.chat.resolve_relation_aware_sender_display_name",
            new=AsyncMock(return_value="دفتر مالک"),
        ) as sender_name_mock, patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 6]),
        ) as users_mock, patch(
            "api.routers.chat.publish_room_activity_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await send_room_activity_signal(chat_id=70, data=activity_data, current_user=current_user, db=db)

        group_member_mock.assert_awaited_once_with(db, chat=group_chat, user_id=5)
        sender_name_mock.assert_awaited_once_with(db, user=current_user)
        users_mock.assert_awaited_once_with(db, chat_id=70)
        publish_mock.assert_awaited_once_with(
            chat=group_chat,
            sender_id=5,
            sender_name="دفتر مالک",
            member_user_ids=[5, 6],
            activity="typing",
            active=True,
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=channel_chat)), patch(
            "api.routers.chat.get_active_channel_member_or_403",
            new=AsyncMock(),
        ) as channel_member_mock, patch(
            "api.routers.chat.resolve_relation_aware_sender_display_name",
            new=AsyncMock(return_value="دفتر مالک"),
        ) as channel_sender_name_mock, patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 7]),
        ) as channel_users_mock, patch(
            "api.routers.chat.publish_room_activity_event",
            new=AsyncMock(),
        ) as channel_publish_mock:
            result = await send_room_activity_signal(chat_id=71, data=activity_data, current_user=current_user, db=db)

        channel_member_mock.assert_awaited_once_with(db, chat=channel_chat, user_id=5)
        channel_sender_name_mock.assert_awaited_once_with(db, user=current_user)
        channel_users_mock.assert_awaited_once_with(db, chat_id=71)
        channel_publish_mock.assert_awaited_once_with(
            chat=channel_chat,
            sender_id=5,
            sender_name="دفتر مالک",
            member_user_ids=[5, 7],
            activity="typing",
            active=True,
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()