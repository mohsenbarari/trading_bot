import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatType
from api.routers.chat import delete_message, send_message, toggle_message_reaction, update_message


class FakeDB:
    def __init__(self, *, get_map=None):
        self.get_map = dict(get_map or {})

    async def get(self, _model, primary_key):
        return self.get_map.get(primary_key)


class ChatRouterDirectMutationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_prepares_persists_publishes_and_serializes(self):
        current_user = SimpleNamespace(id=5, account_name="alpha")
        db = FakeDB()
        receiver = SimpleNamespace(id=9)
        message = SimpleNamespace(id=101)
        serialized = SimpleNamespace(id=101)
        data = SimpleNamespace(
            receiver_id=9,
            content="hello",
            message_type="text",
            reply_to_message_id=11,
            forwarded_from_id=12,
        )

        with patch("api.routers.chat.prepare_direct_message_send", new=AsyncMock(return_value=(receiver, "prepared"))) as prepare_mock, patch(
            "api.routers.chat.persist_sent_direct_message",
            new=AsyncMock(return_value=message),
        ) as persist_mock, patch(
            "api.routers.chat.publish_direct_message_event",
            new=AsyncMock(),
        ) as publish_mock, patch(
            "api.routers.chat.serialize_direct_message_for_response",
            return_value=serialized,
        ) as serialize_mock:
            result = await send_message(data=data, current_user=current_user, db=db)

        prepare_mock.assert_awaited_once_with(db, sender=current_user, receiver_id=9, content="hello", message_type="text")
        persist_mock.assert_awaited_once_with(
            db,
            sender=current_user,
            receiver=receiver,
            content="prepared",
            message_type="text",
            reply_to_message_id=11,
            forwarded_from_id=12,
        )
        publish_mock.assert_awaited_once_with(
            receiver_id=9,
            message=message,
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
            sender_name="alpha",
        )
        serialize_mock.assert_called_once_with(message, serializer=unittest.mock.ANY)
        self.assertIs(result, serialized)

    async def test_send_message_raises_500_when_persist_returns_none(self):
        current_user = SimpleNamespace(id=5, account_name="alpha")
        db = FakeDB()
        receiver = SimpleNamespace(id=9)
        data = SimpleNamespace(
            receiver_id=9,
            content="hello",
            message_type="text",
            reply_to_message_id=None,
            forwarded_from_id=None,
        )

        with patch("api.routers.chat.prepare_direct_message_send", new=AsyncMock(return_value=(receiver, "prepared"))), patch(
            "api.routers.chat.persist_sent_direct_message",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_message(data=data, current_user=current_user, db=db)
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Failed to persist message")

    async def test_update_and_delete_message_delegate_to_service_helpers(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()
        updated = SimpleNamespace(id=44)
        serialized = SimpleNamespace(id=44)

        with patch("api.routers.chat.apply_direct_message_edit", new=AsyncMock(return_value=updated)) as edit_mock, patch(
            "api.routers.chat.serialize_direct_message_for_response",
            return_value=serialized,
        ) as serialize_mock:
            result = await update_message(
                message_id=44,
                data=SimpleNamespace(content="edited"),
                current_user=current_user,
                db=db,
            )

        edit_mock.assert_awaited_once_with(db, message_id=44, actor_id=5, content="edited")
        serialize_mock.assert_called_once_with(updated, serializer=unittest.mock.ANY)
        self.assertIs(result, serialized)

        with patch("api.routers.chat.apply_direct_message_delete", new=AsyncMock()) as delete_mock:
            result = await delete_message(message_id=45, current_user=current_user, db=db)

        delete_mock.assert_awaited_once_with(db, message_id=45, actor_id=5)
        self.assertIsNone(result)

    async def test_toggle_message_reaction_raises_404_when_service_returns_none(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB()

        with patch("api.routers.chat.apply_direct_message_reaction_toggle", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as exc_info:
                await toggle_message_reaction(
                    message_id=50,
                    data=SimpleNamespace(emoji="🔥"),
                    current_user=current_user,
                    db=db,
                )
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "Message not found")

    async def test_toggle_message_reaction_uses_direct_publish_branch(self):
        current_user = SimpleNamespace(id=5)
        message = SimpleNamespace(id=50, chat_id=None)
        payload = SimpleNamespace(id=50)

        db = FakeDB()

        with patch("api.routers.chat.apply_direct_message_reaction_toggle", new=AsyncMock(return_value=message)) as toggle_mock, patch(
            "api.routers.chat.serialize_direct_message_for_response",
            return_value=payload,
        ) as serialize_mock, patch(
            "api.routers.chat.publish_direct_reaction_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await toggle_message_reaction(
                message_id=50,
                data=SimpleNamespace(emoji="🔥"),
                current_user=current_user,
                db=db,
            )

        toggle_mock.assert_awaited_once_with(db, message_id=50, actor_id=5, emoji="🔥")
        serialize_mock.assert_called_once_with(message, serializer=unittest.mock.ANY)
        publish_mock.assert_awaited_once_with(message=message, serializer=unittest.mock.ANY, publisher=unittest.mock.ANY)
        self.assertIs(result, payload)

    async def test_toggle_message_reaction_uses_channel_and_group_publish_branches(self):
        current_user = SimpleNamespace(id=5)
        channel_message = SimpleNamespace(id=51, chat_id=70)
        group_message = SimpleNamespace(id=52, chat_id=71)
        channel_chat = SimpleNamespace(id=70, type=ChatType.CHANNEL, is_deleted=False)
        group_chat = SimpleNamespace(id=71, type=ChatType.GROUP, is_deleted=False)

        with patch("api.routers.chat.apply_direct_message_reaction_toggle", new=AsyncMock(return_value=channel_message)), patch(
            "api.routers.chat.serialize_direct_message_for_response",
            return_value=SimpleNamespace(id=51),
        ), patch(
            "api.routers.chat.list_active_channel_member_user_ids",
            new=AsyncMock(return_value=[5, 6]),
        ) as channel_users_mock, patch(
            "api.routers.chat.publish_channel_reaction_event",
            new=AsyncMock(),
        ) as channel_publish_mock:
            await toggle_message_reaction(
                message_id=51,
                data=SimpleNamespace(emoji="🔥"),
                current_user=current_user,
                db=FakeDB(get_map={70: channel_chat}),
            )

        channel_users_mock.assert_awaited_once_with(unittest.mock.ANY, chat_id=70)
        channel_publish_mock.assert_awaited_once_with(
            chat=channel_chat,
            message=channel_message,
            member_user_ids=[5, 6],
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
        )

        with patch("api.routers.chat.apply_direct_message_reaction_toggle", new=AsyncMock(return_value=group_message)), patch(
            "api.routers.chat.serialize_direct_message_for_response",
            return_value=SimpleNamespace(id=52),
        ), patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 7]),
        ) as group_users_mock, patch(
            "api.routers.chat.publish_group_reaction_event",
            new=AsyncMock(),
        ) as group_publish_mock:
            await toggle_message_reaction(
                message_id=52,
                data=SimpleNamespace(emoji="🔥"),
                current_user=current_user,
                db=FakeDB(get_map={71: group_chat}),
            )

        group_users_mock.assert_awaited_once_with(unittest.mock.ANY, chat_id=71)
        group_publish_mock.assert_awaited_once_with(
            chat=group_chat,
            message=group_message,
            member_user_ids=[5, 7],
            serializer=unittest.mock.ANY,
            publisher=unittest.mock.ANY,
        )


if __name__ == "__main__":
    unittest.main()