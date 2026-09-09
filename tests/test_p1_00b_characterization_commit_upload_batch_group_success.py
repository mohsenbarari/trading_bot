"""Characterization tests for group upload batch commit response mapping and deferred audience forwarding.

Seed: API-MSG-POST-CHAT-COMMIT-UPLOAD-BATCH-ENDPOINT-L2675
Tracked Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001 (.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md)

Lifecycle State:
- P1-00 COMPLETE
- P1-00b IN_PROGRESS
- BLOCKED_EXPLICIT

Evidence Scope:
Group commit_upload_batch_endpoint mapping and deferred audience forwarding with receiver=None (nonempty vs empty member lists).
Handler-boundary evidence only.
No live persistence, delivery, concurrency, process-reload, gap closure, or Stage acceptance claim.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import BackgroundTasks

from api.routers.chat import commit_upload_batch_endpoint
from api.routers.chat_schemas import MessageRead, UploadBatchCommitResponse
from core.enums import MessageType
from core.services.chat_upload_session_service import (
    ResolvedUploadTarget,
    UploadBatchCommitResult,
)
from models.upload_session import UploadBatchStatus, UploadRoomKind


class P100bCommitUploadBatchGroupSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for successful group upload batch commit mapping."""

    async def _assert_group_commit_success(
        self,
        *,
        member_user_ids: list[int],
        sender_name: str | None = "Synthetic Group Sender",
    ) -> None:
        expected_audience = tuple(member_user_ids)
        batch_id = "batch-lookup-group-111"
        current_user = SimpleNamespace(id=42, account_name="group_sender_user")
        db = SimpleNamespace(label="synthetic-async-session")

        lookup_batch = SimpleNamespace(
            id=batch_id,
            status=UploadBatchStatus.UPLOADING,
            committed_items=0,
            target_id=301,
            owner_user_id=42,
            actor_user_id=42,
        )
        committed_batch = SimpleNamespace(
            id="batch-committed-group-222",
            status=UploadBatchStatus.COMMITTED,
            committed_items=2,
            target_id=302,
            owner_user_id=42,
            actor_user_id=42,
        )
        chat = SimpleNamespace(id=303)
        target = ResolvedUploadTarget(
            room_kind=UploadRoomKind.GROUP,
            target_id=300,
            receiver=None,
            chat=chat,
        )

        raw_msg1 = SimpleNamespace(id=101, content="raw-group-payload-1")
        raw_msg2 = SimpleNamespace(id=102, content="raw-group-payload-2")
        raw_messages = [raw_msg1, raw_msg2]

        now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
        ser_msg1 = MessageRead(
            id=201,
            sender_id=42,
            receiver_id=42,
            content="ser-group-text-1",
            message_type=MessageType.TEXT,
            is_read=True,
            created_at=now,
        )
        ser_msg2 = MessageRead(
            id=202,
            sender_id=42,
            receiver_id=42,
            content="ser-group-image-2",
            message_type=MessageType.IMAGE,
            is_read=True,
            created_at=now,
        )
        serialized_messages = [ser_msg1, ser_msg2]

        commit_result = UploadBatchCommitResult(
            batch=committed_batch,
            target=target,
            messages=raw_messages,
        )

        background_tasks = BackgroundTasks()
        call_order: list[str] = []

        async def fake_lookup(session, *, batch_id, current_user):
            call_order.append("lookup")
            return lookup_batch

        async def fake_commit(session, *, batch, current_user):
            call_order.append("commit")
            return commit_result

        async def fake_serializer(session, messages, *, viewer_user_id=None):
            call_order.append("serializer")
            return serialized_messages

        async def fake_sender_resolver(session, *, user):
            call_order.append("sender_resolver")
            return sender_name

        async def fake_room_members(session, *, chat_id):
            call_order.append("member_lookup")
            return member_user_ids

        with patch("api.routers.chat.get_upload_batch_for_current_user", new_callable=AsyncMock) as mock_lookup, \
             patch("api.routers.chat.commit_upload_batch", new_callable=AsyncMock) as mock_commit, \
             patch("api.routers.chat._serialize_direct_messages_with_accountant_contract", new_callable=AsyncMock) as mock_serializer, \
             patch("api.routers.chat.resolve_direct_sender_display_name", new_callable=AsyncMock) as mock_sender_resolver, \
             patch("api.routers.chat.list_active_room_member_user_ids", new_callable=AsyncMock) as mock_room_members, \
             patch("api.routers.chat._publish_upload_batch_commit_message_events", new_callable=AsyncMock) as mock_publish_msg, \
             patch("api.routers.chat._publish_upload_batch_commit_runtime_events", new_callable=AsyncMock) as mock_publish_runtime:

            mock_lookup.side_effect = fake_lookup
            mock_commit.side_effect = fake_commit
            mock_serializer.side_effect = fake_serializer
            mock_sender_resolver.side_effect = fake_sender_resolver
            mock_room_members.side_effect = fake_room_members

            response = await commit_upload_batch_endpoint(
                batch_id=batch_id,
                background_tasks=background_tasks,
                current_user=current_user,
                db=db,
            )

            # Ordered lookup -> commit -> serializer -> sender resolver -> member lookup
            self.assertEqual(
                call_order,
                ["lookup", "commit", "serializer", "sender_resolver", "member_lookup"],
            )

            # Independent call and await counts for all five active boundaries
            self.assertEqual(mock_lookup.call_count, 1)
            self.assertEqual(mock_lookup.await_count, 1)
            mock_lookup.assert_awaited_once_with(db, batch_id=batch_id, current_user=current_user)
            self.assertIs(mock_lookup.call_args[0][0], db)
            self.assertEqual(mock_lookup.call_args[1]["batch_id"], batch_id)
            self.assertIs(mock_lookup.call_args[1]["current_user"], current_user)

            self.assertEqual(mock_commit.call_count, 1)
            self.assertEqual(mock_commit.await_count, 1)
            mock_commit.assert_awaited_once_with(db, batch=lookup_batch, current_user=current_user)
            self.assertIs(mock_commit.call_args[0][0], db)
            self.assertIs(mock_commit.call_args[1]["batch"], lookup_batch)
            self.assertIsNot(mock_commit.call_args[1]["batch"], committed_batch)
            self.assertIs(mock_commit.call_args[1]["current_user"], current_user)

            self.assertEqual(mock_serializer.call_count, 1)
            self.assertEqual(mock_serializer.await_count, 1)
            mock_serializer.assert_awaited_once_with(db, raw_messages, viewer_user_id=42)
            self.assertIs(mock_serializer.call_args[0][0], db)
            self.assertIs(mock_serializer.call_args[0][1], raw_messages)
            self.assertEqual(mock_serializer.call_args[1]["viewer_user_id"], 42)

            self.assertEqual(mock_sender_resolver.call_count, 1)
            self.assertEqual(mock_sender_resolver.await_count, 1)
            mock_sender_resolver.assert_awaited_once_with(db, user=current_user)
            self.assertIs(mock_sender_resolver.call_args[0][0], db)
            self.assertIs(mock_sender_resolver.call_args[1]["user"], current_user)

            self.assertEqual(mock_room_members.call_count, 1)
            self.assertEqual(mock_room_members.await_count, 1)
            mock_room_members.assert_awaited_once_with(db, chat_id=300)
            self.assertIs(mock_room_members.call_args[0][0], db)
            self.assertEqual(mock_room_members.call_args[1]["chat_id"], 300)
            self.assertNotEqual(mock_room_members.call_args[1]["chat_id"], current_user.id)
            self.assertNotEqual(mock_room_members.call_args[1]["chat_id"], lookup_batch.target_id)
            self.assertNotEqual(mock_room_members.call_args[1]["chat_id"], committed_batch.target_id)
            self.assertNotEqual(mock_room_members.call_args[1]["chat_id"], target.chat.id)

            # Response mapping assertions
            self.assertIsInstance(response, UploadBatchCommitResponse)
            self.assertEqual(response.batch_id, "batch-committed-group-222")
            self.assertNotEqual(response.batch_id, lookup_batch.id)
            self.assertEqual(response.status, UploadBatchStatus.COMMITTED)
            self.assertNotEqual(response.status, lookup_batch.status)
            self.assertEqual(response.committed_items, 2)
            self.assertNotEqual(response.committed_items, lookup_batch.committed_items)

            self.assertEqual(len(response.messages), 2)
            self.assertIs(response.messages[0], ser_msg1)
            self.assertIs(response.messages[1], ser_msg2)
            self.assertEqual(response.messages[0].id, 201)
            self.assertEqual(response.messages[0].content, "ser-group-text-1")
            self.assertEqual(response.messages[0].message_type, MessageType.TEXT)
            self.assertEqual(response.messages[1].id, 202)
            self.assertEqual(response.messages[1].content, "ser-group-image-2")
            self.assertEqual(response.messages[1].message_type, MessageType.IMAGE)
            self.assertNotEqual(response.messages[0].id, raw_msg1.id)
            self.assertNotEqual(response.messages[0].content, raw_msg1.content)
            self.assertNotEqual(response.messages[1].id, raw_msg2.id)
            self.assertNotEqual(response.messages[1].content, raw_msg2.content)

            # Deferred background task assertions
            self.assertEqual(len(background_tasks.tasks), 2)

            t0 = background_tasks.tasks[0]
            self.assertIs(t0.func, mock_publish_msg)
            self.assertEqual(t0.args, ())
            self.assertEqual(
                t0.kwargs,
                {
                    "target": target,
                    "messages": raw_messages,
                    "serialized_messages": serialized_messages,
                    "sender_name": sender_name,
                    "group_member_user_ids": member_user_ids,
                },
            )
            self.assertIs(t0.kwargs["target"], target)
            self.assertIs(t0.kwargs["messages"], raw_messages)
            self.assertIs(t0.kwargs["serialized_messages"], serialized_messages)
            self.assertEqual(t0.kwargs["sender_name"], sender_name)

            # Audience content and identity assertions verified against pre-call oracle
            self.assertIs(t0.kwargs["group_member_user_ids"], member_user_ids)
            self.assertEqual(tuple(member_user_ids), expected_audience)
            self.assertEqual(tuple(t0.kwargs["group_member_user_ids"]), expected_audience)
            self.assertEqual(t0.kwargs["group_member_user_ids"], list(expected_audience))

            if len(expected_audience) == 0:
                self.assertIsNotNone(t0.kwargs["group_member_user_ids"])
                self.assertEqual(t0.kwargs["group_member_user_ids"], [])
            else:
                self.assertIn(current_user.id, t0.kwargs["group_member_user_ids"])
                self.assertEqual(len(t0.kwargs["group_member_user_ids"]), len(expected_audience))

            t1 = background_tasks.tasks[1]
            self.assertIs(t1.func, mock_publish_runtime)
            self.assertEqual(t1.args, ())
            self.assertEqual(
                t1.kwargs,
                {
                    "batch_id": "batch-committed-group-222",
                    "current_user_id": 42,
                    "batch_status": UploadBatchStatus.COMMITTED,
                },
            )
            self.assertEqual(t1.kwargs["batch_id"], committed_batch.id)
            self.assertNotEqual(t1.kwargs["batch_id"], lookup_batch.id)
            self.assertEqual(t1.kwargs["current_user_id"], current_user.id)
            self.assertEqual(t1.kwargs["batch_status"], committed_batch.status)
            self.assertNotEqual(t1.kwargs["batch_status"], lookup_batch.status)

            # Deferred publishers must remain uncalled and unawaited before task execution
            self.assertEqual(mock_publish_msg.call_count, 0)
            self.assertEqual(mock_publish_msg.await_count, 0)
            mock_publish_msg.assert_not_called()
            mock_publish_msg.assert_not_awaited()

            self.assertEqual(mock_publish_runtime.call_count, 0)
            self.assertEqual(mock_publish_runtime.await_count, 0)
            mock_publish_runtime.assert_not_called()
            mock_publish_runtime.assert_not_awaited()

    async def test_commit_upload_batch_group_success_with_nonempty_member_list(self) -> None:
        await self._assert_group_commit_success(
            member_user_ids=[88, 42, 77],
            sender_name="Synthetic Group Sender",
        )

    async def test_commit_upload_batch_group_success_with_empty_member_list(self) -> None:
        await self._assert_group_commit_success(
            member_user_ids=[],
            sender_name="Synthetic Group Sender",
        )


if __name__ == "__main__":
    unittest.main()
