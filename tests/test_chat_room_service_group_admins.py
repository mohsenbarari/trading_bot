import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus
from core.services.chat_room_service import update_group_admin_status
from models.chat_member import ChatMember


def scalar_result(member):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: member))


class FakeDB:
    def __init__(self, member):
        self._member = member
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return scalar_result(self._member)


class ChatRoomServiceGroupAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_group_admin_status_raises_404_for_missing_member(self):
        db = FakeDB(None)
        chat = SimpleNamespace(id=10, updated_at=None)

        with self.assertRaises(HTTPException) as exc_info:
            await update_group_admin_status(db, chat=chat, user_id=7, make_admin=True)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "Group member not found")

    async def test_update_group_admin_status_returns_unchanged_when_role_already_matches(self):
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch("core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=4)):
            summary = await update_group_admin_status(db, chat=chat, user_id=7, make_admin=True)

        self.assertTrue(summary.unchanged)
        self.assertEqual(summary.role, ChatMemberRole.ADMIN)
        self.assertEqual(summary.member_count, 4)
        db.commit.assert_not_awaited()

    async def test_update_group_admin_status_prevents_demoting_last_admin(self):
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch("core.services.chat_room_service.count_active_chat_admins", new=AsyncMock(return_value=1)):
            with self.assertRaises(HTTPException) as exc_info:
                await update_group_admin_status(db, chat=chat, user_id=7, make_admin=False)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Group must keep at least one active admin")

    async def test_update_group_admin_status_promotes_member_and_commits(self):
        now = datetime(2026, 5, 7, 23, 0, 0)
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=5)
        ):
            summary = await update_group_admin_status(db, chat=chat, user_id=7, make_admin=True)

        self.assertFalse(summary.unchanged)
        self.assertEqual(summary.role, ChatMemberRole.ADMIN)
        self.assertEqual(summary.member_count, 5)
        self.assertEqual(member.role, ChatMemberRole.ADMIN)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()

    async def test_update_group_admin_status_demotes_admin_when_other_admin_exists(self):
        now = datetime(2026, 5, 7, 23, 5, 0)
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_admins", new=AsyncMock(return_value=2)
        ), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=6)
        ):
            summary = await update_group_admin_status(db, chat=chat, user_id=7, make_admin=False)

        self.assertFalse(summary.unchanged)
        self.assertEqual(summary.role, ChatMemberRole.MEMBER)
        self.assertEqual(summary.member_count, 6)
        self.assertEqual(member.role, ChatMemberRole.MEMBER)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()