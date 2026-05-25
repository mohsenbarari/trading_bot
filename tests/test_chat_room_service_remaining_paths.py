import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, UserRole
from core.services.chat_room_service import (
    _clean_text,
    _has_disallowed_control_chars,
    _unpack_conversation_projection_row,
    build_room_activity_event_payload,
    delete_optional_channel,
    ensure_mandatory_channel_membership,
    ensure_mandatory_channel_rollout,
    get_mandatory_channel,
    leave_channel_chat,
    list_optional_channels,
    publish_room_activity_event,
    set_room_mark_unread_state,
    set_room_mute_state,
    set_room_pin_state,
    sync_mandatory_channel_for_user_state_change,
    update_optional_channel,
)
from models.chat_member import ChatMember


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeExecuteResult:
    def __init__(self, *, values=None, scalar_one_value=None, scalar_one_or_none_value=None):
        self._values = values or []
        self._scalar_one_value = scalar_one_value
        self._scalar_one_or_none_value = scalar_one_or_none_value

    def scalars(self):
        return FakeScalarResult(self._values)

    def scalar_one(self):
        return self._scalar_one_value

    def scalar_one_or_none(self):
        return self._scalar_one_or_none_value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.added = []
        self.commit = AsyncMock()
        self.flush = AsyncMock(side_effect=self._flush)

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError('Unexpected execute() call')
        return self.execute_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, 'id', None) is None:
                obj.id = 700 + index


class ChatRoomServiceRemainingPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_projection_and_mandatory_channel_helpers_cover_remaining_branches(self):
        self.assertIsNone(_clean_text(None))
        self.assertEqual(_clean_text('  a\u0001b  '), 'ab')
        self.assertFalse(_has_disallowed_control_chars(None))
        self.assertFalse(_has_disallowed_control_chars('alpha\tbeta'))
        self.assertTrue(_has_disallowed_control_chars('alpha\u0001beta'))

        ten_tuple = tuple(range(10))
        six_tuple = ('chat', 'admin', 'hello', 'text', 2, 8)
        nine_tuple = ('chat', 'member', True, True, datetime(2026, 5, 10, 10, 0, 0), 'hello', 'text', 1, 3)
        self.assertEqual(_unpack_conversation_projection_row(ten_tuple), ten_tuple + (0,))
        self.assertEqual(
            _unpack_conversation_projection_row(six_tuple),
            ('chat', 'admin', False, False, None, None, 'hello', 'text', 2, 8, 0),
        )
        self.assertEqual(
            _unpack_conversation_projection_row(nine_tuple),
            ('chat', 'member', True, True, nine_tuple[4], None, 'hello', 'text', 1, 3, 0),
        )
        with self.assertRaises(ValueError):
            _unpack_conversation_projection_row((1, 2, 3))

        db = FakeDB([FakeExecuteResult(values=[]), FakeExecuteResult(values=[SimpleNamespace(id=99)])])
        self.assertIsNone(await get_mandatory_channel(db))
        mandatory_chat = await get_mandatory_channel(db)
        self.assertEqual(mandatory_chat.id, 99)

        sentinel_chat = SimpleNamespace(id=500)
        with patch('core.services.chat_room_service.ensure_mandatory_channel_rollout', new=AsyncMock(return_value=sentinel_chat)) as rollout_mock:
            result = await ensure_mandatory_channel_membership(FakeDB(), user=SimpleNamespace(id=5))
        rollout_mock.assert_awaited_once_with(ANY, users=[unittest.mock.ANY])
        self.assertIs(result, sentinel_chat)

        user = SimpleNamespace(id=7, role=UserRole.STANDARD, is_deleted=False, deleted_at=None)
        with patch('core.services.chat_room_service.ensure_mandatory_channel_rollout', new=AsyncMock(return_value='role-changed')) as rollout_mock:
            role_result = await sync_mandatory_channel_for_user_state_change(
                FakeDB(),
                user=user,
                previous_role=UserRole.SUPER_ADMIN,
            )
        rollout_mock.assert_awaited_once()
        self.assertEqual(role_result, 'role-changed')

        with patch('core.services.chat_room_service.ensure_mandatory_channel_membership', new=AsyncMock(return_value='membership-changed')) as membership_mock:
            membership_result = await sync_mandatory_channel_for_user_state_change(
                FakeDB(),
                user=user,
                previous_is_deleted=True,
            )
        membership_mock.assert_awaited_once_with(ANY, user=user)
        self.assertEqual(membership_result, 'membership-changed')

        no_change = await sync_mandatory_channel_for_user_state_change(FakeDB(), user=user)
        self.assertIsNone(no_change)

    async def test_mandatory_rollout_uses_db_users_and_skips_duplicates_or_missing_ids(self):
        user_a = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN, is_deleted=False)
        user_dup = SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN, is_deleted=False)
        user_b = SimpleNamespace(id=8, role=UserRole.STANDARD, is_deleted=False)
        user_missing_id = SimpleNamespace(id=None, role=UserRole.STANDARD, is_deleted=False)
        db = FakeDB([FakeExecuteResult(values=[user_a, user_dup, user_missing_id, user_b])])
        chat = SimpleNamespace(id=501, title='اطلاع‌رسانی', description='کانال اجباری اطلاع‌رسانی سامانه', updated_at=None)

        with patch('core.services.chat_room_service.ensure_mandatory_channel', new=AsyncMock(return_value=chat)), patch(
            'core.services.chat_room_service._get_primary_super_admin_user_id',
            new=AsyncMock(return_value=7),
        ), patch(
            'core.services.chat_room_service._get_latest_chat_member',
            new=AsyncMock(return_value=None),
        ) as latest_member_mock:
            result = await ensure_mandatory_channel_rollout(db)

        self.assertIs(result, chat)
        self.assertEqual(latest_member_mock.await_count, 2)
        self.assertEqual([member.user_id for member in db.added], [7, 8])

    async def test_room_activity_and_channel_wrapper_helpers_cover_remaining_paths(self):
        group_payload = build_room_activity_event_payload(
            chat=SimpleNamespace(id=12, type=ChatType.GROUP, title=None),
            sender_id=5,
            sender_name=None,
            activity='typing',
            active=True,
        )
        self.assertEqual(group_payload['room_kind'], 'group')
        self.assertEqual(group_payload['conversation_title'], 'گروه 12')
        self.assertNotIn('sender_name', group_payload)

        channel_payload = build_room_activity_event_payload(
            chat=SimpleNamespace(id=13, type=ChatType.CHANNEL, title='Desk'),
            sender_id=6,
            sender_name='ali',
            activity='uploading',
            active=False,
        )
        self.assertEqual(channel_payload['room_kind'], 'channel')
        self.assertEqual(channel_payload['sender_name'], 'ali')

        publisher = AsyncMock()
        await publish_room_activity_event(
            chat=SimpleNamespace(id=13, type=ChatType.CHANNEL, title='Desk'),
            sender_id=6,
            sender_name='ali',
            member_user_ids=[6, 7, 8],
            activity='uploading',
            active=False,
            publisher=publisher,
        )
        self.assertEqual(publisher.await_count, 2)
        publisher.assert_any_await(7, 'chat:activity', ANY)
        publisher.assert_any_await(8, 'chat:activity', ANY)

        with patch('core.services.chat_room_service.update_manageable_channel_metadata', new=AsyncMock(return_value='updated')) as update_mock:
            updated = await update_optional_channel(FakeDB(), chat=SimpleNamespace(id=5), title='Desk', description='desc', avatar_file_id='avatar-1')
        update_mock.assert_awaited_once_with(ANY, chat=unittest.mock.ANY, title='Desk', description='desc', avatar_file_id='avatar-1')
        self.assertEqual(updated, 'updated')

        with patch('core.services.chat_room_service.list_manageable_channels', new=AsyncMock(return_value=['channel'])) as list_mock:
            listed = await list_optional_channels(FakeDB())
        list_mock.assert_awaited_once_with(ANY)
        self.assertEqual(listed, ['channel'])

    async def test_channel_delete_and_leave_guards_cover_remaining_branches(self):
        chat = SimpleNamespace(id=9, is_system=True, is_mandatory=False)
        with self.assertRaises(HTTPException) as exc_info:
            await delete_optional_channel(FakeDB(), chat=chat, actor_user_id=1)
        self.assertEqual(exc_info.exception.detail, 'Mandatory/system channels cannot be deleted here')

        mandatory_chat = SimpleNamespace(id=9, is_system=False, is_mandatory=True, created_by_id=1)
        with self.assertRaises(HTTPException) as exc_info:
            await leave_channel_chat(FakeDB(), chat=mandatory_chat, user_id=1)
        self.assertEqual(exc_info.exception.detail, 'Mandatory/system channels cannot be unfollowed')

        regular_chat = SimpleNamespace(id=9, is_system=False, is_mandatory=False, created_by_id=1, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await leave_channel_chat(FakeDB([FakeExecuteResult(values=[])]), chat=regular_chat, user_id=3)
        self.assertEqual(exc_info.exception.detail, 'Channel member not found')

        admin_member = ChatMember(
            chat_id=9,
            user_id=3,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB([
            FakeExecuteResult(values=[admin_member]),
            FakeExecuteResult(scalar_one_value=1),
        ])
        with self.assertRaises(HTTPException) as exc_info:
            await leave_channel_chat(db, chat=regular_chat, user_id=3)
        self.assertEqual(exc_info.exception.detail, 'Channel must keep at least one active admin')

    async def test_room_membership_state_helpers_cover_fallback_and_error_branches(self):
        chat = SimpleNamespace(id=5, updated_at=None)
        with self.assertRaises(HTTPException) as exc_info:
            await set_room_pin_state(FakeDB([FakeExecuteResult(values=[])]), chat=chat, user_id=3, pinned=True)
        self.assertEqual(exc_info.exception.detail, 'Room membership not found')

        now = datetime(2026, 5, 18, 12, 0, 0)
        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB([FakeExecuteResult(values=[member])])
        with patch('core.services.chat_room_service._utcnow', return_value=now), patch(
            'core.services.chat_room_service.get_next_chat_member_pin_order',
            new=AsyncMock(side_effect=AssertionError('missing ordering state')),
        ):
            pinned_member = await set_room_pin_state(db, chat=chat, user_id=3, pinned=True)
        self.assertIs(pinned_member, member)
        self.assertEqual(member.pin_order, 1)
        self.assertFalse(member.is_hidden)
        db.commit.assert_awaited_once()

        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        member.pin_order = 9
        db = FakeDB([FakeExecuteResult(values=[member])])
        unpinned_member = await set_room_pin_state(db, chat=chat, user_id=3, pinned=False)
        self.assertIs(unpinned_member, member)
        self.assertIsNone(member.pin_order)
        db.commit.assert_awaited_once()

        with self.assertRaises(HTTPException) as exc_info:
            await set_room_mute_state(FakeDB([FakeExecuteResult(values=[])]), chat=chat, user_id=3, muted=True)
        self.assertEqual(exc_info.exception.detail, 'Room membership not found')

        with self.assertRaises(HTTPException) as exc_info:
            await set_room_mark_unread_state(FakeDB([FakeExecuteResult(values=[])]), chat=chat, user_id=3, unread=True)
        self.assertEqual(exc_info.exception.detail, 'Room membership not found')

        member = ChatMember(
            chat_id=5,
            user_id=3,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
        db = FakeDB([
            FakeExecuteResult(values=[member]),
            FakeExecuteResult(scalar_one_or_none_value=None),
        ])
        with self.assertRaises(HTTPException) as exc_info:
            await set_room_mark_unread_state(db, chat=chat, user_id=3, unread=True)
        self.assertEqual(exc_info.exception.detail, 'Conversation has no messages to mark unread')


if __name__ == '__main__':
    unittest.main()