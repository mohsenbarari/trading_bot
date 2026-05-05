# trading_bot/api/routers/chat.py
"""
API endpoints for in-app messaging system
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import os
import uuid
import asyncio
import aiofiles
import magic
from jose import jwt, JWTError

from core.db import get_db
from core.config import settings
from models.user import User
from models.chat_file import ChatFile
from api.deps import get_current_user
from api.routers.chat_schemas import (
    ConversationRead,
    MessageRead,
    MessageReactionToggle,
    MessageSend,
    MessageUpdate,
    PollResponse,
    StickerPack,
    TypingSignal,
)

from core.services.chat_service import (
    apply_direct_message_delete,
    apply_direct_message_edit,
    apply_direct_message_reaction_toggle,
    build_direct_conversation_list_stmt,
    build_direct_message_history_statements,
    build_direct_message_search_stmt,
    build_direct_unread_poll_stmt,
    commit_direct_read_state,
    persist_sent_direct_message,
    publish_direct_message_event,
    publish_direct_read_event,
    publish_direct_reaction_event,
    publish_direct_typing_event,
    prepare_direct_message_send,
    serialize_direct_message_for_response,
    serialize_direct_messages_for_response,
)
from core.utils import publish_user_event
import logging

logger = logging.getLogger(__name__)

CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHAT_MEDIA_MAX_UPLOAD_LABEL = "50MB"

router = APIRouter(
    tags=["Chat"]
)


# ===== Endpoints =====

@router.get("/conversations", response_model=List[ConversationRead])
async def get_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """لیست مکالمات کاربر"""
    stmt = build_direct_conversation_list_stmt(current_user.id)
    result = await db.execute(stmt)
    return [ConversationRead(**row) for row in result.mappings().all()]


@router.get("/search", response_model=List[MessageRead])
async def search_messages(
    q: str,
    chat_id: Optional[int] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Search messages.
    """
    query = await build_direct_message_search_stmt(
        db,
        current_user_id=current_user.id,
        query_text=q,
        other_user_id=chat_id,
        limit=limit,
    )
    result = await db.execute(query)
    messages = result.scalars().all()
    # Serializing with custom method
    return serialize_direct_messages_for_response(
        messages,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.get("/messages/{user_id}", response_model=List[MessageRead])
async def get_messages(
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    around_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """تاریخچه پیام‌ها. پشتیبانی از before_id (اسکرول به بالا) و around_id (پرش به پیام)."""
    # بررسی وجود کاربر
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    stmt_older, stmt_newer = await build_direct_message_history_statements(
        db,
        current_user_id=current_user.id,
        other_user_id=user_id,
        limit=limit,
        before_id=before_id,
        around_id=around_id,
    )

    if around_id:
        # Execute
        res_older = await db.execute(stmt_older)
        res_newer = await db.execute(stmt_newer)
        
        older_msgs = res_older.scalars().all() # Descending [M-1, M-2...]
        newer_msgs = res_newer.scalars().all() # Ascending [M, M+1...]
        
        # Combine: older reversed (to be asc) + newer
        messages = list(reversed(older_msgs)) + list(newer_msgs)
        return serialize_direct_messages_for_response(
            messages,
            serializer=MessageRead.from_orm_with_forwarding,
        )

    result = await db.execute(stmt_older)
    messages = result.scalars().all()
    
    # معکوس کردن برای نمایش صعودی
    messages = list(reversed(messages))
    return serialize_direct_messages_for_response(
        messages,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.post("/typing", status_code=status.HTTP_204_NO_CONTENT)
async def send_typing_signal(
    data: TypingSignal,
    current_user: User = Depends(get_current_user)
):
    """ارسال سیگنال تایپ کردن به گیرنده"""
    await publish_direct_typing_event(
        receiver_id=data.receiver_id,
        sender_id=current_user.id,
        publisher=publish_user_event,
    )
    return None


@router.post("/send", response_model=MessageRead)
async def send_message(
    data: MessageSend,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """ارسال پیام"""
    receiver, prepared_content = await prepare_direct_message_send(
        db,
        sender=current_user,
        receiver_id=data.receiver_id,
        content=data.content,
        message_type=data.message_type,
    )
    
    message = await persist_sent_direct_message(
        db,
        sender=current_user,
        receiver=receiver,
        content=prepared_content,
        message_type=data.message_type,
        reply_to_message_id=data.reply_to_message_id,
        forwarded_from_id=data.forwarded_from_id,
    )
    if message is None:
        raise HTTPException(status_code=500, detail="Failed to persist message")
    
    # انتشار پیام برای گیرنده (Real-time update)
    # استفاده از MessageRead برای سریالایز کردن مناسب
    await publish_direct_message_event(
        receiver_id=data.receiver_id,
        message=message,
        serializer=MessageRead.from_orm_with_forwarding,
        publisher=publish_user_event,
        sender_name=current_user.account_name,
    )

    return serialize_direct_message_for_response(
        message,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.put("/messages/{message_id}", response_model=MessageRead)
async def update_message(
    message_id: int,
    data: MessageUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """ویرایش پیام (محدودیت ۴۸ ساعت)"""
    msg = await apply_direct_message_edit(
        db,
        message_id=message_id,
        actor_id=current_user.id,
        content=data.content,
    )
    return serialize_direct_message_for_response(
        msg,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.post("/messages/{message_id}/reaction", response_model=MessageRead)
async def toggle_message_reaction(
    message_id: int,
    data: MessageReactionToggle,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """افزودن/حذف ری‌اکشن روی پیام"""
    updated_message = await apply_direct_message_reaction_toggle(
        db,
        message_id=message_id,
        actor_id=current_user.id,
        emoji=data.emoji,
    )
    if not updated_message:
        raise HTTPException(status_code=404, detail="Message not found")

    reaction_payload = serialize_direct_message_for_response(
        updated_message,
        serializer=MessageRead.from_orm_with_forwarding,
    )
    await publish_direct_reaction_event(
        updated_message,
        serializer=MessageRead.from_orm_with_forwarding,
        publisher=publish_user_event,
    )

    return reaction_payload


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """حذف message (Soft Delete - محدودیت ۴۸ ساعت)"""
    await apply_direct_message_delete(
        db,
        message_id=message_id,
        actor_id=current_user.id,
    )
    return None

@router.post("/read/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def mark_messages_read(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """علامت‌گذاری تمام پیام‌های یک کاربر به عنوان خوانده شده"""
    await commit_direct_read_state(
        db,
        reader=current_user,
        other_user_id=user_id,
    )
    
    # Notify sender that messages are read
    await publish_direct_read_event(
        other_user_id=user_id,
        reader_id=current_user.id,
        publisher=publish_user_event,
    )
    
    return None


@router.get("/poll", response_model=PollResponse)
async def poll_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """پولینگ برای پیام‌های جدید"""
    conv_stmt = build_direct_unread_poll_stmt(current_user.id)
    result = await db.execute(conv_stmt)
    convs = result.mappings().all()

    unread_chats_count = len(convs)
    total_unread = sum((row["unread_count"] or 0) for row in convs)
    conversations_with_unread = [
        {
            "user_id": row["other_user_id"],
            "user_name": row["other_user_name"],
            "unread_count": row["unread_count"],
            "is_deleted": row["other_user_is_deleted"],
        }
        for row in convs
    ]
    
    return PollResponse(
        total_unread=total_unread,
        unread_chats_count=unread_chats_count,
        conversations_with_unread=conversations_with_unread
    )


@router.get("/stickers", response_model=List[StickerPack])
async def get_stickers():
    """لیست استیکرهای موجود"""
    # استیکرهای پیش‌فرض - می‌توان از فایل یا دیتابیس خواند
    stickers = [
        StickerPack(
            id="emotions",
            name="احساسات",
            stickers=[
                "happy", "sad", "angry", "surprised", "love",
                "laugh", "cry", "think", "cool", "sleepy"
            ]
        ),
        StickerPack(
            id="actions", 
            name="اعمال",
            stickers=[
                "thumbs_up", "thumbs_down", "clap", "wave", "pray",
                "handshake", "muscle", "point_up", "peace", "ok"
            ]
        ),
        StickerPack(
            id="trade",
            name="معامله",
            stickers=[
                "deal", "money", "chart_up", "chart_down", "coin",
                "bank", "calculator", "document", "stamp", "check"
            ]
        )
    ]
    return stickers


@router.post("/upload-media")
async def upload_chat_media(
    file: UploadFile = File(...),
    thumbnail: str = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """آپلود فایل برای چت (ذخیره روی دیسک سرور)"""
    allowed_types = [
        "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heic-sequence", "image/heif", "image/heif-sequence",
        "video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "application/mp4", "video/x-m4v", "video/3gpp", "video/quicktime", "application/octet-stream",
        "audio/mp4", "audio/webm", "audio/ogg", "audio/mpeg", "audio/aac", "audio/x-m4a", "audio/wav", "audio/x-wav",
        "application/pdf", "text/plain", "text/csv", "application/json", "application/xml", "text/xml", "application/rtf",
        "application/zip", "application/x-zip-compressed", "application/x-rar-compressed", "application/vnd.rar", "application/x-7z-compressed",
        "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ]
    
    base_content_type = file.content_type.split(";")[0].strip()
    
    if base_content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")
    
    # بررسی محتوای واقعی فایل با استفاده از Magic bytes
    # CPU-bound libmagic probe is offloaded to a thread to avoid blocking the event loop
    contents = await file.read()
    mime = await asyncio.to_thread(lambda: magic.from_buffer(contents, mime=True))
    
    # allow magic to return "video/webm" for "audio/webm" files
    if mime == 'video/webm' and base_content_type == 'audio/webm':
        mime = 'audio/webm'

    if mime not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Invalid file content. Real type is {mime} and base type is {base_content_type}")
    
    # بررسی سایز (حداکثر 50MB)
    size = len(contents)
    if size > CHAT_MEDIA_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {CHAT_MEDIA_MAX_UPLOAD_LABEL})")
        
    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else mime.split("/")[-1]
    file_uuid = str(uuid.uuid4())
    
    # ذخیره در پوشه محلی سرور (نه S3)
    # فضای مخفی - مسیر واقعی هرگز به کاربر نمایش داده نمی‌شود
    upload_dir = os.path.join("uploads", "chat_files", str(current_user.id))
    os.makedirs(upload_dir, exist_ok=True)
    
    file_path = os.path.join(upload_dir, f"{file_uuid}.{ext}")

    # EXIF transpose for images: rotate pixels to match EXIF orientation, then strip the tag.
    # CPU-bound Pillow work runs in a thread pool to avoid blocking the uvicorn event loop,
    # which otherwise stalls unrelated /api/chat/* requests (messages, conversations, poll)
    # while media uploads are being processed.
    img_width = None
    img_height = None
    if mime.startswith("image/") and mime != "image/gif":
        def _exif_transpose_sync(raw: bytes, mime_type: str):
            from PIL import Image as PILImage, ImageOps
            import io as _io
            pil_img = PILImage.open(_io.BytesIO(raw))
            pil_img = ImageOps.exif_transpose(pil_img)
            w, h = pil_img.size
            buf = _io.BytesIO()
            fmt = "JPEG" if mime_type in ("image/jpeg", "image/jpg") else ("PNG" if mime_type == "image/png" else "WEBP")
            pil_img.save(buf, format=fmt, quality=90)
            return buf.getvalue(), w, h

        try:
            new_contents, img_width, img_height = await asyncio.to_thread(
                _exif_transpose_sync, contents, mime
            )
            contents = new_contents
            size = len(contents)
        except Exception as e:
            logger.warning(f"Pillow EXIF transpose failed, saving original: {e}")

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(contents)
            
    # ذخیره در دیتابیس (s3_key = مسیر نسبی فایل روی دیسک)
    chat_file = ChatFile(
        id=file_uuid,
        uploader_id=current_user.id,
        s3_key=file_path,  # در اینجا مسیر فایل ذخیره می‌شود
        file_name=file.filename,
        mime_type=mime,
        size=size,
        thumbnail=thumbnail
    )
    db.add(chat_file)
    await db.commit()
    
    # برگرداندن شناسه، تامنیل و ابعاد تصویر
    result = {
        "file_id": chat_file.id,
        "thumbnail": chat_file.thumbnail,
        "file_name": chat_file.file_name,
        "mime_type": chat_file.mime_type,
        "size": chat_file.size,
    }
    if img_width and img_height:
        result["width"] = img_width
        result["height"] = img_height
    return result

@router.get("/files/{file_id}")
async def get_chat_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    token: str = Query(None)
):
    """دریافت امن فایل چت (استریمینگ از دیسک - مسیر واقعی مخفی است)"""
    if not token:
        raise HTTPException(status_code=401, detail="Token is missing")
    
    try:
        jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # پیداکردن رکورد در دیتابیس
    chat_file = await db.get(ChatFile, file_id)
    if not chat_file:
        raise HTTPException(status_code=404, detail="File not found")
    
    # بررسی وجود فایل روی دیسک
    file_path = chat_file.s3_key  # s3_key حالا مسیر فایل محلی است
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    # ارسال مستقیم فایل از دیسک (مسیر واقعی هرگز نمایش داده نمی‌شود)
    from fastapi.responses import FileResponse
    return FileResponse(
        path=file_path,
        media_type=chat_file.mime_type,
        filename=chat_file.file_name or f"{file_id}"
    )


