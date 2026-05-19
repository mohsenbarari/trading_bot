import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.chat_service import sync_direct_message_threading


class ChatServiceThreadingSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_direct_message_threading_updates_conversation_chat_and_sender_cursor(self):
        created_at = datetime(2026, 5, 7, 23, 30, tzinfo=timezone.utc)
        sender = SimpleNamespace(id=20)
        receiver = SimpleNamespace(id=10)
        message = SimpleNamespace(id=301, chat_id=None, created_at=created_at)
        conversation = SimpleNamespace(
            last_message_id=None,
            last_message_at=None,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        direct_chat = SimpleNamespace(id=77, last_message_id=None, last_message_at=None)
        sender_member = SimpleNamespace(last_read_message_id=None, last_read_at=None)

        with patch(
            "core.services.chat_service.get_or_create_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ) as conversation_mock, patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ) as chat_mock, patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={20: sender_member}),
        ) as members_mock:
            result = await sync_direct_message_threading(
                object(),
                sender=sender,
                receiver=receiver,
                message=message,
            )

        self.assertIs(result, conversation)
        conversation_mock.assert_awaited_once()
        chat_mock.assert_awaited_once()
        members_mock.assert_awaited_once()
        self.assertEqual(message.chat_id, 77)
        self.assertEqual(sender_member.last_read_message_id, 301)
        self.assertEqual(sender_member.last_read_at, created_at)
        self.assertEqual(conversation.last_message_id, 301)
        self.assertEqual(conversation.last_message_at, created_at)
        self.assertEqual(direct_chat.last_message_id, 301)
        self.assertEqual(direct_chat.last_message_at, created_at)
        self.assertEqual(conversation.unread_count_user1, 1)
        self.assertEqual(conversation.unread_count_user2, 0)

    async def test_sync_direct_message_threading_preserves_existing_chat_id_and_uses_now_fallback(self):
        fallback_now = datetime(2026, 5, 7, 23, 45, tzinfo=timezone.utc)
        sender = SimpleNamespace(id=10)
        receiver = SimpleNamespace(id=20)
        message = SimpleNamespace(id=302, chat_id=88, created_at=None)
        conversation = SimpleNamespace(
            last_message_id=None,
            last_message_at=None,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        direct_chat = SimpleNamespace(id=88, last_message_id=None, last_message_at=None)
        sender_member = SimpleNamespace(last_read_message_id=None, last_read_at=None)

        with patch(
            "core.services.chat_service.get_or_create_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: sender_member}),
        ), patch("core.services.chat_service.datetime") as datetime_mock:
            datetime_mock.now.return_value = fallback_now
            result = await sync_direct_message_threading(
                object(),
                sender=sender,
                receiver=receiver,
                message=message,
            )

        self.assertIs(result, conversation)
        self.assertEqual(message.chat_id, 88)
        self.assertEqual(sender_member.last_read_message_id, 302)
        self.assertEqual(sender_member.last_read_at, fallback_now)
        self.assertEqual(conversation.unread_count_user1, 0)
        self.assertEqual(conversation.unread_count_user2, 1)

    async def test_sync_direct_message_threading_handles_missing_sender_member(self):
        created_at = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
        sender = SimpleNamespace(id=10)
        receiver = SimpleNamespace(id=20)
        message = SimpleNamespace(id=303, chat_id=None, created_at=created_at)
        conversation = SimpleNamespace(
            last_message_id=None,
            last_message_at=None,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        direct_chat = SimpleNamespace(id=99, last_message_id=None, last_message_at=None)

        with patch(
            "core.services.chat_service.get_or_create_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={}),
        ):
            await sync_direct_message_threading(
                object(),
                sender=sender,
                receiver=receiver,
                message=message,
            )

        self.assertEqual(message.chat_id, 99)
        self.assertEqual(conversation.last_message_id, 303)
        self.assertEqual(direct_chat.last_message_id, 303)
        self.assertEqual(conversation.unread_count_user2, 1)

    async def test_sync_direct_message_threading_unhides_hidden_members(self):
        created_at = datetime(2026, 5, 8, 0, 5, tzinfo=timezone.utc)
        sender = SimpleNamespace(id=10)
        receiver = SimpleNamespace(id=20)
        message = SimpleNamespace(id=304, chat_id=None, created_at=created_at)
        conversation = SimpleNamespace(
            last_message_id=None,
            last_message_at=None,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        direct_chat = SimpleNamespace(id=100, last_message_id=None, last_message_at=None)
        sender_member = SimpleNamespace(last_read_message_id=None, last_read_at=None, is_hidden=False, hidden_at=None, updated_at=None)
        hidden_member = SimpleNamespace(is_hidden=True, hidden_at=created_at, updated_at=None)

        with patch(
            "core.services.chat_service.get_or_create_direct_conversation",
            new=AsyncMock(return_value=conversation),
        ), patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: sender_member, 20: hidden_member}),
        ):
            await sync_direct_message_threading(object(), sender=sender, receiver=receiver, message=message)

        self.assertFalse(hidden_member.is_hidden)
        self.assertIsNone(hidden_member.hidden_at)
        self.assertEqual(hidden_member.updated_at, created_at)


if __name__ == "__main__":
    unittest.main()