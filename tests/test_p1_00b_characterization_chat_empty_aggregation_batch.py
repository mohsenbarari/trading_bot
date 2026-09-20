"""Hermetic characterization batch tests for chat seed handlers empty aggregation.

This is characterization evidence only and does not claim blocker reduction,
reconciliation, Stage completion, or ledger acceptance.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_conversations, poll_messages  # noqa: E402
from api.routers.chat_schemas import PollResponse  # noqa: E402


@contextlib.contextmanager
def empty_aggregation_lifecycle(user_id: int = 42):
    """Shared fixture lifecycle for empty direct and room aggregation test scenarios."""
    current_user = SimpleNamespace(id=user_id)
    statement_sentinel = object()

    mock_mappings = Mock()
    mock_mappings.all.return_value = []

    mock_result = Mock()
    mock_result.mappings.return_value = mock_mappings

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    with (
        patch(
            "api.routers.chat._get_customer_visible_direct_target_ids",
            new_callable=AsyncMock,
        ) as mock_get_targets,
        patch(
            "api.routers.chat.build_direct_conversation_list_stmt",
        ) as mock_build_list_stmt,
        patch(
            "api.routers.chat.build_direct_poll_summary_stmt",
        ) as mock_build_poll_stmt,
        patch(
            "api.routers.chat._filter_customer_scoped_direct_target_ids",
            new_callable=AsyncMock,
        ) as mock_filter_targets,
        patch(
            "api.routers.chat.list_room_conversations",
            new_callable=AsyncMock,
        ) as mock_list_room_convs,
        patch(
            "api.routers.chat.list_room_poll_summaries",
            new_callable=AsyncMock,
        ) as mock_list_room_polls,
    ):
        mock_get_targets.return_value = None
        mock_build_list_stmt.return_value = statement_sentinel
        mock_build_poll_stmt.return_value = statement_sentinel
        mock_filter_targets.return_value = set()
        mock_list_room_convs.return_value = []
        mock_list_room_polls.return_value = []

        fixture = SimpleNamespace(
            current_user=current_user,
            db=mock_db,
            statement_sentinel=statement_sentinel,
            mock_result=mock_result,
            mock_mappings=mock_mappings,
            mock_get_customer_visible_direct_target_ids=mock_get_targets,
            mock_build_direct_conversation_list_stmt=mock_build_list_stmt,
            mock_build_direct_poll_summary_stmt=mock_build_poll_stmt,
            mock_filter_customer_scoped_direct_target_ids=mock_filter_targets,
            mock_list_room_conversations=mock_list_room_convs,
            mock_list_room_poll_summaries=mock_list_room_polls,
        )
        yield fixture


class ChatEmptyAggregationBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test suite for chat empty aggregation seed handlers."""

    empty_aggregation_lifecycle = staticmethod(empty_aggregation_lifecycle)

    async def test_get_conversations_empty_direct_and_room_aggregation_seed(self) -> None:
        with self.empty_aggregation_lifecycle() as fixture:
            result = await get_conversations(
                current_user=fixture.current_user,
                db=fixture.db,
            )

            fixture.mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
                fixture.db,
                current_user=fixture.current_user,
            )
            fixture.mock_build_direct_conversation_list_stmt.assert_called_once_with(
                fixture.current_user.id
            )
            fixture.db.execute.assert_awaited_once_with(fixture.statement_sentinel)
            fixture.mock_result.mappings.assert_called_once_with()
            fixture.mock_mappings.all.assert_called_once_with()
            fixture.mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
                fixture.db,
                current_user=fixture.current_user,
                target_user_ids=set(),
                customer_allowed_target_ids=None,
            )
            fixture.mock_list_room_conversations.assert_awaited_once_with(
                fixture.db,
                current_user_id=fixture.current_user.id,
            )

            fixture.mock_build_direct_poll_summary_stmt.assert_not_called()
            fixture.mock_list_room_poll_summaries.assert_not_called()

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

    async def test_poll_messages_empty_direct_and_room_aggregation_seed(self) -> None:
        with self.empty_aggregation_lifecycle() as fixture:
            result = await poll_messages(
                current_user=fixture.current_user,
                db=fixture.db,
            )

            fixture.mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
                fixture.db,
                current_user=fixture.current_user,
            )
            fixture.mock_build_direct_poll_summary_stmt.assert_called_once_with(
                fixture.current_user.id
            )
            fixture.db.execute.assert_awaited_once_with(fixture.statement_sentinel)
            fixture.mock_result.mappings.assert_called_once_with()
            fixture.mock_mappings.all.assert_called_once_with()
            fixture.mock_list_room_poll_summaries.assert_awaited_once_with(
                fixture.db,
                current_user_id=fixture.current_user.id,
            )
            fixture.mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
                fixture.db,
                current_user=fixture.current_user,
                target_user_ids=set(),
                customer_allowed_target_ids=None,
            )

            fixture.mock_build_direct_conversation_list_stmt.assert_not_called()
            fixture.mock_list_room_conversations.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
