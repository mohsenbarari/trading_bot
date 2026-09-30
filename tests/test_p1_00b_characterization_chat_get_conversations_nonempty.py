"""Nonempty characterization tests for api.routers.chat.get_conversations ordering and filtering."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_conversations  # noqa: E402
from api.routers.chat_schemas import ConversationRead  # noqa: E402
from core.enums import MessageType  # noqa: E402
from core.services.chat_room_service import ChannelConversationSummary  # noqa: E402


class TestChatGetConversationsNonempty(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing nonempty get_conversations ordering and filtering."""

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_mixed_direct_room_order_filter_and_ties(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        current_user = SimpleNamespace(id=42)
        mock_db = AsyncMock()
        statement_sentinel = object()

        mock_get_customer_visible_direct_target_ids.return_value = None
        mock_build_direct_conversation_list_stmt.return_value = statement_sentinel

        direct_row_1 = {
            "id": 101,
            "other_user_id": 11,
            "other_user_name": "کاربر ۱۱",
            "avatar_file_id": "avatar-11",
            "other_user_is_deleted": False,
            "last_message_content": "سلام مستقیم ۱",
            "last_message_type": MessageType.TEXT,
            "last_message_at": datetime(2026, 9, 30, 8, 30, 0, tzinfo=timezone.utc),
            "unread_count": 1,
            "other_user_last_seen_at": datetime(2026, 9, 30, 8, 25, 0, tzinfo=timezone.utc),
            "room_kind": "direct",
            "chat_id": 1001,
            "can_send": True,
            "member_role": None,
            "member_count": None,
            "max_members": None,
            "is_system": False,
            "is_mandatory": False,
            "is_muted": False,
            "is_pinned": False,
            "pinned_at": None,
            "pin_order": None,
            "unread_mention_count": 0,
        }
        direct_row_2 = {
            "id": 102,
            "other_user_id": 12,
            "other_user_name": "کاربر ۱۲",
            "avatar_file_id": None,
            "other_user_is_deleted": False,
            "last_message_content": "پیام هم‌زمان مستقیم",
            "last_message_type": MessageType.TEXT,
            "last_message_at": datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc),
            "unread_count": 0,
            "other_user_last_seen_at": None,
            "room_kind": "direct",
            "chat_id": 1002,
            "can_send": True,
            "member_role": None,
            "member_count": None,
            "max_members": None,
            "is_system": False,
            "is_mandatory": False,
            "is_muted": True,
            "is_pinned": True,
            "pinned_at": datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
            "pin_order": 1,
            "unread_mention_count": 0,
        }
        direct_row_3 = {
            "id": 103,
            "other_user_id": 13,
            "other_user_name": "کاربر ۱۳ - حذف‌شده از دامنه",
            "avatar_file_id": None,
            "other_user_is_deleted": False,
            "last_message_content": "پیام نباید در خروجی باشد",
            "last_message_type": MessageType.TEXT,
            "last_message_at": datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc),
            "unread_count": 5,
            "other_user_last_seen_at": None,
            "room_kind": "direct",
            "chat_id": 1003,
            "can_send": True,
            "member_role": None,
            "member_count": None,
            "max_members": None,
            "is_system": False,
            "is_mandatory": False,
            "is_muted": False,
            "is_pinned": False,
            "pinned_at": None,
            "pin_order": None,
            "unread_mention_count": 0,
        }
        direct_row_4 = {
            "id": 104,
            "other_user_id": 14,
            "other_user_name": "کاربر ۱۴ بدون پیام",
            "avatar_file_id": None,
            "other_user_is_deleted": False,
            "last_message_content": None,
            "last_message_type": None,
            "last_message_at": None,
            "unread_count": 0,
            "other_user_last_seen_at": None,
            "room_kind": "direct",
            "chat_id": 1004,
            "can_send": True,
            "member_role": None,
            "member_count": None,
            "max_members": None,
            "is_system": False,
            "is_mandatory": False,
            "is_muted": False,
            "is_pinned": False,
            "pinned_at": None,
            "pin_order": None,
            "unread_mention_count": 0,
        }

        mock_mappings = Mock()
        mock_mappings.all.return_value = [direct_row_1, direct_row_2, direct_row_3, direct_row_4]
        mock_result = Mock()
        mock_result.mappings.return_value = mock_mappings
        mock_db.execute.return_value = mock_result

        mock_filter_customer_scoped_direct_target_ids.return_value = {11, 12, 14}

        room_1 = ChannelConversationSummary(
            id=201,
            other_user_id=0,
            other_user_name="گروه فنی",
            avatar_file_id="room-avatar-201",
            other_user_is_deleted=False,
            last_message_content="پیام هم‌زمان گروه",
            last_message_type=MessageType.TEXT,
            last_message_at=datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc),
            unread_count=3,
            other_user_last_seen_at=None,
            room_kind="group",
            chat_id=2001,
            can_send=True,
            member_role="admin",
            member_count=10,
            max_members=50,
            is_system=False,
            is_mandatory=False,
            is_muted=False,
            is_pinned=False,
            pinned_at=None,
            pin_order=None,
            unread_mention_count=1,
        )
        room_2 = ChannelConversationSummary(
            id=202,
            other_user_id=0,
            other_user_name="کانال اطلاع‌رسانی",
            avatar_file_id=None,
            other_user_is_deleted=False,
            last_message_content="اطلاعیه جدید",
            last_message_type=MessageType.TEXT,
            last_message_at=datetime(2026, 9, 30, 11, 30, 0, tzinfo=timezone.utc),
            unread_count=0,
            other_user_last_seen_at=None,
            room_kind="channel",
            chat_id=2002,
            can_send=False,
            member_role="member",
            member_count=150,
            max_members=1000,
            is_system=False,
            is_mandatory=True,
            is_muted=True,
            is_pinned=True,
            pinned_at=datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc),
            pin_order=2,
            unread_mention_count=0,
        )
        room_3 = ChannelConversationSummary(
            id=203,
            other_user_id=0,
            other_user_name="کانال بدون پیام",
            avatar_file_id=None,
            other_user_is_deleted=False,
            last_message_content=None,
            last_message_type=None,
            last_message_at=None,
            unread_count=0,
            other_user_last_seen_at=None,
            room_kind="channel",
            chat_id=2003,
            can_send=False,
            member_role="member",
            member_count=5,
            max_members=100,
            is_system=False,
            is_mandatory=False,
            is_muted=False,
            is_pinned=False,
            pinned_at=None,
            pin_order=None,
            unread_mention_count=0,
        )

        mock_list_room_conversations.return_value = [room_1, room_2, room_3]

        result = await get_conversations(current_user=current_user, db=mock_db)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_conversation_list_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.mappings.assert_called_once_with()
        mock_mappings.all.assert_called_once_with()
        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={11, 12, 13, 14},
            customer_allowed_target_ids=None,
        )
        mock_list_room_conversations.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 6)
        for item in result:
            self.assertIsInstance(item, ConversationRead)

        self.assertEqual([item.id for item in result], [202, 102, 201, 101, 104, 203])
        self.assertNotIn(103, [item.id for item in result])
        self.assertNotIn(13, [item.other_user_id for item in result])

        self.assertEqual(result[0].id, 202)
        self.assertEqual(result[0].room_kind, "channel")
        self.assertEqual(result[0].chat_id, 2002)
        self.assertFalse(result[0].can_send)
        self.assertTrue(result[0].is_mandatory)
        self.assertTrue(result[0].is_muted)
        self.assertEqual(result[0].member_role, "member")
        self.assertEqual(result[0].last_message_at, datetime(2026, 9, 30, 11, 30, 0, tzinfo=timezone.utc))

        self.assertEqual(result[1].id, 102)
        self.assertEqual(result[1].other_user_id, 12)
        self.assertEqual(result[1].room_kind, "direct")
        self.assertTrue(result[1].is_pinned)
        self.assertEqual(result[1].pin_order, 1)
        self.assertEqual(result[1].last_message_at, datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(result[2].id, 201)
        self.assertEqual(result[2].room_kind, "group")
        self.assertEqual(result[2].member_role, "admin")
        self.assertEqual(result[2].unread_mention_count, 1)
        self.assertEqual(result[2].last_message_at, datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(result[3].id, 101)
        self.assertEqual(result[3].other_user_id, 11)
        self.assertEqual(result[3].other_user_name, "کاربر ۱۱")
        self.assertEqual(result[3].last_message_content, "سلام مستقیم ۱")
        self.assertEqual(result[3].unread_count, 1)
        self.assertEqual(result[3].last_message_at, datetime(2026, 9, 30, 8, 30, 0, tzinfo=timezone.utc))

        self.assertEqual(result[4].id, 104)
        self.assertIsNone(result[4].last_message_at)
        self.assertEqual(result[4].other_user_id, 14)

        self.assertEqual(result[5].id, 203)
        self.assertIsNone(result[5].last_message_at)
        self.assertEqual(result[5].room_kind, "channel")

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_customer_target_scope_and_room_only_order(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        current_user = SimpleNamespace(id=77)
        mock_db = AsyncMock()
        statement_sentinel = object()
        allowed_targets = {88, 99}

        mock_get_customer_visible_direct_target_ids.return_value = allowed_targets
        mock_build_direct_conversation_list_stmt.return_value = statement_sentinel

        direct_row_out_of_scope = {
            "id": 301,
            "other_user_id": 999,
            "other_user_name": "کاربر خارج از دامنه ۹۹۹",
            "avatar_file_id": None,
            "other_user_is_deleted": False,
            "last_message_content": "پیام مستقیم نامعتبر",
            "last_message_type": MessageType.TEXT,
            "last_message_at": datetime(2026, 9, 30, 15, 0, 0, tzinfo=timezone.utc),
            "unread_count": 2,
            "other_user_last_seen_at": None,
            "room_kind": "direct",
            "chat_id": 3001,
            "can_send": True,
            "member_role": None,
            "member_count": None,
            "max_members": None,
            "is_system": False,
            "is_mandatory": False,
            "is_muted": False,
            "is_pinned": False,
            "pinned_at": None,
            "pin_order": None,
            "unread_mention_count": 0,
        }

        mock_mappings = Mock()
        mock_mappings.all.return_value = [direct_row_out_of_scope]
        mock_result = Mock()
        mock_result.mappings.return_value = mock_mappings
        mock_db.execute.return_value = mock_result

        mock_filter_customer_scoped_direct_target_ids.return_value = set()

        room_earlier = ChannelConversationSummary(
            id=402,
            other_user_id=0,
            other_user_name="گروه گفتگوی مشتریان",
            avatar_file_id=None,
            other_user_is_deleted=False,
            last_message_content="پاسخ به پرسش متداول",
            last_message_type=MessageType.TEXT,
            last_message_at=datetime(2026, 9, 30, 9, 15, 0, tzinfo=timezone.utc),
            unread_count=0,
            other_user_last_seen_at=None,
            room_kind="group",
            chat_id=4002,
            can_send=True,
            member_role="member",
            member_count=25,
            max_members=100,
            is_system=False,
            is_mandatory=False,
            is_muted=True,
            is_pinned=False,
            pinned_at=None,
            pin_order=None,
            unread_mention_count=0,
        )
        room_later = ChannelConversationSummary(
            id=401,
            other_user_id=0,
            other_user_name="کانال پشتیبانی مشتریان",
            avatar_file_id="support-channel-avatar",
            other_user_is_deleted=False,
            last_message_content="راهنمای جدید خدمات مشتری",
            last_message_type=MessageType.TEXT,
            last_message_at=datetime(2026, 9, 30, 14, 0, 0, tzinfo=timezone.utc),
            unread_count=4,
            other_user_last_seen_at=None,
            room_kind="channel",
            chat_id=4001,
            can_send=False,
            member_role="member",
            member_count=300,
            max_members=5000,
            is_system=False,
            is_mandatory=True,
            is_muted=False,
            is_pinned=True,
            pinned_at=datetime(2026, 9, 25, 10, 0, 0, tzinfo=timezone.utc),
            pin_order=1,
            unread_mention_count=2,
        )

        mock_list_room_conversations.return_value = [room_earlier, room_later]

        result = await get_conversations(current_user=current_user, db=mock_db)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_conversation_list_stmt.assert_called_once_with(
            current_user.id, allowed_target_ids=allowed_targets
        )
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.mappings.assert_called_once_with()
        mock_mappings.all.assert_called_once_with()
        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={999},
            customer_allowed_target_ids=allowed_targets,
        )
        mock_list_room_conversations.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        for item in result:
            self.assertIsInstance(item, ConversationRead)

        self.assertEqual([item.id for item in result], [401, 402])
        self.assertEqual([item.room_kind for item in result], ["channel", "group"])
        self.assertNotIn(301, [item.id for item in result])
        self.assertNotIn(999, [item.other_user_id for item in result])

        self.assertEqual(result[0].id, 401)
        self.assertEqual(result[0].other_user_name, "کانال پشتیبانی مشتریان")
        self.assertEqual(result[0].room_kind, "channel")
        self.assertEqual(result[0].chat_id, 4001)
        self.assertFalse(result[0].can_send)
        self.assertTrue(result[0].is_mandatory)
        self.assertTrue(result[0].is_pinned)
        self.assertEqual(result[0].pin_order, 1)
        self.assertEqual(result[0].unread_count, 4)
        self.assertEqual(result[0].unread_mention_count, 2)
        self.assertEqual(result[0].last_message_at, datetime(2026, 9, 30, 14, 0, 0, tzinfo=timezone.utc))

        self.assertEqual(result[1].id, 402)
        self.assertEqual(result[1].other_user_name, "گروه گفتگوی مشتریان")
        self.assertEqual(result[1].room_kind, "group")
        self.assertEqual(result[1].chat_id, 4002)
        self.assertTrue(result[1].can_send)
        self.assertFalse(result[1].is_mandatory)
        self.assertTrue(result[1].is_muted)
        self.assertEqual(result[1].unread_count, 0)
        self.assertEqual(result[1].unread_mention_count, 0)
        self.assertEqual(result[1].last_message_at, datetime(2026, 9, 30, 9, 15, 0, tzinfo=timezone.utc))

        self.assertGreater(result[0].last_message_at, result[1].last_message_at)


if __name__ == "__main__":
    unittest.main()
