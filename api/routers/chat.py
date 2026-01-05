# trading_bot/api/routers/chat.py
"""
API endpoints for in-app messaging system
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update, func
from sqlalchemy.orm import joinedload
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timedelta, timezone
import os
import uuid

from core.db import get_db
from core.enums import MessageType
from models.message import Message
from models.conversation import Conversation
from models.user import User
from .auth import get_current_user

router = APIRouter(
    prefix="/chat",
    tags=["Chat"],
    dependencies=[Depends(get_current_user)]
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
    
    # Reply support
    reply_to_message: Optional[MessageReplyRead] = None

    class Config:
        from_attributes = True

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


class MessageUpdate(BaseModel):
    """ویرایش پیام"""
    content: str = Field(..., min_length=1, max_length=4000)


class ConversationRead(BaseModel):
    """خواندن مکالمه"""
    id: int
    other_user_id: int
    other_user_name: str
    last_message_content: Optional[str] = None
    last_message_type: Optional[MessageType] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0

    class Config:
        from_attributes = True


class PollResponse(BaseModel):
    """پاسخ پولینگ"""
    total_unread: int
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
    stmt = (
        select(Conversation)
        .options(
            joinedload(Conversation.user1),
            joinedload(Conversation.user2),
            joinedload(Conversation.last_message)
        )
        .where(
            or_(
                Conversation.user1_id == current_user.id,
                Conversation.user2_id == current_user.id
            )
        )
        .order_by(Conversation.last_message_at.desc().nullslast())
    )
    
    result = await db.execute(stmt)
    conversations = result.unique().scalars().all()
    
    response = []
    for conv in conversations:
        # تعیین کاربر مقابل
        if conv.user1_id == current_user.id:
            other_user = conv.user2
            unread = conv.unread_count_user1
        else:
            other_user = conv.user1
            unread = conv.unread_count_user2
        
        response.append(ConversationRead(
            id=conv.id,
            other_user_id=other_user.id,
            other_user_name=other_user.account_name,
            last_message_content=conv.last_message.content if conv.last_message else None,
            last_message_type=conv.last_message.message_type if conv.last_message else None,
            last_message_at=conv.last_message_at,
            unread_count=unread
        ))
    
    return response


@router.get("/messages/{user_id}", response_model=List[MessageRead])
async def get_messages(
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """تاریخچه پیام‌ها با یک کاربر خاص (پیام‌های حذف شده نمایش داده نمی‌شوند)"""
    # بررسی وجود کاربر
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    
    # کوئری پیام‌ها
    conditions = [
        or_(
            and_(Message.sender_id == current_user.id, Message.receiver_id == user_id),
            and_(Message.sender_id == user_id, Message.receiver_id == current_user.id)
        )
    ]
    
    # Filter deleted messages
    conditions.append(Message.is_deleted == False)
    
    if before_id:
        conditions.append(Message.id < before_id)
    
    stmt = (
        select(Message)
        .options(joinedload(Message.reply_to_message))
        .where(*conditions)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    # معکوس کردن برای نمایش صعودی
    return list(reversed(messages))


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
    
    if data.receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")
    
    # ایجاد پیام
    message = Message(
        sender_id=current_user.id,
        receiver_id=data.receiver_id,
        content=data.content,
        message_type=data.message_type,
        reply_to_message_id=data.reply_to_message_id,
        is_read=False
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    # Eager load reply if exists (for response)
    if message.reply_to_message_id:
        result = await db.execute(
            select(Message).options(joinedload(Message.reply_to_message)).where(Message.id == message.id)
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
    
    return message


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
    await db.refresh(msg)
    return msg


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """حذف message (Soft Delete - محدودیت ۴۸ ساعت)"""
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
    return None


@router.get("/poll", response_model=PollResponse)
async def poll_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """پولینگ برای پیام‌های جدید"""
    # محاسبه کل پیام‌های خوانده نشده
    total_stmt = select(func.count(Message.id)).where(
        Message.receiver_id == current_user.id,
        Message.is_read == False
    )
    total_result = await db.execute(total_stmt)
    total_unread = total_result.scalar() or 0
    
    # مکالمات با پیام خوانده نشده
    conv_stmt = (
        select(Conversation)
        .options(joinedload(Conversation.user1), joinedload(Conversation.user2))
        .where(
            or_(
                and_(Conversation.user1_id == current_user.id, Conversation.unread_count_user1 > 0),
                and_(Conversation.user2_id == current_user.id, Conversation.unread_count_user2 > 0)
            )
        )
    )
    result = await db.execute(conv_stmt)
    convs = result.unique().scalars().all()
    
    conversations_with_unread = []
    for conv in convs:
        if conv.user1_id == current_user.id:
            other_user = conv.user2
            unread = conv.unread_count_user1
        else:
            other_user = conv.user1
            unread = conv.unread_count_user2
        
        conversations_with_unread.append({
            "user_id": other_user.id,
            "user_name": other_user.account_name,
            "unread_count": unread
        })
    
    return PollResponse(
        total_unread=total_unread,
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


@router.post("/upload-image")
async def upload_chat_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """آپلود تصویر برای چت"""
    # بررسی نوع فایل
    allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only images are allowed")
    
    # بررسی سایز (حداکثر 5MB)
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")
    
    # ذخیره فایل
    upload_dir = "uploads/chat"
    os.makedirs(upload_dir, exist_ok=True)
    
    ext = file.filename.split(".")[-1] if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4()}.{ext}"
    filepath = os.path.join(upload_dir, filename)
    
    with open(filepath, "wb") as f:
        f.write(contents)
    
    # URL نسبی برای دسترسی
    return {"url": f"/uploads/chat/{filename}"}
