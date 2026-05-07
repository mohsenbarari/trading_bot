import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.chat_service import sync_direct_read_state


class FakeDB:
    def __init__(self, *, other_user=None):
        self.execute = AsyncMock()
        self.other_user = other_user
        self.get_calls = []

    async def get(self, model, primary_key):
        self.get_calls.append((getattr(model, "__name__", str(model)), primary_key))
        return self.other_user


class ChatServiceReadStateSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_direct_read_state_returns_none_when_no_conversation_and_no_chat(self):
        reader = SimpleNamespace(id=10)
        db = FakeDB(other_user=SimpleNamespace(id=20))

        with patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=None),
        ), patch(
            "core.services.chat_service._find_existing_direct_chat_id",
            new=AsyncMock(return_value=None),
        ):
            result = await sync_direct_read_state(db, reader=reader, other_user_id=20)

        self.assertIsNone(result)
        db.execute.assert_awaited_once()
        self.assertEqual(db.get_calls, [])

    async def test_sync_direct_read_state_resets_unread_counter_and_returns_when_other_user_missing(self):
        reader = SimpleNamespace(id=10)
        conversation = SimpleNamespace(unread_count_user1=4, unread_count_user2=9)
        db = FakeDB(other_user=None)

        with patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service._find_existing_direct_chat_id",
            new=AsyncMock(return_value=77),
        ):
            result = await sync_direct_read_state(db, reader=reader, other_user_id=20)

        self.assertIs(result, conversation)
        self.assertEqual(conversation.unread_count_user1, 0)
        self.assertEqual(conversation.unread_count_user2, 9)
        self.assertEqual(db.get_calls, [("User", 20)])

    async def test_sync_direct_read_state_updates_reader_cursor_and_repairs_missing_member(self):
        reader = SimpleNamespace(id=20)
        other_user = SimpleNamespace(id=10)
        conversation = SimpleNamespace(unread_count_user1=5, unread_count_user2=6)
        direct_chat = SimpleNamespace(id=77)
        latest_message = SimpleNamespace(id=900, chat_id=None)
        reader_member = SimpleNamespace(last_read_message_id=None, last_read_at=None)
        db = FakeDB(other_user=other_user)
        now = datetime(2026, 5, 8, 0, 15, tzinfo=timezone.utc)

        with patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service._find_existing_direct_chat_id",
            new=AsyncMock(return_value=77),
        ), patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(side_effect=[direct_chat, direct_chat]),
        ) as direct_chat_mock, patch(
            "core.services.chat_service._get_latest_direct_message",
            new=AsyncMock(return_value=latest_message),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(side_effect=[{}, {20: reader_member}]),
        ) as members_mock, patch("core.services.chat_service.datetime") as datetime_mock:
            datetime_mock.now.return_value = now
            result = await sync_direct_read_state(db, reader=reader, other_user_id=10)

        self.assertIs(result, conversation)
        self.assertEqual(conversation.unread_count_user1, 5)
        self.assertEqual(conversation.unread_count_user2, 0)
        self.assertEqual(latest_message.chat_id, 77)
        self.assertEqual(reader_member.last_read_message_id, 900)
        self.assertEqual(reader_member.last_read_at, now)
        self.assertEqual(direct_chat_mock.await_count, 2)
        self.assertEqual(members_mock.await_count, 2)

    async def test_sync_direct_read_state_returns_after_missing_latest_message(self):
        reader = SimpleNamespace(id=10)
        other_user = SimpleNamespace(id=20)
        conversation = SimpleNamespace(unread_count_user1=2, unread_count_user2=1)
        direct_chat = SimpleNamespace(id=55)
        db = FakeDB(other_user=other_user)

        with patch(
            "core.services.chat_service.get_existing_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service._find_existing_direct_chat_id",
            new=AsyncMock(return_value=55),
        ), patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ), patch(
            "core.services.chat_service._get_latest_direct_message",
            new=AsyncMock(return_value=None),
        ):
            result = await sync_direct_read_state(db, reader=reader, other_user_id=20)

        self.assertIs(result, conversation)
        self.assertEqual(conversation.unread_count_user1, 0)
        self.assertEqual(conversation.unread_count_user2, 1)


if __name__ == "__main__":
    unittest.main()