import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from models.upload_session import UploadBatchStatus, UploadSessionStatus
from api.routers.chat_schemas import MessageRead
from api.routers.chat import (
    _publish_upload_session_runtime_event,
    cancel_upload_batch_endpoint,
    cancel_upload_session_endpoint,
    commit_upload_batch_endpoint,
    finalize_upload_session_endpoint,
    get_upload_session_state,
    patch_upload_session_chunk,
    post_upload_batch,
    post_upload_session,
)


class FakeUploadFile:
    def __init__(self, contents: bytes):
        self._contents = contents
        self.close = AsyncMock()

    async def read(self):
        return self._contents


class ChatRouterUploadSessionEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_upload_batch_delegates_to_service(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        data = SimpleNamespace(
            room_kind="direct",
            target_id=9,
            message_kind="single",
            expected_items=1,
            caption_policy="none",
            idempotency_key="idem-key-1",
        )
        batch = SimpleNamespace(id="batch-1", status=UploadBatchStatus.COLLECTING, expires_at="2026-05-14T00:00:00Z")

        with patch("api.routers.chat.create_upload_batch", new=AsyncMock(return_value=batch)) as create_mock:
            result = await post_upload_batch(data=data, current_user=current_user, db=db)

        create_mock.assert_awaited_once_with(
            db,
            current_user=current_user,
            room_kind="direct",
            target_id=9,
            message_kind="single",
            expected_items=1,
            caption_policy="none",
            idempotency_key="idem-key-1",
        )
        self.assertEqual(result.batch_id, "batch-1")

    async def test_post_upload_session_and_state_delegate_to_service(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        data = SimpleNamespace(
            batch_id="batch-1",
            room_kind="direct",
            target_id=9,
            media_type="document",
            file_name="doc.pdf",
            mime_type="application/pdf",
            total_bytes=123,
            chunk_size=64,
            preview_metadata=SimpleNamespace(model_dump=lambda exclude_none=True: {"caption": "x"}),
            sha256_full=None,
        )
        session = SimpleNamespace(
            id="sess-1",
            resume_token="token-1",
            next_offset=0,
            chunk_size=64,
            expires_at="2026-05-14T00:00:00Z",
            status=UploadSessionStatus.CREATED,
            received_bytes=0,
            total_bytes=123,
            preview_metadata={"caption": "x"},
            final_chat_file_id=None,
        )

        with patch("api.routers.chat.create_upload_session", new=AsyncMock(return_value=session)) as create_mock, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            created = await post_upload_session(data=data, current_user=current_user, db=db)

        create_mock.assert_awaited_once()
        publish_runtime_mock.assert_awaited_once_with(
            db=db,
            current_user=current_user,
            session=session,
            event_name="created",
        )
        self.assertEqual(created.session_id, "sess-1")

        with patch("api.routers.chat.get_upload_session_for_current_user", new=AsyncMock(return_value=session)) as get_mock:
            state = await get_upload_session_state(session_id="sess-1", current_user=current_user, db=db)

        get_mock.assert_awaited_once_with(db, session_id="sess-1", current_user=current_user)
        self.assertEqual(state.session_id, "sess-1")
        self.assertEqual(state.total_bytes, 123)

    async def test_patch_finalize_and_cancel_upload_session_delegate_to_service(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        session = SimpleNamespace(
            id="sess-2",
            received_bytes=10,
            next_offset=10,
            status=UploadSessionStatus.UPLOADING,
            final_chat_file_id=None,
        )
        upload_file = FakeUploadFile(b"chunk")

        with patch("api.routers.chat.get_upload_session_for_current_user", new=AsyncMock(return_value=session)) as get_mock, patch(
            "api.routers.chat.read_upload_chunk_bytes",
            new=AsyncMock(return_value=b"chunk"),
        ) as read_mock, patch(
            "api.routers.chat.append_upload_chunk",
            new=AsyncMock(return_value=session),
        ) as append_mock, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await patch_upload_session_chunk(
                session_id="sess-2",
                resume_token="token",
                offset=0,
                is_last_chunk=False,
                chunk=upload_file,
                current_user=current_user,
                db=db,
            )

        get_mock.assert_awaited_once_with(db, session_id="sess-2", current_user=current_user)
        read_mock.assert_awaited_once_with(upload_file)
        append_mock.assert_awaited_once_with(
            db,
            session=session,
            resume_token="token",
            offset=0,
            chunk_bytes=b"chunk",
            is_last_chunk=False,
        )
        publish_runtime_mock.assert_awaited_once_with(
            db=db,
            current_user=current_user,
            session=session,
            event_name="progress",
        )
        self.assertEqual(result.session_id, "sess-2")

        ready_session = SimpleNamespace(id="sess-2", status=UploadSessionStatus.READY, final_chat_file_id="file-7")
        with patch("api.routers.chat.get_upload_session_for_current_user", new=AsyncMock(return_value=session)), patch(
            "api.routers.chat.finalize_upload_session",
            new=AsyncMock(return_value=ready_session),
        ) as finalize_mock, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await finalize_upload_session_endpoint(session_id="sess-2", current_user=current_user, db=db)

        finalize_mock.assert_awaited_once_with(db, session=session, current_user=current_user)
        publish_runtime_mock.assert_awaited_once_with(
            db=db,
            current_user=current_user,
            session=ready_session,
            event_name="ready",
        )
        self.assertEqual(result.final_chat_file_id, "file-7")

        failed_session = SimpleNamespace(
            id="sess-2",
            status=UploadSessionStatus.FAILED,
            final_chat_file_id=None,
            last_error="boom",
            retry_count=1,
        )

        async def _raise_finalize_failure(*args, **kwargs):
            session.status = failed_session.status
            session.final_chat_file_id = failed_session.final_chat_file_id
            session.last_error = failed_session.last_error
            session.retry_count = failed_session.retry_count
            raise HTTPException(status_code=500, detail="Failed to finalize upload session")

        with patch("api.routers.chat.get_upload_session_for_current_user", new=AsyncMock(return_value=session)), patch(
            "api.routers.chat.finalize_upload_session",
            new=AsyncMock(side_effect=_raise_finalize_failure),
        ), patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            with self.assertRaises(HTTPException):
                await finalize_upload_session_endpoint(session_id="sess-2", current_user=current_user, db=db)

        publish_runtime_mock.assert_awaited_once_with(
            db=db,
            current_user=current_user,
            session=session,
            event_name="failed",
        )

        cancelled = SimpleNamespace(id="sess-2", status=UploadSessionStatus.CANCELLED, final_chat_file_id=None)
        with patch("api.routers.chat.get_upload_session_for_current_user", new=AsyncMock(return_value=session)), patch(
            "api.routers.chat.cancel_upload_session",
            new=AsyncMock(return_value=cancelled),
        ) as cancel_mock, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await cancel_upload_session_endpoint(session_id="sess-2", current_user=current_user, db=db)

        cancel_mock.assert_awaited_once_with(db, session=session)
        publish_runtime_mock.assert_awaited_once_with(
            db=db,
            current_user=current_user,
            session=cancelled,
            event_name="cancelled",
        )
        self.assertEqual(result.status, UploadSessionStatus.CANCELLED)

    async def test_commit_upload_batch_publishes_direct_and_group_branches(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        batch = SimpleNamespace(id="batch-9")
        messages = [SimpleNamespace(id=101), SimpleNamespace(id=102)]
        now = datetime.now(timezone.utc)
        serialized_messages = [
            MessageRead(
                id=101,
                sender_id=5,
                receiver_id=9,
                content='{"file_id":"file-1"}',
                message_type="document",
                is_read=False,
                created_at=now,
            ),
            MessageRead(
                id=102,
                sender_id=5,
                receiver_id=9,
                content='{"file_id":"file-2"}',
                message_type="document",
                is_read=False,
                created_at=now,
            ),
        ]

        direct_result = SimpleNamespace(
            batch=SimpleNamespace(id="batch-9", status=UploadBatchStatus.COMMITTED, committed_items=2),
            target=SimpleNamespace(target_id=9, receiver=SimpleNamespace(id=9), chat=None),
            messages=messages,
        )
        committed_sessions = [
            SimpleNamespace(id="sess-1", batch_id="batch-9"),
            SimpleNamespace(id="sess-2", batch_id="batch-9"),
        ]

        with patch("api.routers.chat.get_upload_batch_for_current_user", new=AsyncMock(return_value=batch)), patch(
            "api.routers.chat.commit_upload_batch",
            new=AsyncMock(return_value=direct_result),
        ) as commit_mock, patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            new=AsyncMock(return_value=serialized_messages),
        ), patch(
            "api.routers.chat.publish_direct_message_event",
            new=AsyncMock(),
        ) as direct_publish_mock, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new=AsyncMock(return_value=committed_sessions),
        ), patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await commit_upload_batch_endpoint(batch_id="batch-9", current_user=current_user, db=db)

        commit_mock.assert_awaited_once_with(db, batch=batch, current_user=current_user)
        self.assertEqual(direct_publish_mock.await_count, 2)
        self.assertEqual(publish_runtime_mock.await_count, 2)
        self.assertEqual(result.committed_items, 2)

        group_result = SimpleNamespace(
            batch=SimpleNamespace(id="batch-10", status=UploadBatchStatus.COMMITTED, committed_items=1),
            target=SimpleNamespace(target_id=70, receiver=None, chat=SimpleNamespace(id=70)),
            messages=[SimpleNamespace(id=201)],
        )
        with patch("api.routers.chat.get_upload_batch_for_current_user", new=AsyncMock(return_value=batch)), patch(
            "api.routers.chat.commit_upload_batch",
            new=AsyncMock(return_value=group_result),
        ), patch(
            "api.routers.chat._serialize_direct_messages_with_accountant_contract",
            new=AsyncMock(return_value=[
                MessageRead(
                    id=201,
                    sender_id=5,
                    receiver_id=5,
                    content='{"file_id":"file-3"}',
                    message_type="image",
                    is_read=True,
                    created_at=now,
                )
            ]),
        ), patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 6]),
        ) as users_mock, patch(
            "api.routers.chat.publish_group_message_event",
            new=AsyncMock(),
        ) as group_publish_mock, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new=AsyncMock(return_value=[SimpleNamespace(id="sess-3", batch_id="batch-10")]),
        ), patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await commit_upload_batch_endpoint(batch_id="batch-10", current_user=current_user, db=db)

        users_mock.assert_awaited_once_with(db, chat_id=70)
        group_publish_mock.assert_awaited_once()
        publish_runtime_mock.assert_awaited_once()
        self.assertEqual(result.batch_id, "batch-10")

    async def test_cancel_upload_batch_delegates(self):
        current_user = SimpleNamespace(id=5)
        db = object()
        batch = SimpleNamespace(id="batch-11", status=UploadBatchStatus.CANCELLED)

        with patch("api.routers.chat.get_upload_batch_for_current_user", new=AsyncMock(return_value=batch)), patch(
            "api.routers.chat.cancel_upload_batch",
            new=AsyncMock(return_value=batch),
        ) as cancel_mock, patch(
            "api.routers.chat._list_batch_upload_sessions",
            new=AsyncMock(return_value=[SimpleNamespace(id="sess-4", batch_id="batch-11")]),
        ), patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new=AsyncMock(),
        ) as publish_runtime_mock:
            result = await cancel_upload_batch_endpoint(batch_id="batch-11", current_user=current_user, db=db)

        cancel_mock.assert_awaited_once_with(db, batch=batch)
        publish_runtime_mock.assert_awaited_once()
        self.assertEqual(result.status, UploadBatchStatus.CANCELLED)

    async def test_publish_upload_session_runtime_event_updates_direct_activity(self):
        current_user = SimpleNamespace(id=5, account_name="sender")
        db = object()
        session = SimpleNamespace(
            id="sess-direct",
            batch_id="batch-direct",
            owner_user_id=5,
            actor_user_id=5,
            room_kind="direct",
            target_id=9,
            media_type="image",
            status=UploadSessionStatus.UPLOADING,
            received_bytes=12,
            total_bytes=40,
            next_offset=12,
            final_chat_file_id=None,
            preview_metadata={"caption": "x"},
            retry_count=2,
            last_error="temporary",
        )

        with patch("api.routers.chat.publish_user_event", new=AsyncMock()) as publish_user_mock, patch(
            "api.routers.chat._increment_upload_session_observability_counters",
            new=AsyncMock(),
        ) as counter_mock, patch(
            "api.routers.chat._has_active_upload_sessions_for_room",
            new=AsyncMock(return_value=True),
        ), patch(
            "api.routers.chat.publish_direct_activity_event",
            new=AsyncMock(),
        ) as direct_activity_mock:
            await _publish_upload_session_runtime_event(
                db=db,
                current_user=current_user,
                session=session,
                event_name="progress",
            )

        publish_user_mock.assert_awaited_once()
        published_payload = publish_user_mock.await_args.args[2]
        self.assertEqual(published_payload["retry_count"], 2)
        self.assertEqual(published_payload["last_error"], "temporary")
        counter_mock.assert_awaited_once_with(event_name="progress", room_kind="direct", media_type="image")
        direct_activity_mock.assert_awaited_once_with(
            receiver_id=9,
            sender_id=5,
            sender_name="sender",
            activity="uploading_file",
            active=True,
            publisher=publish_user_mock,
        )

    async def test_publish_upload_session_runtime_event_updates_group_activity(self):
        current_user = SimpleNamespace(id=5, account_name="sender")
        chat = SimpleNamespace(id=70, is_deleted=False)
        session = SimpleNamespace(
            id="sess-group",
            batch_id="batch-group",
            owner_user_id=5,
            actor_user_id=7,
            room_kind="group",
            target_id=70,
            media_type="video",
            status=UploadSessionStatus.READY,
            received_bytes=90,
            total_bytes=90,
            next_offset=90,
            final_chat_file_id="file-7",
            preview_metadata={"album_index": 1},
            retry_count=0,
            last_error=None,
        )

        with patch("api.routers.chat.publish_user_event", new=AsyncMock()) as publish_user_mock, patch(
            "api.routers.chat._increment_upload_session_observability_counters",
            new=AsyncMock(),
        ) as counter_mock, patch(
            "api.routers.chat._has_active_upload_sessions_for_room",
            new=AsyncMock(return_value=False),
        ), patch(
            "api.routers.chat.list_active_room_member_user_ids",
            new=AsyncMock(return_value=[5, 6]),
        ), patch(
            "api.routers.chat.publish_room_activity_event",
            new=AsyncMock(),
        ) as room_activity_mock:
            db = SimpleNamespace(get=AsyncMock(return_value=chat))
            await _publish_upload_session_runtime_event(
                db=db,
                current_user=current_user,
                session=session,
                event_name="ready",
            )

        publish_user_mock.assert_awaited_once()
        counter_mock.assert_awaited_once_with(event_name="ready", room_kind="group", media_type="video")
        room_activity_mock.assert_awaited_once_with(
            chat=chat,
            sender_id=5,
            sender_name="sender",
            member_user_ids=[5, 6],
            activity="uploading_file",
            active=False,
            publisher=publish_user_mock,
        )


if __name__ == "__main__":
    unittest.main()