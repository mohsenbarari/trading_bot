import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatType
from api.routers.chat import (
    create_group,
    delete_group_member,
    demote_group_admin,
    get_group_member_candidates,
    get_group_detail,
    get_groups,
    patch_group,
    post_group_leave,
    post_group_member,
    promote_group_admin,
)


def mutation_summary(*, role=None, removed=False, left=False, member_count=0, unchanged=False, user_id=9):
    return SimpleNamespace(
        chat_id=77,
        user_id=user_id,
        role=role,
        removed=removed,
        left=left,
        member_count=member_count,
        unchanged=unchanged,
    )


class ChatRouterGroupEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_group_member_candidates_serializes_service_page(self):
        current_user = SimpleNamespace(id=5)
        page = SimpleNamespace(
            items=[
                SimpleNamespace(
                    user_id=9,
                    account_name="member9",
                    full_name="Member Nine",
                    mobile_number="0912",
                    avatar_file_id=None,
                    is_already_member=False,
                )
            ],
            total=1,
            active_total=1,
        )

        with patch("api.routers.chat.is_user_accountant", new=AsyncMock(return_value=True)), patch(
            "api.routers.chat.is_user_customer", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.chat.list_group_member_candidates",
            new=AsyncMock(return_value=page),
        ) as list_mock:
            result = await get_group_member_candidates(
                query_text="ali",
                limit=25,
                offset=5,
                exclude_chat_id=None,
                selected_user_ids=[9],
                current_user=current_user,
                db=object(),
            )

        list_mock.assert_awaited_once_with(
            unittest.mock.ANY,
            current_user=current_user,
            query_text="ali",
            limit=25,
            offset=5,
            exclude_chat_id=None,
            selected_user_ids=[9],
        )
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].user_id, 9)

    async def test_get_groups_serializes_group_room_rows(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        groups = [
            SimpleNamespace(
                id=11,
                type=ChatType.GROUP,
                title="Ops",
                description="desk",
                created_by_id=2,
                member_count=4,
                max_members=50,
                created_at=datetime(2026, 5, 1, 10, 0, 0),
                current_user_role=ChatMemberRole.ADMIN,
            ),
            SimpleNamespace(
                id=12,
                type=ChatType.GROUP,
                title="",
                description=None,
                created_by_id=3,
                member_count=2,
                max_members=50,
                created_at=datetime(2026, 5, 2, 10, 0, 0),
                current_user_role=None,
            ),
        ]

        with patch("api.routers.chat.list_groups_for_user", new=AsyncMock(return_value=groups)) as list_mock:
            result = await get_groups(current_user=current_user, db=db)

        list_mock.assert_awaited_once_with(db, user_id=5)
        self.assertEqual([item.id for item in result], [11, 12])
        self.assertEqual(result[0].current_user_role, "admin")
        self.assertIsNone(result[1].current_user_role)

    async def test_create_group_serializes_created_group(self):
        now = datetime(2026, 5, 3, 10, 0, 0)
        current_user = SimpleNamespace(id=5)
        db = object()
        group = SimpleNamespace(
            id=77,
            title="Alpha",
            description=None,
            created_by_id=5,
            created_at=now,
            max_members=60,
        )
        data = SimpleNamespace(title="Alpha", member_ids=[9, 10])

        with patch("api.routers.chat.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "api.routers.chat.is_user_customer", new=AsyncMock(return_value=False)
        ) as customer_mock, patch(
            "api.routers.chat.create_group_chat", new=AsyncMock(return_value=group)
        ) as create_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=3),
        ) as count_mock:
            result = await create_group(data=data, current_user=current_user, db=db)

        customer_mock.assert_awaited_once_with(db, 5)
        create_mock.assert_awaited_once_with(db, creator=current_user, title="Alpha", member_ids=[9, 10])
        count_mock.assert_awaited_once_with(db, 77)
        self.assertEqual(result.group.id, 77)
        self.assertEqual(result.group.type, ChatType.GROUP)
        self.assertEqual(result.group.member_count, 3)
        self.assertEqual(result.group.current_user_role, "admin")

    async def test_create_group_allows_accountant_users(self):
        now = datetime(2026, 5, 3, 10, 0, 0)
        current_user = SimpleNamespace(id=5)
        group = SimpleNamespace(
            id=91,
            title="Alpha",
            description=None,
            created_by_id=5,
            created_at=now,
            max_members=60,
        )

        with patch("api.routers.chat.is_user_accountant", new=AsyncMock(return_value=True)), patch(
            "api.routers.chat.is_user_customer", new=AsyncMock(return_value=False)
        ), patch(
            "api.routers.chat.create_group_chat", new=AsyncMock(return_value=group)
        ) as create_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=2),
        ):
            result = await create_group(
                data=SimpleNamespace(title="Alpha", member_ids=[9, 10]),
                current_user=current_user,
                db=object(),
            )

        create_mock.assert_awaited_once_with(unittest.mock.ANY, creator=current_user, title="Alpha", member_ids=[9, 10])
        self.assertEqual(result.group.id, 91)

    async def test_create_group_rejects_customer_users(self):
        current_user = SimpleNamespace(id=5)

        with patch("api.routers.chat.is_user_accountant", new=AsyncMock(return_value=False)), patch(
            "api.routers.chat.is_user_customer", new=AsyncMock(return_value=True)
        ), patch(
            "api.routers.chat.create_group_chat", new=AsyncMock()
        ) as create_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await create_group(
                    data=SimpleNamespace(title="Alpha", member_ids=[9, 10]),
                    current_user=current_user,
                    db=object(),
                )

        create_mock.assert_not_called()
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "مشتری در این فاز اجازه ساخت گروه جدید را ندارد",
        )

    async def test_get_group_detail_serializes_group_and_members(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        group = SimpleNamespace(
            id=77,
            type=ChatType.GROUP,
            title="Alpha",
            description="desk",
            created_by_id=4,
            max_members=80,
            created_at=datetime(2026, 5, 3, 10, 0, 0),
        )
        member = SimpleNamespace(role=ChatMemberRole.ADMIN)
        members = [
            SimpleNamespace(
                user_id=5,
                account_name="owner",
                full_name="Owner User",
                mobile_number="0912",
                role=ChatMemberRole.ADMIN,
                joined_at=datetime(2026, 5, 3, 10, 5, 0),
                is_group_creator=False,
            )
        ]

        with patch("api.routers.chat.get_group_or_404", new=AsyncMock(return_value=group)) as group_mock, patch(
            "api.routers.chat.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ) as active_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=7),
        ) as count_mock, patch(
            "api.routers.chat.list_group_members",
            new=AsyncMock(return_value=members),
        ) as members_mock:
            result = await get_group_detail(chat_id=77, current_user=current_user, db=db)

        group_mock.assert_awaited_once_with(db, 77)
        active_mock.assert_awaited_once_with(db, chat=group, user_id=5)
        count_mock.assert_awaited_once_with(db, 77)
        members_mock.assert_awaited_once_with(db, chat=group)
        self.assertEqual(result.group.current_user_role, "admin")
        self.assertEqual(result.group.member_count, 7)
        self.assertEqual(result.members[0].role, "admin")
        self.assertEqual(result.members[0].account_name, "owner")

    async def test_patch_group_uses_admin_guard_and_serializes_result(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        group = SimpleNamespace(
            id=77,
            type=ChatType.GROUP,
            title="Renamed",
            description=None,
            created_by_id=4,
            max_members=50,
            created_at=datetime(2026, 5, 3, 10, 0, 0),
        )
        admin_member = SimpleNamespace(role=ChatMemberRole.ADMIN)
        data = SimpleNamespace(title="Renamed")

        with patch("api.routers.chat.get_group_or_404", new=AsyncMock(return_value=group)) as group_mock, patch(
            "api.routers.chat.get_active_group_admin_or_403",
            new=AsyncMock(return_value=admin_member),
        ) as admin_mock, patch(
            "api.routers.chat.update_group_chat",
            new=AsyncMock(return_value=group),
        ) as update_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=6),
        ) as count_mock:
            result = await patch_group(chat_id=77, data=data, current_user=current_user, db=db)

        group_mock.assert_awaited_once_with(db, 77)
        admin_mock.assert_awaited_once_with(db, chat=group, user_id=5)
        update_mock.assert_awaited_once_with(db, chat=group, title="Renamed")
        count_mock.assert_awaited_once_with(db, 77)
        self.assertEqual(result.member_count, 6)
        self.assertEqual(result.current_user_role, "admin")

    async def test_group_member_mutation_routes_serialize_service_summaries(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        group = SimpleNamespace(id=77)
        data = SimpleNamespace(user_id=9)

        with patch("api.routers.chat.get_group_or_404", new=AsyncMock(return_value=group)), patch(
            "api.routers.chat.get_active_group_admin_or_403",
            new=AsyncMock(return_value=SimpleNamespace(role=ChatMemberRole.ADMIN)),
        ):
            with patch("api.routers.chat.add_group_member", new=AsyncMock(return_value=mutation_summary(role=ChatMemberRole.MEMBER, member_count=8))) as add_mock:
                added = await post_group_member(chat_id=77, data=data, current_user=current_user, db=db)
            with patch("api.routers.chat.remove_group_member", new=AsyncMock(return_value=mutation_summary(role=None, removed=True, member_count=7))) as remove_mock:
                removed = await delete_group_member(chat_id=77, user_id=9, current_user=current_user, db=db)
            with patch("api.routers.chat.update_group_admin_status", new=AsyncMock(return_value=mutation_summary(role=ChatMemberRole.ADMIN, member_count=8))) as promote_mock:
                promoted = await promote_group_admin(chat_id=77, user_id=9, current_user=current_user, db=db)
            with patch("api.routers.chat.update_group_admin_status", new=AsyncMock(return_value=mutation_summary(role=ChatMemberRole.MEMBER, member_count=8))) as demote_mock:
                demoted = await demote_group_admin(chat_id=77, user_id=9, current_user=current_user, db=db)

        add_mock.assert_awaited_once_with(db, chat=group, user_id=9)
        remove_mock.assert_awaited_once_with(db, chat=group, acting_user_id=5, user_id=9)
        promote_mock.assert_awaited_once_with(db, chat=group, user_id=9, make_admin=True)
        demote_mock.assert_awaited_once_with(db, chat=group, user_id=9, make_admin=False)
        self.assertEqual(added.role, "member")
        self.assertTrue(removed.removed)
        self.assertEqual(promoted.role, "admin")
        self.assertEqual(demoted.role, "member")

    async def test_post_group_leave_serializes_leave_summary(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        group = SimpleNamespace(id=77)

        with patch("api.routers.chat.get_group_or_404", new=AsyncMock(return_value=group)) as group_mock, patch(
            "api.routers.chat.leave_group_chat",
            new=AsyncMock(return_value=mutation_summary(role=None, left=True, member_count=6, user_id=5)),
        ) as leave_mock:
            result = await post_group_leave(chat_id=77, current_user=current_user, db=db)

        group_mock.assert_awaited_once_with(db, 77)
        leave_mock.assert_awaited_once_with(db, chat=group, user_id=5)
        self.assertTrue(result.left)
        self.assertEqual(result.member_count, 6)
        self.assertIsNone(result.role)


if __name__ == "__main__":
    unittest.main()