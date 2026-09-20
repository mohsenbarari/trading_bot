"""Characterization tests for chat activity signal success handlers.

Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md.
Assignment: WA-P1-00B-FREEZE-20260904-001.

Stage lifecycle state: P1-00 COMPLETE / P1-00b IN_PROGRESS / BLOCKED_EXPLICIT.

Evidence limits:
These tests characterize current success behavior for send_direct_activity_signal
and send_room_activity_signal endpoints strictly at handler boundaries. Scope
ends at mocked permission, room lookup, group membership, sender-name resolution,
member listing, and publish_user_event boundaries. Underlying database queries,
Redis pub/sub delivery, and FastAPI dependency injection mechanics are mocked
rather than executed or claimed. This module records current baseline behavior without
claiming seed closure, blocker resolution, real publication, Stage acceptance, or tests
already run.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, call, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import send_direct_activity_signal, send_room_activity_signal
from api.routers.chat_schemas import DirectChatActivitySignal, RoomChatActivitySignal
from core.enums import ChatType
from models.chat import Chat
from models.user import User

fence = install_p1_00_network_fence()


class ChatActivitySuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.db = object()
        self.current_user = User(
            id=204,
            account_name="account_sender_204",
            mobile_number="+989120000204",
            normalized_account_name="account_sender_204",
            normalized_mobile_number="+989120000204",
            full_name="Sender Full Name 204",
            address="Tehran, Iran",
        )
        self.denied_connects_before = len(fence["denied_connects"])
        self.live_redis_constructs_before = len(fence["live_redis_constructs"])

    async def test_send_direct_activity_signal_authorized_publishes_exact_event(self) -> None:
        receiver_id = 104
        sender_id = self.current_user.id
        self.assertNotEqual(receiver_id, sender_id)

        resolved_sender_name = "resolved_display_name_204"
        self.assertNotEqual(self.current_user.account_name, resolved_sender_name)
        self.assertNotEqual(self.current_user.full_name, resolved_sender_name)

        signal_data = DirectChatActivitySignal(
            receiver_id=receiver_id,
            activity="uploading_file",
            active=False,
        )

        expected_payload = {
            "room_kind": "direct",
            "sender_id": sender_id,
            "activity": "uploading_file",
            "active": False,
            "sender_name": resolved_sender_name,
        }

        awaited_events: list[str] = []

        async def fake_ensure_customer_access(*args, **kwargs):
            awaited_events.append("permission")
            return None

        async def fake_resolve_direct_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            return resolved_sender_name

        async def fake_publish_user_event(*args, **kwargs):
            awaited_events.append("publish_user_event")
            return None

        permission_mock = AsyncMock(side_effect=fake_ensure_customer_access)
        resolver_mock = AsyncMock(side_effect=fake_resolve_direct_name)
        publisher_mock = AsyncMock(side_effect=fake_publish_user_event)

        with patch(
            "api.routers.chat._ensure_customer_can_access_direct_target",
            new=permission_mock,
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=resolver_mock,
        ), patch(
            "api.routers.chat.publish_user_event",
            new=publisher_mock,
        ):
            result = await send_direct_activity_signal(
                data=signal_data,
                current_user=self.current_user,
                db=self.db,
            )

            self.assertIsNone(result)
            self.assertEqual(len(fence["denied_connects"]), self.denied_connects_before)
            self.assertEqual(len(fence["live_redis_constructs"]), self.live_redis_constructs_before)

        self.assertEqual(awaited_events, ["permission", "resolve_name", "publish_user_event"])

        self.assertEqual(permission_mock.call_count, 1)
        self.assertEqual(permission_mock.await_count, 1)
        permission_mock.assert_called_once_with(
            self.db,
            current_user=self.current_user,
            target_user_id=receiver_id,
        )
        permission_mock.assert_awaited_once_with(
            self.db,
            current_user=self.current_user,
            target_user_id=receiver_id,
        )
        self.assertIs(permission_mock.call_args.args[0], self.db)
        self.assertIs(permission_mock.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(permission_mock.call_args.kwargs["target_user_id"], receiver_id)
        self.assertIs(permission_mock.await_args.args[0], self.db)
        self.assertIs(permission_mock.await_args.kwargs["current_user"], self.current_user)
        self.assertEqual(permission_mock.await_args.kwargs["target_user_id"], receiver_id)

        self.assertEqual(resolver_mock.call_count, 1)
        self.assertEqual(resolver_mock.await_count, 1)
        resolver_mock.assert_called_once_with(
            self.db,
            user=self.current_user,
        )
        resolver_mock.assert_awaited_once_with(
            self.db,
            user=self.current_user,
        )
        self.assertIs(resolver_mock.call_args.args[0], self.db)
        self.assertIs(resolver_mock.call_args.kwargs["user"], self.current_user)
        self.assertIs(resolver_mock.await_args.args[0], self.db)
        self.assertIs(resolver_mock.await_args.kwargs["user"], self.current_user)

        self.assertEqual(publisher_mock.call_count, 1)
        self.assertEqual(publisher_mock.await_count, 1)
        publisher_mock.assert_called_once_with(
            receiver_id,
            "chat:activity",
            expected_payload,
        )
        publisher_mock.assert_awaited_once_with(
            receiver_id,
            "chat:activity",
            expected_payload,
        )
        self.assertEqual(publisher_mock.call_args.args[0], receiver_id)
        self.assertEqual(publisher_mock.call_args.args[1], "chat:activity")
        self.assertEqual(publisher_mock.call_args.args[2], expected_payload)
        self.assertEqual(publisher_mock.await_args.args[0], receiver_id)
        self.assertEqual(publisher_mock.await_args.args[1], "chat:activity")
        self.assertEqual(publisher_mock.await_args.args[2], expected_payload)

    async def test_send_room_activity_signal_active_group_publishes_exact_event(self) -> None:
        chat_id = 77
        chat = Chat(
            id=chat_id,
            type=ChatType.GROUP,
            title="گروه پروژه",
            is_deleted=False,
        )
        self.assertEqual(chat.type, ChatType.GROUP)
        self.assertFalse(chat.is_deleted)
        self.assertTrue(bool(chat.title))

        sender_id = self.current_user.id
        recipient_1 = 301
        recipient_2 = 302
        self.assertNotEqual(recipient_1, sender_id)
        self.assertNotEqual(recipient_2, sender_id)
        self.assertNotEqual(recipient_1, recipient_2)

        member_user_ids = [recipient_1, sender_id, recipient_2]

        resolved_sender_name = "resolved_relation_display_name_204"
        self.assertNotEqual(self.current_user.account_name, resolved_sender_name)
        self.assertNotEqual(self.current_user.full_name, resolved_sender_name)

        signal_data = RoomChatActivitySignal(
            activity="uploading_file",
            active=False,
        )

        expected_payload = {
            "room_kind": "group",
            "chat_id": chat_id,
            "sender_id": sender_id,
            "activity": "uploading_file",
            "active": False,
            "conversation_id": -chat_id,
            "conversation_other_user_id": -chat_id,
            "conversation_title": "گروه پروژه",
            "sender_name": resolved_sender_name,
        }

        awaited_events: list[str] = []

        async def fake_get_room(*args, **kwargs):
            awaited_events.append("room_lookup")
            return chat

        async def fake_get_active_group_member(*args, **kwargs):
            awaited_events.append("group_membership")
            return object()

        async def fake_resolve_room_name(*args, **kwargs):
            awaited_events.append("resolve_name")
            return resolved_sender_name

        async def fake_list_active_members(*args, **kwargs):
            awaited_events.append("list_members")
            return member_user_ids

        async def fake_publish_user_event(user_id, *args, **kwargs):
            awaited_events.append(f"publish:{user_id}")
            return None

        get_room_mock = AsyncMock(side_effect=fake_get_room)
        group_member_mock = AsyncMock(side_effect=fake_get_active_group_member)
        channel_member_mock = AsyncMock()
        resolver_mock = AsyncMock(side_effect=fake_resolve_room_name)
        list_members_mock = AsyncMock(side_effect=fake_list_active_members)
        publisher_mock = AsyncMock(side_effect=fake_publish_user_event)

        with patch(
            "api.routers.chat.get_room_or_404",
            new=get_room_mock,
        ), patch(
            "api.routers.chat.get_active_group_member_or_403",
            new=group_member_mock,
        ), patch(
            "api.routers.chat.get_active_channel_member_or_403",
            new=channel_member_mock,
        ), patch(
            "api.routers.chat.resolve_relation_aware_sender_display_name",
            new=resolver_mock,
        ), patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=list_members_mock,
        ), patch(
            "api.routers.chat.publish_user_event",
            new=publisher_mock,
        ):
            result = await send_room_activity_signal(
                chat_id=chat_id,
                data=signal_data,
                current_user=self.current_user,
                db=self.db,
            )

            self.assertIsNone(result)
            self.assertEqual(len(fence["denied_connects"]), self.denied_connects_before)
            self.assertEqual(len(fence["live_redis_constructs"]), self.live_redis_constructs_before)

        self.assertEqual(
            awaited_events,
            [
                "room_lookup",
                "group_membership",
                "resolve_name",
                "list_members",
                f"publish:{recipient_1}",
                f"publish:{recipient_2}",
            ],
        )

        channel_member_mock.assert_not_called()
        channel_member_mock.assert_not_awaited()

        self.assertEqual(get_room_mock.call_count, 1)
        self.assertEqual(get_room_mock.await_count, 1)
        get_room_mock.assert_called_once_with(self.db, chat_id)
        get_room_mock.assert_awaited_once_with(self.db, chat_id)
        self.assertIs(get_room_mock.call_args.args[0], self.db)
        self.assertEqual(get_room_mock.call_args.args[1], chat_id)
        self.assertIs(get_room_mock.await_args.args[0], self.db)
        self.assertEqual(get_room_mock.await_args.args[1], chat_id)

        self.assertEqual(group_member_mock.call_count, 1)
        self.assertEqual(group_member_mock.await_count, 1)
        group_member_mock.assert_called_once_with(
            self.db,
            chat=chat,
            user_id=sender_id,
        )
        group_member_mock.assert_awaited_once_with(
            self.db,
            chat=chat,
            user_id=sender_id,
        )
        self.assertIs(group_member_mock.call_args.args[0], self.db)
        self.assertIs(group_member_mock.call_args.kwargs["chat"], chat)
        self.assertEqual(group_member_mock.call_args.kwargs["user_id"], sender_id)
        self.assertIs(group_member_mock.await_args.args[0], self.db)
        self.assertIs(group_member_mock.await_args.kwargs["chat"], chat)
        self.assertEqual(group_member_mock.await_args.kwargs["user_id"], sender_id)

        self.assertEqual(resolver_mock.call_count, 1)
        self.assertEqual(resolver_mock.await_count, 1)
        resolver_mock.assert_called_once_with(
            self.db,
            user=self.current_user,
        )
        resolver_mock.assert_awaited_once_with(
            self.db,
            user=self.current_user,
        )
        self.assertIs(resolver_mock.call_args.args[0], self.db)
        self.assertIs(resolver_mock.call_args.kwargs["user"], self.current_user)
        self.assertIs(resolver_mock.await_args.args[0], self.db)
        self.assertIs(resolver_mock.await_args.kwargs["user"], self.current_user)

        self.assertEqual(list_members_mock.call_count, 1)
        self.assertEqual(list_members_mock.await_count, 1)
        list_members_mock.assert_called_once_with(
            self.db,
            chat_id=chat.id,
        )
        list_members_mock.assert_awaited_once_with(
            self.db,
            chat_id=chat.id,
        )
        self.assertIs(list_members_mock.call_args.args[0], self.db)
        self.assertEqual(list_members_mock.call_args.kwargs["chat_id"], chat.id)
        self.assertIs(list_members_mock.await_args.args[0], self.db)
        self.assertEqual(list_members_mock.await_args.kwargs["chat_id"], chat.id)

        self.assertEqual(publisher_mock.call_count, 2)
        self.assertEqual(publisher_mock.await_count, 2)
        expected_publisher_calls = [
            call(recipient_1, "chat:activity", expected_payload),
            call(recipient_2, "chat:activity", expected_payload),
        ]
        publisher_mock.assert_has_calls(expected_publisher_calls, any_order=False)
        publisher_mock.assert_has_awaits(expected_publisher_calls, any_order=False)
        self.assertEqual(publisher_mock.call_args_list, expected_publisher_calls)
        self.assertEqual(publisher_mock.await_args_list, expected_publisher_calls)


if __name__ == "__main__":
    unittest.main()
