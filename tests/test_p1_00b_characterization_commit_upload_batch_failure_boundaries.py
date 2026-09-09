"""Characterization tests for post-service failure boundaries in commit_upload_batch_endpoint.

Seed: API-MSG-POST-CHAT-COMMIT-UPLOAD-BATCH-ENDPOINT-L2675
Tracked Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage: docs/refactor/two-server/section-1/stages/P1-00b.md (P1-00b IN_PROGRESS; P1-00 COMPLETE; BLOCKED_EXPLICIT retained)
Assignment: WA-P1-00B-FREEZE-20260904-001

Evidence scope:
This module provides handler-boundary control-flow characterization evidence for three
currently uncovered post-service failure boundaries in api.routers.chat.commit_upload_batch_endpoint:
1. Serialization failure after successful lookup and service commit.
2. Sender-name resolution failure after successful serialization.
3. Group-member lookup failure after successful serialization and sender-name resolution.

Dependencies at router import sites are mocked with synthetic SimpleNamespace fixtures to verify
boundary control flow, exact argument passing, and zero downstream leakage. This is handler-boundary
evidence only; it does not claim live database/Redis/network/filesystem persistence, event delivery,
concurrency guarantees, process reload, or Stage/gap closure.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Install P1-00 network fence before importing application modules
install_p1_00_network_fence()

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks

from api.routers.chat import commit_upload_batch_endpoint
from api.routers.chat_schemas import UploadBatchCommitResponse
from models.upload_session import UploadBatchStatus


class TestCommitUploadBatchFailureBoundaries(unittest.IsolatedAsyncioTestCase):
    """Characterize post-service failure boundaries in commit_upload_batch_endpoint."""

    def setUp(self) -> None:
        self.caller_user_id = 42
        self.lookup_target_id = 777
        self.room_target_id = 999
        self.batch_id = "batch-lookup-uuid-100"

        self.current_user = SimpleNamespace(
            id=self.caller_user_id,
            account_name="test_caller_user",
        )
        self.db = SimpleNamespace(
            description="synthetic_async_session_token",
        )

        self.lookup_batch = SimpleNamespace(
            id=self.batch_id,
            status=UploadBatchStatus.UPLOADING,
            target_id=self.lookup_target_id,
            owner_user_id=self.caller_user_id,
        )
        self.committed_batch = SimpleNamespace(
            id="batch-committed-uuid-200",
            status=UploadBatchStatus.COMMITTED,
            committed_items=2,
            target_id=self.room_target_id,
        )

        self.raw_message_1 = SimpleNamespace(
            id=101,
            content="raw message 1",
            sender_id=self.caller_user_id,
        )
        self.raw_message_2 = SimpleNamespace(
            id=102,
            content="raw message 2",
            sender_id=self.caller_user_id,
        )
        self.committed_messages = [self.raw_message_1, self.raw_message_2]

        self.serialized_message_1 = SimpleNamespace(
            id=101,
            content="serialized message 1",
            sender_id=self.caller_user_id,
        )
        self.serialized_message_2 = SimpleNamespace(
            id=102,
            content="serialized message 2",
            sender_id=self.caller_user_id,
        )
        self.serialized_messages = [self.serialized_message_1, self.serialized_message_2]

        self.resolved_target = SimpleNamespace(
            receiver=None,
            target_id=self.room_target_id,
        )
        self.commit_result = SimpleNamespace(
            batch=self.committed_batch,
            target=self.resolved_target,
            messages=self.committed_messages,
        )

        # Baseline fixture identity and distinctness verification
        self.assertIsNot(self.lookup_batch, self.committed_batch)
        self.assertNotEqual(self.lookup_batch.id, self.committed_batch.id)
        self.assertIs(self.committed_batch.status, UploadBatchStatus.COMMITTED)
        self.assertIsNot(self.committed_messages, self.serialized_messages)
        self.assertGreater(len(self.committed_messages), 0)
        self.assertGreater(len(self.serialized_messages), 0)
        self.assertIsNot(self.committed_messages[0], self.serialized_messages[0])
        self.assertIsNot(self.committed_messages[1], self.serialized_messages[1])
        self.assertIsNone(self.resolved_target.receiver)
        self.assertNotEqual(self.resolved_target.target_id, self.current_user.id)
        self.assertNotEqual(self.resolved_target.target_id, self.lookup_batch.target_id)

    async def test_commit_upload_batch_serialization_failure_aborts_before_downstream(self) -> None:
        """Requirement 2: serialization failure propagates and aborts before downstream calls."""
        serialization_exc = RuntimeError("synthetic serialization failure at _serialize_direct_messages_with_accountant_contract")
        call_order: list[str] = []

        async def _fake_get_batch(*args, **kwargs):
            call_order.append("get_upload_batch_for_current_user")
            return self.lookup_batch

        async def _fake_commit_batch(*args, **kwargs):
            call_order.append("commit_upload_batch")
            return self.commit_result

        async def _fake_serializer(*args, **kwargs):
            call_order.append("_serialize_direct_messages_with_accountant_contract")
            raise serialization_exc

        async def _fake_resolve_sender(*args, **kwargs):
            call_order.append("resolve_direct_sender_display_name")
            return "Test Sender"

        async def _fake_list_members(*args, **kwargs):
            call_order.append("list_active_room_member_user_ids")
            return [101, 102]

        async def _fake_publish_message_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_message_events")

        async def _fake_publish_runtime_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_runtime_events")

        mock_get_batch = AsyncMock(name="get_upload_batch_for_current_user", side_effect=_fake_get_batch)
        mock_commit_batch = AsyncMock(name="commit_upload_batch", side_effect=_fake_commit_batch)
        mock_serializer = AsyncMock(name="_serialize_direct_messages_with_accountant_contract", side_effect=_fake_serializer)
        mock_resolve_sender = AsyncMock(name="resolve_direct_sender_display_name", side_effect=_fake_resolve_sender)
        mock_list_members = AsyncMock(name="list_active_room_member_user_ids", side_effect=_fake_list_members)
        mock_publish_message_events = AsyncMock(name="_publish_upload_batch_commit_message_events", side_effect=_fake_publish_message_events)
        mock_publish_runtime_events = AsyncMock(name="_publish_upload_batch_commit_runtime_events", side_effect=_fake_publish_runtime_events)

        background_tasks = BackgroundTasks()
        real_add_task = background_tasks.add_task

        def _spy_add_task(*args, **kwargs):
            call_order.append("background_tasks.add_task")
            return real_add_task(*args, **kwargs)

        add_task_spy = MagicMock(name="add_task_spy", wraps=real_add_task, side_effect=_spy_add_task)
        background_tasks.add_task = add_task_spy

        def _spy_response(*args, **kwargs):
            call_order.append("UploadBatchCommitResponse")
            return UploadBatchCommitResponse(*args, **kwargs)

        response_spy = MagicMock(name="response_spy", wraps=UploadBatchCommitResponse, side_effect=_spy_response)

        with patch("api.routers.chat.get_upload_batch_for_current_user", new=mock_get_batch), \
             patch("api.routers.chat.commit_upload_batch", new=mock_commit_batch), \
             patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new=mock_serializer), \
             patch("api.routers.chat.resolve_direct_sender_display_name", new=mock_resolve_sender), \
             patch("api.routers.chat.list_active_room_member_user_ids", new=mock_list_members), \
             patch("api.routers.chat._publish_upload_batch_commit_message_events", new=mock_publish_message_events), \
             patch("api.routers.chat._publish_upload_batch_commit_runtime_events", new=mock_publish_runtime_events), \
             patch("api.routers.chat.UploadBatchCommitResponse", new=response_spy):
            with self.assertRaises(RuntimeError) as ctx:
                await commit_upload_batch_endpoint(
                    batch_id=self.batch_id,
                    background_tasks=background_tasks,
                    current_user=self.current_user,
                    db=self.db,
                )
            self.assertIs(ctx.exception, serialization_exc)

        # Execution order through boundary
        self.assertEqual(
            call_order,
            [
                "get_upload_batch_for_current_user",
                "commit_upload_batch",
                "_serialize_direct_messages_with_accountant_contract",
            ],
        )

        # 1. get_upload_batch_for_current_user assertions
        self.assertEqual(mock_get_batch.call_count, 1)
        self.assertEqual(mock_get_batch.await_count, 1)
        self.assertEqual(len(mock_get_batch.call_args.args), 1)
        self.assertIs(mock_get_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.call_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.call_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_get_batch.await_args.args), 1)
        self.assertIs(mock_get_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.await_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.await_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.await_args.kwargs["current_user"], self.current_user)

        # 2. commit_upload_batch assertions
        self.assertEqual(mock_commit_batch.call_count, 1)
        self.assertEqual(mock_commit_batch.await_count, 1)
        self.assertEqual(len(mock_commit_batch.call_args.args), 1)
        self.assertIs(mock_commit_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.call_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.call_args.kwargs["batch"], self.lookup_batch)
        self.assertIsNot(mock_commit_batch.call_args.kwargs["batch"], self.committed_batch)
        self.assertIs(mock_commit_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_commit_batch.await_args.args), 1)
        self.assertIs(mock_commit_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.await_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.await_args.kwargs["batch"], self.lookup_batch)
        self.assertIs(mock_commit_batch.await_args.kwargs["current_user"], self.current_user)

        # 3. _serialize_direct_messages_with_accountant_contract assertions
        self.assertEqual(mock_serializer.call_count, 1)
        self.assertEqual(mock_serializer.await_count, 1)
        self.assertEqual(len(mock_serializer.call_args.args), 2)
        self.assertIs(mock_serializer.call_args.args[0], self.db)
        self.assertIs(mock_serializer.call_args.args[1], self.committed_messages)
        self.assertIsNot(mock_serializer.call_args.args[1], self.serialized_messages)
        self.assertEqual(set(mock_serializer.call_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.call_args.kwargs["viewer_user_id"], self.current_user.id)
        self.assertEqual(len(mock_serializer.await_args.args), 2)
        self.assertIs(mock_serializer.await_args.args[0], self.db)
        self.assertIs(mock_serializer.await_args.args[1], self.committed_messages)
        self.assertEqual(set(mock_serializer.await_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.await_args.kwargs["viewer_user_id"], self.current_user.id)

        # Downstream uncalled and unawaited
        self.assertEqual(mock_resolve_sender.call_count, 0)
        self.assertEqual(mock_resolve_sender.await_count, 0)
        mock_resolve_sender.assert_not_called()
        mock_resolve_sender.assert_not_awaited()

        self.assertEqual(mock_list_members.call_count, 0)
        self.assertEqual(mock_list_members.await_count, 0)
        mock_list_members.assert_not_called()
        mock_list_members.assert_not_awaited()

        self.assertEqual(add_task_spy.call_count, 0)
        add_task_spy.assert_not_called()
        self.assertEqual(len(background_tasks.tasks), 0)

        self.assertEqual(response_spy.call_count, 0)
        response_spy.assert_not_called()

        self.assertEqual(mock_publish_message_events.call_count, 0)
        self.assertEqual(mock_publish_message_events.await_count, 0)
        mock_publish_message_events.assert_not_called()
        mock_publish_message_events.assert_not_awaited()

        self.assertEqual(mock_publish_runtime_events.call_count, 0)
        self.assertEqual(mock_publish_runtime_events.await_count, 0)
        mock_publish_runtime_events.assert_not_called()
        mock_publish_runtime_events.assert_not_awaited()

    async def test_commit_upload_batch_sender_name_resolution_failure_aborts_before_downstream(self) -> None:
        """Requirement 3: sender-name resolution failure propagates and aborts before downstream calls."""
        sender_name_exc = RuntimeError("synthetic sender-name resolution failure at resolve_direct_sender_display_name")
        call_order: list[str] = []

        async def _fake_get_batch(*args, **kwargs):
            call_order.append("get_upload_batch_for_current_user")
            return self.lookup_batch

        async def _fake_commit_batch(*args, **kwargs):
            call_order.append("commit_upload_batch")
            return self.commit_result

        async def _fake_serializer(*args, **kwargs):
            call_order.append("_serialize_direct_messages_with_accountant_contract")
            return self.serialized_messages

        async def _fake_resolve_sender(*args, **kwargs):
            call_order.append("resolve_direct_sender_display_name")
            raise sender_name_exc

        async def _fake_list_members(*args, **kwargs):
            call_order.append("list_active_room_member_user_ids")
            return [101, 102]

        async def _fake_publish_message_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_message_events")

        async def _fake_publish_runtime_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_runtime_events")

        mock_get_batch = AsyncMock(name="get_upload_batch_for_current_user", side_effect=_fake_get_batch)
        mock_commit_batch = AsyncMock(name="commit_upload_batch", side_effect=_fake_commit_batch)
        mock_serializer = AsyncMock(name="_serialize_direct_messages_with_accountant_contract", side_effect=_fake_serializer)
        mock_resolve_sender = AsyncMock(name="resolve_direct_sender_display_name", side_effect=_fake_resolve_sender)
        mock_list_members = AsyncMock(name="list_active_room_member_user_ids", side_effect=_fake_list_members)
        mock_publish_message_events = AsyncMock(name="_publish_upload_batch_commit_message_events", side_effect=_fake_publish_message_events)
        mock_publish_runtime_events = AsyncMock(name="_publish_upload_batch_commit_runtime_events", side_effect=_fake_publish_runtime_events)

        background_tasks = BackgroundTasks()
        real_add_task = background_tasks.add_task

        def _spy_add_task(*args, **kwargs):
            call_order.append("background_tasks.add_task")
            return real_add_task(*args, **kwargs)

        add_task_spy = MagicMock(name="add_task_spy", wraps=real_add_task, side_effect=_spy_add_task)
        background_tasks.add_task = add_task_spy

        def _spy_response(*args, **kwargs):
            call_order.append("UploadBatchCommitResponse")
            return UploadBatchCommitResponse(*args, **kwargs)

        response_spy = MagicMock(name="response_spy", wraps=UploadBatchCommitResponse, side_effect=_spy_response)

        with patch("api.routers.chat.get_upload_batch_for_current_user", new=mock_get_batch), \
             patch("api.routers.chat.commit_upload_batch", new=mock_commit_batch), \
             patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new=mock_serializer), \
             patch("api.routers.chat.resolve_direct_sender_display_name", new=mock_resolve_sender), \
             patch("api.routers.chat.list_active_room_member_user_ids", new=mock_list_members), \
             patch("api.routers.chat._publish_upload_batch_commit_message_events", new=mock_publish_message_events), \
             patch("api.routers.chat._publish_upload_batch_commit_runtime_events", new=mock_publish_runtime_events), \
             patch("api.routers.chat.UploadBatchCommitResponse", new=response_spy):
            with self.assertRaises(RuntimeError) as ctx:
                await commit_upload_batch_endpoint(
                    batch_id=self.batch_id,
                    background_tasks=background_tasks,
                    current_user=self.current_user,
                    db=self.db,
                )
            self.assertIs(ctx.exception, sender_name_exc)

        # Execution order through boundary
        self.assertEqual(
            call_order,
            [
                "get_upload_batch_for_current_user",
                "commit_upload_batch",
                "_serialize_direct_messages_with_accountant_contract",
                "resolve_direct_sender_display_name",
            ],
        )

        # 1. get_upload_batch_for_current_user assertions
        self.assertEqual(mock_get_batch.call_count, 1)
        self.assertEqual(mock_get_batch.await_count, 1)
        self.assertEqual(len(mock_get_batch.call_args.args), 1)
        self.assertIs(mock_get_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.call_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.call_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_get_batch.await_args.args), 1)
        self.assertIs(mock_get_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.await_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.await_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.await_args.kwargs["current_user"], self.current_user)

        # 2. commit_upload_batch assertions
        self.assertEqual(mock_commit_batch.call_count, 1)
        self.assertEqual(mock_commit_batch.await_count, 1)
        self.assertEqual(len(mock_commit_batch.call_args.args), 1)
        self.assertIs(mock_commit_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.call_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.call_args.kwargs["batch"], self.lookup_batch)
        self.assertIsNot(mock_commit_batch.call_args.kwargs["batch"], self.committed_batch)
        self.assertIs(mock_commit_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_commit_batch.await_args.args), 1)
        self.assertIs(mock_commit_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.await_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.await_args.kwargs["batch"], self.lookup_batch)
        self.assertIs(mock_commit_batch.await_args.kwargs["current_user"], self.current_user)

        # 3. _serialize_direct_messages_with_accountant_contract assertions
        self.assertEqual(mock_serializer.call_count, 1)
        self.assertEqual(mock_serializer.await_count, 1)
        self.assertEqual(len(mock_serializer.call_args.args), 2)
        self.assertIs(mock_serializer.call_args.args[0], self.db)
        self.assertIs(mock_serializer.call_args.args[1], self.committed_messages)
        self.assertIsNot(mock_serializer.call_args.args[1], self.serialized_messages)
        self.assertEqual(set(mock_serializer.call_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.call_args.kwargs["viewer_user_id"], self.current_user.id)
        self.assertEqual(len(mock_serializer.await_args.args), 2)
        self.assertIs(mock_serializer.await_args.args[0], self.db)
        self.assertIs(mock_serializer.await_args.args[1], self.committed_messages)
        self.assertEqual(set(mock_serializer.await_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.await_args.kwargs["viewer_user_id"], self.current_user.id)

        # 4. resolve_direct_sender_display_name assertions
        self.assertEqual(mock_resolve_sender.call_count, 1)
        self.assertEqual(mock_resolve_sender.await_count, 1)
        self.assertEqual(len(mock_resolve_sender.call_args.args), 1)
        self.assertIs(mock_resolve_sender.call_args.args[0], self.db)
        self.assertEqual(set(mock_resolve_sender.call_args.kwargs.keys()), {"user"})
        self.assertIs(mock_resolve_sender.call_args.kwargs["user"], self.current_user)
        self.assertEqual(len(mock_resolve_sender.await_args.args), 1)
        self.assertIs(mock_resolve_sender.await_args.args[0], self.db)
        self.assertEqual(set(mock_resolve_sender.await_args.kwargs.keys()), {"user"})
        self.assertIs(mock_resolve_sender.await_args.kwargs["user"], self.current_user)

        # Downstream uncalled and unawaited
        self.assertEqual(mock_list_members.call_count, 0)
        self.assertEqual(mock_list_members.await_count, 0)
        mock_list_members.assert_not_called()
        mock_list_members.assert_not_awaited()

        self.assertEqual(add_task_spy.call_count, 0)
        add_task_spy.assert_not_called()
        self.assertEqual(len(background_tasks.tasks), 0)

        self.assertEqual(response_spy.call_count, 0)
        response_spy.assert_not_called()

        self.assertEqual(mock_publish_message_events.call_count, 0)
        self.assertEqual(mock_publish_message_events.await_count, 0)
        mock_publish_message_events.assert_not_called()
        mock_publish_message_events.assert_not_awaited()

        self.assertEqual(mock_publish_runtime_events.call_count, 0)
        self.assertEqual(mock_publish_runtime_events.await_count, 0)
        mock_publish_runtime_events.assert_not_called()
        mock_publish_runtime_events.assert_not_awaited()

    async def test_commit_upload_batch_group_member_lookup_failure_aborts_before_downstream(self) -> None:
        """Requirement 4: group-member lookup failure propagates and aborts before downstream calls."""
        member_lookup_exc = RuntimeError("synthetic group-member lookup failure at list_active_room_member_user_ids")
        call_order: list[str] = []

        async def _fake_get_batch(*args, **kwargs):
            call_order.append("get_upload_batch_for_current_user")
            return self.lookup_batch

        async def _fake_commit_batch(*args, **kwargs):
            call_order.append("commit_upload_batch")
            return self.commit_result

        async def _fake_serializer(*args, **kwargs):
            call_order.append("_serialize_direct_messages_with_accountant_contract")
            return self.serialized_messages

        async def _fake_resolve_sender(*args, **kwargs):
            call_order.append("resolve_direct_sender_display_name")
            return "Resolved Sender Name"

        async def _fake_list_members(*args, **kwargs):
            call_order.append("list_active_room_member_user_ids")
            raise member_lookup_exc

        async def _fake_publish_message_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_message_events")

        async def _fake_publish_runtime_events(*args, **kwargs):
            call_order.append("_publish_upload_batch_commit_runtime_events")

        mock_get_batch = AsyncMock(name="get_upload_batch_for_current_user", side_effect=_fake_get_batch)
        mock_commit_batch = AsyncMock(name="commit_upload_batch", side_effect=_fake_commit_batch)
        mock_serializer = AsyncMock(name="_serialize_direct_messages_with_accountant_contract", side_effect=_fake_serializer)
        mock_resolve_sender = AsyncMock(name="resolve_direct_sender_display_name", side_effect=_fake_resolve_sender)
        mock_list_members = AsyncMock(name="list_active_room_member_user_ids", side_effect=_fake_list_members)
        mock_publish_message_events = AsyncMock(name="_publish_upload_batch_commit_message_events", side_effect=_fake_publish_message_events)
        mock_publish_runtime_events = AsyncMock(name="_publish_upload_batch_commit_runtime_events", side_effect=_fake_publish_runtime_events)

        background_tasks = BackgroundTasks()
        real_add_task = background_tasks.add_task

        def _spy_add_task(*args, **kwargs):
            call_order.append("background_tasks.add_task")
            return real_add_task(*args, **kwargs)

        add_task_spy = MagicMock(name="add_task_spy", wraps=real_add_task, side_effect=_spy_add_task)
        background_tasks.add_task = add_task_spy

        def _spy_response(*args, **kwargs):
            call_order.append("UploadBatchCommitResponse")
            return UploadBatchCommitResponse(*args, **kwargs)

        response_spy = MagicMock(name="response_spy", wraps=UploadBatchCommitResponse, side_effect=_spy_response)

        with patch("api.routers.chat.get_upload_batch_for_current_user", new=mock_get_batch), \
             patch("api.routers.chat.commit_upload_batch", new=mock_commit_batch), \
             patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new=mock_serializer), \
             patch("api.routers.chat.resolve_direct_sender_display_name", new=mock_resolve_sender), \
             patch("api.routers.chat.list_active_room_member_user_ids", new=mock_list_members), \
             patch("api.routers.chat._publish_upload_batch_commit_message_events", new=mock_publish_message_events), \
             patch("api.routers.chat._publish_upload_batch_commit_runtime_events", new=mock_publish_runtime_events), \
             patch("api.routers.chat.UploadBatchCommitResponse", new=response_spy):
            with self.assertRaises(RuntimeError) as ctx:
                await commit_upload_batch_endpoint(
                    batch_id=self.batch_id,
                    background_tasks=background_tasks,
                    current_user=self.current_user,
                    db=self.db,
                )
            self.assertIs(ctx.exception, member_lookup_exc)

        # Execution order through boundary
        self.assertEqual(
            call_order,
            [
                "get_upload_batch_for_current_user",
                "commit_upload_batch",
                "_serialize_direct_messages_with_accountant_contract",
                "resolve_direct_sender_display_name",
                "list_active_room_member_user_ids",
            ],
        )

        # 1. get_upload_batch_for_current_user assertions
        self.assertEqual(mock_get_batch.call_count, 1)
        self.assertEqual(mock_get_batch.await_count, 1)
        self.assertEqual(len(mock_get_batch.call_args.args), 1)
        self.assertIs(mock_get_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.call_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.call_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_get_batch.await_args.args), 1)
        self.assertIs(mock_get_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_get_batch.await_args.kwargs.keys()), {"batch_id", "current_user"})
        self.assertIs(mock_get_batch.await_args.kwargs["batch_id"], self.batch_id)
        self.assertIs(mock_get_batch.await_args.kwargs["current_user"], self.current_user)

        # 2. commit_upload_batch assertions
        self.assertEqual(mock_commit_batch.call_count, 1)
        self.assertEqual(mock_commit_batch.await_count, 1)
        self.assertEqual(len(mock_commit_batch.call_args.args), 1)
        self.assertIs(mock_commit_batch.call_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.call_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.call_args.kwargs["batch"], self.lookup_batch)
        self.assertIsNot(mock_commit_batch.call_args.kwargs["batch"], self.committed_batch)
        self.assertIs(mock_commit_batch.call_args.kwargs["current_user"], self.current_user)
        self.assertEqual(len(mock_commit_batch.await_args.args), 1)
        self.assertIs(mock_commit_batch.await_args.args[0], self.db)
        self.assertEqual(set(mock_commit_batch.await_args.kwargs.keys()), {"batch", "current_user"})
        self.assertIs(mock_commit_batch.await_args.kwargs["batch"], self.lookup_batch)
        self.assertIs(mock_commit_batch.await_args.kwargs["current_user"], self.current_user)

        # 3. _serialize_direct_messages_with_accountant_contract assertions
        self.assertEqual(mock_serializer.call_count, 1)
        self.assertEqual(mock_serializer.await_count, 1)
        self.assertEqual(len(mock_serializer.call_args.args), 2)
        self.assertIs(mock_serializer.call_args.args[0], self.db)
        self.assertIs(mock_serializer.call_args.args[1], self.committed_messages)
        self.assertIsNot(mock_serializer.call_args.args[1], self.serialized_messages)
        self.assertEqual(set(mock_serializer.call_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.call_args.kwargs["viewer_user_id"], self.current_user.id)
        self.assertEqual(len(mock_serializer.await_args.args), 2)
        self.assertIs(mock_serializer.await_args.args[0], self.db)
        self.assertIs(mock_serializer.await_args.args[1], self.committed_messages)
        self.assertEqual(set(mock_serializer.await_args.kwargs.keys()), {"viewer_user_id"})
        self.assertEqual(mock_serializer.await_args.kwargs["viewer_user_id"], self.current_user.id)

        # 4. resolve_direct_sender_display_name assertions
        self.assertEqual(mock_resolve_sender.call_count, 1)
        self.assertEqual(mock_resolve_sender.await_count, 1)
        self.assertEqual(len(mock_resolve_sender.call_args.args), 1)
        self.assertIs(mock_resolve_sender.call_args.args[0], self.db)
        self.assertEqual(set(mock_resolve_sender.call_args.kwargs.keys()), {"user"})
        self.assertIs(mock_resolve_sender.call_args.kwargs["user"], self.current_user)
        self.assertEqual(len(mock_resolve_sender.await_args.args), 1)
        self.assertIs(mock_resolve_sender.await_args.args[0], self.db)
        self.assertEqual(set(mock_resolve_sender.await_args.kwargs.keys()), {"user"})
        self.assertIs(mock_resolve_sender.await_args.kwargs["user"], self.current_user)

        # 5. list_active_room_member_user_ids assertions
        self.assertEqual(mock_list_members.call_count, 1)
        self.assertEqual(mock_list_members.await_count, 1)
        self.assertEqual(len(mock_list_members.call_args.args), 1)
        self.assertIs(mock_list_members.call_args.args[0], self.db)
        self.assertEqual(set(mock_list_members.call_args.kwargs.keys()), {"chat_id"})
        self.assertEqual(mock_list_members.call_args.kwargs["chat_id"], self.room_target_id)
        self.assertNotEqual(mock_list_members.call_args.kwargs["chat_id"], self.current_user.id)
        self.assertNotEqual(mock_list_members.call_args.kwargs["chat_id"], self.lookup_batch.target_id)
        self.assertEqual(len(mock_list_members.await_args.args), 1)
        self.assertIs(mock_list_members.await_args.args[0], self.db)
        self.assertEqual(set(mock_list_members.await_args.kwargs.keys()), {"chat_id"})
        self.assertEqual(mock_list_members.await_args.kwargs["chat_id"], self.room_target_id)

        # Downstream uncalled and unawaited
        self.assertEqual(add_task_spy.call_count, 0)
        add_task_spy.assert_not_called()
        self.assertEqual(len(background_tasks.tasks), 0)

        self.assertEqual(response_spy.call_count, 0)
        response_spy.assert_not_called()

        self.assertEqual(mock_publish_message_events.call_count, 0)
        self.assertEqual(mock_publish_message_events.await_count, 0)
        mock_publish_message_events.assert_not_called()
        mock_publish_message_events.assert_not_awaited()

        self.assertEqual(mock_publish_runtime_events.call_count, 0)
        self.assertEqual(mock_publish_runtime_events.await_count, 0)
        mock_publish_runtime_events.assert_not_called()
        mock_publish_runtime_events.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
