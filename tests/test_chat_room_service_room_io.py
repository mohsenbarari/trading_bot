import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, MessageType
from core.services.chat_room_service import (
    mark_channel_messages_read,
    mark_channel_messages_read_with_broadcast,
    mark_group_messages_read,
    mark_group_messages_read_with_broadcast,
    send_channel_message,
    send_group_message,
)


class FakeExecuteResult:
    def __init__(self, *, scalar_one_or_none_value=None):
        self._scalar_one_or_none_value = scalar_one_or_none_value

    def scalar_one_or_none(self):
        return self._scalar_one_or_none_value


class FakeDB:
    def __init__(self, *, get_map=None, execute_results=None):
        self.get_map = dict(get_map or {})
        self.execute_results = list(execute_results or [])
        self.added = []
        self.flush = AsyncMock(side_effect=self._flush)
        self.commit = AsyncMock()

    async def get(self, _model, primary_key):
        return self.get_map.get(primary_key)

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 900 + index

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class ChatRoomServiceRoomIOTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_channel_message_requires_admin_and_valid_reply_target(self):
        member = SimpleNamespace(role=ChatMemberRole.MEMBER)
        chat = SimpleNamespace(id=50, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=7)

        with patch(
            "core.services.chat_room_service.get_active_channel_member_or_403",
            new=AsyncMock(return_value=member),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_channel_message(
                    FakeDB(),
                    chat=chat,
                    sender=sender,
                    content="hi",
                    message_type=MessageType.TEXT,
                )
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Only channel admins can post messages")

        member = SimpleNamespace(role=ChatMemberRole.ADMIN)
        db = FakeDB(get_map={12: SimpleNamespace(id=12, chat_id=999)})
        with patch(
            "core.services.chat_room_service.get_active_channel_member_or_403",
            new=AsyncMock(return_value=member),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_channel_message(
                    db,
                    chat=chat,
                    sender=sender,
                    content="hi",
                    message_type=MessageType.TEXT,
                    reply_to_message_id=12,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Reply target is not part of this channel")

    async def test_send_channel_message_persists_updates_chat_and_reloads(self):
        now = datetime(2026, 5, 8, 1, 40, 0)
        member = SimpleNamespace(
            role=ChatMemberRole.ADMIN,
            last_read_message_id=None,
            last_read_at=None,
            updated_at=None,
        )
        chat = SimpleNamespace(id=50, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=7)
        db = FakeDB()
        reloaded = SimpleNamespace(id=901, chat_id=50)

        with patch(
            "core.services.chat_room_service.get_active_channel_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service._reload_channel_message",
            new=AsyncMock(return_value=reloaded),
        ) as reload_mock:
            result = await send_channel_message(
                db,
                chat=chat,
                sender=sender,
                content="hello",
                message_type=MessageType.TEXT,
                forwarded_from_id=33,
            )

        self.assertIs(result, reloaded)
        self.assertEqual(len(db.added), 1)
        message = db.added[0]
        self.assertEqual(message.sender_id, 7)
        self.assertEqual(message.receiver_id, 7)
        self.assertEqual(message.chat_id, 50)
        self.assertTrue(message.is_read)
        self.assertEqual(message.created_at, now)
        self.assertEqual(chat.last_message_id, 901)
        self.assertEqual(chat.last_message_at, now)
        self.assertEqual(chat.updated_at, now)
        self.assertEqual(member.last_read_message_id, 901)
        self.assertEqual(member.last_read_at, now)
        self.assertEqual(member.updated_at, now)
        db.flush.assert_awaited_once()
        db.commit.assert_awaited_once()
        reload_mock.assert_awaited_once_with(db, 901)

    async def test_send_channel_message_raises_when_reload_fails(self):
        member = SimpleNamespace(role=ChatMemberRole.ADMIN, last_read_message_id=None, last_read_at=None, updated_at=None)
        chat = SimpleNamespace(id=50, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=7)
        db = FakeDB()

        with patch(
            "core.services.chat_room_service.get_active_channel_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch(
            "core.services.chat_room_service._reload_channel_message",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_channel_message(
                    db,
                    chat=chat,
                    sender=sender,
                    content="hello",
                    message_type=MessageType.TEXT,
                )
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Failed to reload channel message")

    async def test_send_group_message_validates_reply_target_and_reloads(self):
        now = datetime(2026, 5, 8, 1, 45, 0)
        member = SimpleNamespace(last_read_message_id=None, last_read_at=None, updated_at=None)
        chat = SimpleNamespace(id=70, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=9)

        db = FakeDB(get_map={12: SimpleNamespace(id=12, chat_id=999)})
        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_group_message(
                    db,
                    chat=chat,
                    sender=sender,
                    content="hi",
                    message_type=MessageType.TEXT,
                    reply_to_message_id=12,
                )
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Reply target is not part of this group")

        db = FakeDB(get_map={12: SimpleNamespace(id=12, chat_id=70)})
        reloaded = SimpleNamespace(id=901, chat_id=70)
        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service._reload_channel_message",
            new=AsyncMock(return_value=reloaded),
        ) as reload_mock:
            result = await send_group_message(
                db,
                chat=chat,
                sender=sender,
                content="hi",
                message_type=MessageType.TEXT,
                reply_to_message_id=12,
            )

        self.assertIs(result, reloaded)
        self.assertEqual(chat.last_message_id, 901)
        self.assertEqual(member.last_read_message_id, 901)
        reload_mock.assert_awaited_once_with(db, 901)

    async def test_send_group_message_raises_when_reload_fails(self):
        member = SimpleNamespace(last_read_message_id=None, last_read_at=None, updated_at=None)
        chat = SimpleNamespace(id=70, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=9)
        db = FakeDB()

        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch(
            "core.services.chat_room_service._reload_channel_message",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await send_group_message(
                    db,
                    chat=chat,
                    sender=sender,
                    content="hi",
                    message_type=MessageType.TEXT,
                )
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Failed to reload group message")

    async def test_mark_channel_messages_read_updates_member_and_broadcasts(self):
        now = datetime(2026, 5, 8, 1, 50, 0)
        member = SimpleNamespace(last_read_message_id=None, last_read_at=None, updated_at=None)
        chat = SimpleNamespace(id=50)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_or_none_value=321)])

        with patch(
            "core.services.chat_room_service.get_active_channel_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service._utcnow", return_value=now):
            await mark_channel_messages_read(db, chat=chat, user_id=7)

        self.assertEqual(member.last_read_message_id, 321)
        self.assertEqual(member.last_read_at, now)
        self.assertEqual(member.updated_at, now)
        db.commit.assert_awaited_once()

        with patch(
            "core.services.chat_room_service.mark_channel_messages_read",
            new=AsyncMock(),
        ) as mark_mock, patch(
            "core.services.chat_room_service.list_active_channel_member_user_ids",
            new=AsyncMock(return_value=[7, 8, 9]),
        ):
            summary = await mark_channel_messages_read_with_broadcast(FakeDB(), chat=chat, user_id=7)

        mark_mock.assert_awaited_once()
        self.assertEqual(summary.member_user_ids, [7, 8, 9])
        self.assertEqual(summary.reader_id, 7)
        self.assertEqual(summary.chat_id, 50)

    async def test_mark_group_messages_read_updates_member_and_broadcasts(self):
        now = datetime(2026, 5, 8, 1, 55, 0)
        member = SimpleNamespace(last_read_message_id=None, last_read_at=None, updated_at=None)
        chat = SimpleNamespace(id=70)
        db = FakeDB(execute_results=[FakeExecuteResult(scalar_one_or_none_value=654)])

        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service._utcnow", return_value=now):
            await mark_group_messages_read(db, chat=chat, user_id=9)

        self.assertEqual(member.last_read_message_id, 654)
        self.assertEqual(member.last_read_at, now)
        self.assertEqual(member.updated_at, now)
        db.commit.assert_awaited_once()

        with patch(
            "core.services.chat_room_service.mark_group_messages_read",
            new=AsyncMock(),
        ) as mark_mock, patch(
            "core.services.chat_room_service.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[9, 10]),
        ):
            summary = await mark_group_messages_read_with_broadcast(FakeDB(), chat=chat, user_id=9)

        mark_mock.assert_awaited_once()
        self.assertEqual(summary.member_user_ids, [9, 10])
        self.assertEqual(summary.reader_id, 9)
        self.assertEqual(summary.chat_id, 70)


if __name__ == "__main__":
    unittest.main()