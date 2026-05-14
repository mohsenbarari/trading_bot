import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from core.services.chat_upload_session_service import (
    UploadBatchCommitResult,
    append_upload_chunk,
    commit_upload_batch,
    finalize_upload_session,
    persist_chat_media_file_bytes,
)
from models.upload_session import (
    UploadBatchMessageKind,
    UploadBatchStatus,
    UploadCaptionPolicy,
    UploadMediaType,
    UploadRoomKind,
    UploadSessionStatus,
)


class FakeAsyncFile:
    def __init__(self, read_result: bytes | None = None):
        self.write = AsyncMock()
        self.read = AsyncMock(return_value=read_result)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDB:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.delete = AsyncMock()
        self.get = AsyncMock(return_value=None)

    def add(self, obj):
        self.added.append(obj)


class ChatUploadSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_persist_chat_media_file_bytes_validates_and_writes(self):
        db = FakeDB()
        fake_file = FakeAsyncFile()

        async def fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("core.services.chat_upload_session_service._ensure_directory", new=AsyncMock()), patch(
            "core.services.chat_upload_session_service.asyncio.to_thread",
            new=AsyncMock(side_effect=fake_to_thread),
        ), patch(
            "core.services.chat_upload_session_service.magic.from_buffer",
            return_value="application/pdf",
        ), patch(
            "core.services.chat_upload_session_service.uuid.uuid4",
            return_value="uuid-1",
        ), patch(
            "core.services.chat_upload_session_service.aiofiles.open",
            return_value=fake_file,
        ):
            result = await persist_chat_media_file_bytes(
                db,
                uploader_id=5,
                file_name="report.pdf",
                declared_content_type="application/pdf",
                contents=b"pdf-bytes",
                thumbnail="thumb",
            )

        fake_file.write.assert_awaited_once_with(b"pdf-bytes")
        db.flush.assert_awaited_once()
        self.assertEqual(result.chat_file.id, "uuid-1")
        self.assertEqual(result.chat_file.file_name, "report.pdf")
        self.assertEqual(result.chat_file.mime_type, "application/pdf")
        self.assertEqual(result.chat_file.thumbnail, "thumb")

    async def test_append_upload_chunk_updates_offsets_and_rejects_mismatch(self):
        db = FakeDB()
        session = SimpleNamespace(
            id="sess-1",
            status=UploadSessionStatus.CREATED,
            resume_token="resume-1",
            next_offset=0,
            received_bytes=0,
            total_bytes=6,
            chunk_count=0,
            retry_count=0,
            last_error=None,
            temp_storage_path="/tmp/sess-1.part",
            batch_id=None,
            expires_at=__import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc),
        )

        fake_file = FakeAsyncFile()
        with patch("core.services.chat_upload_session_service.aiofiles.open", return_value=fake_file), patch(
            "core.services.chat_upload_session_service._ensure_directory",
            new=AsyncMock(),
        ):
            updated = await append_upload_chunk(
                db,
                session=session,
                resume_token="resume-1",
                offset=0,
                chunk_bytes=b"abc",
                is_last_chunk=False,
            )

        self.assertIs(updated, session)
        self.assertEqual(session.received_bytes, 3)
        self.assertEqual(session.next_offset, 3)
        self.assertEqual(session.status, UploadSessionStatus.UPLOADING)
        fake_file.write.assert_awaited_once_with(b"abc")
        db.commit.assert_awaited_once()

        with self.assertRaises(HTTPException) as exc_info:
            await append_upload_chunk(
                db,
                session=session,
                resume_token="resume-1",
                offset=0,
                chunk_bytes=b"x",
                is_last_chunk=False,
            )
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_finalize_upload_session_persists_chat_file_and_marks_ready(self):
        db = FakeDB()
        session = SimpleNamespace(
            id="sess-2",
            status=UploadSessionStatus.UPLOADED,
            received_bytes=4,
            total_bytes=4,
            temp_storage_path="/tmp/sess-2.part",
            original_file_name="voice.ogg",
            mime_type="audio/ogg",
            preview_metadata={"thumbnail": None, "durationMs": 8000},
            final_chat_file_id=None,
            batch_id=None,
            retry_count=0,
            last_error=None,
            expires_at=__import__("datetime").datetime.max.replace(tzinfo=__import__("datetime").timezone.utc),
        )
        current_user = SimpleNamespace(id=5)
        fake_file = FakeAsyncFile(read_result=b"data")

        with patch("core.services.chat_upload_session_service.os.path.exists", return_value=True), patch(
            "core.services.chat_upload_session_service.aiofiles.open",
            return_value=fake_file,
        ), patch(
            "core.services.chat_upload_session_service.persist_chat_media_file_bytes",
            new=AsyncMock(return_value=SimpleNamespace(chat_file=SimpleNamespace(id="file-9", thumbnail=None), width=None, height=None)),
        ), patch(
            "core.services.chat_upload_session_service._remove_file_if_exists",
            new=AsyncMock(),
        ) as remove_mock:
            updated = await finalize_upload_session(
                db,
                session=session,
                current_user=current_user,
            )

        self.assertIs(updated, session)
        self.assertEqual(session.status, UploadSessionStatus.READY)
        self.assertEqual(session.final_chat_file_id, "file-9")
        remove_mock.assert_awaited_once_with("/tmp/sess-2.part")
        self.assertGreaterEqual(db.commit.await_count, 2)

    async def test_commit_upload_batch_marks_sessions_and_batch_committed(self):
        db = FakeDB()
        batch = SimpleNamespace(
            id="batch-1",
            status=UploadBatchStatus.UPLOADED,
            room_kind=UploadRoomKind.DIRECT,
            target_id=9,
            expected_items=1,
            message_kind=UploadBatchMessageKind.SINGLE,
            caption_policy=UploadCaptionPolicy.NONE,
            committed_items=0,
        )
        session = SimpleNamespace(
            id="sess-3",
            status=UploadSessionStatus.READY,
            media_type=UploadMediaType.DOCUMENT,
            final_chat_file_id="file-5",
            original_file_name="doc.pdf",
            mime_type="application/pdf",
            total_bytes=123,
            preview_metadata={},
        )
        current_user = SimpleNamespace(id=5)
        receiver = SimpleNamespace(id=9)
        persisted_message = SimpleNamespace(id=101)
        delivered_message = SimpleNamespace(id=101)

        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False), SimpleNamespace(room_kind=UploadRoomKind.DIRECT, target_id=9, receiver=receiver, chat=None))),
        ), patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[session]),
        ), patch(
            "core.services.chat_upload_session_service._persist_batch_direct_message",
            new=AsyncMock(return_value=persisted_message),
        ) as persist_mock, patch(
            "core.services.chat_upload_session_service._reload_messages_for_delivery",
            new=AsyncMock(return_value=[delivered_message]),
        ):
            result = await commit_upload_batch(
                db,
                batch=batch,
                current_user=current_user,
            )

        persist_mock.assert_awaited_once()
        self.assertIsInstance(result, UploadBatchCommitResult)
        self.assertEqual(batch.status, UploadBatchStatus.COMMITTED)
        self.assertEqual(batch.committed_items, 1)
        self.assertEqual(session.status, UploadSessionStatus.COMMITTED)
        self.assertEqual(session.preview_metadata["committed_message_id"], 101)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()