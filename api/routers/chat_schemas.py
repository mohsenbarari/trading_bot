from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from core.enums import ChatType, MessageType
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


class RoomMessageSend(BaseModel):
    """ارسال پیام/پست به room غیرمستقیم"""

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
    room_kind: str = "direct"
    chat_id: Optional[int] = None
    can_send: bool = True
    member_role: Optional[str] = None

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


class ChannelRoomRead(BaseModel):
    id: int
    type: ChatType
    title: str
    description: Optional[str] = None
    created_by_id: Optional[int] = None
    is_system: bool = False
    is_mandatory: bool = False
    member_count: int = 0
    created_at: datetime


class GroupRoomRead(BaseModel):
    id: int
    type: ChatType
    title: str
    description: Optional[str] = None
    created_by_id: Optional[int] = None
    member_count: int = 0
    max_members: int = 50
    created_at: datetime
    current_user_role: Optional[str] = None


class GroupMemberRead(BaseModel):
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    role: str
    joined_at: datetime
    is_group_creator: bool = False


class GroupCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    member_ids: List[int] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Group title is required")
        return cleaned

    @field_validator("member_ids")
    @classmethod
    def normalize_member_ids(cls, value: List[int]) -> List[int]:
        normalized: List[int] = []
        seen: set[int] = set()
        for user_id in value:
            if user_id <= 0 or user_id in seen:
                continue
            seen.add(user_id)
            normalized.append(user_id)
        return normalized


class GroupCreateResponse(BaseModel):
    group: GroupRoomRead


class GroupDetailRead(BaseModel):
    group: GroupRoomRead
    members: List[GroupMemberRead]


class ChannelMemberRead(BaseModel):
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    role: str
    joined_at: datetime
    is_channel_creator: bool = False


class ChannelCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Channel title is required")
        return cleaned

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ChannelCreateResponse(BaseModel):
    channel: ChannelRoomRead
    member_picker_required: bool = True


class ChannelUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Channel title is required")
        return cleaned

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ChannelInviteCandidateRead(BaseModel):
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    is_already_member: bool = False


class ChannelInviteCandidateListResponse(BaseModel):
    items: List[ChannelInviteCandidateRead]
    total: int
    active_total: int


class ChannelBulkMemberAddRequest(BaseModel):
    user_ids: List[int] = Field(default_factory=list)
    select_all_active_users: bool = False

    @field_validator("user_ids")
    @classmethod
    def normalize_user_ids(cls, value: List[int]) -> List[int]:
        normalized: List[int] = []
        seen: set[int] = set()
        for user_id in value:
            if user_id <= 0 or user_id in seen:
                continue
            seen.add(user_id)
            normalized.append(user_id)
        return normalized

    @model_validator(mode="after")
    def validate_selection_mode(self):
        if self.select_all_active_users and self.user_ids:
            raise ValueError("Provide either user_ids or select_all_active_users")
        if not self.select_all_active_users and not self.user_ids:
            raise ValueError("No users selected")
        return self


class ChannelBulkMemberAddResponse(BaseModel):
    chat_id: int
    processed_user_ids: List[int]
    added_count: int
    reactivated_count: int
    already_member_count: int
    member_count: int
    select_all_active_users: bool = False


class ChannelMemberUpdateRequest(BaseModel):
    role: Optional[str] = None
    remove_member: bool = False

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"admin", "member"}:
            raise ValueError("Unsupported channel member role")
        return normalized

    @model_validator(mode="after")
    def validate_change_request(self):
        if self.remove_member and self.role is not None:
            raise ValueError("Provide either role or remove_member")
        if not self.remove_member and self.role is None:
            raise ValueError("No membership change requested")
        return self


class ChannelMemberMutationResponse(BaseModel):
    chat_id: int
    user_id: int
    role: Optional[str] = None
    removed: bool = False
    member_count: int