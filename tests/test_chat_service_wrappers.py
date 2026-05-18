import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.chat_service import (
    commit_direct_read_state,
    ensure_direct_message_chat_link,
    publish_direct_message_event,
    publish_direct_read_event,
    publish_direct_reaction_event,
    publish_direct_typing_event,
    persist_direct_message_change,
)


class FakeDB:
    def __init__(self, *, sender=None, receiver=None):
        self.sender = sender
        self.receiver = receiver
        self.get_calls = []
        self.commit = AsyncMock()

    async def get(self, model, primary_key):
        self.get_calls.append((getattr(model, "__name__", str(model)), primary_key))
        if primary_key == getattr(self.sender, "id", None):
            return self.sender
        if primary_key == getattr(self.receiver, "id", None):
            return self.receiver
        return None


class ChatServiceWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_direct_message_chat_link_short_circuits_when_chat_id_exists(self):
        message = SimpleNamespace(chat_id=5, sender_id=1, receiver_id=2)
        db = FakeDB()

        result = await ensure_direct_message_chat_link(db, message)

        self.assertIs(result, message)
        self.assertEqual(db.get_calls, [])

    async def test_ensure_direct_message_chat_link_returns_message_when_users_missing(self):
        message = SimpleNamespace(chat_id=None, sender_id=1, receiver_id=2)
        db = FakeDB(sender=None, receiver=None)

        result = await ensure_direct_message_chat_link(db, message)

        self.assertIs(result, message)
        self.assertIsNone(message.chat_id)

    async def test_ensure_direct_message_chat_link_sets_chat_and_last_message_when_needed(self):
        created_at = datetime(2026, 5, 8, 0, 30, tzinfo=timezone.utc)
        message = SimpleNamespace(id=200, chat_id=None, sender_id=1, receiver_id=2, created_at=created_at)
        sender = SimpleNamespace(id=1)
        receiver = SimpleNamespace(id=2)
        direct_chat = SimpleNamespace(id=44, last_message_id=None, last_message_at=None)
        db = FakeDB(sender=sender, receiver=receiver)

        with patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=direct_chat),
        ) as chat_mock:
            result = await ensure_direct_message_chat_link(db, message)

        self.assertIs(result, message)
        chat_mock.assert_awaited_once_with(db, sender, receiver)
        self.assertEqual(message.chat_id, 44)
        self.assertEqual(direct_chat.last_message_id, 200)
        self.assertEqual(direct_chat.last_message_at, created_at)

    async def test_persist_direct_message_change_links_commits_and_reloads(self):
        message = SimpleNamespace(id=300)
        persisted = SimpleNamespace(id=300, content="updated")
        db = FakeDB()

        with patch(
            "core.services.chat_service.ensure_direct_message_chat_link",
            new=AsyncMock(return_value=message),
        ) as link_mock, patch(
            "core.services.chat_service.reload_direct_message",
            new=AsyncMock(return_value=persisted),
        ) as reload_mock:
            result = await persist_direct_message_change(db, message, include_sender=True)

        self.assertIs(result, persisted)
        link_mock.assert_awaited_once_with(db, message)
        db.commit.assert_awaited_once()
        reload_mock.assert_awaited_once_with(db, 300, include_sender=True)

    async def test_commit_direct_read_state_delegates_and_commits(self):
        conversation = SimpleNamespace(id=9)
        reader = SimpleNamespace(id=1)
        db = FakeDB()

        with patch(
            "core.services.chat_service.sync_direct_read_state",
            new=AsyncMock(return_value=conversation),
        ) as sync_mock:
            result = await commit_direct_read_state(db, reader=reader, other_user_id=2)

        self.assertIs(result, conversation)
        sync_mock.assert_awaited_once_with(db, reader=reader, other_user_id=2)
        db.commit.assert_awaited_once()

    async def test_publish_direct_typing_message_reaction_and_read_events_use_expected_targets(self):
        publisher = AsyncMock()
        message = SimpleNamespace(sender_id=10, receiver_id=20)

        await publish_direct_typing_event(receiver_id=20, sender_id=10, publisher=publisher)
        await publish_direct_message_event(
            receiver_id=20,
            message=SimpleNamespace(id=9),
            serializer=lambda msg: {"id": msg.id},
            publisher=publisher,
            sender_name="user-10",
        )
        await publish_direct_reaction_event(
            message=message,
            serializer=lambda msg: {"sender_id": msg.sender_id, "receiver_id": msg.receiver_id},
            publisher=publisher,
        )
        await publish_direct_read_event(other_user_id=20, reader_id=10, publisher=publisher)

        self.assertEqual([call.args[0] for call in publisher.await_args_list], [20, 20, 10, 20, 20])
        self.assertEqual(publisher.await_args_list[0].args[1], "chat:activity")
        self.assertEqual(publisher.await_args_list[1].args[1], "chat:message")
        self.assertEqual(publisher.await_args_list[2].args[1], "chat:reaction")
        self.assertEqual(publisher.await_args_list[3].args[1], "chat:reaction")
        self.assertEqual(publisher.await_args_list[4].args[1], "chat:read")
        self.assertEqual(publisher.await_args_list[1].args[2]["sender_name"], "user-10")
        self.assertEqual(publisher.await_args_list[4].args[2], {"reader_id": 10})

    async def test_publish_direct_typing_event_skips_self_target(self):
        publisher = AsyncMock()

        await publish_direct_typing_event(receiver_id=10, sender_id=10, publisher=publisher)

        publisher.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()