import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.enums import ChatMemberRole, ChatType
from api.routers.chat import (
    bulk_invite_channel_members,
    create_channel,
    get_channel_invite_candidates,
    get_channel_members,
    get_channels,
    patch_channel_member,
    pin_room_conversation,
    unfollow_channel,
    update_channel,
)


def channel_summary(*, role=None, removed=False, user_id=9, member_count=0):
    return SimpleNamespace(
        chat_id=88,
        user_id=user_id,
        role=role,
        removed=removed,
        member_count=member_count,
    )


class ChatRouterChannelEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_channels_serializes_channel_rows(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        channels = [
            SimpleNamespace(
                id=20,
                type=ChatType.CHANNEL,
                title="VIP",
                description="alerts",
                created_by_id=1,
                is_system=False,
                is_mandatory=False,
                member_count=4,
                created_at=datetime(2026, 5, 1, 8, 0, 0),
            )
        ]

        with patch("api.routers.chat.list_manageable_channels", new=AsyncMock(return_value=channels)) as list_mock:
            result = await get_channels(current_user=current_user, db=db)

        list_mock.assert_awaited_once_with(db)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 20)
        self.assertEqual(result[0].title, "VIP")
        self.assertEqual(result[0].member_count, 4)

    async def test_create_channel_serializes_created_channel_and_picker_flag(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        channel = SimpleNamespace(
            id=88,
            title="VIP",
            description="alerts",
            created_by_id=1,
            is_system=False,
            is_mandatory=False,
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        data = SimpleNamespace(title="VIP", description="alerts")

        with patch("api.routers.chat.create_optional_channel", new=AsyncMock(return_value=channel)) as create_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=1),
        ) as count_mock:
            result = await create_channel(data=data, current_user=current_user, db=db)

        create_mock.assert_awaited_once_with(db, creator=current_user, title="VIP", description="alerts")
        count_mock.assert_awaited_once_with(db, 88)
        self.assertEqual(result.channel.id, 88)
        self.assertEqual(result.channel.type, ChatType.CHANNEL)
        self.assertEqual(result.channel.member_count, 1)
        self.assertTrue(result.member_picker_required)

    async def test_update_channel_serializes_updated_channel(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        channel = SimpleNamespace(
            id=88,
            type=ChatType.CHANNEL,
            title="Renamed",
            description="desk",
            created_by_id=1,
            is_system=False,
            is_mandatory=False,
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        data = SimpleNamespace(title="Renamed", description="desk")

        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)) as get_mock, patch(
            "api.routers.chat.update_manageable_channel_metadata",
            new=AsyncMock(return_value=channel),
        ) as update_mock, patch(
            "api.routers.chat.count_active_chat_members",
            new=AsyncMock(return_value=9),
        ) as count_mock:
            result = await update_channel(chat_id=88, data=data, current_user=current_user, db=db)

        get_mock.assert_awaited_once_with(db, 88)
        update_mock.assert_awaited_once_with(db, chat=channel, title="Renamed", description="desk")
        count_mock.assert_awaited_once_with(db, 88)
        self.assertEqual(result.member_count, 9)
        self.assertEqual(result.title, "Renamed")

    async def test_get_channel_members_serializes_member_rows(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        channel = SimpleNamespace(id=88)
        members = [
            SimpleNamespace(
                user_id=7,
                account_name="owner",
                full_name="Owner User",
                mobile_number="0912",
                role=ChatMemberRole.ADMIN,
                joined_at=datetime(2026, 5, 1, 8, 5, 0),
                is_channel_creator=True,
            )
        ]

        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)) as get_mock, patch(
            "api.routers.chat.list_channel_members",
            new=AsyncMock(return_value=members),
        ) as members_mock:
            result = await get_channel_members(chat_id=88, current_user=current_user, db=db)

        get_mock.assert_awaited_once_with(db, 88)
        members_mock.assert_awaited_once_with(db, chat=channel)
        self.assertEqual(result[0].role, "admin")
        self.assertTrue(result[0].is_channel_creator)

    async def test_patch_channel_member_normalizes_role_and_serializes_summary(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        channel = SimpleNamespace(id=88)

        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)) as get_mock, patch(
            "api.routers.chat.update_channel_member",
            new=AsyncMock(return_value=channel_summary(role=ChatMemberRole.ADMIN, member_count=5)),
        ) as update_mock:
            result = await patch_channel_member(
                chat_id=88,
                user_id=9,
                data=SimpleNamespace(role="admin", remove_member=False),
                current_user=current_user,
                db=db,
            )

        get_mock.assert_awaited_once_with(db, 88)
        update_mock.assert_awaited_once_with(
            db,
            chat=channel,
            user_id=9,
            role=ChatMemberRole.ADMIN,
            remove_member=False,
        )
        self.assertEqual(result.role, "admin")
        self.assertEqual(result.member_count, 5)

        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)), patch(
            "api.routers.chat.update_channel_member",
            new=AsyncMock(return_value=channel_summary(role=None, removed=True, member_count=4)),
        ) as remove_mock:
            removed = await patch_channel_member(
                chat_id=88,
                user_id=9,
                data=SimpleNamespace(role=None, remove_member=True),
                current_user=current_user,
                db=db,
            )

        remove_mock.assert_awaited_once_with(
            db,
            chat=channel,
            user_id=9,
            role=None,
            remove_member=True,
        )
        self.assertTrue(removed.removed)
        self.assertIsNone(removed.role)

    async def test_channel_invite_candidates_and_bulk_invite_routes_serialize_service_payloads(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        page = SimpleNamespace(
            items=[
                SimpleNamespace(
                    user_id=5,
                    account_name="beta",
                    full_name="Beta User",
                    mobile_number="0912",
                    is_already_member=False,
                )
            ],
            total=1,
            active_total=10,
        )
        channel = SimpleNamespace(id=88)
        bulk_summary = SimpleNamespace(
            chat_id=88,
            processed_user_ids=[5, 6],
            added_count=1,
            reactivated_count=1,
            already_member_count=0,
            member_count=7,
            select_all_active_users=False,
        )

        with patch(
            "api.routers.chat.list_channel_invite_candidates",
            new=AsyncMock(return_value=page),
        ) as candidates_mock:
            result = await get_channel_invite_candidates(
                q="bet",
                limit=25,
                offset=10,
                exclude_chat_id=88,
                current_user=current_user,
                db=db,
            )

        candidates_mock.assert_awaited_once_with(db, query_text="bet", limit=25, offset=10, exclude_chat_id=88)
        self.assertEqual(result.total, 1)
        self.assertEqual(result.active_total, 10)
        self.assertEqual(result.items[0].account_name, "beta")

        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)) as get_mock, patch(
            "api.routers.chat.bulk_add_channel_members",
            new=AsyncMock(return_value=bulk_summary),
        ) as bulk_mock:
            bulk_result = await bulk_invite_channel_members(
                chat_id=88,
                data=SimpleNamespace(user_ids=[5, 6], select_all_active_users=False),
                current_user=current_user,
                db=db,
            )

        get_mock.assert_awaited_once_with(db, 88)
        bulk_mock.assert_awaited_once_with(db, chat=channel, user_ids=[5, 6], select_all_active_users=False)
        self.assertEqual(bulk_result.processed_user_ids, [5, 6])
        self.assertEqual(bulk_result.added_count, 1)
        self.assertEqual(bulk_result.reactivated_count, 1)

    async def test_pin_room_and_unfollow_channel_routes_serialize_summaries(self):
        current_user = SimpleNamespace(id=1)
        db = object()
        room = SimpleNamespace(id=88, type=ChatType.CHANNEL)
        member = SimpleNamespace(is_pinned=True, pinned_at=datetime(2026, 5, 10, 7, 30, 0))
        summary = channel_summary(role=None, removed=False, user_id=1, member_count=6)
        summary.left = True
        summary.unchanged = False

        with patch("api.routers.chat.get_room_or_404", new=AsyncMock(return_value=room)) as get_room_mock, patch(
            "api.routers.chat.set_room_pin_state",
            new=AsyncMock(return_value=member),
        ) as pin_mock:
            pin_result = await pin_room_conversation(
                chat_id=88,
                data=SimpleNamespace(pinned=True),
                current_user=current_user,
                db=db,
            )

        get_room_mock.assert_awaited_once_with(db, 88)
        pin_mock.assert_awaited_once_with(db, chat=room, user_id=1, pinned=True)
        self.assertEqual(pin_result.target_id, -88)
        self.assertEqual(pin_result.chat_id, 88)
        self.assertTrue(pin_result.is_pinned)

        channel = SimpleNamespace(id=88)
        with patch("api.routers.chat.get_channel_or_404", new=AsyncMock(return_value=channel)) as get_channel_mock, patch(
            "api.routers.chat.leave_channel_chat",
            new=AsyncMock(return_value=summary),
        ) as unfollow_mock:
            unfollow_result = await unfollow_channel(chat_id=88, current_user=current_user, db=db)

        get_channel_mock.assert_awaited_once_with(db, 88)
        unfollow_mock.assert_awaited_once_with(db, chat=channel, user_id=1)
        self.assertEqual(unfollow_result.user_id, 1)
        self.assertEqual(unfollow_result.member_count, 6)
        self.assertTrue(unfollow_result.left)


if __name__ == "__main__":
    unittest.main()