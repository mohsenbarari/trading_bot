"""Direct-characterization tests for api.routers.chat.poll_messages.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import poll_messages  # noqa: E402
from api.routers.chat_schemas import PollResponse  # noqa: E402


class CharacterizeChatPollMessagesTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing poll_messages."""

    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.list_room_poll_summaries", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_poll_summary_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_poll_messages_empty_direct_and_room_aggregation(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_poll_summary_stmt: Mock,
        mock_list_room_poll_summaries: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=42)
        statement_sentinel = object()

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_poll_summary_stmt", (user_id,), {}))
            return statement_sentinel

        mock_mappings = Mock()

        def fake_mappings_all():
            event_log.append(("mappings_all", (), {}))
            return []

        mock_mappings.all.side_effect = fake_mappings_all

        mock_result = Mock()

        def fake_result_mappings():
            event_log.append(("direct_poll_result_mappings", (), {}))
            return mock_mappings

        mock_result.mappings.side_effect = fake_result_mappings

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

        async def fake_list_room_poll_summaries(db, *, current_user_id):
            event_log.append(("list_room_poll_summaries", (db,), {"current_user_id": current_user_id}))
            return []

        async def fake_filter_targets(db, *, current_user, target_user_ids, customer_allowed_target_ids):
            event_log.append((
                "_filter_customer_scoped_direct_target_ids",
                (db,),
                {
                    "current_user": current_user,
                    "target_user_ids": target_user_ids,
                    "customer_allowed_target_ids": customer_allowed_target_ids,
                },
            ))
            return set()

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_poll_summary_stmt.side_effect = fake_build_stmt
        mock_list_room_poll_summaries.side_effect = fake_list_room_poll_summaries
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets

        result = await poll_messages(current_user=current_user, db=mock_db)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_poll_summary_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.mappings.assert_called_once_with()
        mock_mappings.all.assert_called_once_with()
        mock_list_room_poll_summaries.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )
        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids=set(),
            customer_allowed_target_ids=None,
        )

        expected_events = [
            ("get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_poll_summary_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
            ("direct_poll_result_mappings", (), {}),
            ("mappings_all", (), {}),
            ("list_room_poll_summaries", (mock_db,), {"current_user_id": current_user.id}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": set(),
                    "customer_allowed_target_ids": None,
                },
            ),
        ]
        self.assertEqual(event_log, expected_events)

        self.assertIsInstance(result, PollResponse)
        expected_payload = {
            "total_unread": 0,
            "unread_chats_count": 0,
            "conversations_with_unread": [],
            "muted_conversation_ids": [],
            "total_unread_mentions": 0,
        }
        self.assertEqual(result.model_dump(), expected_payload)
        self.assertEqual(result.total_unread, 0)
        self.assertEqual(result.unread_chats_count, 0)
        self.assertEqual(result.conversations_with_unread, [])
        self.assertEqual(result.muted_conversation_ids, [])
        self.assertEqual(result.total_unread_mentions, 0)

    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.list_room_poll_summaries", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_poll_summary_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_poll_messages_direct_query_failure_ordering_and_short_circuiting(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_poll_summary_stmt: Mock,
        mock_list_room_poll_summaries: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=101)
        statement_sentinel = object()
        db_error = RuntimeError("direct query failed: sentinel-error-101")

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_poll_summary_stmt", (user_id,), {}))
            return statement_sentinel

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            raise db_error

        mock_db.execute.side_effect = fake_db_execute

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_poll_summary_stmt.side_effect = fake_build_stmt

        with self.assertRaises(RuntimeError) as ctx:
            await poll_messages(current_user=current_user, db=mock_db)

        self.assertIs(ctx.exception, db_error)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_poll_summary_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(statement_sentinel)

        expected_events = [
            ("get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_poll_summary_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
        ]
        self.assertEqual(event_log, expected_events)

        mock_list_room_poll_summaries.assert_not_called()
        mock_list_room_poll_summaries.assert_not_awaited()
        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()

    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.list_room_poll_summaries", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_poll_summary_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_poll_messages_room_summary_failure_ordering_and_short_circuiting(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_poll_summary_stmt: Mock,
        mock_list_room_poll_summaries: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=202)
        statement_sentinel = object()
        room_error = RuntimeError("room summary failure: sentinel-error-202")

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_poll_summary_stmt", (user_id,), {}))
            return statement_sentinel

        mock_mappings = Mock()

        def fake_mappings_all():
            event_log.append(("mappings_all", (), {}))
            return []

        mock_mappings.all.side_effect = fake_mappings_all

        mock_result = Mock()

        def fake_result_mappings():
            event_log.append(("direct_poll_result_mappings", (), {}))
            return mock_mappings

        mock_result.mappings.side_effect = fake_result_mappings

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

        async def fake_list_room_poll_summaries(db, *, current_user_id):
            event_log.append(("list_room_poll_summaries", (db,), {"current_user_id": current_user_id}))
            raise room_error

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_poll_summary_stmt.side_effect = fake_build_stmt
        mock_list_room_poll_summaries.side_effect = fake_list_room_poll_summaries

        with self.assertRaises(RuntimeError) as ctx:
            await poll_messages(current_user=current_user, db=mock_db)

        self.assertIs(ctx.exception, room_error)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_poll_summary_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.mappings.assert_called_once_with()
        mock_mappings.all.assert_called_once_with()
        mock_list_room_poll_summaries.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )

        expected_events = [
            ("get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_poll_summary_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
            ("direct_poll_result_mappings", (), {}),
            ("mappings_all", (), {}),
            ("list_room_poll_summaries", (mock_db,), {"current_user_id": current_user.id}),
        ]
        self.assertEqual(event_log, expected_events)

        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
