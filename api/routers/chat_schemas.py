from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from core.enums import MessageType
from core.services.chat_service import (
    COMMON_MESSAGE_REACTION_SET,
    normalize_message_reactions,
)
from models.message import Message


class TypingSignal(BaseModel):
    receiver_id: int


class MessageReplyRead(BaseModel):
    """خلاصه پیام برای نمایش در ریپلای"""

    id: int
    sender_id: int
    content: str
    message_type: MessageType
    is_deleted: bool = False

    class Config:
        from_attributes = True


class MessageReactionRead(BaseModel):
    emoji: str
    user_id: int


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
    forwarded_from_id: Optional[int] = None
    forwarded_from_name: Optional[str] = None
    sender_name: Optional[str] = None
    reply_to_message: Optional[MessageReplyRead] = None
    reactions: List[MessageReactionRead] = Field(default_factory=list)

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
            "reactions": normalize_message_reactions(getattr(obj, "reactions", [])),
            "forwarded_from_id": obj.forwarded_from_id,
            "forwarded_from_name": obj.forwarded_from.account_name
            if getattr(obj, "forwarded_from", None)
            else None,
            "sender_name": obj.sender.account_name if getattr(obj, "sender", None) else None,
        }
        return cls(**data)

    @field_validator("reply_to_message")
    @classmethod
    def filter_deleted_reply(cls, value):
        if value and value.is_deleted:
            return None
        return value


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


class MessageReactionToggle(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=8)

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, value: str) -> str:
        emoji = value.strip()
        if emoji not in COMMON_MESSAGE_REACTION_SET:
            raise ValueError("Unsupported reaction")
        return emoji


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