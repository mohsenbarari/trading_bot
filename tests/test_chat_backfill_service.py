import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.enums import ChatMembershipStatus, ChatType
from core.services import chat_backfill_service


def scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


class ChatBackfillHelperTests(unittest.TestCase):
    def test_stats_as_dict_and_membership_status_helpers(self):
        stats = chat_backfill_service.DirectChatBackfillStats(
            conversations_scanned=2,
            conversations_skipped=1,
            chats_created=1,
            chats_updated=3,
            members_created=4,
            members_updated=5,
            messages_linked=6,
        )
        active_user = SimpleNamespace(is_deleted=False)
        deleted_user = SimpleNamespace(is_deleted=True)

        self.assertEqual(
            stats.as_dict(),
            {
                "conversations_scanned": 2,
                "conversations_skipped": 1,
                "chats_created": 1,
                "chats_updated": 3,
                "members_created": 4,
                "members_updated": 5,
                "messages_linked": 6,
            },
        )
        self.assertEqual(
            chat_backfill_service._membership_status_for_user(active_user),
            ChatMembershipStatus.ACTIVE,
        )
        self.assertEqual(
            chat_backfill_service._membership_status_for_user(deleted_user),
            ChatMembershipStatus.INACTIVE,
        )

    def test_datetime_normalization_and_comparison_handle_naive_and_aware_values(self):
        naive = datetime(2026, 5, 7, 12, 0, 0)
        aware_tehran = datetime(2026, 5, 7, 15, 30, 0, tzinfo=timezone(timedelta(hours=3, minutes=30)))
        aware_utc = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)

        self.assertIsNone(chat_backfill_service._normalize_datetime(None))
        self.assertEqual(chat_backfill_service._normalize_datetime(naive), naive)
        self.assertEqual(chat_backfill_service._normalize_datetime(aware_tehran), naive)
        self.assertTrue(chat_backfill_service._same_datetime(aware_tehran, aware_utc))
        self.assertFalse(chat_backfill_service._same_datetime(naive, datetime(2026, 5, 7, 12, 0, 1)))


class BackfillDirectChatsTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_queries_load_existing_chat_members_and_unlinked_counts(self):
        existing_chat_result = Mock()
        existing_chat_result.scalar_one_or_none.return_value = 44
        member_a = SimpleNamespace(user_id=10)
        member_b = SimpleNamespace(user_id=10)
        member_c = SimpleNamespace(user_id=20)
        members_result = Mock()
        members_result.scalars.return_value.all.return_value = [member_a, member_b, member_c]
        message_count_result = Mock()
        message_count_result.scalar_one.return_value = 5
        statements = []

        async def execute(stmt):
            statements.append(str(stmt))
            return [existing_chat_result, members_result, message_count_result][len(statements) - 1]

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

        self.assertEqual(await chat_backfill_service._find_existing_direct_chat_id(db, 10, 20), 44)
        members = await chat_backfill_service._load_existing_members(db, 44, 10, 20)
        self.assertEqual(members, {10: member_a, 20: member_c})
        self.assertEqual(await chat_backfill_service._count_unlinked_messages(db, 10, 20), 5)
        self.assertEqual(len(statements), 3)

    async def test_backfill_applies_optional_conversation_filter_and_limit(self):
        captured = []

        async def execute(stmt):
            captured.append(stmt)
            return scalars_result([])

        db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

        stats = await chat_backfill_service.backfill_direct_chats(db, conversation_id=12, limit=4)

        self.assertEqual(stats.as_dict(), chat_backfill_service.DirectChatBackfillStats().as_dict())
        self.assertEqual(len(captured), 1)
        self.assertIn("conversations.id", str(captured[0]))
        self.assertIsNotNone(getattr(captured[0], "_limit_clause", None))

    async def test_backfill_returns_empty_stats_when_no_conversations_match(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=scalars_result([])))

        stats = await chat_backfill_service.backfill_direct_chats(db)

        self.assertEqual(stats.as_dict(), chat_backfill_service.DirectChatBackfillStats().as_dict())
        db.execute.assert_awaited_once()

    async def test_dry_run_counts_new_chat_and_unlinked_messages(self):
        conversation = SimpleNamespace(
            id=1,
            user1_id=10,
            user2_id=20,
            last_message_id=99,
            last_message_at=datetime(2026, 5, 7, 11, 0, 0),
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        users = [
            SimpleNamespace(id=10, is_deleted=False, deleted_at=None),
            SimpleNamespace(id=20, is_deleted=False, deleted_at=None),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[scalars_result([conversation]), scalars_result(users)]))

        with patch("core.services.chat_backfill_service._find_existing_direct_chat_id", AsyncMock(return_value=None)), \
             patch("core.services.chat_backfill_service._count_unlinked_messages", AsyncMock(return_value=4)):
            stats = await chat_backfill_service.backfill_direct_chats(db, dry_run=True)

        self.assertEqual(stats.conversations_scanned, 1)
        self.assertEqual(stats.chats_created, 1)
        self.assertEqual(stats.members_created, 2)
        self.assertEqual(stats.messages_linked, 4)

    async def test_dry_run_skips_conversation_when_user_row_is_missing(self):
        conversation = SimpleNamespace(
            id=2,
            user1_id=10,
            user2_id=30,
            last_message_id=None,
            last_message_at=None,
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        users = [SimpleNamespace(id=10, is_deleted=False, deleted_at=None)]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[scalars_result([conversation]), scalars_result(users)]))

        stats = await chat_backfill_service.backfill_direct_chats(db, dry_run=True)

        self.assertEqual(stats.conversations_scanned, 1)
        self.assertEqual(stats.conversations_skipped, 1)
        self.assertEqual(stats.chats_created, 0)
        self.assertEqual(stats.messages_linked, 0)

    async def test_non_dry_run_updates_existing_chat_members_and_links_messages(self):
        left_at_old = datetime(2026, 5, 1, 10, 0, 0)
        deleted_at_new = datetime(2026, 5, 2, 10, 0, 0)
        conversation = SimpleNamespace(
            id=3,
            user1_id=11,
            user2_id=22,
            last_message_id=333,
            last_message_at=datetime(2026, 5, 7, 12, 30, 0),
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        users = [
            SimpleNamespace(id=11, is_deleted=False, deleted_at=None),
            SimpleNamespace(id=22, is_deleted=True, deleted_at=deleted_at_new),
        ]
        chat = SimpleNamespace(id=88, last_message_id=111, last_message_at=datetime(2026, 5, 7, 9, 0, 0))
        existing_member = SimpleNamespace(
            user_id=22,
            membership_status=ChatMembershipStatus.ACTIVE,
            left_at=left_at_old,
        )
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalars_result([conversation]), scalars_result(users), SimpleNamespace(rowcount=3)]),
            get=AsyncMock(return_value=chat),
            add=Mock(),
        )

        with patch("core.services.chat_backfill_service._find_existing_direct_chat_id", AsyncMock(return_value=88)), \
             patch("core.services.chat_backfill_service._load_existing_members", AsyncMock(return_value={22: existing_member})):
            stats = await chat_backfill_service.backfill_direct_chats(db, dry_run=False)

        self.assertEqual(stats.chats_updated, 1)
        self.assertEqual(stats.members_created, 1)
        self.assertEqual(stats.members_updated, 1)
        self.assertEqual(stats.messages_linked, 3)
        self.assertEqual(chat.last_message_id, 333)
        self.assertEqual(chat.last_message_at, conversation.last_message_at)
        self.assertEqual(existing_member.membership_status, ChatMembershipStatus.INACTIVE)
        self.assertEqual(existing_member.left_at, deleted_at_new)
        created_member = db.add.call_args.args[0]
        self.assertEqual(created_member.chat_id, 88)
        self.assertEqual(created_member.user_id, 11)

    async def test_non_dry_run_creates_chat_members_and_links_messages_for_new_chat(self):
        conversation = SimpleNamespace(
            id=4,
            user1_id=15,
            user2_id=25,
            last_message_id=444,
            last_message_at=datetime(2026, 5, 7, 14, 0, 0),
            created_at=datetime(2026, 5, 1, 8, 0, 0),
        )
        users = [
            SimpleNamespace(id=15, is_deleted=False, deleted_at=None),
            SimpleNamespace(id=25, is_deleted=True, deleted_at=datetime(2026, 5, 3, 8, 0, 0)),
        ]
        added_objects = []

        def add_object(obj):
            added_objects.append(obj)

        async def flush_chat_id():
            added_objects[0].id = 91

        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[scalars_result([conversation]), scalars_result(users), SimpleNamespace(rowcount=5)]),
            get=AsyncMock(),
            add=Mock(side_effect=add_object),
            flush=AsyncMock(side_effect=flush_chat_id),
        )

        with patch("core.services.chat_backfill_service._find_existing_direct_chat_id", AsyncMock(return_value=None)), \
             patch("core.services.chat_backfill_service._load_existing_members", AsyncMock(return_value={})):
            stats = await chat_backfill_service.backfill_direct_chats(db, dry_run=False)

        self.assertEqual(stats.chats_created, 1)
        self.assertEqual(stats.members_created, 2)
        self.assertEqual(stats.messages_linked, 5)
        self.assertEqual(len(added_objects), 3)
        created_chat = added_objects[0]
        self.assertEqual(created_chat.type, ChatType.DIRECT)
        self.assertEqual(created_chat.last_message_id, 444)
        self.assertEqual(created_chat.id, 91)

        first_member = added_objects[1]
        second_member = added_objects[2]
        self.assertEqual(first_member.chat_id, 91)
        self.assertEqual(second_member.chat_id, 91)
        self.assertEqual(first_member.membership_status, ChatMembershipStatus.ACTIVE)
        self.assertEqual(second_member.membership_status, ChatMembershipStatus.INACTIVE)
        self.assertEqual(second_member.left_at, users[1].deleted_at)


if __name__ == "__main__":
    unittest.main()