# trading_bot/api/routers/chat.py
"""
API endpoints for in-app messaging system
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update, func, case
from sqlalchemy.orm import joinedload, aliased
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timedelta, timezone
import os
import uuid
import asyncio
import aiofiles
import magic
from jose import jwt, JWTError

from core.db import get_db
from core.enums import MessageType
from core.config import settings
from models.message import Message
from models.conversation import Conversation
from models.user import User
from models.chat_file import ChatFile
from api.deps import get_current_user

from core.utils import publish_user_event
import httpx
import json
import logging

logger = logging.getLogger(__name__)

CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHAT_MEDIA_MAX_UPLOAD_LABEL = "50MB"


async def generate_location_snapshot(db: AsyncSession, uploader_id: int, lat: float, lng: float) -> Optional[str]:
    """Generate a static map image from the internal tileserver, save to uploads, and create ChatFile entry."""
    try:
        # Use tileserver-gl static API via docker network
        tile_url = f"http://tileserver:8080/styles/basic-preview/static/{lng},{lat},15/600x400.png"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(tile_url)
            if resp.status_code != 200:
                logger.warning(f"Tileserver returned {resp.status_code} for location snapshot")
                return None

        file_id = str(uuid.uuid4())
        upload_dir = os.path.join(os.getcwd(), "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, f"{file_id}.png")

        async with aiofiles.open(file_path, "wb") as f:
            await f.write(resp.content)

        # Create DB entry for the file so the frontend can request it via /api/chat/files/{id}
        chat_file = ChatFile(
            id=file_id,
            uploader_id=uploader_id,
            s3_key=file_path,
            file_name=f"location_preview_{file_id[:8]}.png",
            mime_type="image/png",
            size=len(resp.content)
        )
        db.add(chat_file)
        await db.flush()

        return file_id
    except Exception as e:
        logger.warning(f"Failed to generate location snapshot: {e}")
        return None


class TypingSignal(BaseModel):
    receiver_id: int

router = APIRouter(
    tags=["Chat"]
)


# ===== Pydantic Schemas =====


class MessageReplyRead(BaseModel):
    """خلاصه پیام برای نمایش در ریپلای"""
    id: int
    sender_id: int
    content: str
    message_type: MessageType
    is_deleted: bool = False

    class Config:
        from_attributes = True


class MessageRead(BaseModel):
    """خواندن پیام"""
    id: int
    sender_id: int
    receiver_id: int
    content: str
    message_type: MessageType
    is_read: bool
    is_deleted: bool = False
    updated_at: Optional[datetime] = None
    created_at: datetime
    
    # Forward support
    forwarded_from_id: Optional[int] = None
    forwarded_from_name: Optional[str] = None
    
    # Sender info
    sender_name: Optional[str] = None
    
    # Reply support
    reply_to_message: Optional[MessageReplyRead] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_forwarding(cls, obj: Message):
        data = {
            "id": obj.id,
            "sender_id": obj.sender_id,
            "receiver_id": obj.receiver_id,
            "content": obj.content,
            "message_type": obj.message_type,
            "is_read": obj.is_read,
            "is_deleted": obj.is_deleted,
            "updated_at": obj.updated_at,
            "created_at": obj.created_at,
            "reply_to_message": obj.reply_to_message,
            "forwarded_from_id": obj.forwarded_from_id,
            "forwarded_from_name": obj.forwarded_from.account_name if getattr(obj, "forwarded_from", None) else None,
            "sender_name": obj.sender.account_name if getattr(obj, "sender", None) else None
        }
        return cls(**data)

    @field_validator('reply_to_message')
    @classmethod
    def filter_deleted_reply(cls, v):
        # If the replied-to message is deleted, hide the reply context completely
        if v and v.is_deleted:
            return None
        return v


class MessageSend(BaseModel):
    """ارسال پیام جدید"""
    receiver_id: int
    content: str = Field(..., min_length=1, max_length=4000)
    message_type: MessageType = MessageType.TEXT
    reply_to_message_id: Optional[int] = None
    forwarded_from_id: Optional[int] = None


class MessageUpdate(BaseModel):
    """ویرایش پیام"""
    content: str = Field(..., min_length=1, max_length=4000)


class ConversationRead(BaseModel):
    """خواندن مکالمه"""
    id: int
    other_user_id: int
    other_user_name: str
    other_user_is_deleted: bool = False
    last_message_content: Optional[str] = None
    last_message_type: Optional[MessageType] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    other_user_last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PollResponse(BaseModel):
    """پاسخ پولینگ"""
    total_unread: int
    unread_chats_count: int
    conversations_with_unread: List[dict]


class StickerPack(BaseModel):
    """پک استیکر"""
    id: str
    name: str
    stickers: List[str]


# ===== Helper Functions =====

def get_conversation_key(user1_id: int, user2_id: int) -> tuple:
    """همیشه user1_id < user2_id برای یکتایی"""
    return (min(user1_id, user2_id), max(user1_id, user2_id))


def build_conversation_projection_stmt(current_user_id: int):
    """Return a projected conversation query with only the fields needed by list/poll endpoints."""
    user1_alias = aliased(User)
    user2_alias = aliased(User)
    last_message_alias = aliased(Message)

    other_user_id = case(
        (Conversation.user1_id == current_user_id, Conversation.user2_id),
        else_=Conversation.user1_id,
    ).label("other_user_id")
    other_user_name = case(
        (Conversation.user1_id == current_user_id, user2_alias.account_name),
        else_=user1_alias.account_name,
    ).label("other_user_name")
    other_user_is_deleted = case(
        (Conversation.user1_id == current_user_id, user2_alias.is_deleted),
        else_=user1_alias.is_deleted,
    ).label("other_user_is_deleted")
    other_user_last_seen_at = case(
        (Conversation.user1_id == current_user_id, user2_alias.last_seen_at),
        else_=user1_alias.last_seen_at,
    ).label("other_user_last_seen_at")
    unread_count = case(
        (Conversation.user1_id == current_user_id, Conversation.unread_count_user1),
        else_=Conversation.unread_count_user2,
    ).label("unread_count")
    last_message_content = case(
        (last_message_alias.is_deleted.is_(True), "پیام حذف شد"),
        (last_message_alias.message_type == MessageType.TEXT, last_message_alias.content),
        else_=None,
    ).label("last_message_content")

    stmt = (
        select(
            Conversation.id.label("id"),
            other_user_id,
            other_user_name,
            other_user_is_deleted,
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            Conversation.last_message_at.label("last_message_at"),
            unread_count,
            other_user_last_seen_at,
        )
        .select_from(Conversation)
        .join(user1_alias, Conversation.user1_id == user1_alias.id)
        .join(user2_alias, Conversation.user2_id == user2_alias.id)
        .outerjoin(last_message_alias, Conversation.last_message_id == last_message_alias.id)
    )
    return stmt, unread_count


async def get_or_create_conversation(
    db: AsyncSession, 
    user1_id: int, 
    user2_id: int
) -> Conversation:
    """دریافت یا ایجاد مکالمه بین دو کاربر"""
    u1, u2 = get_conversation_key(user1_id, user2_id)
    
    stmt = select(Conversation).where(
        Conversation.user1_id == u1,
        Conversation.user2_id == u2
    )
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()
    
    if not conversation:
        conversation = Conversation(
            user1_id=u1,
            user2_id=u2,
            unread_count_user1=0,
            unread_count_user2=0
        )
        db.add(conversation)
        await db.flush()
    
    return conversation


# ===== Endpoints =====

@router.get("/conversations", response_model=List[ConversationRead])
async def get_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """لیست مکالمات کاربر"""
    stmt, _ = build_conversation_projection_stmt(current_user.id)
    stmt = (
        stmt
        .where(
            or_(
                Conversation.user1_id == current_user.id,
                Conversation.user2_id == current_user.id
            )
        )
        .order_by(Conversation.last_message_at.desc().nullslast())
    )

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
    query = select(Message).where(
        or_(
            Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id
        ),
        Message.content.ilike(f"%{q}%"),
        Message.is_deleted == False
    ).order_by(Message.created_at.desc())
    
    if chat_id:
        query = query.where(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == chat_id),
                and_(Message.sender_id == chat_id, Message.receiver_id == current_user.id)
            )
        )
        
    query = query.limit(limit)
    
    # Needs joinedload for forwarding and sender
    query = query.options(joinedload(Message.forwarded_from), joinedload(Message.reply_to_message), joinedload(Message.sender))
    
    result = await db.execute(query)
    messages = result.scalars().all()
    # Serializing with custom method
    return [MessageRead.from_orm_with_forwarding(m) for m in messages]


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
    
    # شرط مکالمه
    base_conditions = [
        or_(
            and_(Message.sender_id == current_user.id, Message.receiver_id == user_id),
            and_(Message.sender_id == user_id, Message.receiver_id == current_user.id)
        ),
        Message.is_deleted == False
    ]
    
    if around_id:
        # Load context: 1/2 older, 1/2 newer + target
        # Older
        stmt_older = (
            select(Message)
            .where(*base_conditions, Message.id < around_id)
            .order_by(Message.created_at.desc())
            .limit(limit // 2)
        )
        
        # Newer (inclusive of around_id? No, treat around_id as center. Fetch it explicitly or in newer?)
        # Let's include around_id in newer set logic or explicit fetch.
        # Simpler: Older < ID, Newer >= ID
        stmt_newer = (
            select(Message)
            .where(*base_conditions, Message.id >= around_id)
            .order_by(Message.created_at.asc())
            .limit(limit // 2 + 1) # +1 to ensure center is included if limit is odd
        )
        
        # Execute
        res_older = await db.execute(stmt_older.options(joinedload(Message.reply_to_message), joinedload(Message.forwarded_from), joinedload(Message.sender)))
        res_newer = await db.execute(stmt_newer.options(joinedload(Message.reply_to_message), joinedload(Message.forwarded_from), joinedload(Message.sender)))
        
        older_msgs = res_older.scalars().all() # Descending [M-1, M-2...]
        newer_msgs = res_newer.scalars().all() # Ascending [M, M+1...]
        
        # Combine: older reversed (to be asc) + newer
        messages = list(reversed(older_msgs)) + list(newer_msgs)
        return [MessageRead.from_orm_with_forwarding(m) for m in messages]

    # Default / Pagination (before_id)
    conditions = base_conditions.copy()
    if before_id:
        conditions.append(Message.id < before_id)
    
    stmt = (
        select(Message)
        .options(joinedload(Message.reply_to_message), joinedload(Message.forwarded_from), joinedload(Message.sender))
        .where(*conditions)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    # معکوس کردن برای نمایش صعودی
    messages = list(reversed(messages))
    return [MessageRead.from_orm_with_forwarding(m) for m in messages]


@router.post("/typing", status_code=status.HTTP_204_NO_CONTENT)
async def send_typing_signal(
    data: TypingSignal,
    current_user: User = Depends(get_current_user)
):
    """ارسال سیگنال تایپ کردن به گیرنده"""
    if data.receiver_id != current_user.id:
        await publish_user_event(
            user_id=data.receiver_id,
            event="chat:typing",
            data={"sender_id": current_user.id}
        )
    return None


@router.post("/send", response_model=MessageRead)
async def send_message(
    data: MessageSend,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """ارسال پیام"""
    # بررسی وجود کاربر گیرنده
    receiver = await db.get(User, data.receiver_id)
    if not receiver:
        raise HTTPException(status_code=404, detail="Receiver not found")
        
    if receiver.is_deleted:
        raise HTTPException(status_code=400, detail="امکان ارسال پیام به کاربر غیرفعال وجود ندارد")
    
    if data.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")
        
    # Synchronously generate location snapshot to attach its ID in content immediately
    if data.message_type == MessageType.LOCATION:
        try:
            loc = json.loads(data.content)
            lat = float(loc.get("lat", loc.get("latitude")))
            lng = float(loc.get("lng", loc.get("longitude")))
            loc["lat"], loc["lng"] = lat, lng # normalize
            
            file_id = await generate_location_snapshot(db, current_user.id, lat, lng)
            if file_id:
                loc["snapshot_id"] = file_id
            
            data.content = json.dumps(loc)
        except Exception as e:
            logger.warning(f"Failed to generate location snapshot: {e}")
    
    # ایجاد پیام
    message = Message(
        sender_id=current_user.id,
        receiver_id=data.receiver_id,
        content=data.content,
        message_type=data.message_type,
        reply_to_message_id=data.reply_to_message_id,
        forwarded_from_id=data.forwarded_from_id,
        is_read=False
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    # Eager load reply/forwarded_from if exists (for response)
    if message.reply_to_message_id or message.forwarded_from_id:
        result = await db.execute(
            select(Message)
            .options(joinedload(Message.reply_to_message), joinedload(Message.forwarded_from))
            .where(Message.id == message.id)
        )
        message = result.scalars().first()
    
    # بروزرسانی مکالمه
    conversation = await get_or_create_conversation(db, current_user.id, data.receiver_id)
    conversation.last_message_id = message.id
    conversation.last_message_at = message.created_at
    
    # افزایش شمارنده خوانده نشده برای گیرنده
    u1, u2 = get_conversation_key(current_user.id, data.receiver_id)
    if data.receiver_id == u1:
        conversation.unread_count_user1 += 1
    else:
        conversation.unread_count_user2 += 1
    
    await db.commit()
    
    # انتشار پیام برای گیرنده (Real-time update)
    # استفاده از MessageRead برای سریالایز کردن مناسب
    msg_orm = MessageRead.from_orm_with_forwarding(message)
    msg_data = jsonable_encoder(msg_orm)
    # Inject sender_name manually since current_user is the sender
    msg_data["sender_name"] = current_user.account_name
    await publish_user_event(data.receiver_id, "chat:message", msg_data)

    return MessageRead.from_orm_with_forwarding(message)


@router.put("/messages/{message_id}", response_model=MessageRead)
async def update_message(
    message_id: int,
    data: MessageUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """ویرایش پیام (محدودیت ۴۸ ساعت)"""
    msg = await db.get(Message, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
        
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
        
    if msg.message_type != MessageType.TEXT:
        raise HTTPException(status_code=400, detail="Only text messages can be edited")
        
    if getattr(msg, 'forwarded_from_id', None):
        raise HTTPException(status_code=400, detail="Forwarded messages cannot be edited")

    # بررسی زمان (۴۸ ساعت)
    now = datetime.now(timezone.utc)
    if msg.created_at < now - timedelta(hours=48):
        raise HTTPException(status_code=400, detail="Message is too old to edit")

    # ذخیره تاریخچه
    history = list(msg.edit_history) if msg.edit_history else []
    history.append({
        "content": msg.content,
        "updated_at": str(now)
    })
    # نگه داشتن ۳ نسخه آخر
    if len(history) > 3:
        history.pop(0)
    
    msg.edit_history = history
    msg.content = data.content
    msg.updated_at = now
    
    await db.commit()
    
    # Eager load reply_to_message and forwarded_from to avoid lazy loading in async context
    result = await db.execute(
        select(Message)
        .options(joinedload(Message.reply_to_message), joinedload(Message.forwarded_from))
        .where(Message.id == message_id)
    )
    msg = result.scalars().first()
    return MessageRead.from_orm_with_forwarding(msg)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """حذف message (Soft Delete - محدودیت ۴۸ ساعت)"""
    if message_id <= 0 or message_id > 2147483647:
        raise HTTPException(status_code=404, detail="Message not found")

    msg = await db.get(Message, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
        
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own messages")

    # بررسی زمان (۴۸ ساعت)
    now = datetime.now(timezone.utc)
    if msg.created_at < now - timedelta(hours=48):
        raise HTTPException(status_code=400, detail="Message is too old to delete")

    msg.is_deleted = True
    msg.updated_at = now
    
    await db.commit()
    return None

@router.post("/read/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def mark_messages_read(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """علامت‌گذاری تمام پیام‌های یک کاربر به عنوان خوانده شده"""
    # بروزرسانی پیام‌ها
    stmt = (
        update(Message)
        .where(
            Message.sender_id == user_id,
            Message.receiver_id == current_user.id,
            Message.is_read == False
        )
        .values(is_read=True)
    )
    await db.execute(stmt)
    
    # بروزرسانی شمارنده مکالمه
    u1, u2 = get_conversation_key(current_user.id, user_id)
    conv_stmt = select(Conversation).where(
        Conversation.user1_id == u1,
        Conversation.user2_id == u2
    )
    result = await db.execute(conv_stmt)
    conversation = result.scalar_one_or_none()
    
    if conversation:
        if current_user.id == u1:
            conversation.unread_count_user1 = 0
        else:
            conversation.unread_count_user2 = 0
    
    await db.commit()
    
    # Notify sender that messages are read
    await publish_user_event(
        user_id=user_id,
        event="chat:read",
        data={"reader_id": current_user.id}
    )
    
    return None


@router.get("/poll", response_model=PollResponse)
async def poll_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """پولینگ برای پیام‌های جدید"""
    conv_stmt, unread_count_expr = build_conversation_projection_stmt(current_user.id)
    conv_stmt = (
        conv_stmt
        .where(
            or_(
                and_(Conversation.user1_id == current_user.id, Conversation.unread_count_user1 > 0),
                and_(Conversation.user2_id == current_user.id, Conversation.unread_count_user2 > 0)
            )
        )
    )
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
        "image/jpeg", "image/png", "image/gif", "image/webp",
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


