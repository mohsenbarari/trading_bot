import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus
from core.services.chat_room_service import add_group_member, list_group_member_candidates
from models.chat_member import ChatMember


def scalar_result(member):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: member))


class FakeDB:
    def __init__(self, *, user=None, member=None):
        self.get = AsyncMock(return_value=user)
        self._member = member
        self.added = []
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return scalar_result(self._member)

    def add(self, obj):
        self.added.append(obj)


class ChatRoomServiceGroupMemberTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_group_member_rejects_missing_or_deleted_users(self):
        chat = SimpleNamespace(id=10, max_members=50, updated_at=None)
        cases = [
            None,
            SimpleNamespace(id=7, is_deleted=True),
        ]

        for user in cases:
            with self.subTest(user=user):
                db = FakeDB(user=user, member=None)
                with self.assertRaises(HTTPException) as exc_info:
                    await add_group_member(db, chat=chat, user_id=7)
                self.assertEqual(exc_info.exception.status_code, 404)
                self.assertEqual(exc_info.exception.detail, "User not found")

    async def test_add_group_member_returns_unchanged_for_existing_active_member(self):
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB(user=SimpleNamespace(id=7, is_deleted=False), member=member)
        chat = SimpleNamespace(id=10, max_members=50, updated_at=None)

        with patch("core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=4)):
            summary = await add_group_member(db, chat=chat, user_id=7)

        self.assertTrue(summary.unchanged)
        self.assertEqual(summary.role, ChatMemberRole.ADMIN)
        self.assertEqual(summary.member_count, 4)
        self.assertEqual(db.added, [])
        db.commit.assert_not_awaited()

    async def test_add_group_member_enforces_group_member_limit(self):
        db = FakeDB(user=SimpleNamespace(id=7, is_deleted=False), member=None)
        chat = SimpleNamespace(id=10, max_members=2, updated_at=None)

        with patch("core.services.chat_room_service.count_active_chat_members", new=AsyncMock(return_value=2)):
            with self.assertRaises(HTTPException) as exc_info:
                await add_group_member(db, chat=chat, user_id=7)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "Group member limit exceeded (2)")

    async def test_add_group_member_rejects_invalid_customer_group_membership(self):
        db = FakeDB(user=SimpleNamespace(id=7, is_deleted=False), member=None)
        chat = SimpleNamespace(id=10, max_members=50, updated_at=None)

        with patch(
            "core.services.chat_room_service.count_active_chat_members",
            new=AsyncMock(return_value=1),
        ), patch(
            "core.services.chat_room_service.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[4, 19]),
        ), patch(
            "core.services.chat_room_service._validate_customer_group_membership_context",
            new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Customer groups may only include the owner, same-owner accountants, and that customer")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await add_group_member(db, chat=chat, user_id=7)

        self.assertEqual(
            exc_info.exception.detail,
            "Customer groups may only include the owner, same-owner accountants, and that customer",
        )

    async def test_add_group_member_creates_new_member_and_commits(self):
        now = datetime(2026, 5, 7, 22, 0, 0)
        db = FakeDB(user=SimpleNamespace(id=7, is_deleted=False), member=None)
        chat = SimpleNamespace(id=10, max_members=50, updated_at=None)

        with patch(
            "core.services.chat_room_service.count_active_chat_members",
            new=AsyncMock(side_effect=[1, 2]),
        ), patch(
            "core.services.chat_room_service.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.services.chat_room_service._validate_customer_group_membership_context",
            new=AsyncMock(),
        ), patch("core.services.chat_room_service._utcnow", return_value=now):
            summary = await add_group_member(db, chat=chat, user_id=7)

        self.assertFalse(summary.unchanged)
        self.assertEqual(summary.role, ChatMemberRole.MEMBER)
        self.assertEqual(summary.member_count, 2)
        self.assertEqual(chat.updated_at, now)
        self.assertEqual(len(db.added), 1)
        self.assertIsInstance(db.added[0], ChatMember)
        self.assertEqual(db.added[0].membership_status, ChatMembershipStatus.ACTIVE)
        self.assertEqual(db.added[0].joined_at, now)
        self.assertEqual(db.added[0].updated_at, now)
        db.commit.assert_awaited_once()

    async def test_add_group_member_reactivates_existing_inactive_member(self):
        now = datetime(2026, 5, 7, 22, 5, 0)
        member = ChatMember(
            chat_id=10,
            user_id=7,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.LEFT,
            left_at=datetime(2026, 5, 6, 10, 0, 0),
        )
        db = FakeDB(user=SimpleNamespace(id=7, is_deleted=False), member=member)
        chat = SimpleNamespace(id=10, max_members=50, updated_at=None)

        with patch(
            "core.services.chat_room_service.count_active_chat_members",
            new=AsyncMock(side_effect=[1, 2]),
        ), patch(
            "core.services.chat_room_service.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[]),
        ), patch(
            "core.services.chat_room_service._validate_customer_group_membership_context",
            new=AsyncMock(),
        ), patch("core.services.chat_room_service._utcnow", return_value=now):
            summary = await add_group_member(db, chat=chat, user_id=7)

        self.assertFalse(summary.unchanged)
        self.assertEqual(member.role, ChatMemberRole.MEMBER)
        self.assertEqual(member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(member.left_at)
        self.assertEqual(member.joined_at, now)
        self.assertEqual(member.updated_at, now)
        self.assertEqual(chat.updated_at, now)
        db.commit.assert_awaited_once()

    async def test_list_group_member_candidates_filters_by_selected_customer_context(self):
        current_user = SimpleNamespace(id=7)
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(
                        all=lambda: [
                            SimpleNamespace(id=19, account_name="customer", full_name="Customer User", mobile_number="0912", avatar_file_id=None),
                            SimpleNamespace(id=11, account_name="accountant", full_name="Accountant User", mobile_number="0913", avatar_file_id=None),
                            SimpleNamespace(id=30, account_name="outsider", full_name="Outsider User", mobile_number="0914", avatar_file_id=None),
                            SimpleNamespace(id=20, account_name="second-customer", full_name="Second Customer", mobile_number="0915", avatar_file_id=None),
                        ]
                    ),
                    all=lambda: [
                        (19, "Customer User"),
                        (20, "Second Customer"),
                    ],
                )
            )
        )

        with patch(
            "core.services.chat_room_service._load_active_customer_owner_map",
            new=AsyncMock(return_value={19: 7, 20: 99}),
        ), patch(
            "core.services.chat_room_service._load_active_accountant_owner_map",
            new=AsyncMock(return_value={11: 7}),
        ):
            page = await list_group_member_candidates(
                db,
                current_user=current_user,
                selected_user_ids=[19],
            )

        self.assertEqual([item.user_id for item in page.items], [19, 11])
        self.assertEqual(page.total, 2)


if __name__ == "__main__":
    unittest.main()
