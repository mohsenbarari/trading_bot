import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus
from core.services.chat_room_service import leave_group_chat, remove_group_member
from models.chat_member import ChatMember


def scalar_result(member):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: member))


class FakeDB:
    def __init__(self, member=None):
        self._member = member
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return scalar_result(self._member)


class ChatRoomServiceGroupDepartureTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_group_member_rejects_self_removal_and_missing_member(self):
        chat = SimpleNamespace(id=10, updated_at=None)

        with self.assertRaises(HTTPException) as exc_info:
            await remove_group_member(FakeDB(), chat=chat, acting_user_id=7, user_id=7)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Use the leave endpoint to leave a group")

        with self.assertRaises(HTTPException) as exc_info:
            await remove_group_member(FakeDB(None), chat=chat, acting_user_id=1, user_id=7)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "Group member not found")

    async def test_remove_group_member_prevents_removing_last_admin(self):
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
                await remove_group_member(db, chat=chat, acting_user_id=1, user_id=7)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Group must keep at least one active admin")

    async def test_remove_group_member_marks_member_removed_and_commits(self):
        now = datetime(2026, 5, 8, 1, 0, 0)
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=4)
        ):
            summary = await remove_group_member(db, chat=chat, acting_user_id=1, user_id=7)

        self.assertTrue(summary.removed)
        self.assertFalse(summary.left)
        self.assertEqual(summary.member_count, 4)
        self.assertEqual(member.membership_status, ChatMembershipStatus.REMOVED)
        self.assertEqual(member.left_at, now)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()

    async def test_leave_group_chat_prevents_last_admin_leaving(self):
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service.count_active_chat_admins", new=AsyncMock(return_value=1)):
            with self.assertRaises(HTTPException) as exc_info:
                await leave_group_chat(FakeDB(member), chat=chat, user_id=7)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Group must keep at least one active admin")

    async def test_leave_group_chat_marks_member_left_and_commits(self):
        now = datetime(2026, 5, 8, 1, 10, 0)
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=10, updated_at=None)

        with patch(
            "core.services.chat_room_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ), patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=3)
        ):
            summary = await leave_group_chat(db, chat=chat, user_id=7)

        self.assertFalse(summary.removed)
        self.assertTrue(summary.left)
        self.assertEqual(summary.member_count, 3)
        self.assertEqual(member.membership_status, ChatMembershipStatus.LEFT)
        self.assertEqual(member.left_at, now)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()