from datetime import datetime
from typing import List, Literal, Optional, Dict, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from core.enums import ChatType, MessageType
from core.services.chat_service import (
    COMMON_MESSAGE_REACTION_SET,
    normalize_message_reactions,
)
from models.upload_session import (
    UploadBatchMessageKind,
    UploadBatchStatus,
    UploadCaptionPolicy,
    UploadMediaType,
    UploadRoomKind,
    UploadSessionStatus,
)
from models.message import Message


class TypingSignal(BaseModel):
    receiver_id: int


ChatActivityKind = Literal["typing", "uploading_file"]


class DirectChatActivitySignal(BaseModel):
    receiver_id: int
    activity: ChatActivityKind = "typing"
    active: bool = True


class RoomChatActivitySignal(BaseModel):
    activity: ChatActivityKind = "typing"
    active: bool = True


class MessageReplyRead(BaseModel):
    """خلاصه پیام برای نمایش در ریپلای"""

    id: int
    sender_id: int
    content: str
    message_type: MessageType
    is_deleted: bool = False

    class Config:
        from_attributes = True


class UserMentionRead(BaseModel):
    user_id: int
    account_name: str

    class Config:
        from_attributes = True


class GroupMessageSeenRead(BaseModel):
    user_id: int
    account_name: str
    full_name: Optional[str] = None
    avatar_file_id: Optional[str] = None
    customer_management_name: Optional[str] = None
    seen_at: datetime

    class Config:
        from_attributes = True


class MessageReactionRead(BaseModel):
    emoji: str
    user_id: int


class RecoveryActionRead(BaseModel):
    recovery_id: str
    status: str
    prompt_type: Literal["initial_request", "identity_submitted"]
    expires_at: Optional[datetime] = None
    can_approve: bool = False
    can_reject: bool = False
    can_request_identity: bool = False
    current_action_message_id: Optional[int] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None


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
    forwarded_from_name_override: Optional[str] = None
    forwarded_from_name: Optional[str] = None
    forwarded_from_profile_user_id: Optional[int] = None
    forwarded_from_profile_account_name: Optional[str] = None
    forwarded_from_resolved_from_accountant_id: Optional[int] = None
    forwarded_from_highlight_accountant_user_id: Optional[int] = None
    forwarded_from_highlight_accountant_relation_display_name: Optional[str] = None
    sender_name: Optional[str] = None
    sender_profile_user_id: Optional[int] = None
    sender_profile_account_name: Optional[str] = None
    sender_resolved_from_accountant_id: Optional[int] = None
    sender_highlight_accountant_user_id: Optional[int] = None
    sender_highlight_accountant_relation_display_name: Optional[str] = None
    reply_to_message: Optional[MessageReplyRead] = None
    reactions: List[MessageReactionRead] = Field(default_factory=list)
    recovery_action: Optional[RecoveryActionRead] = None
    mentions: List[int] = Field(default_factory=list)
    mention_all: bool = False
    mention_details: List[UserMentionRead] = Field(default_factory=list)

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_forwarding(cls, obj: Message):
        forwarded_from_name_override = getattr(obj, "forwarded_from_name_override", None)
        forwarded_from_name = forwarded_from_name_override
        if forwarded_from_name is None and getattr(obj, "forwarded_from", None):
            forwarded_from_name = (
                getattr(obj.forwarded_from, "customer_management_name", None)
                or obj.forwarded_from.account_name
            )
        sender = getattr(obj, "sender", None)
        sender_name = (
            getattr(sender, "customer_management_name", None)
            or getattr(sender, "account_name", None)
            if sender is not None
            else None
        )

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
            "forwarded_from_name_override": forwarded_from_name_override,
            "forwarded_from_name": forwarded_from_name,
            "sender_name": sender_name,
            "mentions": getattr(obj, "mentions", []),
            "mention_all": getattr(obj, "mention_all", False),
            "mention_details": getattr(obj, "mention_details", []),
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
    forwarded_from_name_override: Optional[str] = Field(None, max_length=255)
    mentions: List[int] = Field(default_factory=list)
    mention_all: bool = False


class RoomMessageSend(BaseModel):
    """ارسال پیام/پست به room غیرمستقیم"""

    content: str = Field(..., min_length=1, max_length=4000)
    message_type: MessageType = MessageType.TEXT
    reply_to_message_id: Optional[int] = None
    forwarded_from_id: Optional[int] = None
    forwarded_from_name_override: Optional[str] = Field(None, max_length=255)
    mentions: List[int] = Field(default_factory=list)
    mention_all: bool = False


class MessageUpdate(BaseModel):
    """ویرایش پیام"""

    content: str = Field(..., min_length=1, max_length=4000)


class UploadPreviewMetadata(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    duration_ms: Optional[int] = None
    thumbnail: Optional[str] = None
    caption: Optional[str] = Field(None, max_length=4000)
    album_index: Optional[int] = None
    waveform: Optional[list[int]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "duration_ms" not in normalized and "durationMs" in normalized:
            normalized["duration_ms"] = normalized.get("durationMs")
        if "album_index" not in normalized and "albumIndex" in normalized:
            normalized["album_index"] = normalized.get("albumIndex")
        return normalized

    @field_validator("caption")
    @classmethod
    def normalize_caption(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        caption = value.strip()
        return caption or None


class UploadBatchCreateRequest(BaseModel):
    room_kind: UploadRoomKind
    target_id: int
    message_kind: UploadBatchMessageKind
    expected_items: int = Field(..., ge=1, le=50)
    caption_policy: UploadCaptionPolicy = UploadCaptionPolicy.NONE
    idempotency_key: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def validate_single_batch_shape(self):
        if self.message_kind == UploadBatchMessageKind.SINGLE and self.expected_items != 1:
            raise ValueError("Single-item batches must expect exactly one item")
        return self


class UploadBatchCreateResponse(BaseModel):
    batch_id: str
    status: UploadBatchStatus
    expires_at: datetime


class UploadSessionCreateRequest(BaseModel):
    batch_id: Optional[str] = None
    room_kind: UploadRoomKind
    target_id: int
    media_type: UploadMediaType
    file_name: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(..., min_length=1, max_length=100)
    total_bytes: int = Field(..., gt=0, le=50 * 1024 * 1024)
    chunk_size: int = Field(..., gt=0, le=10 * 1024 * 1024)
    preview_metadata: UploadPreviewMetadata = Field(default_factory=UploadPreviewMetadata)
    sha256_full: Optional[str] = Field(None, min_length=16, max_length=128)

    @field_validator("file_name")
    @classmethod
    def normalize_file_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("file_name is required")
        return cleaned


class UploadSessionCreateResponse(BaseModel):
    session_id: str
    resume_token: str
    next_offset: int = 0
    chunk_size: int
    expires_at: datetime
    status: UploadSessionStatus


class UploadSessionChunkAppendResponse(BaseModel):
    session_id: str
    received_bytes: int
    next_offset: int
    status: UploadSessionStatus


class UploadSessionStateRead(BaseModel):
    session_id: str
    status: UploadSessionStatus
    next_offset: int
    received_bytes: int
    total_bytes: int
    preview_metadata: UploadPreviewMetadata = Field(default_factory=UploadPreviewMetadata)
    final_chat_file_id: Optional[str] = None


class UploadSessionStatusChangeResponse(BaseModel):
    session_id: str
    status: UploadSessionStatus
    final_chat_file_id: Optional[str] = None


class UploadBatchCommitResponse(BaseModel):
    batch_id: str
    status: UploadBatchStatus
    committed_items: int
    messages: List[MessageRead] = Field(default_factory=list)


class UploadBatchCancelResponse(BaseModel):
    batch_id: str
    status: UploadBatchStatus


class MessageReactionToggle(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=8)

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, value: str) -> str:
        emoji = value.strip()
        if emoji not in COMMON_MESSAGE_REACTION_SET:
            raise ValueError("Unsupported reaction")
        return emoji


class MessagePinUpdateRequest(BaseModel):
    pinned: bool = True


class PinnedMessageStateResponse(BaseModel):
    chat_id: Optional[int] = None
    room_kind: str
    pinned_at: Optional[datetime] = None
    pinned_by_user_id: Optional[int] = None
    message: Optional[MessageRead] = None


class ConversationRead(BaseModel):
    """خواندن مکالمه"""

    id: int
    other_user_id: int
    other_user_name: str
    avatar_file_id: Optional[str] = None
    profile_user_id: Optional[int] = None
    profile_account_name: Optional[str] = None
    resolved_from_accountant_id: Optional[int] = None
    chat_role_kind: Optional[str] = None
    chat_role_label: Optional[str] = None
    chat_accountant_owner_name: Optional[str] = None
    chat_accountant_owner_label: Optional[str] = None
    customer_management_name: Optional[str] = None
    highlight_accountant_user_id: Optional[int] = None
    highlight_accountant_relation_display_name: Optional[str] = None
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
    member_count: Optional[int] = None
    max_members: Optional[int] = None
    is_system: bool = False
    is_mandatory: bool = False
    is_muted: bool = False
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    pin_order: Optional[int] = None
    unread_mention_count: int = 0

    class Config:
        from_attributes = True


class PollResponse(BaseModel):
    """پاسخ پولینگ"""

    total_unread: int
    unread_chats_count: int
    conversations_with_unread: List[dict]
    muted_conversation_ids: List[int] = Field(default_factory=list)
    total_unread_mentions: int = 0


class ConversationMuteUpdateRequest(BaseModel):
    muted: bool


class ConversationMuteResponse(BaseModel):
    target_id: int
    chat_id: Optional[int] = None
    is_muted: bool = False


class ConversationUnreadUpdateRequest(BaseModel):
    unread: bool = True


class ConversationUnreadResponse(BaseModel):
    target_id: int
    chat_id: Optional[int] = None
    unread_count: int = 0


class ConversationPinUpdateRequest(BaseModel):
    pinned: bool


class ConversationPinResponse(BaseModel):
    target_id: int
    chat_id: Optional[int] = None
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    pin_order: Optional[int] = None


class ConversationPinReorderUpdateRequest(BaseModel):
    direction: str = Field(..., min_length=2, max_length=4)

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, value: str) -> str:
        direction = value.strip().lower()
        if direction not in {"up", "down"}:
            raise ValueError("Direction must be up or down")
        return direction


class ConversationPinReorderResponse(BaseModel):
    target_id: int
    chat_id: Optional[int] = None
    pin_order: Optional[int] = None


class ConversationHideResponse(BaseModel):
    target_id: int
    chat_id: Optional[int] = None
    hidden: bool = True


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
    avatar_file_id: Optional[str] = None
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
    avatar_file_id: Optional[str] = None
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
    avatar_file_id: Optional[str] = None
    role: str
    joined_at: datetime
    is_group_creator: bool = False
    chat_role_kind: Optional[str] = None
    chat_role_label: Optional[str] = None
    chat_accountant_owner_name: Optional[str] = None
    chat_accountant_owner_label: Optional[str] = None
    customer_management_name: Optional[str] = None


class GroupCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    avatar_file_id: Optional[str] = Field(None, max_length=36)
    member_ids: List[int] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Group title is required")
        return cleaned

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("avatar_file_id")
    @classmethod
    def normalize_avatar_file_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

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


class GroupUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    avatar_file_id: Optional[str] = Field(None, max_length=36)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Group title is required")
        return cleaned

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("avatar_file_id")
    @classmethod
    def normalize_avatar_file_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class GroupMemberAddRequest(BaseModel):
    user_id: int = Field(..., gt=0)


class GroupMemberMutationResponse(BaseModel):
    chat_id: int
    user_id: int
    role: Optional[str] = None
    removed: bool = False
    left: bool = False
    member_count: int
    unchanged: bool = False


class ChannelMemberRead(BaseModel):
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    avatar_file_id: Optional[str] = None
    role: str
    joined_at: datetime
    is_channel_creator: bool = False
    chat_role_kind: Optional[str] = None
    chat_role_label: Optional[str] = None
    chat_accountant_owner_name: Optional[str] = None
    chat_accountant_owner_label: Optional[str] = None
    customer_management_name: Optional[str] = None


class ChannelCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    avatar_file_id: Optional[str] = Field(None, max_length=36)

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

    @field_validator("avatar_file_id")
    @classmethod
    def normalize_avatar_file_id(cls, value: Optional[str]) -> Optional[str]:
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
    avatar_file_id: Optional[str] = Field(None, max_length=36)

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

    @field_validator("avatar_file_id")
    @classmethod
    def normalize_avatar_file_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ChannelInviteCandidateRead(BaseModel):
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    avatar_file_id: Optional[str] = None
    is_already_member: bool = False
    chat_role_kind: Optional[str] = None
    chat_role_label: Optional[str] = None
    chat_accountant_owner_name: Optional[str] = None
    chat_accountant_owner_label: Optional[str] = None
    customer_management_name: Optional[str] = None


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
    left: bool = False
    unchanged: bool = False
