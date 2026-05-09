import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType
from core.services.chat_room_service import (
    GROUP_MAX_MEMBERS,
    MANDATORY_CHANNEL_DESCRIPTION,
    MANDATORY_CHANNEL_TITLE,
    _reload_channel_message,
    count_active_chat_admins,
    count_active_chat_members,
    create_group_chat,
    create_optional_channel,
    ensure_mandatory_channel_rollout,
    get_channel_or_404,
    get_group_or_404,
    get_room_or_404,
    list_active_channel_member_user_ids,
    list_active_room_member_user_ids,
    update_group_chat,
    update_optional_channel,
)


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, *, scalar_one_value=None, scalar_one_or_none_value=None, scalars=None):
        self._scalar_one_value = scalar_one_value
        self._scalar_one_or_none_value = scalar_one_or_none_value
        self._scalars = scalars or []

    def scalar_one(self):
        return self._scalar_one_value

    def scalar_one_or_none(self):
        return self._scalar_one_or_none_value

    def scalars(self):
        return FakeScalarResult(self._scalars)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.flush = AsyncMock(side_effect=self._flush)
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 500 + index


class ChatRoomServiceRoomSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_channel_message_returns_first_loaded_message_or_none(self):
        message = SimpleNamespace(id=77)

        self.assertIs(
            await _reload_channel_message(FakeDB([FakeExecuteResult(scalars=[message])]), 77),
            message,
        )
        self.assertIsNone(await _reload_channel_message(FakeDB([FakeExecuteResult(scalars=[])]), 88))

    async def test_room_lookup_helpers_return_rows_or_raise_404(self):
        channel = SimpleNamespace(id=10, type=ChatType.CHANNEL)
        group = SimpleNamespace(id=11, type=ChatType.GROUP)

        self.assertIs(await get_channel_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=channel)]), 10), channel)
        self.assertIs(await get_group_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=group)]), 11), group)
        self.assertIs(await get_room_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=group)]), 11), group)

        with self.assertRaises(HTTPException) as exc_info:
            await get_channel_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=None)]), 10)
        self.assertEqual(exc_info.exception.detail, "Channel not found")

        with self.assertRaises(HTTPException) as exc_info:
            await get_group_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=None)]), 11)
        self.assertEqual(exc_info.exception.detail, "Group not found")

        with self.assertRaises(HTTPException) as exc_info:
            await get_room_or_404(FakeDB([FakeExecuteResult(scalar_one_or_none_value=None)]), 12)
        self.assertEqual(exc_info.exception.detail, "Room not found")

    async def test_count_and_active_member_helpers_shape_scalar_results(self):
        self.assertEqual(await count_active_chat_members(FakeDB([FakeExecuteResult(scalar_one_value=3)]), 10), 3)
        self.assertEqual(await count_active_chat_admins(FakeDB([FakeExecuteResult(scalar_one_value=None)]), 10), 0)
        self.assertEqual(
            await list_active_room_member_user_ids(FakeDB([FakeExecuteResult(scalars=[7, "8"])]), chat_id=10),
            [7, 8],
        )

        with patch(
            "core.services.chat_room_service.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[1, 2]),
        ) as room_mock:
            result = await list_active_channel_member_user_ids(FakeDB(), chat_id=55)

        self.assertEqual(result, [1, 2])
        room_mock.assert_awaited_once_with(unittest.mock.ANY, chat_id=55)

    async def test_create_optional_channel_validates_title_and_adds_creator_admin(self):
        creator = SimpleNamespace(id=4)
        now = datetime(2026, 5, 8, 2, 0, 0)
        db = FakeDB()

        with self.assertRaises(HTTPException) as exc_info:
            await create_optional_channel(db, creator=creator, title="   ")
        self.assertEqual(exc_info.exception.detail, "Channel title is required")

        with patch("core.services.chat_room_service._utcnow", return_value=now):
            chat = await create_optional_channel(
                db,
                creator=creator,
                title="  VIP News  ",
                description="  alerts  ",
            )

        self.assertEqual(chat.id, 501)
        self.assertEqual(chat.type, ChatType.CHANNEL)
        self.assertEqual(chat.title, "VIP News")
        self.assertEqual(chat.description, "alerts")
        self.assertEqual(chat.created_by_id, 4)
        self.assertEqual(chat.updated_at, now)
        self.assertEqual(len(db.added), 2)
        creator_member = db.added[1]
        self.assertEqual(creator_member.chat_id, 501)
        self.assertEqual(creator_member.user_id, 4)
        self.assertEqual(creator_member.role, ChatMemberRole.ADMIN)
        self.assertEqual(creator_member.membership_status, ChatMembershipStatus.ACTIVE)
        db.flush.assert_awaited_once()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(chat)

    async def test_create_group_chat_validates_title_limit_and_selected_members(self):
        creator = SimpleNamespace(id=4)
        db = FakeDB()

        with self.assertRaises(HTTPException) as exc_info:
            await create_group_chat(db, creator=creator, title="   ", member_ids=[])
        self.assertEqual(exc_info.exception.detail, "Group title is required")

        with self.assertRaises(HTTPException) as exc_info:
            await create_group_chat(
                db,
                creator=creator,
                title="Team",
                member_ids=list(range(1, GROUP_MAX_MEMBERS + 5)),
            )
        self.assertEqual(exc_info.exception.detail, f"Group member limit exceeded ({GROUP_MAX_MEMBERS})")

        db = FakeDB([FakeExecuteResult(scalars=[SimpleNamespace(id=10, is_deleted=False)])])
        with self.assertRaises(HTTPException) as exc_info:
            await create_group_chat(db, creator=creator, title="Team", member_ids=[10, 11])
        self.assertEqual(exc_info.exception.detail, "Some selected group members are invalid")

    async def test_create_group_chat_creates_creator_and_normalized_members(self):
        creator = SimpleNamespace(id=4)
        now = datetime(2026, 5, 8, 2, 10, 0)
        users = [SimpleNamespace(id=10, is_deleted=False), SimpleNamespace(id=11, is_deleted=False)]
        db = FakeDB([FakeExecuteResult(scalars=users)])

        with patch("core.services.chat_room_service._utcnow", return_value=now):
            chat = await create_group_chat(
                db,
                creator=creator,
                title="  Team Alpha  ",
                member_ids=[10, 10, -3, creator.id, 11],
            )

        self.assertEqual(chat.id, 501)
        self.assertEqual(chat.type, ChatType.GROUP)
        self.assertEqual(chat.title, "Team Alpha")
        self.assertEqual(chat.max_members, GROUP_MAX_MEMBERS)
        self.assertEqual(len(db.added), 4)
        creator_member = db.added[1]
        self.assertEqual(creator_member.user_id, 4)
        self.assertEqual(creator_member.role, ChatMemberRole.ADMIN)
        self.assertEqual(creator_member.joined_at, now)
        group_member_ids = [db.added[2].user_id, db.added[3].user_id]
        self.assertEqual(group_member_ids, [10, 11])
        self.assertTrue(all(member.role == ChatMemberRole.MEMBER for member in db.added[2:]))
        db.flush.assert_awaited_once()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(chat)

    async def test_update_group_and_optional_channel_validate_and_refresh(self):
        now = datetime(2026, 5, 8, 2, 20, 0)
        db = FakeDB()
        group_chat = SimpleNamespace(title="Old", updated_at=None)

        with self.assertRaises(HTTPException) as exc_info:
            await update_group_chat(db, chat=group_chat, title="   ")
        self.assertEqual(exc_info.exception.detail, "Group title is required")

        with patch("core.services.chat_room_service._utcnow", return_value=now):
            result = await update_group_chat(db, chat=group_chat, title="  New Group  ")
        self.assertIs(result, group_chat)
        self.assertEqual(group_chat.title, "New Group")
        self.assertEqual(group_chat.updated_at, now)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(group_chat)

        db = FakeDB()
        channel = SimpleNamespace(is_system=True, is_mandatory=False, title="Old", description=None, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await update_optional_channel(db, chat=channel, title="Name")
        self.assertEqual(exc_info.exception.detail, "Only optional channels can be updated")

        channel = SimpleNamespace(is_system=False, is_mandatory=False, title="Old", description=None, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await update_optional_channel(db, chat=channel, title="   ")
        self.assertEqual(exc_info.exception.detail, "Channel title is required")

        with patch("core.services.chat_room_service._utcnow", return_value=now):
            result = await update_optional_channel(db, chat=channel, title="  News  ", description="  desk ")
        self.assertIs(result, channel)
        self.assertEqual(channel.title, "News")
        self.assertEqual(channel.description, "desk")
        self.assertEqual(channel.updated_at, now)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(channel)

    async def test_ensure_mandatory_channel_rollout_creates_channel_and_memberships(self):
        admin_user = SimpleNamespace(id=7, is_deleted=False)
        normal_user = SimpleNamespace(id=8, is_deleted=False)
        deleted_user = SimpleNamespace(id=9, is_deleted=True)
        db = FakeDB(
            [
                FakeExecuteResult(scalar_one_or_none_value=None),
                FakeExecuteResult(scalar_one_or_none_value=7),
                FakeExecuteResult(scalar_one_or_none_value=None),
                FakeExecuteResult(scalar_one_or_none_value=None),
                FakeExecuteResult(scalar_one_or_none_value=None),
            ]
        )

        chat = await ensure_mandatory_channel_rollout(db, users=[admin_user, normal_user, deleted_user])

        self.assertEqual(chat.id, 501)
        self.assertEqual(chat.title, MANDATORY_CHANNEL_TITLE)
        self.assertEqual(chat.description, MANDATORY_CHANNEL_DESCRIPTION)
        self.assertTrue(chat.is_system)
        self.assertTrue(chat.is_mandatory)
        self.assertEqual(len(db.added), 4)

        admin_member = db.added[1]
        self.assertEqual(admin_member.user_id, 7)
        self.assertEqual(admin_member.role, ChatMemberRole.ADMIN)
        self.assertEqual(admin_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(admin_member.left_at)

        normal_member = db.added[2]
        self.assertEqual(normal_member.user_id, 8)
        self.assertEqual(normal_member.role, ChatMemberRole.MEMBER)
        self.assertEqual(normal_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(normal_member.left_at)

        deleted_member = db.added[3]
        self.assertEqual(deleted_member.user_id, 9)
        self.assertEqual(deleted_member.role, ChatMemberRole.MEMBER)
        self.assertEqual(deleted_member.membership_status, ChatMembershipStatus.INACTIVE)
        self.assertIsNotNone(deleted_member.left_at)

    async def test_ensure_mandatory_channel_rollout_updates_existing_memberships(self):
        now = datetime(2026, 5, 8, 3, 0, 0)
        chat = SimpleNamespace(id=44)
        admin_user = SimpleNamespace(id=7, is_deleted=False)
        normal_user = SimpleNamespace(id=8, is_deleted=False)
        deleted_user = SimpleNamespace(id=9, is_deleted=True)
        admin_member = SimpleNamespace(
            id=100,
            chat_id=44,
            user_id=7,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
            updated_at=None,
        )
        reactivated_member = SimpleNamespace(
            id=101,
            chat_id=44,
            user_id=8,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.LEFT,
            left_at=datetime(2026, 5, 1, 0, 0, 0),
            updated_at=None,
        )
        deleted_member = SimpleNamespace(
            id=102,
            chat_id=44,
            user_id=9,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=None,
            updated_at=None,
        )
        db = FakeDB(
            [
                FakeExecuteResult(scalar_one_or_none_value=chat),
                FakeExecuteResult(scalar_one_or_none_value=7),
                FakeExecuteResult(scalar_one_or_none_value=admin_member),
                FakeExecuteResult(scalar_one_or_none_value=reactivated_member),
                FakeExecuteResult(scalar_one_or_none_value=deleted_member),
            ]
        )

        with patch("core.services.chat_room_service._utcnow", return_value=now):
            result = await ensure_mandatory_channel_rollout(db, users=[admin_user, normal_user, deleted_user])

        self.assertIs(result, chat)
        self.assertEqual(admin_member.role, ChatMemberRole.ADMIN)
        self.assertEqual(admin_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertEqual(admin_member.updated_at, now)

        self.assertEqual(reactivated_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertIsNone(reactivated_member.left_at)
        self.assertEqual(reactivated_member.updated_at, now)

        self.assertEqual(deleted_member.membership_status, ChatMembershipStatus.INACTIVE)
        self.assertEqual(deleted_member.role, ChatMemberRole.MEMBER)
        self.assertEqual(deleted_member.left_at, now)
        self.assertEqual(deleted_member.updated_at, now)
        self.assertEqual(db.added, [])


if __name__ == "__main__":
    unittest.main()