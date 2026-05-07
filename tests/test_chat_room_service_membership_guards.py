import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus
from core.services.chat_room_service import (
    get_active_channel_member_or_403,
    get_active_group_admin_or_403,
    get_active_group_member_or_403,
)
from models.chat_member import ChatMember


def scalar_result(member):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: member))


class FakeDB:
    def __init__(self, member):
        async def execute(_stmt):
            return scalar_result(member)

        self.execute = execute


class ChatRoomServiceMembershipGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_active_channel_member_returns_member(self):
        member = ChatMember(
            chat_id=12,
            user_id=7,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=12)

        result = await get_active_channel_member_or_403(db, chat=chat, user_id=7)

        self.assertIs(result, member)

    async def test_get_active_channel_member_raises_403_when_missing(self):
        db = FakeDB(None)
        chat = SimpleNamespace(id=12)

        with self.assertRaises(HTTPException) as exc_info:
            await get_active_channel_member_or_403(db, chat=chat, user_id=7)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "You are not a member of this channel")

    async def test_get_active_group_member_raises_403_when_missing(self):
        db = FakeDB(None)
        chat = SimpleNamespace(id=18)

        with self.assertRaises(HTTPException) as exc_info:
            await get_active_group_member_or_403(db, chat=chat, user_id=9)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "You are not a member of this group")

    async def test_get_active_group_admin_returns_admin_member(self):
        member = ChatMember(
            chat_id=18,
            user_id=9,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=18)

        result = await get_active_group_admin_or_403(db, chat=chat, user_id=9)

        self.assertIs(result, member)

    async def test_get_active_group_admin_raises_403_for_non_admin_member(self):
        member = ChatMember(
            chat_id=18,
            user_id=9,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(member)
        chat = SimpleNamespace(id=18)

        with self.assertRaises(HTTPException) as exc_info:
            await get_active_group_admin_or_403(db, chat=chat, user_id=9)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "Only group admins can manage this group")


if __name__ == "__main__":
    unittest.main()