"""Characterization tests for early failure boundaries in commit_upload_batch_endpoint.

Seed: API-MSG-POST-CHAT-COMMIT-UPLOAD-BATCH-ENDPOINT-L2675
Tracked Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage Card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001 (assignments/WA-P1-00B-FREEZE-20260904-001.md)

State:
- P1-00: COMPLETE
- P1-00b: IN_PROGRESS
- Gap status: BLOCKED_EXPLICIT (no inventory or state closure)

This module characterizes the two missing early failure boundaries of
api.routers.chat.commit_upload_batch_endpoint:
1. Lookup 404 scenario: HTTPException(status_code=404, detail="Upload batch not found")
   injected from get_upload_batch_for_current_user escaping without commit or downstream side effects.
2. Commit service failure scenario: unexpected RuntimeError raised from commit_upload_batch
   after successful lookup, escaping while preserving call order and aborting all downstream side effects.

Evidence Scope:
Evidence is provided as mocked handler-boundary propagation and control-flow only.
This suite does not exercise or claim persistence, actual message delivery,
concurrency, database/process reload, or live service execution. Dependency
closures terminate at mocked router-site service, serializer, publisher, and
response-constructor boundaries.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import BackgroundTasks, HTTPException

import api.routers.chat as chat_router
from api.routers.chat import commit_upload_batch_endpoint


class TestCommitUploadBatchEarlyFailuresCharacterization(unittest.IsolatedAsyncioTestCase):
    """IsolatedAsyncioTestCase scenarios characterizing early failure boundaries of commit_upload_batch_endpoint."""

    def _assert_downstream_operations_untouched(
        self,
        *,
        mock_serialize: AsyncMock,
        mock_resolve_sender: AsyncMock,
        mock_list_members: AsyncMock,
        mock_publish_msg: AsyncMock,
        mock_publish_runtime: AsyncMock,
        mock_response_cls: MagicMock,
        spy_add_task: MagicMock,
        background_tasks: BackgroundTasks,
    ) -> None:
        """Assert that all post-failure downstream operations and side effects were aborted."""
        mock_serialize.assert_not_called()
        mock_serialize.assert_not_awaited()
        self.assertEqual(mock_serialize.call_count, 0)
        self.assertEqual(mock_serialize.await_count, 0)

        mock_resolve_sender.assert_not_called()
        mock_resolve_sender.assert_not_awaited()
        self.assertEqual(mock_resolve_sender.call_count, 0)
        self.assertEqual(mock_resolve_sender.await_count, 0)

        mock_list_members.assert_not_called()
        mock_list_members.assert_not_awaited()
        self.assertEqual(mock_list_members.call_count, 0)
        self.assertEqual(mock_list_members.await_count, 0)

        mock_publish_msg.assert_not_called()
        mock_publish_msg.assert_not_awaited()
        self.assertEqual(mock_publish_msg.call_count, 0)
        self.assertEqual(mock_publish_msg.await_count, 0)

        mock_publish_runtime.assert_not_called()
        mock_publish_runtime.assert_not_awaited()
        self.assertEqual(mock_publish_runtime.call_count, 0)
        self.assertEqual(mock_publish_runtime.await_count, 0)

        mock_response_cls.assert_not_called()
        self.assertEqual(mock_response_cls.call_count, 0)

        spy_add_task.assert_not_called()
        self.assertEqual(spy_add_task.call_count, 0)

        self.assertEqual(background_tasks.tasks, [])
        self.assertEqual(len(background_tasks.tasks), 0)

    async def test_lookup_404_escapes_without_commit_or_downstream_side_effects(self) -> None:
        """Lookup-404 scenario: HTTPException(404) from lookup escapes; commit and downstream are untouched."""
        db = object()
        current_user = object()
        batch_id = "batch-test-404-uuid"

        lookup_404_error = HTTPException(status_code=404, detail="Upload batch not found")
        mock_get_upload_batch = AsyncMock(side_effect=lookup_404_error)
        mock_commit_upload_batch = AsyncMock()
        mock_serialize = AsyncMock()
        mock_resolve_sender = AsyncMock()
        mock_list_members = AsyncMock()
        mock_publish_msg = AsyncMock()
        mock_publish_runtime = AsyncMock()
        mock_response_cls = MagicMock()

        background_tasks = BackgroundTasks()
        self.assertEqual(background_tasks.tasks, [])
        spy_add_task = MagicMock(wraps=background_tasks.add_task)
        background_tasks.add_task = spy_add_task

        with (
            patch.object(chat_router, "get_upload_batch_for_current_user", mock_get_upload_batch),
            patch.object(chat_router, "commit_upload_batch", mock_commit_upload_batch),
            patch.object(chat_router, "_serialize_direct_messages_with_accountant_contract", mock_serialize),
            patch.object(chat_router, "resolve_direct_sender_display_name", mock_resolve_sender),
            patch.object(chat_router, "list_active_room_member_user_ids", mock_list_members),
            patch.object(chat_router, "_publish_upload_batch_commit_message_events", mock_publish_msg),
            patch.object(chat_router, "_publish_upload_batch_commit_runtime_events", mock_publish_runtime),
            patch.object(chat_router, "UploadBatchCommitResponse", mock_response_cls),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await commit_upload_batch_endpoint(
                    batch_id=batch_id,
                    background_tasks=background_tasks,
                    current_user=current_user,
                    db=db,
                )

        # Assert exact same exception instance escapes with unchanged status/detail
        self.assertIs(exc_info.exception, lookup_404_error)
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "Upload batch not found")

        # Lookup is called and awaited exactly once with exact db, batch_id and current_user
        self.assertEqual(mock_get_upload_batch.call_count, 1)
        self.assertEqual(mock_get_upload_batch.await_count, 1)
        mock_get_upload_batch.assert_called_once_with(db, batch_id=batch_id, current_user=current_user)
        mock_get_upload_batch.assert_awaited_once_with(db, batch_id=batch_id, current_user=current_user)

        self.assertEqual(len(mock_get_upload_batch.call_args.args), 1)
        self.assertIs(mock_get_upload_batch.call_args.args[0], db)
        self.assertEqual(set(mock_get_upload_batch.call_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_upload_batch.call_args.kwargs["batch_id"], batch_id)
        self.assertIs(mock_get_upload_batch.call_args.kwargs["current_user"], current_user)

        # Commit is neither called nor awaited
        mock_commit_upload_batch.assert_not_called()
        mock_commit_upload_batch.assert_not_awaited()
        self.assertEqual(mock_commit_upload_batch.call_count, 0)
        self.assertEqual(mock_commit_upload_batch.await_count, 0)

        # Downstream operations are untouched
        self._assert_downstream_operations_untouched(
            mock_serialize=mock_serialize,
            mock_resolve_sender=mock_resolve_sender,
            mock_list_members=mock_list_members,
            mock_publish_msg=mock_publish_msg,
            mock_publish_runtime=mock_publish_runtime,
            mock_response_cls=mock_response_cls,
            spy_add_task=spy_add_task,
            background_tasks=background_tasks,
        )

    async def test_commit_service_failure_escapes_with_ordered_calls_and_no_downstream_side_effects(self) -> None:
        """Commit-service-failure scenario: RuntimeError from commit escapes; call order preserved, downstream untouched."""
        db = object()
        current_user = object()
        batch_sentinel = object()
        batch_id = "batch-test-runtime-uuid"

        commit_error = RuntimeError("unexpected commit failure in service")
        order_tracker: list[str] = []

        async def fake_get_upload_batch(db_arg, *, batch_id, current_user):
            order_tracker.append("lookup")
            return batch_sentinel

        async def fake_commit_upload_batch(db_arg, *, batch, current_user):
            order_tracker.append("commit")
            raise commit_error

        mock_get_upload_batch = AsyncMock(side_effect=fake_get_upload_batch)
        mock_commit_upload_batch = AsyncMock(side_effect=fake_commit_upload_batch)

        call_tracker = MagicMock()
        call_tracker.attach_mock(mock_get_upload_batch, "get_upload_batch_for_current_user")
        call_tracker.attach_mock(mock_commit_upload_batch, "commit_upload_batch")

        mock_serialize = AsyncMock()
        mock_resolve_sender = AsyncMock()
        mock_list_members = AsyncMock()
        mock_publish_msg = AsyncMock()
        mock_publish_runtime = AsyncMock()
        mock_response_cls = MagicMock()

        background_tasks = BackgroundTasks()
        self.assertEqual(background_tasks.tasks, [])
        spy_add_task = MagicMock(wraps=background_tasks.add_task)
        background_tasks.add_task = spy_add_task

        with (
            patch.object(chat_router, "get_upload_batch_for_current_user", mock_get_upload_batch),
            patch.object(chat_router, "commit_upload_batch", mock_commit_upload_batch),
            patch.object(chat_router, "_serialize_direct_messages_with_accountant_contract", mock_serialize),
            patch.object(chat_router, "resolve_direct_sender_display_name", mock_resolve_sender),
            patch.object(chat_router, "list_active_room_member_user_ids", mock_list_members),
            patch.object(chat_router, "_publish_upload_batch_commit_message_events", mock_publish_msg),
            patch.object(chat_router, "_publish_upload_batch_commit_runtime_events", mock_publish_runtime),
            patch.object(chat_router, "UploadBatchCommitResponse", mock_response_cls),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                await commit_upload_batch_endpoint(
                    batch_id=batch_id,
                    background_tasks=background_tasks,
                    current_user=current_user,
                    db=db,
                )

        # Assert exact same exception escapes
        self.assertIs(exc_info.exception, commit_error)

        # Independently verify lookup-before-commit order
        self.assertEqual(order_tracker, ["lookup", "commit"])
        self.assertEqual(
            [call[0] for call in call_tracker.mock_calls],
            ["get_upload_batch_for_current_user", "commit_upload_batch"],
        )

        # Verify exact once call and await counts
        self.assertEqual(mock_get_upload_batch.call_count, 1)
        self.assertEqual(mock_get_upload_batch.await_count, 1)
        mock_get_upload_batch.assert_called_once()
        mock_get_upload_batch.assert_awaited_once()

        self.assertEqual(mock_commit_upload_batch.call_count, 1)
        self.assertEqual(mock_commit_upload_batch.await_count, 1)
        mock_commit_upload_batch.assert_called_once()
        mock_commit_upload_batch.assert_awaited_once()

        # Verify db/current_user identity, lookup batch_id and commit batch identity with no extra arguments
        mock_get_upload_batch.assert_awaited_once_with(db, batch_id=batch_id, current_user=current_user)
        self.assertEqual(len(mock_get_upload_batch.call_args.args), 1)
        self.assertIs(mock_get_upload_batch.call_args.args[0], db)
        self.assertEqual(set(mock_get_upload_batch.call_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_upload_batch.call_args.kwargs["batch_id"], batch_id)
        self.assertIs(mock_get_upload_batch.call_args.kwargs["current_user"], current_user)

        mock_commit_upload_batch.assert_awaited_once_with(db, batch=batch_sentinel, current_user=current_user)
        self.assertEqual(len(mock_commit_upload_batch.call_args.args), 1)
        self.assertIs(mock_commit_upload_batch.call_args.args[0], db)
        self.assertEqual(set(mock_commit_upload_batch.call_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_upload_batch.call_args.kwargs["batch"], batch_sentinel)
        self.assertIs(mock_commit_upload_batch.call_args.kwargs["current_user"], current_user)

        # Downstream operations are untouched
        self._assert_downstream_operations_untouched(
            mock_serialize=mock_serialize,
            mock_resolve_sender=mock_resolve_sender,
            mock_list_members=mock_list_members,
            mock_publish_msg=mock_publish_msg,
            mock_publish_runtime=mock_publish_runtime,
            mock_response_cls=mock_response_cls,
            spy_add_task=spy_add_task,
            background_tasks=background_tasks,
        )


if __name__ == "__main__":
    unittest.main()
