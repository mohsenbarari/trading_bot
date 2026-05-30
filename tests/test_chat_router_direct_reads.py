import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.chat import (
    delete_direct_conversation,
    get_conversations,
    get_messages,
    mark_messages_read,
    mark_direct_conversation_unread,
    mute_direct_conversation,
    pin_direct_conversation,
    poll_messages,
    search_messages,
    send_direct_activity_signal,
    send_typing_signal,
)


class FakeScalarResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeMappingResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeExecuteResult:
    def __init__(self, *, scalars=None, mappings=None):
        self._scalars = scalars or []
        self._mappings = mappings or []

    def scalars(self):
        return FakeScalarResult(self._scalars)

    def mappings(self):
        return FakeMappingResult(self._mappings)


class FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self.execute_results = list(execute_results or [])
        self.get_map = dict(get_map or {})

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)

    async def get(self, _model, primary_key):
        return self.get_map.get(primary_key)


class ChatRouterDirectReadEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_conversations_merges_and_sorts_direct_group_and_channel_rows(self):
        current_user = SimpleNamespace(id=5)
        direct_rows = [
            {
                "id": 1,
                "other_user_id": 9,
                "other_user_name": "Direct",
                "other_user_is_deleted": False,
                "last_message_content": "d",
                "last_message_type": "text",
                "last_message_at": datetime(2026, 5, 1, 10, 0, 0),
                "unread_count": 1,
                "other_user_last_seen_at": None,
                "room_kind": "direct",
                "chat_id": None,
                "can_send": True,
                "member_role": None,
            }
        ]
        group_row = SimpleNamespace(
            id=-20,
            other_user_id=-20,
            other_user_name="Group",
            other_user_is_deleted=False,
            last_message_content="g",
            last_message_type="text",
            last_message_at=datetime(2026, 5, 2, 10, 0, 0),
            unread_count=2,
            other_user_last_seen_at=None,
            room_kind="group",
            chat_id=20,
            can_send=True,
            member_role="admin",
            member_count=4,
            max_members=50,
            is_system=False,
            is_mandatory=False,
        )
        channel_row = SimpleNamespace(
            id=-30,
            other_user_id=-30,
            other_user_name="Channel",
            other_user_is_deleted=False,
            last_message_content="c",
            last_message_type="text",
            last_message_at=datetime(2026, 5, 3, 10, 0, 0),
            unread_count=3,
            other_user_last_seen_at=None,
            room_kind="channel",
            chat_id=30,
            can_send=False,
            member_role="member",
            member_count=12,
            max_members=None,
            is_system=True,
            is_mandatory=True,
        )
        db = FakeDB(execute_results=[FakeExecuteResult(mappings=direct_rows)])

        direct_rows[0]["other_user_name"] = "دفتر مستقیم"
        direct_rows[0]["profile_user_id"] = 90
        direct_rows[0]["profile_account_name"] = "owner-90"
        direct_rows[0]["resolved_from_accountant_id"] = 9
        direct_rows[0]["highlight_accountant_user_id"] = 9
        direct_rows[0]["highlight_accountant_relation_display_name"] = "دفتر مستقیم"
        direct_rows[0]["avatar_file_id"] = "avatar-90"

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.chat.build_direct_conversation_list_stmt", return_value="stmt") as stmt_mock, patch(
            "api.routers.chat.list_group_conversations",
            new=AsyncMock(return_value=[group_row]),
        ) as groups_mock, patch(
            "api.routers.chat.list_channel_conversations",
            new=AsyncMock(return_value=[channel_row]),
        ) as channels_mock:
            result = await get_conversations(current_user=current_user, db=db)

        stmt_mock.assert_called_once_with(5)
        groups_mock.assert_awaited_once_with(db, current_user_id=5)
        channels_mock.assert_awaited_once_with(db, current_user_id=5)
        self.assertEqual([item.other_user_name for item in result], ["Channel", "Group", "دفتر مستقیم"])
        self.assertEqual(result[2].profile_user_id, 90)
        self.assertEqual(result[2].profile_account_name, "owner-90")
        self.assertEqual(result[0].member_count, 12)
        self.assertIsNone(result[0].max_members)
        self.assertTrue(result[0].is_system)
        self.assertTrue(result[0].is_mandatory)
        self.assertEqual(result[1].member_count, 4)
        self.assertEqual(result[1].max_members, 50)
        self.assertFalse(result[1].is_system)
        self.assertFalse(result[1].is_mandatory)

    async def test_get_conversations_filters_disallowed_direct_rows_for_customer_viewer(self):
        current_user = SimpleNamespace(id=91)
        direct_rows = [
            {
                "id": 1,
                "other_user_id": 20,
                "other_user_name": "Owner",
                "other_user_is_deleted": False,
                "last_message_content": "allowed",
                "last_message_type": "text",
                "last_message_at": datetime(2026, 5, 1, 10, 0, 0),
                "unread_count": 1,
                "other_user_last_seen_at": None,
                "room_kind": "direct",
                "chat_id": None,
                "can_send": True,
                "member_role": None,
            },
            {
                "id": 2,
                "other_user_id": 1,
                "other_user_name": "SuperAdmin",
                "other_user_is_deleted": False,
                "last_message_content": "hidden",
                "last_message_type": "text",
                "last_message_at": datetime(2026, 5, 2, 10, 0, 0),
                "unread_count": 2,
                "other_user_last_seen_at": None,
                "room_kind": "direct",
                "chat_id": None,
                "can_send": True,
                "member_role": None,
            },
        ]
        db = FakeDB(execute_results=[FakeExecuteResult(mappings=direct_rows)])

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=20)),
        ), patch(
            "api.routers.chat.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[20, 44]),
        ), patch("api.routers.chat.build_direct_conversation_list_stmt", return_value="stmt"), patch(
            "api.routers.chat.list_group_conversations",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.chat.list_channel_conversations",
            new=AsyncMock(return_value=[]),
        ):
            result = await get_conversations(current_user=current_user, db=db)

        self.assertEqual([item.other_user_id for item in result], [20])

    async def test_search_messages_builds_query_and_serializes(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB(execute_results=[FakeExecuteResult(scalars=[SimpleNamespace(id=1), SimpleNamespace(id=2)])])
        serialized = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.chat.build_direct_message_search_stmt", new=AsyncMock(return_value="stmt")) as build_mock, patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            return_value=serialized,
        ) as serialize_mock:
            result = await search_messages(q="hello", chat_id=9, limit=15, db=db, current_user=current_user)

        build_mock.assert_awaited_once_with(db, current_user_id=5, query_text="hello", other_user_id=9, limit=15)
        serialize_mock.assert_awaited_once()
        self.assertIs(result, serialized)

    async def test_get_messages_raises_404_when_target_user_missing(self):
        current_user = SimpleNamespace(id=5)
        db = FakeDB(get_map={})

        with self.assertRaises(HTTPException) as exc_info:
            await get_messages(user_id=9, current_user=current_user, db=db)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")

    async def test_get_messages_handles_around_and_default_paths(self):
        current_user = SimpleNamespace(id=5)
        target = SimpleNamespace(id=9)
        serialized = [SimpleNamespace(id=8), SimpleNamespace(id=9), SimpleNamespace(id=10)]
        db = FakeDB(
            execute_results=[
                FakeExecuteResult(scalars=[SimpleNamespace(id=9), SimpleNamespace(id=8)]),
                FakeExecuteResult(scalars=[SimpleNamespace(id=10)]),
                FakeExecuteResult(scalars=[SimpleNamespace(id=7), SimpleNamespace(id=6)]),
            ],
            get_map={9: target},
        )

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.chat.build_direct_message_history_statements",
            new=AsyncMock(return_value=("older", "newer")),
        ) as build_mock, patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            side_effect=[serialized, [SimpleNamespace(id=6), SimpleNamespace(id=7)]],
        ) as serialize_mock:
            around_result = await get_messages(user_id=9, around_id=9, current_user=current_user, db=db)
            default_result = await get_messages(user_id=9, current_user=current_user, db=db)

        self.assertEqual(build_mock.await_count, 2)
        self.assertEqual(serialize_mock.await_count, 2)
        self.assertEqual([item.id for item in around_result], [8, 9, 10])
        self.assertEqual([item.id for item in default_result], [6, 7])

    async def test_get_messages_denies_customer_viewer_for_disallowed_direct_target(self):
        current_user = SimpleNamespace(id=91)
        target = SimpleNamespace(id=1)
        db = FakeDB(get_map={1: target})

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=20)),
        ), patch(
            "api.routers.chat.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[20, 44]),
        ), patch(
            "api.routers.chat.build_direct_message_history_statements",
            new=AsyncMock(return_value=("older", "newer")),
        ) as build_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await get_messages(user_id=1, current_user=current_user, db=db)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        build_mock.assert_not_called()

    async def test_send_typing_signal_and_mark_messages_read_publish_events(self):
        current_user = SimpleNamespace(id=5, account_name="ali-user")
        db = object()
        typing_data = SimpleNamespace(receiver_id=9)

        with patch("api.routers.chat.publish_direct_typing_event", new=AsyncMock()) as typing_mock:
            result = await send_typing_signal(data=typing_data, current_user=current_user)
        typing_mock.assert_awaited_once_with(
            receiver_id=9,
            sender_id=5,
            sender_name="ali-user",
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)

        with patch("api.routers.chat.commit_direct_read_state", new=AsyncMock()) as commit_mock, patch(
            "api.routers.chat.publish_direct_read_event",
            new=AsyncMock(),
        ) as publish_mock:
            result = await mark_messages_read(user_id=9, current_user=current_user, db=db)
        commit_mock.assert_awaited_once_with(db, reader=current_user, other_user_id=9)
        publish_mock.assert_awaited_once_with(other_user_id=9, reader_id=5, publisher=unittest.mock.ANY)
        self.assertIsNone(result)

    async def test_poll_messages_filters_disallowed_direct_unread_for_customer_viewer(self):
        current_user = SimpleNamespace(id=91)
        direct_rows = [
            {
                "other_user_id": 20,
                "other_user_name": "Owner",
                "other_user_is_deleted": False,
                "unread_count": 1,
                "unread_mention_count": 0,
                "is_muted": False,
            },
            {
                "other_user_id": 1,
                "other_user_name": "SuperAdmin",
                "other_user_is_deleted": False,
                "unread_count": 3,
                "unread_mention_count": 0,
                "is_muted": False,
            },
        ]
        db = FakeDB(execute_results=[FakeExecuteResult(mappings=direct_rows)])

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=SimpleNamespace(owner_user_id=20)),
        ), patch(
            "api.routers.chat.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[20, 44]),
        ), patch("api.routers.chat.build_direct_conversation_list_stmt", return_value="stmt"), patch(
            "api.routers.chat.list_group_conversations",
            new=AsyncMock(return_value=[]),
        ), patch(
            "api.routers.chat.list_channel_conversations",
            new=AsyncMock(return_value=[]),
        ):
            result = await poll_messages(current_user=current_user, db=db)

        self.assertEqual(result.total_unread, 1)
        self.assertEqual(result.unread_chats_count, 1)
        self.assertEqual(result.conversations_with_unread[0]["user_id"], 20)

    async def test_send_typing_signal_uses_relation_aware_sender_name_when_available(self):
        current_user = SimpleNamespace(id=5, account_name="ali-user")
        typing_data = SimpleNamespace(receiver_id=9)
        db = FakeDB()

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=AsyncMock(return_value="دفتر مالک"),
        ) as sender_name_mock, patch("api.routers.chat.publish_direct_typing_event", new=AsyncMock()) as typing_mock:
            result = await send_typing_signal(data=typing_data, current_user=current_user, db=db)

        sender_name_mock.assert_awaited_once_with(db, user=current_user)
        typing_mock.assert_awaited_once_with(
            receiver_id=9,
            sender_id=5,
            sender_name="دفتر مالک",
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)

    async def test_send_direct_activity_signal_publishes_general_activity(self):
        current_user = SimpleNamespace(id=5, account_name="ali-user")
        activity_data = SimpleNamespace(receiver_id=9, activity="uploading_file", active=False)

        with patch("api.routers.chat.publish_direct_activity_event", new=AsyncMock()) as activity_mock:
            result = await send_direct_activity_signal(data=activity_data, current_user=current_user)

        activity_mock.assert_awaited_once_with(
            receiver_id=9,
            sender_id=5,
            sender_name="ali-user",
            activity="uploading_file",
            active=False,
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)

    async def test_send_direct_activity_signal_uses_relation_aware_sender_name_when_available(self):
        current_user = SimpleNamespace(id=5, account_name="ali-user")
        activity_data = SimpleNamespace(receiver_id=9, activity="typing", active=True)
        db = FakeDB()

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "api.routers.chat.resolve_direct_sender_display_name",
            new=AsyncMock(return_value="دفتر مالک"),
        ) as sender_name_mock, patch("api.routers.chat.publish_direct_activity_event", new=AsyncMock()) as activity_mock:
            result = await send_direct_activity_signal(data=activity_data, current_user=current_user, db=db)

        sender_name_mock.assert_awaited_once_with(db, user=current_user)
        activity_mock.assert_awaited_once_with(
            receiver_id=9,
            sender_id=5,
            sender_name="دفتر مالک",
            activity="typing",
            active=True,
            publisher=unittest.mock.ANY,
        )
        self.assertIsNone(result)

    async def test_poll_messages_shapes_unread_summary(self):
        current_user = SimpleNamespace(id=5)
        rows = [
            {"other_user_id": 9, "other_user_name": "A", "unread_count": 2, "other_user_is_deleted": False, "is_muted": True},
            {"other_user_id": 10, "other_user_name": "B", "unread_count": 0, "other_user_is_deleted": True, "is_muted": False},
        ]
        db = FakeDB(execute_results=[FakeExecuteResult(mappings=rows)])
        group_row = SimpleNamespace(
            other_user_id=-20,
            other_user_name="Group",
            unread_count=1,
            other_user_is_deleted=False,
            is_muted=False,
        )
        channel_row = SimpleNamespace(
            other_user_id=-30,
            other_user_name="Channel",
            unread_count=4,
            other_user_is_deleted=False,
            is_muted=True,
        )

        rows[0]["other_user_name"] = "دفتر A"

        with patch(
            "api.routers.chat.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch("api.routers.chat.build_direct_conversation_list_stmt", return_value="stmt") as stmt_mock, patch(
            "api.routers.chat.list_group_conversations",
            new=AsyncMock(return_value=[group_row]),
        ) as groups_mock, patch(
            "api.routers.chat.list_channel_conversations",
            new=AsyncMock(return_value=[channel_row]),
        ) as channels_mock:
            result = await poll_messages(current_user=current_user, db=db)

        stmt_mock.assert_called_once_with(5)
        groups_mock.assert_awaited_once_with(db, current_user_id=5)
        channels_mock.assert_awaited_once_with(db, current_user_id=5)
        self.assertEqual(result.total_unread, 7)
        self.assertEqual(result.unread_chats_count, 3)
        self.assertEqual(result.conversations_with_unread[0]["user_name"], "دفتر A")
        self.assertEqual(result.conversations_with_unread[1]["user_id"], -20)
        self.assertEqual(result.conversations_with_unread[2]["user_id"], -30)
        self.assertEqual(result.muted_conversation_ids, [-30, 9])

    async def test_direct_conversation_pin_and_hide_routes_serialize_service_state(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        pinned_at = datetime(2026, 5, 10, 7, 20, 0)
        member = SimpleNamespace(chat_id=44, is_pinned=True, pinned_at=pinned_at, is_hidden=True)

        with patch(
            "api.routers.chat.set_direct_chat_pin_state",
            new=AsyncMock(return_value=member),
        ) as pin_mock:
            pin_result = await pin_direct_conversation(
                user_id=9,
                data=SimpleNamespace(pinned=True),
                current_user=current_user,
                db=db,
            )

        pin_mock.assert_awaited_once_with(db, actor=current_user, other_user_id=9, pinned=True)
        self.assertEqual(pin_result.target_id, 9)
        self.assertEqual(pin_result.chat_id, 44)
        self.assertTrue(pin_result.is_pinned)
        self.assertEqual(pin_result.pinned_at, pinned_at)

        with patch(
            "api.routers.chat.hide_direct_conversation",
            new=AsyncMock(return_value=member),
        ) as hide_mock:
            hide_result = await delete_direct_conversation(user_id=9, current_user=current_user, db=db)

        hide_mock.assert_awaited_once_with(db, actor=current_user, other_user_id=9)
        self.assertEqual(hide_result.target_id, 9)
        self.assertEqual(hide_result.chat_id, 44)
        self.assertTrue(hide_result.hidden)

    async def test_direct_conversation_mute_and_mark_unread_routes_serialize_state(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        member = SimpleNamespace(chat_id=45, is_muted=True)

        with patch(
            "api.routers.chat.set_direct_chat_mute_state",
            new=AsyncMock(return_value=member),
        ) as mute_mock:
            mute_result = await mute_direct_conversation(
                user_id=9,
                data=SimpleNamespace(muted=True),
                current_user=current_user,
                db=db,
            )

        mute_mock.assert_awaited_once_with(db, actor=current_user, other_user_id=9, muted=True)
        self.assertEqual(mute_result.target_id, 9)
        self.assertTrue(mute_result.is_muted)

        unread_member = SimpleNamespace(chat_id=46, is_marked_unread=True)
        with patch(
            "api.routers.chat.set_direct_chat_mark_unread_state",
            new=AsyncMock(return_value=unread_member),
        ) as unread_mock:
            unread_result = await mark_direct_conversation_unread(
                user_id=9,
                data=SimpleNamespace(unread=True),
                current_user=current_user,
                db=db,
            )

        unread_mock.assert_awaited_once_with(db, actor=current_user, other_user_id=9, unread=True)
        self.assertEqual(unread_result.target_id, 9)
        self.assertEqual(unread_result.chat_id, 46)
        self.assertEqual(unread_result.unread_count, 1)


if __name__ == "__main__":
    unittest.main()