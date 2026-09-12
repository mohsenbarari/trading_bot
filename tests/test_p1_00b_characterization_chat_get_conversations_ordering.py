"""Direct-characterization tests for api.routers.chat.get_conversations ordering and validation boundaries.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_conversations  # noqa: E402


class CharacterizeChatGetConversationsOrderingTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_conversations ordering and validation boundaries."""

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_get_conversations_empty_direct_and_room_aggregation(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=42)
        statement_sentinel = object()

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("_get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_conversation_list_stmt", (user_id,), {}))
            return statement_sentinel

        mock_mappings = Mock()

        def fake_mappings_all():
            event_log.append(("mappings_all", (), {}))
            return []

        mock_mappings.all.side_effect = fake_mappings_all

        mock_result = Mock()

        def fake_result_mappings():
            event_log.append(("direct_conversation_result_mappings", (), {}))
            return mock_mappings

        mock_result.mappings.side_effect = fake_result_mappings

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

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

        async def fake_list_room_conversations(db, *, current_user_id):
            event_log.append(("list_room_conversations", (db,), {"current_user_id": current_user_id}))
            return []

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_conversation_list_stmt.side_effect = fake_build_stmt
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets
        mock_list_room_conversations.side_effect = fake_list_room_conversations

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
            target_user_ids=set(),
            customer_allowed_target_ids=None,
        )
        mock_list_room_conversations.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_conversation_list_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
            ("direct_conversation_result_mappings", (), {}),
            ("mappings_all", (), {}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": set(),
                    "customer_allowed_target_ids": None,
                },
            ),
            ("list_room_conversations", (mock_db,), {"current_user_id": current_user.id}),
        ]
        self.assertEqual(event_log, expected_events)

        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_get_conversations_malformed_direct_row_validation_earlier_failure_boundary(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=101)
        statement_sentinel = object()
        malformed_direct_row = {"id": 1}

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("_get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_conversation_list_stmt", (user_id,), {}))
            return statement_sentinel

        mock_mappings = Mock()

        def fake_mappings_all():
            event_log.append(("mappings_all", (), {}))
            return [malformed_direct_row]

        mock_mappings.all.side_effect = fake_mappings_all

        mock_result = Mock()

        def fake_result_mappings():
            event_log.append(("direct_conversation_result_mappings", (), {}))
            return mock_mappings

        mock_result.mappings.side_effect = fake_result_mappings

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_conversation_list_stmt.side_effect = fake_build_stmt

        with self.assertRaises(ValidationError) as ctx:
            await get_conversations(current_user=current_user, db=mock_db)

        self.assertIsInstance(ctx.exception, ValidationError)

        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_conversation_list_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(statement_sentinel)
        mock_result.mappings.assert_called_once_with()
        mock_mappings.all.assert_called_once_with()

        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()
        mock_list_room_conversations.assert_not_called()
        mock_list_room_conversations.assert_not_awaited()

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_conversation_list_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
            ("direct_conversation_result_mappings", (), {}),
            ("mappings_all", (), {}),
        ]
        self.assertEqual(event_log, expected_events)

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_get_conversations_room_list_failure_later_failure_boundary(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        event_log = []
        current_user = SimpleNamespace(id=202)
        statement_sentinel = object()
        room_error = RuntimeError("room list failed: sentinel-room-error-202")

        async def fake_get_customer_targets(db, *, current_user):
            event_log.append(("_get_customer_visible_direct_target_ids", (db,), {"current_user": current_user}))
            return None

        def fake_build_stmt(user_id):
            event_log.append(("build_direct_conversation_list_stmt", (user_id,), {}))
            return statement_sentinel

        mock_mappings = Mock()

        def fake_mappings_all():
            event_log.append(("mappings_all", (), {}))
            return []

        mock_mappings.all.side_effect = fake_mappings_all

        mock_result = Mock()

        def fake_result_mappings():
            event_log.append(("direct_conversation_result_mappings", (), {}))
            return mock_mappings

        mock_result.mappings.side_effect = fake_result_mappings

        mock_db = AsyncMock()

        async def fake_db_execute(stmt):
            event_log.append(("db_execute", (stmt,), {}))
            return mock_result

        mock_db.execute.side_effect = fake_db_execute

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

        async def fake_list_room_conversations(db, *, current_user_id):
            event_log.append(("list_room_conversations", (db,), {"current_user_id": current_user_id}))
            raise room_error

        mock_get_customer_visible_direct_target_ids.side_effect = fake_get_customer_targets
        mock_build_direct_conversation_list_stmt.side_effect = fake_build_stmt
        mock_filter_customer_scoped_direct_target_ids.side_effect = fake_filter_targets
        mock_list_room_conversations.side_effect = fake_list_room_conversations

        with self.assertRaises(RuntimeError) as ctx:
            await get_conversations(current_user=current_user, db=mock_db)

        self.assertIs(ctx.exception, room_error)

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
            target_user_ids=set(),
            customer_allowed_target_ids=None,
        )
        mock_list_room_conversations.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )

        expected_events = [
            ("_get_customer_visible_direct_target_ids", (mock_db,), {"current_user": current_user}),
            ("build_direct_conversation_list_stmt", (current_user.id,), {}),
            ("db_execute", (statement_sentinel,), {}),
            ("direct_conversation_result_mappings", (), {}),
            ("mappings_all", (), {}),
            (
                "_filter_customer_scoped_direct_target_ids",
                (mock_db,),
                {
                    "current_user": current_user,
                    "target_user_ids": set(),
                    "customer_allowed_target_ids": None,
                },
            ),
            ("list_room_conversations", (mock_db,), {"current_user_id": current_user.id}),
        ]
        self.assertEqual(event_log, expected_events)


if __name__ == "__main__":
    unittest.main()
