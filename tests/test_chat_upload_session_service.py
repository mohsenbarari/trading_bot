import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from core.services.chat_upload_session_service import (
    UploadBatchCommitResult,
    _build_final_message_content,
    _build_temp_storage_path,
    _batch_order_key,
    _cleanup_uncommitted_chat_file,
    _coalesce_actor_user_id,
    _ensure_directory,
    _expire_session_if_needed,
    _flush_if_supported,
    _get_existing_upload_batch_by_idempotency,
    _message_type_from_media_type,
    _normalize_preview_metadata,
    _persist_batch_group_message,
    _remove_file_if_exists,
    append_upload_chunk,
    cancel_upload_batch,
    cancel_upload_session,
    create_upload_batch,
    create_upload_session,
    commit_upload_batch,
    finalize_upload_session,
    get_upload_batch_for_current_user,
    get_upload_session_for_current_user,
    persist_chat_media_file_bytes,
    read_upload_chunk_bytes,
    resolve_upload_target,
)
from core.enums import ChatType
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


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class FakeExecuteResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return FakeScalarResult(self.rows)


class ChatUploadSessionServiceTests(unittest.IsolatedAsyncioTestCase):
    def _future(self):
        return datetime.now(timezone.utc) + timedelta(days=1)

    async def test_small_helpers_cover_actor_paths_storage_and_optional_flush(self):
        owner = SimpleNamespace(id=5)
        accountant_actor = SimpleNamespace(
            owner_user=owner,
            actor_user=SimpleNamespace(id=9),
            is_accountant_context=True,
        )
        owner_actor = SimpleNamespace(owner_user=owner, actor_user=owner, is_accountant_context=False)

        self.assertEqual(_coalesce_actor_user_id(accountant_actor), 9)
        self.assertIsNone(_coalesce_actor_user_id(owner_actor))
        self.assertEqual(_message_type_from_media_type(UploadMediaType.IMAGE).value, "image")
        self.assertEqual(_message_type_from_media_type(UploadMediaType.VIDEO).value, "video")
        self.assertEqual(_message_type_from_media_type(UploadMediaType.VOICE).value, "voice")
        self.assertEqual(_message_type_from_media_type(UploadMediaType.DOCUMENT).value, "document")
        self.assertEqual(_normalize_preview_metadata(None), {})
        self.assertEqual(_normalize_preview_metadata({"width": 10}), {"width": 10})
        self.assertTrue(_build_temp_storage_path(5, "sess-1").endswith("uploads/chat_sessions/5/sess-1.part"))

        await _flush_if_supported(SimpleNamespace())
        async_flush = AsyncMock()
        await _flush_if_supported(SimpleNamespace(flush=async_flush))
        async_flush.assert_awaited_once()

        with patch("core.services.chat_upload_session_service.asyncio.to_thread", new=AsyncMock()) as to_thread_mock:
            await _ensure_directory("uploads/tmp")
            to_thread_mock.assert_awaited_once()

        with patch("core.services.chat_upload_session_service.asyncio.to_thread", new=AsyncMock()) as to_thread_mock:
            await _remove_file_if_exists(None)
            to_thread_mock.assert_not_awaited()

        with patch("core.services.chat_upload_session_service.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn: fn())), patch(
            "core.services.chat_upload_session_service.os.path.exists",
            return_value=True,
        ), patch("core.services.chat_upload_session_service.os.remove", side_effect=FileNotFoundError) as remove_mock:
            await _remove_file_if_exists("gone.part")
            remove_mock.assert_called_once_with("gone.part")

    async def test_idempotency_lookup_filters_actor_null_and_actor_bound_batches(self):
        db = FakeDB()
        db.execute = AsyncMock(return_value=FakeExecuteResult(["batch-row"]))

        null_actor_result = await _get_existing_upload_batch_by_idempotency(
            db,
            owner_user_id=5,
            actor_user_id=None,
            room_kind=UploadRoomKind.DIRECT,
            target_id=9,
            idempotency_key="idem-1",
        )
        actor_result = await _get_existing_upload_batch_by_idempotency(
            db,
            owner_user_id=5,
            actor_user_id=11,
            room_kind=UploadRoomKind.GROUP,
            target_id=70,
            idempotency_key="idem-2",
        )

        self.assertEqual(null_actor_result, "batch-row")
        self.assertEqual(actor_result, "batch-row")
        self.assertEqual(db.execute.await_count, 2)

    async def test_create_upload_batch_persists_new_batch_when_existing_is_terminal(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        owner_actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)
        terminal_existing = SimpleNamespace(status=UploadBatchStatus.CANCELLED)

        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service._get_existing_upload_batch_by_idempotency",
            new=AsyncMock(return_value=terminal_existing),
        ):
            batch = await create_upload_batch(
                db,
                current_user=current_user,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                caption_policy=UploadCaptionPolicy.NONE,
                idempotency_key="fresh-after-terminal",
            )

        self.assertIs(db.added[-1], batch)
        self.assertEqual(batch.owner_user_id, 5)
        self.assertEqual(batch.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(batch.target_id, 9)
        self.assertEqual(batch.status, UploadBatchStatus.COLLECTING)
        db.commit.assert_awaited()

    async def test_get_upload_batch_rejects_actor_mismatch_after_owner_match(self):
        current_user = SimpleNamespace(id=5)
        actor_user = SimpleNamespace(id=9)
        effective_actor = SimpleNamespace(owner_user=current_user, actor_user=actor_user, is_accountant_context=True)
        db = FakeDB()
        db.get = AsyncMock(return_value=SimpleNamespace(owner_user_id=5, actor_user_id=10))

        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=effective_actor),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_upload_batch_for_current_user(db, batch_id="batch-actor-mismatch", current_user=current_user)

        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_create_upload_session_rejects_size_chunk_and_terminal_batch_guards(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        owner_actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)

        with self.assertRaises(HTTPException) as exc_info:
            await create_upload_session(
                db,
                current_user=current_user,
                batch_id=None,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                media_type=UploadMediaType.DOCUMENT,
                file_name="big.bin",
                mime_type="application/octet-stream",
                total_bytes=51 * 1024 * 1024,
                chunk_size=1,
                preview_metadata=None,
            )
        self.assertEqual(exc_info.exception.status_code, 413)

        with self.assertRaises(HTTPException) as exc_info:
            await create_upload_session(
                db,
                current_user=current_user,
                batch_id=None,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                media_type=UploadMediaType.DOCUMENT,
                file_name="doc.pdf",
                mime_type="application/pdf",
                total_bytes=10,
                chunk_size=0,
                preview_metadata=None,
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        terminal_batch = SimpleNamespace(
            id="batch-terminal",
            room_kind=UploadRoomKind.DIRECT,
            target_id=9,
            status=UploadBatchStatus.COMMITTED,
            expected_items=1,
        )
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service.get_upload_batch_for_current_user",
            new=AsyncMock(return_value=terminal_batch),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_upload_session(
                    db,
                    current_user=current_user,
                    batch_id="batch-terminal",
                    room_kind=UploadRoomKind.DIRECT,
                    target_id=9,
                    media_type=UploadMediaType.DOCUMENT,
                    file_name="doc.pdf",
                    mime_type="application/pdf",
                    total_bytes=10,
                    chunk_size=5,
                    preview_metadata=None,
                )
        self.assertEqual(exc_info.exception.status_code, 409)

    async def test_persist_chat_media_file_bytes_rejects_declared_real_and_oversized_files(self):
        db = FakeDB()
        with self.assertRaises(HTTPException) as exc_info:
            await persist_chat_media_file_bytes(
                db,
                uploader_id=5,
                file_name="script.sh",
                declared_content_type="application/x-shellscript",
                contents=b"echo no",
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        with patch(
            "core.services.chat_upload_session_service.asyncio.to_thread",
            new=AsyncMock(return_value="application/x-msdownload"),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await persist_chat_media_file_bytes(
                    db,
                    uploader_id=5,
                    file_name="fake.pdf",
                    declared_content_type="application/pdf",
                    contents=b"not-pdf",
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        async def fake_magic(fn, *args, **kwargs):
            return "application/pdf"

        with patch("core.services.chat_upload_session_service.asyncio.to_thread", new=AsyncMock(side_effect=fake_magic)):
            with self.assertRaises(HTTPException) as exc_info:
                await persist_chat_media_file_bytes(
                    db,
                    uploader_id=5,
                    file_name="huge.pdf",
                    declared_content_type="application/pdf",
                    contents=b"x" * (51 * 1024 * 1024),
                )
        self.assertEqual(exc_info.exception.status_code, 413)

        fake_file = FakeAsyncFile()
        async def fake_to_thread(fn, *args, **kwargs):
            result = fn(*args, **kwargs)
            return "video/webm" if result == "video/webm" else result

        with patch("core.services.chat_upload_session_service._ensure_directory", new=AsyncMock()), patch(
            "core.services.chat_upload_session_service.asyncio.to_thread",
            new=AsyncMock(side_effect=fake_to_thread),
        ), patch("core.services.chat_upload_session_service.magic.from_buffer", return_value="video/webm"), patch(
            "core.services.chat_upload_session_service.uuid.uuid4",
            return_value="voice-uuid",
        ), patch("core.services.chat_upload_session_service.aiofiles.open", return_value=fake_file):
            result = await persist_chat_media_file_bytes(
                db,
                uploader_id=5,
                file_name="voice.webm",
                declared_content_type="audio/webm",
                contents=b"voice-bytes",
            )

        self.assertEqual(result.chat_file.mime_type, "audio/webm")

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

    async def test_resolve_upload_target_direct_group_and_rejects_channel(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        owner_actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)
        receiver = SimpleNamespace(id=9)
        group = SimpleNamespace(id=70, type=ChatType.GROUP)
        channel = SimpleNamespace(id=71, type=ChatType.CHANNEL)

        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=owner_actor),
        ), patch(
            "core.services.chat_upload_session_service.prepare_direct_message_send",
            new=AsyncMock(return_value=(receiver, None)),
        ) as prepare_mock:
            effective, target = await resolve_upload_target(
                db,
                current_user=current_user,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
            )

        self.assertIs(effective, owner_actor)
        self.assertIs(target.receiver, receiver)
        prepare_mock.assert_awaited_once()

        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=owner_actor),
        ), patch(
            "core.services.chat_upload_session_service.get_room_or_404",
            new=AsyncMock(return_value=group),
        ), patch(
            "core.services.chat_upload_session_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=SimpleNamespace(user_id=5)),
        ) as member_mock:
            _, target = await resolve_upload_target(
                db,
                current_user=current_user,
                room_kind=UploadRoomKind.GROUP,
                target_id=70,
            )

        self.assertIs(target.chat, group)
        member_mock.assert_awaited_once_with(db, chat=group, user_id=5)

        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=owner_actor),
        ), patch(
            "core.services.chat_upload_session_service.get_room_or_404",
            new=AsyncMock(return_value=channel),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await resolve_upload_target(
                    db,
                    current_user=current_user,
                    room_kind=UploadRoomKind.GROUP,
                    target_id=71,
                )
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_create_upload_batch_reuses_existing_and_validates_single_shape(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        owner_actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)
        existing = SimpleNamespace(status=UploadBatchStatus.UPLOADING)

        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service._get_existing_upload_batch_by_idempotency",
            new=AsyncMock(return_value=existing),
        ):
            result = await create_upload_batch(
                db,
                current_user=current_user,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                message_kind=UploadBatchMessageKind.SINGLE,
                expected_items=1,
                caption_policy=UploadCaptionPolicy.NONE,
                idempotency_key="same-key",
            )

        self.assertIs(result, existing)
        db.commit.assert_not_awaited()
        self.assertEqual(db.added, [])

        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service._get_existing_upload_batch_by_idempotency",
            new=AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_upload_batch(
                    db,
                    current_user=current_user,
                    room_kind=UploadRoomKind.DIRECT,
                    target_id=9,
                    message_kind=UploadBatchMessageKind.SINGLE,
                    expected_items=2,
                    caption_policy=UploadCaptionPolicy.NONE,
                    idempotency_key="bad-single",
                )
        self.assertEqual(exc_info.exception.status_code, 400)

    async def test_get_upload_batch_and_session_guard_owner_actor_access(self):
        current_user = SimpleNamespace(id=5)
        actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)
        db = FakeDB()
        db.get = AsyncMock(return_value=None)
        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=actor),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_upload_batch_for_current_user(db, batch_id="missing", current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        db.get = AsyncMock(return_value=SimpleNamespace(owner_user_id=8, actor_user_id=None))
        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=actor),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_upload_batch_for_current_user(db, batch_id="foreign", current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        db.get = AsyncMock(return_value=SimpleNamespace(owner_user_id=5, actor_user_id=99))
        with patch(
            "core.services.chat_upload_session_service.resolve_effective_owner_actor",
            new=AsyncMock(return_value=actor),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await get_upload_session_for_current_user(db, session_id="foreign-actor", current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 403)

    async def test_create_upload_session_validation_batch_limits_and_success(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        owner_actor = SimpleNamespace(owner_user=current_user, actor_user=current_user, is_accountant_context=False)

        with self.assertRaises(HTTPException) as exc_info:
            await create_upload_session(
                db,
                current_user=current_user,
                batch_id=None,
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                media_type=UploadMediaType.DOCUMENT,
                file_name="doc.pdf",
                mime_type="application/pdf",
                total_bytes=0,
                chunk_size=1,
                preview_metadata=None,
            )
        self.assertEqual(exc_info.exception.status_code, 400)

        batch = SimpleNamespace(
            id="batch-1",
            room_kind=UploadRoomKind.GROUP,
            target_id=70,
            status=UploadBatchStatus.UPLOADING,
            expected_items=1,
            updated_at=None,
            last_activity_at=None,
        )
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service.get_upload_batch_for_current_user",
            new=AsyncMock(return_value=batch),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_upload_session(
                    db,
                    current_user=current_user,
                    batch_id="batch-1",
                    room_kind=UploadRoomKind.DIRECT,
                    target_id=9,
                    media_type=UploadMediaType.DOCUMENT,
                    file_name="doc.pdf",
                    mime_type="application/pdf",
                    total_bytes=10,
                    chunk_size=5,
                    preview_metadata=None,
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        batch.room_kind = UploadRoomKind.DIRECT
        batch.target_id = 9
        db.execute = AsyncMock(return_value=FakeExecuteResult(["existing-session"]))
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service.get_upload_batch_for_current_user",
            new=AsyncMock(return_value=batch),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await create_upload_session(
                    db,
                    current_user=current_user,
                    batch_id="batch-1",
                    room_kind=UploadRoomKind.DIRECT,
                    target_id=9,
                    media_type=UploadMediaType.DOCUMENT,
                    file_name="doc.pdf",
                    mime_type="application/pdf",
                    total_bytes=10,
                    chunk_size=5,
                    preview_metadata=None,
                )
        self.assertEqual(exc_info.exception.status_code, 400)

        db.execute = AsyncMock(return_value=FakeExecuteResult([]))
        batch.status = UploadBatchStatus.COLLECTING
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(owner_actor, SimpleNamespace(target_id=9))),
        ), patch(
            "core.services.chat_upload_session_service.get_upload_batch_for_current_user",
            new=AsyncMock(return_value=batch),
        ), patch(
            "core.services.chat_upload_session_service._ensure_directory",
            new=AsyncMock(),
        ), patch("core.services.chat_upload_session_service.uuid.uuid4", return_value="sess-uuid"), patch(
            "core.services.chat_upload_session_service.secrets.token_urlsafe",
            return_value="resume-token",
        ):
            session = await create_upload_session(
                db,
                current_user=current_user,
                batch_id="batch-1",
                room_kind=UploadRoomKind.DIRECT,
                target_id=9,
                media_type=UploadMediaType.IMAGE,
                file_name="photo.jpg",
                mime_type="image/jpeg; charset=binary",
                total_bytes=10,
                chunk_size=5,
                preview_metadata={"album_index": 1},
            )

        self.assertEqual(session.id, "sess-uuid")
        self.assertEqual(session.mime_type, "image/jpeg")
        self.assertEqual(session.resume_token, "resume-token")
        self.assertEqual(batch.status, UploadBatchStatus.UPLOADING)
        self.assertEqual(db.added[-1], session)

    async def test_cleanup_expiry_and_chunk_guard_branches(self):
        db = FakeDB()
        now = datetime.now(timezone.utc)

        no_file_session = SimpleNamespace(final_chat_file_id=None, status=UploadSessionStatus.READY)
        await _cleanup_uncommitted_chat_file(db, no_file_session)
        db.get.assert_not_awaited()

        committed_session = SimpleNamespace(final_chat_file_id="file-committed", status=UploadSessionStatus.COMMITTED)
        await _cleanup_uncommitted_chat_file(db, committed_session)
        db.get.assert_not_awaited()

        missing_file_session = SimpleNamespace(final_chat_file_id="missing-file", status=UploadSessionStatus.READY)
        db.get = AsyncMock(return_value=None)
        await _cleanup_uncommitted_chat_file(db, missing_file_session)
        db.delete.assert_not_awaited()

        chat_file = SimpleNamespace(s3_key="uploads/chat_files/5/avatar.png")
        cleanup_session = SimpleNamespace(final_chat_file_id="file-clean", status=UploadSessionStatus.READY)
        db.get = AsyncMock(return_value=chat_file)
        with patch("core.services.chat_upload_session_service._remove_file_if_exists", new=AsyncMock()) as remove_mock:
            await _cleanup_uncommitted_chat_file(db, cleanup_session)
        remove_mock.assert_awaited_once_with(chat_file.s3_key)
        db.delete.assert_awaited_once_with(chat_file)
        self.assertIsNone(cleanup_session.final_chat_file_id)

        terminal = SimpleNamespace(status=UploadSessionStatus.CANCELLED, expires_at=now - timedelta(seconds=1))
        self.assertIs(await _expire_session_if_needed(db, terminal), terminal)

        future = SimpleNamespace(status=UploadSessionStatus.UPLOADING, expires_at=now + timedelta(seconds=5))
        self.assertIs(await _expire_session_if_needed(db, future), future)

        expired = SimpleNamespace(
            id="expired-session",
            status=UploadSessionStatus.UPLOADING,
            expires_at=now - timedelta(seconds=1),
            final_chat_file_id=None,
            temp_storage_path="uploads/chat_sessions/5/expired.part",
            last_error=None,
            updated_at=None,
            last_activity_at=None,
        )
        with patch("core.services.chat_upload_session_service._remove_file_if_exists", new=AsyncMock()) as remove_mock:
            result = await _expire_session_if_needed(db, expired)
        self.assertIs(result, expired)
        self.assertEqual(expired.status, UploadSessionStatus.EXPIRED)
        self.assertEqual(expired.last_error, "expired")
        remove_mock.assert_awaited_once_with("uploads/chat_sessions/5/expired.part")
        db.commit.assert_awaited()

        base_session = SimpleNamespace(
            id="chunk-session",
            status=UploadSessionStatus.UPLOADING,
            expires_at=now + timedelta(hours=1),
            resume_token="resume-token",
            next_offset=0,
            received_bytes=0,
            total_bytes=5,
            temp_storage_path="uploads/chat_sessions/5/chunk.part",
            batch_id=None,
            chunk_count=0,
            retry_count=2,
            last_error="old",
            updated_at=None,
            last_activity_at=None,
        )

        for session_overrides, kwargs, expected_status in [
            ({"status": UploadSessionStatus.COMMITTED}, {"resume_token": "resume-token", "offset": 0, "chunk_bytes": b"x"}, 409),
            ({}, {"resume_token": "wrong", "offset": 0, "chunk_bytes": b"x"}, 403),
            ({"next_offset": 3}, {"resume_token": "resume-token", "offset": 0, "chunk_bytes": b"x"}, 409),
            ({}, {"resume_token": "resume-token", "offset": 0, "chunk_bytes": b""}, 400),
            ({"received_bytes": 4, "next_offset": 4}, {"resume_token": "resume-token", "offset": 4, "chunk_bytes": b"xx"}, 400),
        ]:
            session = SimpleNamespace(**{**base_session.__dict__, **session_overrides})
            with self.assertRaises(HTTPException) as exc_info:
                await append_upload_chunk(db, session=session, is_last_chunk=False, **kwargs)
            self.assertEqual(exc_info.exception.status_code, expected_status)

        success_session = SimpleNamespace(**base_session.__dict__)
        fake_file = FakeAsyncFile()
        with patch("core.services.chat_upload_session_service._ensure_directory", new=AsyncMock()), patch(
            "core.services.chat_upload_session_service.aiofiles.open",
            return_value=fake_file,
        ):
            result = await append_upload_chunk(
                db,
                session=success_session,
                resume_token="resume-token",
                offset=0,
                chunk_bytes=b"abc",
                is_last_chunk=True,
            )
        self.assertIs(result, success_session)
        fake_file.write.assert_awaited_once_with(b"abc")
        self.assertEqual(success_session.status, UploadSessionStatus.UPLOADING)
        self.assertEqual(success_session.last_error, "last_chunk_before_total")

    async def test_batch_order_and_group_message_persistence_helpers(self):
        session_a = SimpleNamespace(id="b", preview_metadata={"album_index": 2})
        session_b = SimpleNamespace(id="a", preview_metadata={})
        self.assertEqual(sorted([session_a, session_b], key=_batch_order_key), [session_b, session_a])

        db = FakeDB()
        chat = SimpleNamespace(id=77, last_message_id=None, last_message_at=None, updated_at=None)
        sender = SimpleNamespace(id=5)
        member = SimpleNamespace(last_read_message_id=None, last_read_at=None, is_marked_unread=True, updated_at=None)
        with patch(
            "core.services.chat_upload_session_service.get_active_group_member_or_403",
            new=AsyncMock(return_value=member),
        ) as member_mock:
            message = await _persist_batch_group_message(
                db,
                chat=chat,
                sender=sender,
                content="group-content",
                message_type=_message_type_from_media_type(UploadMediaType.IMAGE),
            )

        member_mock.assert_awaited_once_with(db, chat=chat, user_id=5)
        self.assertIs(db.added[-1], message)
        self.assertEqual(message.chat_id, 77)
        self.assertEqual(message.sender_id, 5)
        self.assertEqual(message.receiver_id, 5)
        self.assertIs(member.last_read_message_id, message.id)
        self.assertFalse(member.is_marked_unread)

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

    async def test_append_upload_chunk_guards_token_empty_excess_and_last_marker(self):
        db = FakeDB()
        session = SimpleNamespace(
            id="sess-guard",
            status=UploadSessionStatus.CREATED,
            resume_token="resume-1",
            next_offset=0,
            received_bytes=0,
            total_bytes=5,
            chunk_count=0,
            retry_count=0,
            last_error=None,
            temp_storage_path="/tmp/sess-guard.part",
            batch_id="batch-guard",
            expires_at=self._future(),
        )

        with self.assertRaises(HTTPException) as exc_info:
            await append_upload_chunk(db, session=session, resume_token="bad", offset=0, chunk_bytes=b"abc")
        self.assertEqual(exc_info.exception.status_code, 403)

        with self.assertRaises(HTTPException) as exc_info:
            await append_upload_chunk(db, session=session, resume_token="resume-1", offset=0, chunk_bytes=b"")
        self.assertEqual(exc_info.exception.status_code, 400)

        with self.assertRaises(HTTPException) as exc_info:
            await append_upload_chunk(db, session=session, resume_token="resume-1", offset=0, chunk_bytes=b"abcdef")
        self.assertEqual(exc_info.exception.status_code, 400)

        batch = SimpleNamespace(status=UploadBatchStatus.COLLECTING, updated_at=None, last_activity_at=None, expires_at=None)
        db.get = AsyncMock(return_value=batch)
        fake_file = FakeAsyncFile()
        with patch("core.services.chat_upload_session_service.aiofiles.open", return_value=fake_file), patch(
            "core.services.chat_upload_session_service._ensure_directory",
            new=AsyncMock(),
        ):
            await append_upload_chunk(
                db,
                session=session,
                resume_token="resume-1",
                offset=0,
                chunk_bytes=b"ab",
                is_last_chunk=True,
            )

        self.assertEqual(session.last_error, "last_chunk_before_total")
        self.assertEqual(batch.status, UploadBatchStatus.UPLOADING)

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

    async def test_finalize_upload_session_early_guards_failure_and_batch_update(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        committed = SimpleNamespace(status=UploadSessionStatus.COMMITTED, final_chat_file_id="file-1", expires_at=self._future())
        self.assertIs(await finalize_upload_session(db, session=committed, current_user=current_user), committed)

        ready = SimpleNamespace(status=UploadSessionStatus.READY, final_chat_file_id="file-2", expires_at=self._future())
        self.assertIs(await finalize_upload_session(db, session=ready, current_user=current_user), ready)

        incomplete = SimpleNamespace(
            id="incomplete",
            status=UploadSessionStatus.UPLOADING,
            received_bytes=1,
            total_bytes=2,
            final_chat_file_id=None,
            expires_at=self._future(),
        )
        with self.assertRaises(HTTPException) as exc_info:
            await finalize_upload_session(db, session=incomplete, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 409)

        missing = SimpleNamespace(
            id="missing",
            status=UploadSessionStatus.UPLOADED,
            received_bytes=2,
            total_bytes=2,
            temp_storage_path="/tmp/missing.part",
            final_chat_file_id=None,
            expires_at=self._future(),
        )
        with patch("core.services.chat_upload_session_service.os.path.exists", return_value=False):
            with self.assertRaises(HTTPException) as exc_info:
                await finalize_upload_session(db, session=missing, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 404)

        failing = SimpleNamespace(
            id="failing",
            status=UploadSessionStatus.UPLOADED,
            received_bytes=4,
            total_bytes=4,
            temp_storage_path="/tmp/failing.part",
            original_file_name="bad.bin",
            mime_type="application/pdf",
            preview_metadata={},
            final_chat_file_id=None,
            batch_id=None,
            retry_count=0,
            last_error=None,
            expires_at=self._future(),
        )
        with patch("core.services.chat_upload_session_service.os.path.exists", return_value=True), patch(
            "core.services.chat_upload_session_service.aiofiles.open",
            return_value=FakeAsyncFile(read_result=b"data"),
        ), patch(
            "core.services.chat_upload_session_service.persist_chat_media_file_bytes",
            new=AsyncMock(side_effect=RuntimeError("disk down")),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await finalize_upload_session(db, session=failing, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(failing.status, UploadSessionStatus.FAILED)
        self.assertEqual(failing.retry_count, 1)

        batch = SimpleNamespace(id="batch-1", status=UploadBatchStatus.UPLOADING, expected_items=2, updated_at=None, last_activity_at=None, expires_at=None)
        db.get = AsyncMock(return_value=batch)
        batch_session = SimpleNamespace(
            id="sess-batch",
            status=UploadSessionStatus.UPLOADED,
            received_bytes=4,
            total_bytes=4,
            temp_storage_path="/tmp/sess-batch.part",
            original_file_name="photo.jpg",
            mime_type="image/jpeg",
            preview_metadata={},
            final_chat_file_id=None,
            batch_id="batch-1",
            retry_count=0,
            last_error=None,
            expires_at=self._future(),
        )
        other_ready = SimpleNamespace(id="other", status=UploadSessionStatus.READY)
        with patch("core.services.chat_upload_session_service.os.path.exists", return_value=True), patch(
            "core.services.chat_upload_session_service.aiofiles.open",
            return_value=FakeAsyncFile(read_result=b"data"),
        ), patch(
            "core.services.chat_upload_session_service.persist_chat_media_file_bytes",
            new=AsyncMock(return_value=SimpleNamespace(chat_file=SimpleNamespace(id="file-batch", thumbnail="thumb"), width=320, height=240)),
        ), patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[batch_session, other_ready]),
        ), patch(
            "core.services.chat_upload_session_service._remove_file_if_exists",
            new=AsyncMock(),
        ):
            await finalize_upload_session(db, session=batch_session, current_user=current_user)

        self.assertEqual(batch.status, UploadBatchStatus.UPLOADED)
        self.assertEqual(batch_session.preview_metadata["width"], 320)
        self.assertEqual(batch_session.preview_metadata["height"], 240)

    def test_build_final_message_content_for_media_variants(self):
        batch = SimpleNamespace(id="album-1", message_kind=UploadBatchMessageKind.ALBUM)
        image_session = SimpleNamespace(
            final_chat_file_id="file-img",
            media_type=UploadMediaType.IMAGE,
            original_file_name="photo.jpg",
            mime_type="image/jpeg",
            total_bytes=100,
            preview_metadata={"thumbnail": "thumb", "width": "640", "height": "480", "album_index": 2, "caption": " hello "},
        )
        content = _build_final_message_content(session=image_session, batch=batch, include_caption=True)
        self.assertIn('"album_id": "album-1"', content)
        self.assertIn('"caption": "hello"', content)

        voice_session = SimpleNamespace(
            final_chat_file_id="file-voice",
            media_type=UploadMediaType.VOICE,
            original_file_name="voice.ogg",
            mime_type="audio/ogg",
            total_bytes=50,
            preview_metadata={"durationMs": 1200, "waveform": [1, 2]},
        )
        voice_content = _build_final_message_content(session=voice_session, batch=batch, include_caption=False)
        self.assertIn('"durationMs": 1200', voice_content)
        self.assertIn('"waveform": [1, 2]', voice_content)

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

    async def test_commit_upload_batch_guards_idempotent_group_and_rollback(self):
        db = FakeDB()
        current_user = SimpleNamespace(id=5)
        committed = SimpleNamespace(id="batch-done", status=UploadBatchStatus.COMMITTED, room_kind=UploadRoomKind.DIRECT, target_id=9)
        committed_session = SimpleNamespace(preview_metadata={"committed_message_id": "101"})
        with patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[committed_session]),
        ), patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(None, SimpleNamespace(room_kind=UploadRoomKind.DIRECT, target_id=9, receiver=SimpleNamespace(id=9), chat=None))),
        ), patch(
            "core.services.chat_upload_session_service._reload_messages_for_delivery",
            new=AsyncMock(return_value=[SimpleNamespace(id=101)]),
        ) as reload_mock:
            result = await commit_upload_batch(db, batch=committed, current_user=current_user)
        reload_mock.assert_awaited_once_with(db, [101])
        self.assertEqual([message.id for message in result.messages], [101])

        terminal = SimpleNamespace(id="batch-failed", status=UploadBatchStatus.FAILED)
        with self.assertRaises(HTTPException) as exc_info:
            await commit_upload_batch(db, batch=terminal, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 409)

        missing = SimpleNamespace(
            id="batch-missing",
            status=UploadBatchStatus.UPLOADING,
            room_kind=UploadRoomKind.DIRECT,
            target_id=9,
            expected_items=2,
            message_kind=UploadBatchMessageKind.ALBUM,
            caption_policy=UploadCaptionPolicy.NONE,
        )
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(None, SimpleNamespace(room_kind=UploadRoomKind.DIRECT, target_id=9, receiver=SimpleNamespace(id=9), chat=None))),
        ), patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[SimpleNamespace(status=UploadSessionStatus.READY)]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await commit_upload_batch(db, batch=missing, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 409)

        group_batch = SimpleNamespace(
            id="batch-group",
            status=UploadBatchStatus.UPLOADED,
            room_kind=UploadRoomKind.GROUP,
            target_id=70,
            expected_items=1,
            message_kind=UploadBatchMessageKind.SINGLE,
            caption_policy=UploadCaptionPolicy.NONE,
            committed_items=0,
        )
        group_session = SimpleNamespace(
            id="sess-group",
            status=UploadSessionStatus.READY,
            media_type=UploadMediaType.IMAGE,
            final_chat_file_id="file-img",
            original_file_name="photo.jpg",
            mime_type="image/jpeg",
            total_bytes=22,
            preview_metadata={"width": 100, "height": 80},
        )
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(None, SimpleNamespace(room_kind=UploadRoomKind.GROUP, target_id=70, receiver=None, chat=SimpleNamespace(id=70)))),
        ), patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[group_session]),
        ), patch(
            "core.services.chat_upload_session_service._persist_batch_group_message",
            new=AsyncMock(return_value=SimpleNamespace(id=201)),
        ) as persist_mock, patch(
            "core.services.chat_upload_session_service._reload_messages_for_delivery",
            new=AsyncMock(return_value=[SimpleNamespace(id=201)]),
        ):
            result = await commit_upload_batch(db, batch=group_batch, current_user=current_user)
        persist_mock.assert_awaited_once()
        self.assertEqual(result.messages[0].id, 201)

        broken = SimpleNamespace(**{**group_batch.__dict__, "status": UploadBatchStatus.UPLOADED, "committed_items": 0})
        group_session.status = UploadSessionStatus.READY
        with patch(
            "core.services.chat_upload_session_service.resolve_upload_target",
            new=AsyncMock(return_value=(None, SimpleNamespace(room_kind=UploadRoomKind.DIRECT, target_id=9, receiver=None, chat=None))),
        ), patch(
            "core.services.chat_upload_session_service.list_upload_batch_sessions",
            new=AsyncMock(return_value=[group_session]),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await commit_upload_batch(db, batch=broken, current_user=current_user)
        self.assertEqual(exc_info.exception.status_code, 500)
        db.rollback.assert_awaited_once()

    async def test_cancel_upload_session_batch_and_read_chunk_close_paths(self):
        db = FakeDB()
        with self.assertRaises(HTTPException) as exc_info:
            await cancel_upload_session(db, session=SimpleNamespace(status=UploadSessionStatus.COMMITTED))
        self.assertEqual(exc_info.exception.status_code, 409)

        session = SimpleNamespace(
            status=UploadSessionStatus.READY,
            final_chat_file_id="file-1",
            temp_storage_path="/tmp/session.part",
            last_error=None,
            updated_at=None,
            last_activity_at=None,
        )
        db.get = AsyncMock(return_value=SimpleNamespace(s3_key="/tmp/file.bin"))
        with patch("core.services.chat_upload_session_service._remove_file_if_exists", new=AsyncMock()) as remove_mock:
            await cancel_upload_session(db, session=session, reason="user_cancelled")
        self.assertEqual(session.status, UploadSessionStatus.CANCELLED)
        self.assertIsNone(session.final_chat_file_id)
        self.assertEqual(remove_mock.await_count, 2)

        with self.assertRaises(HTTPException) as exc_info:
            await cancel_upload_batch(db, batch=SimpleNamespace(status=UploadBatchStatus.COMMITTED))
        self.assertEqual(exc_info.exception.status_code, 409)

        batch = SimpleNamespace(id="batch-cancel", status=UploadBatchStatus.UPLOADING, updated_at=None, last_activity_at=None)
        sessions = [
            SimpleNamespace(status=UploadSessionStatus.COMMITTED, temp_storage_path="/tmp/done", final_chat_file_id=None),
            SimpleNamespace(status=UploadSessionStatus.READY, temp_storage_path="/tmp/open", final_chat_file_id=None, last_error=None, updated_at=None, last_activity_at=None),
        ]
        with patch("core.services.chat_upload_session_service.list_upload_batch_sessions", new=AsyncMock(return_value=sessions)), patch(
            "core.services.chat_upload_session_service._remove_file_if_exists",
            new=AsyncMock(),
        ) as remove_mock:
            await cancel_upload_batch(db, batch=batch, reason="batch_cancelled")
        self.assertEqual(batch.status, UploadBatchStatus.CANCELLED)
        self.assertEqual(sessions[1].status, UploadSessionStatus.CANCELLED)
        remove_mock.assert_awaited_once_with("/tmp/open")

        async_file = SimpleNamespace(read=AsyncMock(return_value=b"abc"), close=AsyncMock())
        self.assertEqual(await read_upload_chunk_bytes(async_file), b"abc")
        async_file.close.assert_awaited_once()

        sync_close = Mock()
        sync_file = SimpleNamespace(read=AsyncMock(return_value=b"def"), close=sync_close)
        self.assertEqual(await read_upload_chunk_bytes(sync_file), b"def")
        sync_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()