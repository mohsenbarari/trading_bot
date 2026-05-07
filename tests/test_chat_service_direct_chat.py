import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType
from core.services.chat_service import get_or_create_direct_chat
from models.chat import Chat
from models.chat_member import ChatMember


class FakeDB:
    def __init__(self, *, get_result=None, new_chat_id=77):
        self.get = AsyncMock(return_value=get_result)
        self._new_chat_id = new_chat_id
        self.added = []
        self.flush = AsyncMock(side_effect=self._flush)

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for obj in reversed(self.added):
            if isinstance(obj, Chat) and obj.id is None:
                obj.id = self._new_chat_id
                return


class GetOrCreateDirectChatTests(unittest.IsolatedAsyncioTestCase):
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
        db.flush.assert_awaited_once()

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


if __name__ == "__main__":
    unittest.main()