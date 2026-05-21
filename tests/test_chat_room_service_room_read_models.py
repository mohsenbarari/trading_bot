import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatMemberRole, ChatType
from core.services.chat_room_service import (
    GROUP_MAX_MEMBERS,
    list_channel_conversations,
    list_channel_messages,
    list_group_conversations,
    list_group_members,
    list_group_messages,
    list_groups_for_user,
)


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalars=None):
        self._rows = rows or []
        self._scalars = scalars or []

    def all(self):
        return self._rows

    def scalars(self):
        return FakeScalarResult(self._scalars)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class ChatRoomServiceRoomReadModelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_groups_for_user_shapes_group_summaries(self):
        created_at = datetime(2026, 5, 1, 9, 0, 0)
        first_chat = SimpleNamespace(
            id=11,
            type=ChatType.GROUP,
            title="Desk",
            description="coordination",
            created_by_id=3,
            max_members=99,
            created_at=created_at,
        )
        second_chat = SimpleNamespace(
            id=12,
            type=ChatType.GROUP,
            title=None,
            description=None,
            created_by_id=4,
            max_members=None,
            created_at=created_at,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(
                    rows=[
                        (first_chat, ChatMemberRole.ADMIN, 8),
                        (second_chat, ChatMemberRole.MEMBER, None),
                    ]
                )
            ]
        )

        items = await list_groups_for_user(db, user_id=7)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, 11)
        self.assertEqual(items[0].title, "Desk")
        self.assertEqual(items[0].member_count, 8)
        self.assertEqual(items[0].max_members, 99)
        self.assertEqual(items[0].current_user_role, ChatMemberRole.ADMIN)
        self.assertEqual(items[1].title, "")
        self.assertEqual(items[1].member_count, 0)
        self.assertEqual(items[1].max_members, GROUP_MAX_MEMBERS)

    async def test_list_group_conversations_shapes_synthetic_rows(self):
        last_message_at = datetime(2026, 5, 3, 10, 0, 0)
        titled_chat = SimpleNamespace(id=21, title="Ops", last_message_at=last_message_at, max_members=100, is_system=False, is_mandatory=False)
        untitled_chat = SimpleNamespace(id=22, title=None, last_message_at=None, max_members=None, is_system=False, is_mandatory=False)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(
                    rows=[
                        (titled_chat, ChatMemberRole.ADMIN, False, False, None, None, "hello", None, 4, 10, 2),
                        (untitled_chat, None, False, False, None, None, None, None, None, 0, 0),
                    ]
                )
            ]
        )

        rows = await list_group_conversations(db, current_user_id=5)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, -21)
        self.assertEqual(rows[0].room_kind, "group")
        self.assertEqual(rows[0].other_user_name, "Ops")
        self.assertEqual(rows[0].unread_count, 4)
        self.assertTrue(rows[0].can_send)
        self.assertEqual(rows[0].member_role, ChatMemberRole.ADMIN.value)
        self.assertEqual(rows[0].member_count, 10)
        self.assertEqual(rows[0].max_members, 100)
        self.assertEqual(rows[0].unread_mention_count, 2)
        self.assertEqual(rows[1].id, -22)
        self.assertEqual(rows[1].other_user_name, "گروه 22")
        self.assertEqual(rows[1].unread_count, 0)
        self.assertEqual(rows[1].unread_mention_count, 0)
        self.assertIsNone(rows[1].member_role)
        self.assertEqual(rows[1].member_count, 0)
        self.assertEqual(rows[1].max_members, GROUP_MAX_MEMBERS)

    async def test_list_channel_conversations_shapes_send_capability_by_role(self):
        last_message_at = datetime(2026, 5, 3, 11, 0, 0)
        admin_chat = SimpleNamespace(id=31, title="Broadcast", last_message_at=last_message_at, is_system=False, is_mandatory=False)
        member_chat = SimpleNamespace(id=32, title=None, last_message_at=None, is_system=True, is_mandatory=True)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(
                    rows=[
                        (admin_chat, ChatMemberRole.ADMIN, False, False, None, None, "notice", None, 2, 100, 1),
                        (member_chat, ChatMemberRole.MEMBER, False, False, None, None, None, None, None, 50, 0),
                    ]
                )
            ]
        )

        rows = await list_channel_conversations(db, current_user_id=5)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, -31)
        self.assertEqual(rows[0].room_kind, "channel")
        self.assertTrue(rows[0].can_send)
        self.assertEqual(rows[0].member_role, ChatMemberRole.ADMIN.value)
        self.assertEqual(rows[0].unread_mention_count, 1)
        self.assertEqual(rows[1].id, -32)
        self.assertEqual(rows[1].other_user_name, "کانال 32")
        self.assertFalse(rows[1].can_send)
        self.assertEqual(rows[1].member_role, ChatMemberRole.MEMBER.value)
        self.assertEqual(rows[1].unread_mention_count, 0)
        self.assertEqual(rows[0].member_count, 100)
        self.assertFalse(rows[0].is_mandatory)
        self.assertEqual(rows[1].member_count, 50)
        self.assertTrue(rows[1].is_system)
        self.assertTrue(rows[1].is_mandatory)

    async def test_list_group_members_shapes_creator_flag(self):
        joined_at = datetime(2026, 5, 2, 8, 0, 0)
        member = SimpleNamespace(role=ChatMemberRole.ADMIN, joined_at=joined_at)
        user = SimpleNamespace(id=50, account_name="owner", full_name="Owner User", mobile_number="0912")
        chat = SimpleNamespace(id=44, created_by_id=50)
        db = FakeDB(execute_results=[FakeExecuteResult(rows=[(member, user)])])

        items = await list_group_members(db, chat=chat)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].user_id, 50)
        self.assertEqual(items[0].role, ChatMemberRole.ADMIN)
        self.assertTrue(items[0].is_group_creator)

    async def test_list_channel_messages_returns_descending_window_reversed(self):
        chat = SimpleNamespace(id=60)
        messages = [SimpleNamespace(id=4), SimpleNamespace(id=3), SimpleNamespace(id=2)]
        db = FakeDB(execute_results=[FakeExecuteResult(scalars=messages)])

        result = await list_channel_messages(db, chat=chat, limit=3)

        self.assertEqual([message.id for message in result], [2, 3, 4])

    async def test_list_channel_messages_around_id_stitches_older_and_newer_windows(self):
        chat = SimpleNamespace(id=60)
        older_desc = [SimpleNamespace(id=9), SimpleNamespace(id=8)]
        newer_asc = [SimpleNamespace(id=10), SimpleNamespace(id=11), SimpleNamespace(id=12)]
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalars=older_desc),
                FakeExecuteResult(scalars=newer_asc),
            ]
        )

        result = await list_channel_messages(db, chat=chat, limit=5, around_id=10)

        self.assertEqual([message.id for message in result], [8, 9, 10, 11, 12])

    async def test_list_group_messages_delegates_to_channel_helper(self):
        chat = SimpleNamespace(id=70)
        sentinel_messages = [SimpleNamespace(id=1)]

        with patch(
            "core.services.chat_room_service.list_channel_messages",
            new=AsyncMock(return_value=sentinel_messages),
        ) as channel_mock:
            result = await list_group_messages(db=FakeDB(), chat=chat, limit=7, before_id=100, around_id=90)

        self.assertIs(result, sentinel_messages)
        channel_mock.assert_awaited_once_with(
            unittest.mock.ANY,
            chat=chat,
            limit=7,
            before_id=100,
            around_id=90,
        )


if __name__ == "__main__":
    unittest.main()