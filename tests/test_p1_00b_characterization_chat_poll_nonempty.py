"""Hermetic characterization tests for api.routers.chat.poll_messages nonempty aggregation.

Characterizes handler aggregation logic for nonempty direct and room helper results
under Stage P1-00b (Assignment WA-P1-00B-EVIDENCE-RECONCILE-20260930-003).
Exclusions: This module characterizes handler aggregation after helper calls, not
SQL selection, real customer relations, HTTP routing, or origin-matrix cells.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import poll_messages  # noqa: E402
from api.routers.chat_schemas import PollResponse  # noqa: E402
from core.services.chat_room_service import RoomPollSummary  # noqa: E402


class ChatPollNonemptyCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for live poll_messages nonempty direct and room aggregation."""

    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.list_room_poll_summaries", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_poll_summary_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_mixed_visible_direct_and_room_rows_aggregate_exact_response(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_poll_summary_stmt: Mock,
        mock_list_room_poll_summaries: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
    ) -> None:
        """Scenario 1: Mixed visible direct and room rows aggregate with exact order, mute, and mentions."""
        current_user = SimpleNamespace(id=55)
        stmt_sentinel = object()
        mock_build_direct_poll_summary_stmt.return_value = stmt_sentinel

        # Direct mappings in order: ID 9 (unread 2, muted True), ID 7 (unread 0, muted True), ID 8 (unread 4, muted False)
        direct_rows = [
            {
                "other_user_id": 9,
                "other_user_name": "Direct Nine",
                "other_user_is_deleted": False,
                "unread_count": 2,
                "unread_mention_count": 0,
                "is_muted": True,
            },
            {
                "other_user_id": 7,
                "other_user_name": "Direct Seven",
                "other_user_is_deleted": False,
                "unread_count": 0,
                "unread_mention_count": 0,
                "is_muted": True,
            },
            {
                "other_user_id": 8,
                "other_user_name": "Hidden Eight",
                "other_user_is_deleted": False,
                "unread_count": 4,
                "unread_mention_count": 0,
                "is_muted": False,
            },
        ]
        mock_mappings = Mock()
        mock_mappings.all.return_value = direct_rows
        mock_execute_result = Mock()
        mock_execute_result.mappings.return_value = mock_mappings

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_execute_result

        # Customer allowed IDs: {7, 9}
        customer_allowed_ids = {7, 9}
        mock_get_customer_visible_direct_target_ids.return_value = customer_allowed_ids

        # RoomPollSummary objects in order: ID 80 (unread 3, mentions 2, muted True), ID 81 (unread 0, mentions 5, muted False)
        room_summaries = [
            RoomPollSummary(
                other_user_id=80,
                other_user_name="Room Eighty",
                other_user_is_deleted=False,
                unread_count=3,
                unread_mention_count=2,
                is_muted=True,
            ),
            RoomPollSummary(
                other_user_id=81,
                other_user_name="Quiet Room",
                other_user_is_deleted=False,
                unread_count=0,
                unread_mention_count=5,
                is_muted=False,
            ),
        ]
        mock_list_room_poll_summaries.return_value = room_summaries

        # Mocked scope filter receives {7, 8, 9} and returns {7, 9}
        mock_filter_customer_scoped_direct_target_ids.return_value = {7, 9}

        response = await poll_messages(current_user=current_user, db=mock_db)

        # Explicit assertions for helper signatures and arguments
        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_poll_summary_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(stmt_sentinel)
        mock_mappings.all.assert_called_once_with()
        mock_list_room_poll_summaries.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )
        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={7, 8, 9},
            customer_allowed_target_ids=customer_allowed_ids,
        )

        # No extra DB work
        self.assertEqual(mock_db.execute.await_count, 1)
        mock_db.commit.assert_not_called()
        mock_db.rollback.assert_not_called()

        # Exact PollResponse assertions
        self.assertIsInstance(response, PollResponse)
        expected_conversations = [
            {
                "user_id": 9,
                "user_name": "Direct Nine",
                "unread_count": 2,
                "unread_mention_count": 0,
                "is_deleted": False,
            },
            {
                "user_id": 80,
                "user_name": "Room Eighty",
                "unread_count": 3,
                "unread_mention_count": 2,
                "is_deleted": False,
            },
        ]
        expected_model_dump = {
            "total_unread": 5,
            "unread_chats_count": 2,
            "conversations_with_unread": expected_conversations,
            "muted_conversation_ids": [7, 9, 80],
            "total_unread_mentions": 7,
        }
        self.assertEqual(response.model_dump(), expected_model_dump)
        self.assertEqual(response.total_unread, 5)
        self.assertEqual(response.unread_chats_count, 2)
        self.assertEqual(response.conversations_with_unread, expected_conversations)
        self.assertEqual(response.muted_conversation_ids, [7, 9, 80])
        self.assertEqual(response.total_unread_mentions, 7)

        # Verify hidden direct 8 is absent
        unread_user_ids = [conv["user_id"] for conv in response.conversations_with_unread]
        self.assertNotIn(8, unread_user_ids)
        self.assertNotIn(8, response.muted_conversation_ids)

        # Verify zero-unread rows remain outside conversations_with_unread
        self.assertNotIn(7, unread_user_ids)
        self.assertNotIn(81, unread_user_ids)
        # Direct 7 still contributes to muted_conversation_ids
        self.assertIn(7, response.muted_conversation_ids)
        # Room 81 still contributes mentions to total_unread_mentions
        self.assertEqual(response.total_unread_mentions, 7)

    @patch("api.routers.chat._filter_customer_scoped_direct_target_ids", new_callable=AsyncMock)
    @patch("api.routers.chat.list_room_poll_summaries", new_callable=AsyncMock)
    @patch("api.routers.chat.build_direct_poll_summary_stmt")
    @patch("api.routers.chat._get_customer_visible_direct_target_ids", new_callable=AsyncMock)
    async def test_zero_unread_attribute_row_retains_muted_ids_and_room_mentions(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_poll_summary_stmt: Mock,
        mock_list_room_poll_summaries: AsyncMock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
    ) -> None:
        """Scenario 2: Zero-unread attribute-style direct row retains muted IDs and room mentions."""
        current_user = SimpleNamespace(id=77)
        stmt_sentinel = object()
        mock_build_direct_poll_summary_stmt.return_value = stmt_sentinel

        # Attribute-style direct row: ID 12 / unread 0 / muted True
        direct_attr_row = SimpleNamespace(
            other_user_id=12,
            other_user_name="Direct Twelve",
            other_user_is_deleted=False,
            unread_count=0,
            unread_mention_count=0,
            is_muted=True,
        )
        mock_mappings = Mock()
        mock_mappings.all.return_value = [direct_attr_row]
        mock_execute_result = Mock()
        mock_execute_result.mappings.return_value = mock_mappings

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_execute_result

        mock_get_customer_visible_direct_target_ids.return_value = None

        # RoomPollSummary: ID 40 / unread 0 / mentions 2 / muted True
        room_summary = RoomPollSummary(
            other_user_id=40,
            other_user_name="Room Forty",
            other_user_is_deleted=False,
            unread_count=0,
            unread_mention_count=2,
            is_muted=True,
        )
        mock_list_room_poll_summaries.return_value = [room_summary]

        # Filter receives {12} and returns {12}
        mock_filter_customer_scoped_direct_target_ids.return_value = {12}

        response = await poll_messages(current_user=current_user, db=mock_db)

        # Helper signature assertions
        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            mock_db, current_user=current_user
        )
        mock_build_direct_poll_summary_stmt.assert_called_once_with(current_user.id)
        mock_db.execute.assert_awaited_once_with(stmt_sentinel)
        mock_mappings.all.assert_called_once_with()
        mock_list_room_poll_summaries.assert_awaited_once_with(
            mock_db, current_user_id=current_user.id
        )
        mock_filter_customer_scoped_direct_target_ids.assert_awaited_once_with(
            mock_db,
            current_user=current_user,
            target_user_ids={12},
            customer_allowed_target_ids=None,
        )

        # No extra DB work or publication
        self.assertEqual(mock_db.execute.await_count, 1)
        mock_db.commit.assert_not_called()
        mock_db.rollback.assert_not_called()

        # Exact PollResponse assertions
        self.assertIsInstance(response, PollResponse)
        expected_model_dump = {
            "total_unread": 0,
            "unread_chats_count": 0,
            "conversations_with_unread": [],
            "muted_conversation_ids": [12, 40],
            "total_unread_mentions": 2,
        }
        self.assertEqual(response.model_dump(), expected_model_dump)
        self.assertEqual(response.total_unread, 0)
        self.assertEqual(response.unread_chats_count, 0)
        self.assertEqual(response.conversations_with_unread, [])
        self.assertEqual(response.muted_conversation_ids, [12, 40])
        self.assertEqual(response.total_unread_mentions, 2)


if __name__ == "__main__":
    unittest.main()
