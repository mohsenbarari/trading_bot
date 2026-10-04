"""Direct-characterization tests for api.routers.chat.get_conversations direct execute failure.

This module characterizes one uncovered direct-conversation database execution failure boundary in get_conversations:
- When db.execute raises a synthetic RuntimeError, the identical exception instance propagates unchanged.
- Target lookup is invoked once and awaited once with the db and current_user keyword.
- Synchronous statement builder is invoked once with only current_user.id and no allowed_target_ids keyword, returning a unique opaque statement object.
- Direct-conversation db.execute is invoked once and awaited once with the exact opaque statement object and no kwargs.
- Exception propagation halts handler execution immediately at the execute boundary: ConversationRead result conversion, customer-scoped direct target filtering, and room conversation listing are never called or awaited.

Bounded mocked-handler propagation and explicit exclusions:
- Observed mocked execution-boundary propagation; no proof of real DB, query, or service failure reachability in runtime services.
- Excludes persistence, database transactions, and real SQL execution.
- Excludes authorization, HTTP status codes, auth dependencies, and FastAPI Depends mechanics.
- Excludes origin tracking, concurrency, retry logic, configuration reload, and canonical seed/registration/blocker/Stage closure.
- Preserves all accepted characterization tests and source files unchanged; only this single execution failure scenario belongs in this module.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from api.routers.chat import get_conversations


class ChatConversationsExecutionFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization tests for direct-conversation db.execute failure in get_conversations."""

    @patch("api.routers.chat.ConversationRead")
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
    async def test_execute_exception_propagates_before_downstream_work(
        self,
        mock_get_customer_visible_direct_target_ids: AsyncMock,
        mock_build_direct_conversation_list_stmt: Mock,
        mock_filter_customer_scoped_direct_target_ids: AsyncMock,
        mock_list_room_conversations: AsyncMock,
        mock_conversation_read: Mock,
    ) -> None:
        current_user = SimpleNamespace(id=292)
        synthetic_error = RuntimeError(
            "synthetic database execute failure: sentinel-292-exec-err"
        )
        mock_execute = AsyncMock(side_effect=synthetic_error)
        db = SimpleNamespace(execute=mock_execute)
        opaque_statement = object()

        mock_get_customer_visible_direct_target_ids.return_value = None
        mock_build_direct_conversation_list_stmt.return_value = opaque_statement

        call_tracker = Mock()
        call_tracker.attach_mock(
            mock_get_customer_visible_direct_target_ids, "target_lookup"
        )
        call_tracker.attach_mock(
            mock_build_direct_conversation_list_stmt, "stmt_builder"
        )
        call_tracker.attach_mock(mock_execute, "execute")

        with self.assertRaises(RuntimeError) as ctx:
            await get_conversations(current_user=current_user, db=db)

        self.assertIs(ctx.exception, synthetic_error)

        expected_calls = [
            call.target_lookup(db, current_user=current_user),
            call.stmt_builder(current_user.id),
            call.execute(opaque_statement),
        ]
        self.assertEqual(call_tracker.mock_calls, expected_calls)
        self.assertEqual(
            [c[0] for c in call_tracker.mock_calls],
            ["target_lookup", "stmt_builder", "execute"],
        )

        mock_get_customer_visible_direct_target_ids.assert_called_once_with(
            db, current_user=current_user
        )
        mock_get_customer_visible_direct_target_ids.assert_awaited_once_with(
            db, current_user=current_user
        )
        self.assertEqual(
            mock_get_customer_visible_direct_target_ids.call_count, 1
        )
        self.assertEqual(
            mock_get_customer_visible_direct_target_ids.await_count, 1
        )

        mock_build_direct_conversation_list_stmt.assert_called_once_with(
            current_user.id
        )
        self.assertEqual(mock_build_direct_conversation_list_stmt.call_count, 1)
        self.assertEqual(
            mock_build_direct_conversation_list_stmt.call_args.args,
            (current_user.id,),
        )
        self.assertEqual(
            mock_build_direct_conversation_list_stmt.call_args.kwargs, {}
        )
        self.assertNotIn(
            "allowed_target_ids",
            mock_build_direct_conversation_list_stmt.call_args.kwargs,
        )

        mock_execute.assert_called_once_with(opaque_statement)
        mock_execute.assert_awaited_once_with(opaque_statement)
        self.assertEqual(mock_execute.call_count, 1)
        self.assertEqual(mock_execute.await_count, 1)
        self.assertEqual(len(mock_execute.call_args.args), 1)
        self.assertIs(mock_execute.call_args.args[0], opaque_statement)
        self.assertEqual(mock_execute.call_args.kwargs, {})
        self.assertEqual(len(mock_execute.await_args.args), 1)
        self.assertIs(mock_execute.await_args.args[0], opaque_statement)
        self.assertEqual(mock_execute.await_args.kwargs, {})

        mock_conversation_read.assert_not_called()
        self.assertEqual(mock_conversation_read.call_count, 0)

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
