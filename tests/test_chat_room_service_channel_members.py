import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus
from core.services.chat_room_service import bulk_add_channel_members, leave_channel_chat, set_room_pin_state, update_channel_member
from models.chat_member import ChatMember


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, values=None, scalar_one_value=None):
        self._values = values or []
        self._scalar_one_value = scalar_one_value

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one(self):
        return self._scalar_one_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)


class ChatRoomServiceChannelMembersTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_add_channel_members_rejects_mandatory_and_empty_selection(self):
        chat = SimpleNamespace(id=5, is_mandatory=True, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await bulk_add_channel_members(FakeDB(), chat=chat, user_ids=[1], select_all_active_users=False)
        self.assertEqual(exc_info.exception.status_code, 400)

        chat = SimpleNamespace(id=5, is_mandatory=False, updated_at=None)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[])])
        with self.assertRaises(HTTPException) as exc_info:
            await bulk_add_channel_members(db, chat=chat, user_ids=[0, -1, 1, 1], select_all_active_users=False)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "No active users available to add")

    async def test_bulk_add_channel_members_adds_reactivates_and_counts_existing(self):
        now = datetime(2026, 5, 8, 1, 20, 0)
        inactive_member = ChatMember(
            chat_id=5,
            user_id=2,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.LEFT,
        )
        active_member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(
                    values=[
                        SimpleNamespace(id=1, is_deleted=False),
                        SimpleNamespace(id=2, is_deleted=False),
                        SimpleNamespace(id=3, is_deleted=False),
                    ]
                ),
                FakeExecuteResult(values=[inactive_member, active_member]),
            ]
        )
        chat = SimpleNamespace(id=5, is_mandatory=False, updated_at=None)

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=6)
        ):
            summary = await bulk_add_channel_members(
                db,
                chat=chat,
                user_ids=[1, 2, 3],
                select_all_active_users=False,
            )

        self.assertEqual(summary.processed_user_ids, [1, 2, 3])
        self.assertEqual(summary.added_count, 1)
        self.assertEqual(summary.reactivated_count, 1)
        self.assertEqual(summary.already_member_count, 1)
        self.assertEqual(summary.member_count, 6)
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0].user_id, 1)
        self.assertEqual(db.added[0].membership_status, ChatMembershipStatus.ACTIVE)
        self.assertEqual(inactive_member.role, ChatMemberRole.MEMBER)
        self.assertEqual(inactive_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertEqual(inactive_member.joined_at, now)
        self.assertEqual(inactive_member.left_at, None)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()

    async def test_bulk_add_channel_members_uses_all_active_users_when_requested(self):
        now = datetime(2026, 5, 8, 1, 25, 0)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[11, 12]),
                FakeExecuteResult(values=[SimpleNamespace(id=11, is_deleted=False), SimpleNamespace(id=12, is_deleted=False)]),
                FakeExecuteResult(values=[]),
            ]
        )
        chat = SimpleNamespace(id=8, is_mandatory=False, updated_at=None)

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=2)
        ):
            summary = await bulk_add_channel_members(
                db,
                chat=chat,
                user_ids=None,
                select_all_active_users=True,
            )

        self.assertTrue(summary.select_all_active_users)
        self.assertEqual(summary.processed_user_ids, [11, 12])
        self.assertEqual(summary.added_count, 2)
        self.assertEqual(len(db.added), 2)

    async def test_update_channel_member_validates_basic_guards(self):
        chat = SimpleNamespace(id=5, is_system=True, is_mandatory=False, created_by_id=1, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await update_channel_member(FakeDB(), chat=chat, user_id=2, role=ChatMemberRole.ADMIN)
        self.assertEqual(exc_info.exception.status_code, 400)

        chat = SimpleNamespace(id=5, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[])])
        with self.assertRaises(HTTPException) as exc_info:
            await update_channel_member(db, chat=chat, user_id=2, role=ChatMemberRole.ADMIN)
        self.assertEqual(exc_info.exception.status_code, 404)

        member = ChatMember(
            chat_id=5,
            user_id=2,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(values=[member])])
        with self.assertRaises(HTTPException) as exc_info:
            await update_channel_member(db, chat=chat, user_id=2)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "No membership change requested")

    async def test_update_channel_member_protects_creator_and_last_admin(self):
        creator_member = ChatMember(
            chat_id=5,
            user_id=1,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        chat = SimpleNamespace(id=5, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[creator_member])])

        with self.assertRaises(HTTPException) as exc_info:
            await update_channel_member(db, chat=chat, user_id=1, role=ChatMemberRole.MEMBER)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Channel creator must remain an active admin")

        other_admin = ChatMember(
            chat_id=5,
            user_id=2,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(values=[other_admin]),
                FakeExecuteResult(scalar_one_value=1),
            ]
        )
        with self.assertRaises(HTTPException) as exc_info:
            await update_channel_member(db, chat=chat, user_id=2, remove_member=True)
        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Channel must keep at least one active admin")

    async def test_update_channel_member_removes_or_updates_role_and_commits(self):
        now = datetime(2026, 5, 8, 1, 30, 0)
        member = ChatMember(
            chat_id=5,
            user_id=2,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        chat = SimpleNamespace(id=5, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[member])])

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=9)
        ):
            summary = await update_channel_member(db, chat=chat, user_id=2, role=ChatMemberRole.ADMIN)

        self.assertEqual(summary.role, ChatMemberRole.ADMIN)
        self.assertFalse(summary.removed)
        self.assertEqual(summary.member_count, 9)
        self.assertEqual(member.role, ChatMemberRole.ADMIN)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()

        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(values=[member])])
        chat = SimpleNamespace(id=5, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=8)
        ):
            summary = await update_channel_member(db, chat=chat, user_id=3, remove_member=True)

        self.assertTrue(summary.removed)
        self.assertIsNone(summary.role)
        self.assertEqual(member.membership_status, ChatMembershipStatus.REMOVED)
        self.assertEqual(member.left_at, now)

    async def test_leave_channel_chat_and_set_room_pin_state_commit_membership_changes(self):
        now = datetime(2026, 5, 8, 1, 40, 0)
        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        chat = SimpleNamespace(id=5, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        db = FakeDB(execute_results=[FakeExecuteResult(values=[member])])

        with patch("core.services.chat_room_service._utcnow", return_value=now), patch(
            "core.services.chat_room_service.count_active_chat_members",
            new=AsyncMock(return_value=4),
        ):
            summary = await leave_channel_chat(db, chat=chat, user_id=3)

        self.assertTrue(summary.left)
        self.assertEqual(summary.member_count, 4)
        self.assertEqual(member.membership_status, ChatMembershipStatus.LEFT)
        self.assertFalse(member.is_pinned)
        self.assertIsNone(member.pinned_at)
        db.commit.assert_awaited_once()

        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(values=[member])])
        with patch("core.services.chat_room_service._utcnow", return_value=now):
            pinned_member = await set_room_pin_state(db, chat=chat, user_id=3, pinned=True)

        self.assertIs(pinned_member, member)
        self.assertTrue(member.is_pinned)
        self.assertEqual(member.pinned_at, now)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()