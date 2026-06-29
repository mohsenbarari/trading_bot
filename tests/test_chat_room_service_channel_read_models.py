import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatMemberRole, ChatType
from core.services.chat_room_service import (
    list_channel_invite_candidates,
    list_channel_members,
    list_manageable_channels,
)


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeExecuteResult:
    def __init__(self, *, rows=None, scalars=None, scalar_one_value=None):
        self._rows = rows or []
        self._scalars = scalars or []
        self._scalar_one_value = scalar_one_value

    def all(self):
        return self._rows

    def scalars(self):
        return FakeScalarResult(self._scalars)

    def scalar_one(self):
        return self._scalar_one_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class ChatRoomServiceChannelReadModelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_manageable_channels_shapes_channel_summaries(self):
        created_at = datetime(2026, 5, 1, 10, 0, 0)
        mandatory = SimpleNamespace(
            id=4,
            type=ChatType.CHANNEL,
            title="اطلاع‌رسانی",
            description="کانال اجباری اطلاع‌رسانی سامانه",
            created_by_id=None,
            is_system=True,
            is_mandatory=True,
            created_at=created_at,
        )
        chat = SimpleNamespace(
            id=5,
            type=ChatType.CHANNEL,
            title="VIP",
            description="alerts",
            created_by_id=3,
            is_system=False,
            is_mandatory=False,
            created_at=created_at,
        )
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(
                    rows=[
                        (mandatory, 120),
                        (chat, 12),
                        (SimpleNamespace(**{**chat.__dict__, "id": 6, "title": None}), None),
                    ]
                )
            ]
        )

        summaries = await list_manageable_channels(db)

        self.assertEqual(len(summaries), 3)
        self.assertEqual(summaries[0].id, 4)
        self.assertTrue(summaries[0].is_mandatory)
        self.assertEqual(summaries[0].title, "اطلاع‌رسانی")
        self.assertEqual(summaries[0].member_count, 120)
        self.assertEqual(summaries[1].id, 5)
        self.assertEqual(summaries[1].title, "VIP")
        self.assertEqual(summaries[1].member_count, 12)
        self.assertEqual(summaries[2].id, 6)
        self.assertEqual(summaries[2].title, "")
        self.assertEqual(summaries[2].member_count, 0)

    async def test_list_channel_members_shapes_current_members(self):
        joined_at = datetime(2026, 5, 2, 11, 0, 0)
        member = SimpleNamespace(role=ChatMemberRole.ADMIN, joined_at=joined_at, user_id=8)
        user = SimpleNamespace(id=8, account_name="alpha", full_name="Alpha User", mobile_number="0912")
        chat = SimpleNamespace(id=5, created_by_id=8)
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(rows=[(member, user)]),
                FakeExecuteResult(rows=[]),
            ]
        )

        members = await list_channel_members(db, chat=chat)

        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].user_id, 8)
        self.assertEqual(members[0].account_name, "alpha")
        self.assertEqual(members[0].role, ChatMemberRole.ADMIN)
        self.assertTrue(members[0].is_channel_creator)

    async def test_list_channel_invite_candidates_counts_filters_and_excludes(self):
        users = [
            SimpleNamespace(id=11, account_name="beta", full_name="Beta User", mobile_number="09120000001"),
        ]
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalar_one_value=14),
                FakeExecuteResult(scalar_one_value=1),
                FakeExecuteResult(scalars=users),
            ]
        )

        with patch("core.services.chat_room_service.get_channel_or_404", new=AsyncMock()) as channel_mock:
            page = await list_channel_invite_candidates(
                db,
                query_text="  beta ",
                limit=20,
                offset=5,
                exclude_chat_id=77,
            )

        channel_mock.assert_awaited_once_with(db, 77)
        self.assertEqual(page.active_total, 14)
        self.assertEqual(page.total, 1)
        self.assertEqual([item.user_id for item in page.items], [11])
        self.assertEqual(page.items[0].account_name, "beta")
        self.assertFalse(page.items[0].is_already_member)


if __name__ == "__main__":
    unittest.main()
