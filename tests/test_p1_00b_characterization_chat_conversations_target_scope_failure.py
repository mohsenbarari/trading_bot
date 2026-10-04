"""Direct-characterization tests for api.routers.chat.get_conversations target-scope exception propagation.

This module characterizes early target-scope exception propagation in get_conversations:
- When _get_customer_visible_direct_target_ids raises a RuntimeError, the identical exception instance propagates unchanged.
- Downstream statement building, DB execution, customer scoping filter, and room conversation listing are not started or awaited.

Mocked helper failure does not prove real lookup reachability, authorization, DB query/persistence or HTTP/auth/Depends behavior.
Exclusions:
- Excludes prior ordering, nonempty, room-failure, and seen-member scenarios.
- Excludes concurrency, retry, and process reload.
- Excludes canonical registration, seed inventory, origin matrix, blocker, and Stage closure gates.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers.chat import get_conversations


class ChatConversationsTargetScopeFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for early target-scope lookup failure in get_conversations."""

    @patch("api.routers.chat.list_room_conversations", new_callable=AsyncMock)
    @patch(
        "api.routers.chat._filter_customer_scoped_direct_target_ids",
        new_callable=AsyncMock,
    )
    @patch("api.routers.chat.build_direct_conversation_list_stmt")
    @patch(
        "api.routers.chat._get_customer_visible_direct_target_ids",
        new_callable=AsyncMock,
    )
    async def test_target_scope_exception_propagates_before_downstream_work(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        opaque_db = object()
        current_user = SimpleNamespace(id=303)
        synthetic_error = RuntimeError(
            "synthetic target-scope lookup error: sentinel-303"
        )

        mock_get_customer_visible_direct_target_ids.side_effect = synthetic_error

        with self.assertRaises(RuntimeError) as ctx:
            await get_conversations(current_user=current_user, db=opaque_db)

        self.assertIs(ctx.exception, synthetic_error)

        mock_get_customer_visible_direct_target_ids.assert_called_once_with(
            opaque_db, current_user=current_user
        )
        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            opaque_db, current_user=current_user
        )
        self.assertEqual(mock_get_customer_visible_direct_target_ids.call_count, 1)
        self.assertEqual(mock_get_customer_visible_direct_target_ids.await_count, 1)

        mock_build_direct_conversation_list_stmt.assert_not_called()

        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()

        mock_list_room_conversations.assert_not_called()
        mock_list_room_conversations.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
