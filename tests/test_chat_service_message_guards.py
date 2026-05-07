import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from core.enums import ChatMembershipStatus, ChatType, MessageType
from core.services.chat_service import (
    get_deletable_direct_message,
    get_editable_direct_message,
    get_reactable_direct_message,
)
from models.message import Message


class FakeDB:
    def __init__(self, *, message=None, chat=None, member_exists=None):
        self.message = message
        self.chat = chat
        self.member_exists = member_exists
        self.get_calls = []
        self.execute_calls = []

    async def get(self, model, primary_key):
        self.get_calls.append((model, primary_key))
        model_name = getattr(model, "__name__", str(model))
        if model_name == "Message":
            return self.message
        if model_name == "Chat":
            return self.chat
        return None

    async def execute(self, stmt):
        self.execute_calls.append(stmt)
        return SimpleNamespace(scalar_one_or_none=lambda: self.member_exists)


def make_message(**overrides):
    payload = {
        "id": 11,
        "sender_id": 1,
        "receiver_id": 2,
        "content": "hello",
        "message_type": MessageType.TEXT,
        "created_at": datetime.now(timezone.utc),
        "is_deleted": False,
        "forwarded_from_id": None,
        "chat_id": None,
    }
    payload.update(overrides)
    return Message(**payload)


class ChatServiceMessageGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_editable_direct_message_returns_valid_own_text_message(self):
        now = datetime.now(timezone.utc)
        message = make_message(sender_id=7, created_at=now - timedelta(hours=1))
        db = FakeDB(message=message)

        result = await get_editable_direct_message(db, message_id=11, actor_id=7, now=now)

        self.assertIs(result, message)

    async def test_get_editable_direct_message_enforces_all_preconditions(self):
        now = datetime.now(timezone.utc)
        cases = [
            (None, 404, "Message not found"),
            (make_message(sender_id=9), 403, "You can only edit your own messages"),
            (make_message(sender_id=7, message_type=MessageType.IMAGE), 400, "Only text messages can be edited"),
            (make_message(sender_id=7, forwarded_from_id=99), 400, "Forwarded messages cannot be edited"),
            (make_message(sender_id=7, created_at=now - timedelta(hours=49)), 400, "Message is too old to edit"),
        ]

        for message, status_code, detail in cases:
            with self.subTest(status_code=status_code, detail=detail):
                db = FakeDB(message=message)
                with self.assertRaises(HTTPException) as exc_info:
                    await get_editable_direct_message(db, message_id=11, actor_id=7, now=now)
                self.assertEqual(exc_info.exception.status_code, status_code)
                self.assertEqual(exc_info.exception.detail, detail)

    async def test_get_reactable_direct_message_allows_direct_participants(self):
        message = make_message(sender_id=7, receiver_id=8)
        db = FakeDB(message=message)

        result = await get_reactable_direct_message(db, message_id=11, actor_id=8)

        self.assertIs(result, message)

    async def test_get_reactable_direct_message_allows_active_room_member_for_group_or_channel(self):
        message = make_message(sender_id=7, receiver_id=8, chat_id=22)
        chat = SimpleNamespace(id=22, type=ChatType.GROUP, is_deleted=False)
        db = FakeDB(message=message, chat=chat, member_exists=123)

        result = await get_reactable_direct_message(db, message_id=11, actor_id=99)

        self.assertIs(result, message)
        self.assertEqual(len(db.execute_calls), 1)

    async def test_get_reactable_direct_message_rejects_missing_deleted_or_unauthorized_actor(self):
        message = make_message(sender_id=7, receiver_id=8)
        cases = [
            (FakeDB(message=None), 404, "Message not found"),
            (FakeDB(message=make_message(is_deleted=True)), 404, "Message not found"),
            (FakeDB(message=message), 403, "You can only react to messages in your own conversations"),
            (
                FakeDB(
                    message=make_message(sender_id=7, receiver_id=8, chat_id=22),
                    chat=SimpleNamespace(id=22, type=ChatType.CHANNEL, is_deleted=True),
                    member_exists=123,
                ),
                403,
                "You can only react to messages in your own conversations",
            ),
            (
                FakeDB(
                    message=make_message(sender_id=7, receiver_id=8, chat_id=22),
                    chat=SimpleNamespace(id=22, type=ChatType.GROUP, is_deleted=False),
                    member_exists=None,
                ),
                403,
                "You can only react to messages in your own conversations",
            ),
        ]

        for db, status_code, detail in cases:
            with self.subTest(status_code=status_code, detail=detail):
                with self.assertRaises(HTTPException) as exc_info:
                    await get_reactable_direct_message(db, message_id=11, actor_id=99)
                self.assertEqual(exc_info.exception.status_code, status_code)
                self.assertEqual(exc_info.exception.detail, detail)

    async def test_get_deletable_direct_message_returns_valid_own_message(self):
        now = datetime.now(timezone.utc)
        message = make_message(sender_id=7, created_at=now - timedelta(hours=2))
        db = FakeDB(message=message)

        result = await get_deletable_direct_message(db, message_id=11, actor_id=7, now=now)

        self.assertIs(result, message)

    async def test_get_deletable_direct_message_enforces_range_owner_and_age(self):
        now = datetime.now(timezone.utc)
        cases = [
            (0, None, 404, "Message not found"),
            (2147483648, None, 404, "Message not found"),
            (11, None, 404, "Message not found"),
            (11, make_message(sender_id=9), 403, "You can only delete your own messages"),
            (11, make_message(sender_id=7, created_at=now - timedelta(hours=49)), 400, "Message is too old to delete"),
        ]

        for message_id, message, status_code, detail in cases:
            with self.subTest(message_id=message_id, status_code=status_code):
                db = FakeDB(message=message)
                with self.assertRaises(HTTPException) as exc_info:
                    await get_deletable_direct_message(db, message_id=message_id, actor_id=7, now=now)
                self.assertEqual(exc_info.exception.status_code, status_code)
                self.assertEqual(exc_info.exception.detail, detail)


if __name__ == "__main__":
    unittest.main()