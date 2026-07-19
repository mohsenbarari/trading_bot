"""Resumable chat upload session helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import inspect
import os
import secrets
import uuid

import aiofiles
from fastapi import HTTPException, UploadFile
import magic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.enums import ChatType, MessageType
from core.dr_blob_plane import bind_chat_file_blob
from core.services.accountant_relation_service import EffectiveOwnerActor, resolve_effective_owner_actor
from core.services.chat_room_service import get_active_group_member_or_403, get_room_or_404
from core.services.chat_service import (
    get_or_create_direct_chat,
    prepare_direct_message_send,
    reload_direct_message,
    sync_direct_message_threading,
)
from core.utils import utc_now
from models.chat import Chat
from models.chat_file import ChatFile
from models.message import Message
from models.upload_session import (
    UploadBatch,
    UploadBatchMessageKind,
    UploadBatchStatus,
    UploadCaptionPolicy,
    UploadMediaType,
    UploadRoomKind,
    UploadSession,
    UploadSessionStatus,
)
from models.user import User

import logging


logger = logging.getLogger(__name__)


CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHAT_MEDIA_MAX_UPLOAD_LABEL = "50MB"
UPLOAD_BATCH_LIFETIME = timedelta(hours=24)
UPLOAD_SESSION_LIFETIME = timedelta(hours=24)
UPLOAD_SESSION_ROOT = os.path.join("uploads", "chat_sessions")
TERMINAL_BATCH_STATUSES = {
    UploadBatchStatus.COMMITTED,
    UploadBatchStatus.CANCELLED,
    UploadBatchStatus.EXPIRED,
    UploadBatchStatus.FAILED,
}
TERMINAL_SESSION_STATUSES = {
    UploadSessionStatus.COMMITTED,
    UploadSessionStatus.CANCELLED,
    UploadSessionStatus.EXPIRED,
}
MUTABLE_SESSION_STATUSES = {
    UploadSessionStatus.CREATED,
    UploadSessionStatus.UPLOADING,
    UploadSessionStatus.UPLOADED,
    UploadSessionStatus.FAILED,
}
READY_LIKE_SESSION_STATUSES = {
    UploadSessionStatus.READY,
    UploadSessionStatus.COMMITTED,
}
ALLOWED_UPLOAD_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heic-sequence", "image/heif", "image/heif-sequence",
    "video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "application/mp4", "video/x-m4v", "video/3gpp", "application/octet-stream",
    "audio/mp4", "audio/webm", "audio/ogg", "audio/mpeg", "audio/aac", "audio/x-m4a", "audio/wav", "audio/x-wav",
    "application/pdf", "text/plain", "text/csv", "application/json", "application/xml", "text/xml", "application/rtf",
    "application/zip", "application/x-zip-compressed", "application/x-rar-compressed", "application/vnd.rar", "application/x-7z-compressed",
    "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True)
class ResolvedUploadTarget:
    room_kind: UploadRoomKind
    target_id: int
    receiver: User | None = None
    chat: Chat | None = None


@dataclass(frozen=True)
class UploadFilePersistenceResult:
    chat_file: ChatFile
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class UploadBatchCommitResult:
    batch: UploadBatch
    target: ResolvedUploadTarget
    messages: list[Message]


def _coalesce_actor_user_id(effective_actor: EffectiveOwnerActor) -> int | None:
    if effective_actor.is_accountant_context:
        return effective_actor.actor_user.id
    return None


def _message_type_from_media_type(media_type: UploadMediaType) -> MessageType:
    mapping = {
        UploadMediaType.IMAGE: MessageType.IMAGE,
        UploadMediaType.VIDEO: MessageType.VIDEO,
        UploadMediaType.VOICE: MessageType.VOICE,
        UploadMediaType.DOCUMENT: MessageType.DOCUMENT,
    }
    return mapping[media_type]


def _normalize_preview_metadata(preview_metadata: dict | None) -> dict:
    return dict(preview_metadata or {})


def _build_temp_storage_path(owner_user_id: int, session_id: str) -> str:
    return os.path.join(UPLOAD_SESSION_ROOT, str(owner_user_id), f"{session_id}.part")


async def _ensure_directory(path: str) -> None:
    await asyncio.to_thread(os.makedirs, path, exist_ok=True)


async def _remove_file_if_exists(path: str | None) -> None:
    if not path:
        return

    def _remove() -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except FileNotFoundError:
            return

    await asyncio.to_thread(_remove)


async def _flush_if_supported(db: AsyncSession) -> None:
    flush = getattr(db, "flush", None)
    if not callable(flush):
        return
    result = flush()
    if inspect.isawaitable(result):
        await result


async def _validate_direct_upload_target(
    db: AsyncSession,
    *,
    sender: User,
    receiver_id: int,
) -> User:
    receiver, _ = await prepare_direct_message_send(
        db,
        sender=sender,
        receiver_id=receiver_id,
        content="upload-session-validation",
        message_type=MessageType.TEXT,
    )
    return receiver


async def resolve_upload_target(
    db: AsyncSession,
    *,
    current_user: User,
    room_kind: UploadRoomKind,
    target_id: int,
) -> tuple[EffectiveOwnerActor, ResolvedUploadTarget]:
    effective_actor = await resolve_effective_owner_actor(db, current_user)
    if room_kind == UploadRoomKind.DIRECT:
        receiver = await _validate_direct_upload_target(
            db,
            sender=current_user,
            receiver_id=target_id,
        )
        return effective_actor, ResolvedUploadTarget(
            room_kind=room_kind,
            target_id=target_id,
            receiver=receiver,
        )

    chat = await get_room_or_404(db, target_id)
    if chat.type != ChatType.GROUP:
        raise HTTPException(
            status_code=400,
            detail="Only direct and group resumable uploads are supported in this phase",
        )
    await get_active_group_member_or_403(db, chat=chat, user_id=current_user.id)
    return effective_actor, ResolvedUploadTarget(
        room_kind=room_kind,
        target_id=target_id,
        chat=chat,
    )


def _assert_upload_batch_shape(batch: UploadBatch) -> None:
    if batch.message_kind == UploadBatchMessageKind.SINGLE and batch.expected_items != 1:
        raise HTTPException(status_code=400, detail="Single-item batches must expect exactly one item")


async def _get_existing_upload_batch_by_idempotency(
    db: AsyncSession,
    *,
    owner_user_id: int,
    actor_user_id: int | None,
    room_kind: UploadRoomKind,
    target_id: int,
    idempotency_key: str,
) -> UploadBatch | None:
    stmt = (
        select(UploadBatch)
        .where(
            UploadBatch.owner_user_id == owner_user_id,
            UploadBatch.room_kind == room_kind,
            UploadBatch.target_id == target_id,
            UploadBatch.idempotency_key == idempotency_key,
        )
        .order_by(UploadBatch.created_at.desc(), UploadBatch.id.desc())
    )
    if actor_user_id is None:
        stmt = stmt.where(UploadBatch.actor_user_id.is_(None))
    else:
        stmt = stmt.where(UploadBatch.actor_user_id == actor_user_id)
    return (await db.execute(stmt)).scalars().first()


async def create_upload_batch(
    db: AsyncSession,
    *,
    current_user: User,
    room_kind: UploadRoomKind,
    target_id: int,
    message_kind: UploadBatchMessageKind,
    expected_items: int,
    caption_policy: UploadCaptionPolicy,
    idempotency_key: str,
) -> UploadBatch:
    effective_actor, _ = await resolve_upload_target(
        db,
        current_user=current_user,
        room_kind=room_kind,
        target_id=target_id,
    )
    actor_user_id = _coalesce_actor_user_id(effective_actor)
    existing = await _get_existing_upload_batch_by_idempotency(
        db,
        owner_user_id=effective_actor.owner_user.id,
        actor_user_id=actor_user_id,
        room_kind=room_kind,
        target_id=target_id,
        idempotency_key=idempotency_key,
    )
    if existing is not None and existing.status not in TERMINAL_BATCH_STATUSES:
        return existing

    now = utc_now()
    batch = UploadBatch(
        owner_user_id=effective_actor.owner_user.id,
        actor_user_id=actor_user_id,
        room_kind=room_kind,
        target_id=target_id,
        message_kind=message_kind,
        expected_items=expected_items,
        caption_policy=caption_policy,
        idempotency_key=idempotency_key,
        status=UploadBatchStatus.COLLECTING,
        committed_items=0,
        created_at=now,
        updated_at=now,
        expires_at=now + UPLOAD_BATCH_LIFETIME,
        last_activity_at=now,
    )
    _assert_upload_batch_shape(batch)
    db.add(batch)
    await db.commit()
    return batch


async def get_upload_batch_for_current_user(
    db: AsyncSession,
    *,
    batch_id: str,
    current_user: User,
) -> UploadBatch:
    batch = await db.get(UploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Upload batch not found")

    effective_actor = await resolve_effective_owner_actor(db, current_user)
    actor_user_id = _coalesce_actor_user_id(effective_actor)
    if batch.owner_user_id != effective_actor.owner_user.id:
        raise HTTPException(status_code=404, detail="Upload batch not found")
    if batch.actor_user_id != actor_user_id:
        raise HTTPException(status_code=403, detail="Upload batch belongs to another active session owner")
    return batch


async def create_upload_session(
    db: AsyncSession,
    *,
    current_user: User,
    batch_id: str | None,
    room_kind: UploadRoomKind,
    target_id: int,
    media_type: UploadMediaType,
    file_name: str,
    mime_type: str,
    total_bytes: int,
    chunk_size: int,
    preview_metadata: dict | None,
    sha256_full: str | None = None,
) -> UploadSession:
    if total_bytes <= 0:
        raise HTTPException(status_code=400, detail="total_bytes must be positive")
    if total_bytes > CHAT_MEDIA_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {CHAT_MEDIA_MAX_UPLOAD_LABEL})")
    if chunk_size <= 0:
        raise HTTPException(status_code=400, detail="chunk_size must be positive")

    effective_actor, target = await resolve_upload_target(
        db,
        current_user=current_user,
        room_kind=room_kind,
        target_id=target_id,
    )
    actor_user_id = _coalesce_actor_user_id(effective_actor)

    batch = None
    if batch_id is not None:
        batch = await get_upload_batch_for_current_user(
            db,
            batch_id=batch_id,
            current_user=current_user,
        )
        if batch.room_kind != room_kind or batch.target_id != target_id:
            raise HTTPException(status_code=400, detail="Upload session target does not match batch target")
        if batch.status in TERMINAL_BATCH_STATUSES:
            raise HTTPException(status_code=409, detail="Upload batch is no longer mutable")

        existing_count_result = await db.execute(
            select(UploadSession.id).where(UploadSession.batch_id == batch.id)
        )
        existing_count = len(existing_count_result.scalars().all())
        if existing_count >= batch.expected_items:
            raise HTTPException(status_code=400, detail="Upload batch already has the expected number of items")

    session_id = str(uuid.uuid4())
    temp_storage_path = _build_temp_storage_path(effective_actor.owner_user.id, session_id)
    await _ensure_directory(os.path.dirname(temp_storage_path))

    now = utc_now()
    session = UploadSession(
        id=session_id,
        batch_id=batch.id if batch is not None else None,
        owner_user_id=effective_actor.owner_user.id,
        actor_user_id=actor_user_id,
        room_kind=room_kind,
        target_id=target.target_id,
        media_type=media_type,
        original_file_name=os.path.basename(file_name or f"upload_{session_id}"),
        mime_type=(mime_type or "application/octet-stream").split(";")[0].strip(),
        total_bytes=total_bytes,
        chunk_size=chunk_size,
        received_bytes=0,
        next_offset=0,
        chunk_count=0,
        sha256_full=sha256_full,
        preview_metadata=_normalize_preview_metadata(preview_metadata),
        status=UploadSessionStatus.CREATED,
        temp_storage_path=temp_storage_path,
        resume_token=secrets.token_urlsafe(24),
        retry_count=0,
        created_at=now,
        updated_at=now,
        expires_at=now + UPLOAD_SESSION_LIFETIME,
        last_activity_at=now,
    )
    db.add(session)

    if batch is not None and batch.status == UploadBatchStatus.COLLECTING:
        batch.status = UploadBatchStatus.UPLOADING
        batch.updated_at = now
        batch.last_activity_at = now

    await db.commit()
    return session


async def get_upload_session_for_current_user(
    db: AsyncSession,
    *,
    session_id: str,
    current_user: User,
) -> UploadSession:
    session = await db.get(UploadSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Upload session not found")

    effective_actor = await resolve_effective_owner_actor(db, current_user)
    actor_user_id = _coalesce_actor_user_id(effective_actor)
    if session.owner_user_id != effective_actor.owner_user.id:
        raise HTTPException(status_code=404, detail="Upload session not found")
    if session.actor_user_id != actor_user_id:
        raise HTTPException(status_code=403, detail="Upload session belongs to another active session owner")
    return session


async def _cleanup_uncommitted_chat_file(
    db: AsyncSession,
    session: UploadSession,
) -> None:
    if not session.final_chat_file_id or session.status == UploadSessionStatus.COMMITTED:
        return

    chat_file = await db.get(ChatFile, session.final_chat_file_id)
    if chat_file is None:
        return
    # Content-addressed blobs are immutable and may be shared by other rows or
    # needed by a recovery point. They are reclaimed only by the delayed DR GC.
    if not getattr(chat_file, "content_hash", None):
        await _remove_file_if_exists(chat_file.s3_key)
    await db.delete(chat_file)
    session.final_chat_file_id = None


async def _expire_session_if_needed(db: AsyncSession, session: UploadSession) -> UploadSession:
    if session.status in TERMINAL_SESSION_STATUSES:
        return session
    if session.expires_at > utc_now():
        return session

    now = utc_now()
    session.status = UploadSessionStatus.EXPIRED
    session.last_error = "expired"
    session.updated_at = now
    session.last_activity_at = now
    await _cleanup_uncommitted_chat_file(db, session)
    await _remove_file_if_exists(session.temp_storage_path)
    await db.commit()
    return session


async def append_upload_chunk(
    db: AsyncSession,
    *,
    session: UploadSession,
    resume_token: str,
    offset: int,
    chunk_bytes: bytes,
    is_last_chunk: bool = False,
) -> UploadSession:
    session = await _expire_session_if_needed(db, session)
    if session.status not in MUTABLE_SESSION_STATUSES:
        raise HTTPException(status_code=409, detail="Upload session is no longer writable")
    if session.resume_token != resume_token:
        raise HTTPException(status_code=403, detail="Invalid resume token")
    if offset != session.next_offset:
        raise HTTPException(
            status_code=409,
            detail={"message": "Offset mismatch", "next_offset": session.next_offset},
        )
    if not chunk_bytes and session.next_offset < session.total_bytes:
        raise HTTPException(status_code=400, detail="Chunk payload is empty")
    if session.received_bytes + len(chunk_bytes) > session.total_bytes:
        raise HTTPException(status_code=400, detail="Chunk exceeds declared file size")

    await _ensure_directory(os.path.dirname(session.temp_storage_path))
    async with aiofiles.open(session.temp_storage_path, "ab") as file_handle:
        await file_handle.write(chunk_bytes)

    now = utc_now()
    session.received_bytes += len(chunk_bytes)
    session.next_offset = session.received_bytes
    session.chunk_count += 1
    session.retry_count = 0
    session.last_error = None
    session.updated_at = now
    session.last_activity_at = now
    session.expires_at = now + UPLOAD_SESSION_LIFETIME
    session.status = (
        UploadSessionStatus.UPLOADED
        if session.received_bytes >= session.total_bytes
        else UploadSessionStatus.UPLOADING
    )

    if session.batch_id:
        batch = await db.get(UploadBatch, session.batch_id)
        if batch is not None and batch.status not in TERMINAL_BATCH_STATUSES:
            batch.status = UploadBatchStatus.UPLOADING
            batch.updated_at = now
            batch.last_activity_at = now
            batch.expires_at = now + UPLOAD_BATCH_LIFETIME

    if is_last_chunk and session.received_bytes < session.total_bytes:
        session.last_error = "last_chunk_before_total"

    await db.commit()
    return session


async def persist_chat_media_file_bytes(
    db: AsyncSession,
    *,
    uploader_id: int,
    file_name: str,
    declared_content_type: str,
    contents: bytes,
    thumbnail: str | None = None,
) -> UploadFilePersistenceResult:
    base_content_type = (declared_content_type or "application/octet-stream").split(";")[0].strip()
    if base_content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {declared_content_type}")

    mime = await asyncio.to_thread(lambda: magic.from_buffer(contents, mime=True))
    if mime == "video/webm" and base_content_type == "audio/webm":
        mime = "audio/webm"
    if mime not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file content. Real type is {mime} and base type is {base_content_type}",
        )

    size = len(contents)
    if size > CHAT_MEDIA_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {CHAT_MEDIA_MAX_UPLOAD_LABEL})")

    img_width = None
    img_height = None
    if mime.startswith("image/") and mime != "image/gif":
        def _exif_transpose_sync(raw: bytes, mime_type: str):
            from PIL import Image as PILImage, ImageOps
            import io as _io

            pil_img = PILImage.open(_io.BytesIO(raw))
            pil_img = ImageOps.exif_transpose(pil_img)
            width, height = pil_img.size
            buf = _io.BytesIO()
            if mime_type in ("image/jpeg", "image/jpg"):
                fmt = "JPEG"
                output_mime = "image/jpeg"
                output_ext = "jpg"
            elif mime_type == "image/png":
                fmt = "PNG"
                output_mime = "image/png"
                output_ext = "png"
            else:
                fmt = "WEBP"
                output_mime = "image/webp"
                output_ext = "webp"
            pil_img.save(buf, format=fmt, quality=90)
            return buf.getvalue(), width, height, output_mime, output_ext

        try:
            contents, img_width, img_height, mime, ext = await asyncio.to_thread(_exif_transpose_sync, contents, mime)
            size = len(contents)
        except Exception as exc:
            logger.warning("Pillow EXIF transpose failed, saving original: %s", exc)
            ext = file_name.split(".")[-1] if file_name and "." in file_name else mime.split("/")[-1]
    else:
        ext = file_name.split(".")[-1] if file_name and "." in file_name else mime.split("/")[-1]

    file_uuid = str(uuid.uuid4())
    chat_file = ChatFile(
        id=file_uuid,
        uploader_id=uploader_id,
        s3_key="pending-content-address",
        file_name=file_name,
        mime_type=mime,
        size=size,
        thumbnail=thumbnail,
    )
    db.add(chat_file)
    await bind_chat_file_blob(db, chat_file=chat_file, contents=contents)
    await _flush_if_supported(db)
    return UploadFilePersistenceResult(
        chat_file=chat_file,
        width=img_width,
        height=img_height,
        mime_type=mime,
        size=size,
    )


async def finalize_upload_session(
    db: AsyncSession,
    *,
    session: UploadSession,
    current_user: User,
) -> UploadSession:
    session = await _expire_session_if_needed(db, session)
    if session.status == UploadSessionStatus.COMMITTED:
        return session
    if session.status == UploadSessionStatus.READY and session.final_chat_file_id:
        return session
    if session.received_bytes != session.total_bytes:
        raise HTTPException(status_code=409, detail="Upload session is incomplete")
    if not os.path.exists(session.temp_storage_path):
        raise HTTPException(status_code=404, detail="Upload session file is missing on disk")

    preview = _normalize_preview_metadata(session.preview_metadata)
    now = utc_now()
    session.status = UploadSessionStatus.FINALIZING
    session.updated_at = now
    session.last_activity_at = now
    await db.commit()

    try:
        async with aiofiles.open(session.temp_storage_path, "rb") as file_handle:
            contents = await file_handle.read()

        file_result = await persist_chat_media_file_bytes(
            db,
            uploader_id=current_user.id,
            file_name=session.original_file_name,
            declared_content_type=session.mime_type,
            contents=contents,
            thumbnail=preview.get("thumbnail"),
        )
        preview["thumbnail"] = file_result.chat_file.thumbnail
        if file_result.width and file_result.height:
            preview["width"] = file_result.width
            preview["height"] = file_result.height

        ready_at = utc_now()
        session.preview_metadata = preview
        session.final_chat_file_id = file_result.chat_file.id
        session.status = UploadSessionStatus.READY
        session.last_error = None
        session.updated_at = ready_at
        session.last_activity_at = ready_at
        session.expires_at = ready_at + UPLOAD_SESSION_LIFETIME

        if session.batch_id:
            batch = await db.get(UploadBatch, session.batch_id)
            if batch is not None and batch.status not in TERMINAL_BATCH_STATUSES:
                sessions = await list_upload_batch_sessions(db, batch.id)
                active_ready = [item for item in sessions if item.status in READY_LIKE_SESSION_STATUSES or item.id == session.id]
                active_sessions = [item for item in sessions if item.status != UploadSessionStatus.CANCELLED]
                if active_sessions and len(active_ready) == len(active_sessions) and len(active_sessions) >= batch.expected_items:
                    batch.status = UploadBatchStatus.UPLOADED
                else:
                    batch.status = UploadBatchStatus.UPLOADING
                batch.updated_at = ready_at
                batch.last_activity_at = ready_at
                batch.expires_at = ready_at + UPLOAD_BATCH_LIFETIME

        await db.commit()
        await _remove_file_if_exists(session.temp_storage_path)
        return session
    except Exception as exc:
        logger.exception("Failed to finalize upload session %s", session.id)
        failed_at = utc_now()
        session.status = UploadSessionStatus.FAILED
        session.last_error = str(exc)
        session.retry_count = (session.retry_count or 0) + 1
        session.updated_at = failed_at
        session.last_activity_at = failed_at
        await db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="Failed to finalize upload session") from exc


async def list_upload_batch_sessions(db: AsyncSession, batch_id: str) -> list[UploadSession]:
    stmt = (
        select(UploadSession)
        .where(UploadSession.batch_id == batch_id)
        .order_by(UploadSession.created_at.asc(), UploadSession.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


def _build_final_message_content(
    *,
    session: UploadSession,
    batch: UploadBatch,
    include_caption: bool,
) -> str:
    import json

    preview = _normalize_preview_metadata(session.preview_metadata)
    payload: dict[str, object] = {"file_id": session.final_chat_file_id}
    if session.media_type != UploadMediaType.DOCUMENT:
        thumbnail = preview.get("thumbnail")
        if thumbnail:
            payload["thumbnail"] = thumbnail

    if session.media_type == UploadMediaType.DOCUMENT:
        payload["file_name"] = session.original_file_name
        payload["mime_type"] = session.mime_type
        payload["size"] = session.total_bytes
    else:
        width = preview.get("width")
        height = preview.get("height")
        if width and height:
            payload["width"] = int(width)
            payload["height"] = int(height)

        if batch.message_kind == UploadBatchMessageKind.ALBUM:
            payload["album_id"] = batch.id
            payload["album_index"] = int(preview.get("album_index") or 0)

        duration_ms = preview.get("duration_ms", preview.get("durationMs"))
        if isinstance(duration_ms, (int, float)):
            payload["durationMs"] = int(duration_ms)

        waveform = preview.get("waveform")
        if waveform is not None and session.media_type == UploadMediaType.VOICE:
            payload["waveform"] = waveform

        if include_caption:
            caption = (preview.get("caption") or "").strip()
            if caption:
                payload["caption"] = caption

    return json.dumps(payload)


def _batch_order_key(session: UploadSession) -> tuple[int, str]:
    preview = _normalize_preview_metadata(session.preview_metadata)
    return (int(preview.get("album_index") or 0), str(session.id))


async def _reload_messages_for_delivery(
    db: AsyncSession,
    message_ids: list[int],
) -> list[Message]:
    if not message_ids:
        return []

    stmt = (
        select(Message)
        .options(
            joinedload(Message.reply_to_message),
            joinedload(Message.forwarded_from),
            joinedload(Message.sender),
        )
        .where(Message.id.in_(message_ids))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    by_id = {message.id: message for message in rows}
    return [by_id[message_id] for message_id in message_ids if message_id in by_id]


async def _persist_batch_direct_message(
    db: AsyncSession,
    *,
    sender: User,
    receiver: User,
    content: str,
    message_type: MessageType,
) -> Message:
    message = Message(
        sender_id=sender.id,
        receiver_id=receiver.id,
        content=content,
        message_type=message_type,
        is_read=False,
        created_at=utc_now(),
    )
    db.add(message)
    await _flush_if_supported(db)
    await sync_direct_message_threading(
        db,
        sender=sender,
        receiver=receiver,
        message=message,
    )
    return message


async def _persist_batch_group_message(
    db: AsyncSession,
    *,
    chat: Chat,
    sender: User,
    content: str,
    message_type: MessageType,
) -> Message:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=sender.id)
    now = utc_now()
    message = Message(
        chat_id=chat.id,
        sender_id=sender.id,
        receiver_id=sender.id,
        content=content,
        message_type=message_type,
        is_read=True,
        created_at=now,
    )
    db.add(message)
    await _flush_if_supported(db)

    chat.last_message_id = message.id
    chat.last_message_at = now
    chat.updated_at = now
    member.last_read_message_id = message.id
    member.last_read_at = now
    member.is_marked_unread = False
    member.updated_at = now
    return message


async def commit_upload_batch(
    db: AsyncSession,
    *,
    batch: UploadBatch,
    current_user: User,
) -> UploadBatchCommitResult:
    if batch.status == UploadBatchStatus.COMMITTED:
        sessions = await list_upload_batch_sessions(db, batch.id)
        committed_ids = [
            int((item.preview_metadata or {}).get("committed_message_id"))
            for item in sessions
            if (item.preview_metadata or {}).get("committed_message_id")
        ]
        target = (await resolve_upload_target(
            db,
            current_user=current_user,
            room_kind=batch.room_kind,
            target_id=batch.target_id,
        ))[1]
        return UploadBatchCommitResult(
            batch=batch,
            target=target,
            messages=await _reload_messages_for_delivery(db, committed_ids),
        )
    if batch.status in TERMINAL_BATCH_STATUSES:
        raise HTTPException(status_code=409, detail="Upload batch is not committable")

    _, target = await resolve_upload_target(
        db,
        current_user=current_user,
        room_kind=batch.room_kind,
        target_id=batch.target_id,
    )
    sessions = await list_upload_batch_sessions(db, batch.id)
    active_sessions = [session for session in sessions if session.status != UploadSessionStatus.CANCELLED]
    if len(active_sessions) < batch.expected_items:
        raise HTTPException(status_code=409, detail="Upload batch is still missing items")
    if any(session.status not in READY_LIKE_SESSION_STATUSES for session in active_sessions):
        raise HTTPException(status_code=409, detail="Upload batch contains sessions that are not ready")
    if batch.message_kind == UploadBatchMessageKind.SINGLE and len(active_sessions) != 1:
        raise HTTPException(status_code=400, detail="Single-item batch has an unexpected number of sessions")

    ordered_sessions = sorted(active_sessions, key=_batch_order_key)
    batch.status = UploadBatchStatus.COMMITTING
    batch.updated_at = utc_now()
    batch.last_activity_at = batch.updated_at

    message_ids: list[int] = []
    try:
        receiver = target.receiver
        chat = target.chat
        for index, session in enumerate(ordered_sessions):
            message_type = _message_type_from_media_type(session.media_type)
            include_caption = (
                batch.message_kind == UploadBatchMessageKind.SINGLE
                or batch.caption_policy == UploadCaptionPolicy.FIRST_ITEM_ONLY and index == 0
            )
            content = _build_final_message_content(
                session=session,
                batch=batch,
                include_caption=include_caption,
            )
            if target.room_kind == UploadRoomKind.DIRECT:
                if receiver is None:
                    raise HTTPException(status_code=500, detail="Resolved receiver is missing")
                message = await _persist_batch_direct_message(
                    db,
                    sender=current_user,
                    receiver=receiver,
                    content=content,
                    message_type=message_type,
                )
            else:
                if chat is None:
                    raise HTTPException(status_code=500, detail="Resolved group chat is missing")
                message = await _persist_batch_group_message(
                    db,
                    chat=chat,
                    sender=current_user,
                    content=content,
                    message_type=message_type,
                )

            preview = _normalize_preview_metadata(session.preview_metadata)
            preview["committed_message_id"] = message.id
            session.preview_metadata = preview
            session.status = UploadSessionStatus.COMMITTED
            session.updated_at = utc_now()
            session.last_activity_at = session.updated_at
            message_ids.append(message.id)

        batch.committed_items = len(message_ids)
        batch.status = UploadBatchStatus.COMMITTED
        batch.updated_at = utc_now()
        batch.last_activity_at = batch.updated_at
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    messages = await _reload_messages_for_delivery(db, message_ids)
    return UploadBatchCommitResult(batch=batch, target=target, messages=messages)


async def cancel_upload_session(
    db: AsyncSession,
    *,
    session: UploadSession,
    reason: str | None = None,
) -> UploadSession:
    if session.status == UploadSessionStatus.COMMITTED:
        raise HTTPException(status_code=409, detail="Committed upload sessions cannot be cancelled")

    now = utc_now()
    session.status = UploadSessionStatus.CANCELLED
    session.last_error = reason or session.last_error
    session.updated_at = now
    session.last_activity_at = now
    await _cleanup_uncommitted_chat_file(db, session)
    await _remove_file_if_exists(session.temp_storage_path)
    await db.commit()
    return session


async def cancel_upload_batch(
    db: AsyncSession,
    *,
    batch: UploadBatch,
    reason: str | None = None,
) -> UploadBatch:
    if batch.status == UploadBatchStatus.COMMITTED:
        raise HTTPException(status_code=409, detail="Committed upload batches cannot be cancelled")

    sessions = await list_upload_batch_sessions(db, batch.id)
    now = utc_now()
    for session in sessions:
        if session.status == UploadSessionStatus.COMMITTED:
            continue
        session.status = UploadSessionStatus.CANCELLED
        session.last_error = reason or session.last_error
        session.updated_at = now
        session.last_activity_at = now
        await _cleanup_uncommitted_chat_file(db, session)
        await _remove_file_if_exists(session.temp_storage_path)

    batch.status = UploadBatchStatus.CANCELLED
    batch.updated_at = now
    batch.last_activity_at = now
    await db.commit()
    return batch


async def read_upload_chunk_bytes(file: UploadFile) -> bytes:
    try:
        return await file.read()
    finally:
        close_file = getattr(file, "close", None)
        if callable(close_file):
            close_result = close_file()
            if inspect.isawaitable(close_result):
                await close_result
