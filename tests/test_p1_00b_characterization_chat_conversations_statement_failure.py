"""Direct-characterization tests for api.routers.chat.get_conversations synchronous statement builder failure.

This module characterizes one uncovered synchronous statement-construction failure boundary in get_conversations:
- When build_direct_conversation_list_stmt raises a RuntimeError, the identical exception instance propagates unchanged.
- Target lookup is invoked once and awaited once with the opaque DB and current_user keyword.
- Synchronous statement builder is invoked once with only current_user.id and no allowed_target_ids keyword.
- Downstream DB execution, customer scoping filter, and room conversation listing are not called or awaited.

Bounded mocked-handler propagation and explicit exclusions:
- No proof of target-lookup or query-builder failure reachability in the runtime services.
- Excludes authorization, DB execution/query/persistence, and response serialization.
- Excludes HTTP/auth/Depends, origin, concurrency/retry/reload, or canonical seed/registration/blocker/Stage closure.
- Preserves accepted target-scope, ordering/nonempty, and seen-members tests unchanged.
- Only this one failure scenario and one test selector belong in this module.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from api.routers.chat import get_conversations


class ChatConversationsStatementFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for synchronous statement builder failure in get_conversations."""

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
    async def test_statement_builder_exception_propagates_before_downstream_work(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
    ) -> None:
        opaque_db = object()
        current_user = SimpleNamespace(id=292)
        synthetic_error = RuntimeError(
            "synthetic statement builder failure: sentinel-292-stmt-err"
        )

        mock_get_customer_visible_direct_target_ids.return_value = None
        mock_build_direct_conversation_list_stmt.side_effect = synthetic_error

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

        mock_build_direct_conversation_list_stmt.assert_called_once_with(
            current_user.id
        )
        self.assertEqual(mock_build_direct_conversation_list_stmt.call_count, 1)
        self.assertEqual(
            mock_build_direct_conversation_list_stmt.call_args.args,
            (current_user.id,),
        )
        self.assertEqual(mock_build_direct_conversation_list_stmt.call_args.kwargs, {})
        self.assertNotIn(
            "allowed_target_ids",
            mock_build_direct_conversation_list_stmt.call_args.kwargs,
        )

        mock_filter_customer_scoped_direct_target_ids.assert_not_called()
        mock_filter_customer_scoped_direct_target_ids.assert_not_awaited()
        self.assertEqual(
            mock_filter_customer_scoped_direct_target_ids.call_count, 0
        )
        self.assertEqual(
            mock_filter_customer_scoped_direct_target_ids.await_count, 0
        )

        mock_list_room_conversations.assert_not_called()
        mock_list_room_conversations.assert_not_awaited()
        self.assertEqual(mock_list_room_conversations.call_count, 0)
        self.assertEqual(mock_list_room_conversations.await_count, 0)


if __name__ == "__main__":
    unittest.main()
