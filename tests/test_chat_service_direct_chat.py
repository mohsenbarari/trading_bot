import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType
from core.services.chat_service import (
    get_existing_direct_chat,
    get_or_create_direct_chat,
    hide_direct_conversation,
    set_direct_chat_mark_unread_state,
    set_direct_chat_mute_state,
    set_direct_chat_pin_state,
)
from models.chat import Chat
from models.chat_member import ChatMember


class FakeDB:
    def __init__(self, *, get_result=None, new_chat_id=77):
        self.get = AsyncMock(return_value=get_result)
        self._new_chat_id = new_chat_id
        self.added = []
        self.flush = AsyncMock(side_effect=self._flush)
        self.commit = AsyncMock()

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for obj in reversed(self.added):
            if isinstance(obj, Chat) and obj.id is None:
                obj.id = self._new_chat_id
                return


class GetOrCreateDirectChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_existing_direct_chat_returns_none_or_loaded_chat(self):
        db = FakeDB(get_result=SimpleNamespace(id=44))

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=None)):
            self.assertIsNone(await get_existing_direct_chat(db, 10, 20))

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=44)):
            chat = await get_existing_direct_chat(db, 10, 20)

        self.assertEqual(chat.id, 44)
        db.get.assert_awaited_with(Chat, 44)

    async def test_creates_chat_and_members_with_expected_statuses(self):
        deleted_at = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
        user1 = SimpleNamespace(id=10, is_deleted=False, deleted_at=None)
        user2 = SimpleNamespace(id=20, is_deleted=True, deleted_at=deleted_at)
        db = FakeDB(new_chat_id=55)

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=None)), patch(
            "core.services.chat_service._load_direct_chat_members", new=AsyncMock(return_value={})
        ):
            chat = await get_or_create_direct_chat(db, user1, user2)

        self.assertEqual(chat.id, 55)
        self.assertEqual(chat.type, ChatType.DIRECT)
        db.get.assert_not_awaited()
        self.assertEqual(db.flush.await_count, 2)

        self.assertEqual(len(db.added), 3)
        self.assertIsInstance(db.added[0], Chat)
        created_members = [obj for obj in db.added if isinstance(obj, ChatMember)]
        self.assertEqual(len(created_members), 2)

        by_user_id = {member.user_id: member for member in created_members}
        self.assertEqual(by_user_id[10].chat_id, 55)
        self.assertEqual(by_user_id[10].role, ChatMemberRole.MEMBER)
        self.assertEqual(by_user_id[10].membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(by_user_id[10].left_at)
        self.assertEqual(by_user_id[20].membership_status, ChatMembershipStatus.INACTIVE)
        self.assertEqual(by_user_id[20].left_at, deleted_at)

    async def test_reuses_existing_chat_and_reactivates_member(self):
        existing_chat = Chat(id=66, type=ChatType.DIRECT)
        reactivated_member = ChatMember(
            chat_id=66,
            user_id=10,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.INACTIVE,
            left_at=datetime(2026, 5, 6, 10, 0, 0),
        )
        other_member = ChatMember(
            chat_id=66,
            user_id=20,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
        )
        user1 = SimpleNamespace(id=10, is_deleted=False, deleted_at=None)
        user2 = SimpleNamespace(id=20, is_deleted=False, deleted_at=None)
        db = FakeDB(get_result=existing_chat)

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=66)), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: reactivated_member, 20: other_member}),
        ):
            chat = await get_or_create_direct_chat(db, user1, user2)

        self.assertIs(chat, existing_chat)
        db.get.assert_awaited_once_with(Chat, 66)
        db.flush.assert_not_awaited()
        self.assertEqual(db.added, [])
        self.assertEqual(reactivated_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(reactivated_member.left_at)

    async def test_existing_member_is_marked_inactive_when_user_is_deleted(self):
        existing_chat = Chat(id=77, type=ChatType.DIRECT)
        deleted_at = datetime(2026, 5, 7, 18, 30, tzinfo=timezone.utc)
        deleted_member = ChatMember(
            chat_id=77,
            user_id=10,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
        )
        other_member = ChatMember(
            chat_id=77,
            user_id=20,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
        )
        user1 = SimpleNamespace(id=10, is_deleted=True, deleted_at=deleted_at)
        user2 = SimpleNamespace(id=20, is_deleted=False, deleted_at=None)
        db = FakeDB(get_result=existing_chat)

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=77)), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: deleted_member, 20: other_member}),
        ):
            chat = await get_or_create_direct_chat(db, user1, user2)

        self.assertIs(chat, existing_chat)
        self.assertEqual(deleted_member.membership_status, ChatMembershipStatus.INACTIVE)
        self.assertEqual(deleted_member.left_at, deleted_at)

    async def test_adds_missing_member_to_existing_chat(self):
        existing_chat = Chat(id=88, type=ChatType.DIRECT)
        existing_member = ChatMember(
            chat_id=88,
            user_id=10,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
        )
        user1 = SimpleNamespace(id=10, is_deleted=False, deleted_at=None)
        user2 = SimpleNamespace(id=20, is_deleted=False, deleted_at=None)
        db = FakeDB(get_result=existing_chat)

        with patch("core.services.chat_service._find_existing_direct_chat_id", new=AsyncMock(return_value=88)), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: existing_member}),
        ):
            chat = await get_or_create_direct_chat(db, user1, user2)

        self.assertIs(chat, existing_chat)
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], ChatMember)
        self.assertEqual(db.added[0].chat_id, 88)
        self.assertEqual(db.added[0].user_id, 20)
        self.assertEqual(db.added[0].membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(db.added[0].left_at)
        db.flush.assert_awaited_once()

    async def test_set_direct_chat_pin_state_updates_actor_membership(self):
        actor = SimpleNamespace(id=10)
        other_user = SimpleNamespace(id=20)
        actor_member = ChatMember(chat_id=77, user_id=10, membership_status=ChatMembershipStatus.ACTIVE)
        db = FakeDB(get_result=other_user)

        with patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=SimpleNamespace(id=77)),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: actor_member}),
        ), patch(
            "core.services.chat_service.get_next_chat_member_pin_order",
            new=AsyncMock(return_value=3),
        ):
            member = await set_direct_chat_pin_state(db, actor=actor, other_user_id=20, pinned=True)

        self.assertIs(member, actor_member)
        self.assertTrue(actor_member.is_pinned)
        self.assertIsNotNone(actor_member.pinned_at)
        self.assertFalse(actor_member.is_hidden)
        db.commit.assert_awaited_once()

    async def test_hide_direct_conversation_marks_member_hidden_and_clears_pin(self):
        actor = SimpleNamespace(id=10)
        other_user = SimpleNamespace(id=20)
        actor_member = ChatMember(
            chat_id=91,
            user_id=10,
            membership_status=ChatMembershipStatus.ACTIVE,
            is_pinned=True,
            pinned_at=datetime(2026, 5, 10, 7, 25, tzinfo=timezone.utc),
        )
        db = FakeDB(get_result=other_user)

        with patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=SimpleNamespace(id=91)),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: actor_member}),
        ):
            member = await hide_direct_conversation(db, actor=actor, other_user_id=20)

        self.assertIs(member, actor_member)
        self.assertTrue(actor_member.is_hidden)
        self.assertIsNotNone(actor_member.hidden_at)
        self.assertFalse(actor_member.is_pinned)
        self.assertIsNone(actor_member.pinned_at)
        db.commit.assert_awaited_once()

    async def test_set_direct_chat_mute_and_mark_unread_state_update_actor_membership(self):
        actor = SimpleNamespace(id=10)
        other_user = SimpleNamespace(id=20)
        actor_member = ChatMember(chat_id=77, user_id=10, membership_status=ChatMembershipStatus.ACTIVE)
        latest_message = SimpleNamespace(id=301)
        db = FakeDB(get_result=other_user)

        with patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=SimpleNamespace(id=77)),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: actor_member}),
        ):
            muted_member = await set_direct_chat_mute_state(db, actor=actor, other_user_id=20, muted=True)

        self.assertIs(muted_member, actor_member)
        self.assertTrue(actor_member.is_muted)
        db.commit.assert_awaited_once()

        db.commit.reset_mock()
        with patch(
            "core.services.chat_service.get_or_create_direct_chat",
            new=AsyncMock(return_value=SimpleNamespace(id=77)),
        ), patch(
            "core.services.chat_service._load_direct_chat_members",
            new=AsyncMock(return_value={10: actor_member}),
        ), patch(
            "core.services.chat_service._get_latest_direct_message",
            new=AsyncMock(return_value=latest_message),
        ):
            unread_member = await set_direct_chat_mark_unread_state(db, actor=actor, other_user_id=20, unread=True)

        self.assertIs(unread_member, actor_member)
        self.assertTrue(actor_member.is_marked_unread)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()