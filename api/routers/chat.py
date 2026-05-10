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
from models.chat import Chat
from models.message import Message
from models.user import User
from models.chat_file import ChatFile
from api.deps import get_current_user, verify_super_admin
from api.routers.chat_schemas import (
    ChannelBulkMemberAddRequest,
    ChannelBulkMemberAddResponse,
    ChannelCreateRequest,
    ChannelCreateResponse,
    ChannelInviteCandidateListResponse,
    ConversationMuteResponse,
    ConversationMuteUpdateRequest,
    ChannelMemberMutationResponse,
    ChannelMemberRead,
    ChannelMemberUpdateRequest,
    ChannelRoomRead,
    ChannelUpdateRequest,
    ConversationHideResponse,
    ConversationPinResponse,
    ConversationPinReorderResponse,
    ConversationPinReorderUpdateRequest,
    ConversationPinUpdateRequest,
    ConversationRead,
    ConversationUnreadResponse,
    ConversationUnreadUpdateRequest,
    GroupCreateRequest,
    GroupCreateResponse,
    GroupDetailRead,
    GroupMemberAddRequest,
    GroupMemberRead,
    GroupMemberMutationResponse,
    GroupRoomRead,
    GroupUpdateRequest,
    MessageRead,
    MessageReactionToggle,
    MessagePinUpdateRequest,
    MessageSend,
    MessageUpdate,
    PinnedMessageStateResponse,
    PollResponse,
    RoomMessageSend,
    StickerPack,
    TypingSignal,
)

from core.enums import ChatMemberRole, ChatType
from core.services.chat_room_service import (
    add_group_member,
    bulk_add_channel_members,
    count_active_chat_members,
    create_group_chat,
    create_optional_channel,
    get_room_or_404,
    get_active_group_admin_or_403,
    get_active_group_member_or_403,
    get_active_channel_member_or_403,
    get_group_or_404,
    get_channel_or_404,
    leave_group_chat,
    leave_channel_chat,
    list_group_conversations,
    list_group_members,
    list_group_messages,
    list_groups_for_user,
    list_active_room_member_user_ids,
    list_channel_conversations,
    list_active_channel_member_user_ids,
    list_channel_invite_candidates,
    list_channel_members,
    list_channel_messages,
    list_manageable_channels,
    mark_channel_messages_read,
    mark_channel_messages_read_with_broadcast,
    mark_group_messages_read_with_broadcast,
    publish_group_message_event,
    publish_group_reaction_event,
    publish_group_read_event,
    publish_channel_message_event,
    publish_channel_reaction_event,
    publish_channel_read_event,
    remove_group_member,
    send_group_message,
    send_channel_message,
    set_room_mark_unread_state,
    set_room_mute_state,
    set_room_pin_state,
    update_group_admin_status,
    update_group_chat,
    update_manageable_channel_metadata,
    update_channel_member,
)
from core.services.chat_service import (
    apply_direct_message_delete,
    apply_direct_message_edit,
    apply_message_pin_state,
    apply_direct_message_reaction_toggle,
    build_direct_conversation_list_stmt,
    build_direct_message_history_statements,
    build_direct_message_search_stmt,
    build_direct_unread_poll_stmt,
    commit_direct_read_state,
    get_existing_direct_chat,
    get_pinned_message_for_chat,
    hide_direct_conversation,
    persist_sent_direct_message,
    publish_direct_message_event,
    publish_direct_read_event,
    publish_direct_reaction_event,
    publish_direct_typing_event,
    prepare_direct_message_send,
    reorder_chat_member_pin_order,
    set_direct_chat_mark_unread_state,
    set_direct_chat_mute_state,
    set_direct_chat_pin_state,
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


def _serialize_pinned_message_state(
    *,
    room_kind: str,
    chat: Chat | None,
    message: Message | None,
) -> PinnedMessageStateResponse:
    return PinnedMessageStateResponse(
        chat_id=chat.id if chat is not None else None,
        room_kind=room_kind,
        pinned_at=getattr(chat, "pinned_message_at", None) if chat is not None else None,
        pinned_by_user_id=getattr(chat, "pinned_message_by_id", None) if chat is not None else None,
        message=(
            serialize_direct_message_for_response(
                message,
                serializer=MessageRead.from_orm_with_forwarding,
            )
            if message is not None
            else None
        ),
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
    direct_conversations = [ConversationRead(**row) for row in result.mappings().all()]
    group_conversations = [
        ConversationRead(
            id=row.id,
            other_user_id=row.other_user_id,
            other_user_name=row.other_user_name,
            other_user_is_deleted=row.other_user_is_deleted,
            last_message_content=row.last_message_content,
            last_message_type=row.last_message_type,
            last_message_at=row.last_message_at,
            unread_count=row.unread_count,
            other_user_last_seen_at=row.other_user_last_seen_at,
            room_kind=row.room_kind,
            chat_id=row.chat_id,
            can_send=row.can_send,
            member_role=row.member_role,
            member_count=row.member_count,
            max_members=row.max_members,
            is_system=row.is_system,
            is_mandatory=row.is_mandatory,
            is_muted=getattr(row, "is_muted", False),
            is_pinned=getattr(row, "is_pinned", False),
            pinned_at=getattr(row, "pinned_at", None),
            pin_order=getattr(row, "pin_order", None),
        )
        for row in await list_group_conversations(db, current_user_id=current_user.id)
    ]
    channel_conversations = [
        ConversationRead(
            id=row.id,
            other_user_id=row.other_user_id,
            other_user_name=row.other_user_name,
            other_user_is_deleted=row.other_user_is_deleted,
            last_message_content=row.last_message_content,
            last_message_type=row.last_message_type,
            last_message_at=row.last_message_at,
            unread_count=row.unread_count,
            other_user_last_seen_at=row.other_user_last_seen_at,
            room_kind=row.room_kind,
            chat_id=row.chat_id,
            can_send=row.can_send,
            member_role=row.member_role,
            member_count=row.member_count,
            max_members=row.max_members,
            is_system=row.is_system,
            is_mandatory=row.is_mandatory,
            is_muted=getattr(row, "is_muted", False),
            is_pinned=getattr(row, "is_pinned", False),
            pinned_at=getattr(row, "pinned_at", None),
            pin_order=getattr(row, "pin_order", None),
        )
        for row in await list_channel_conversations(db, current_user_id=current_user.id)
    ]
    conversations = [*direct_conversations, *group_conversations, *channel_conversations]
    conversations.sort(
        key=lambda item: item.last_message_at.isoformat() if item.last_message_at else "",
        reverse=True,
    )
    return conversations


@router.post("/direct/{user_id}/pin", response_model=ConversationPinResponse)
async def pin_direct_conversation(
    user_id: int,
    data: ConversationPinUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    member = await set_direct_chat_pin_state(
        db,
        actor=current_user,
        other_user_id=user_id,
        pinned=data.pinned,
    )
    return ConversationPinResponse(
        target_id=user_id,
        chat_id=member.chat_id,
        is_pinned=bool(member.is_pinned),
        pinned_at=member.pinned_at,
        pin_order=getattr(member, "pin_order", None),
    )


@router.post("/direct/{user_id}/pin-order", response_model=ConversationPinReorderResponse)
async def reorder_direct_conversation_pin(
    user_id: int,
    data: ConversationPinReorderUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await set_direct_chat_pin_state(
        db,
        actor=current_user,
        other_user_id=user_id,
        pinned=True,
    )
    reordered_member = await reorder_chat_member_pin_order(
        db,
        user_id=current_user.id,
        chat_id=member.chat_id,
        direction=data.direction,
    )
    return ConversationPinReorderResponse(
        target_id=user_id,
        chat_id=reordered_member.chat_id,
        pin_order=reordered_member.pin_order,
    )


@router.get("/direct/{user_id}/pinned-message", response_model=PinnedMessageStateResponse)
async def get_direct_pinned_message(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    chat = await get_existing_direct_chat(db, current_user.id, user_id)
    message = await get_pinned_message_for_chat(db, chat) if chat is not None else None
    return _serialize_pinned_message_state(room_kind="direct", chat=chat, message=message)


@router.post("/direct/{user_id}/mute", response_model=ConversationMuteResponse)
async def mute_direct_conversation(
    user_id: int,
    data: ConversationMuteUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    member = await set_direct_chat_mute_state(
        db,
        actor=current_user,
        other_user_id=user_id,
        muted=data.muted,
    )
    return ConversationMuteResponse(
        target_id=user_id,
        chat_id=member.chat_id,
        is_muted=bool(member.is_muted),
    )


@router.post("/direct/{user_id}/mark-unread", response_model=ConversationUnreadResponse)
async def mark_direct_conversation_unread(
    user_id: int,
    data: ConversationUnreadUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    member = await set_direct_chat_mark_unread_state(
        db,
        actor=current_user,
        other_user_id=user_id,
        unread=data.unread,
    )
    return ConversationUnreadResponse(
        target_id=user_id,
        chat_id=member.chat_id,
        unread_count=1 if data.unread else 0,
    )


@router.delete("/direct/{user_id}", response_model=ConversationHideResponse)
async def delete_direct_conversation(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    member = await hide_direct_conversation(
        db,
        actor=current_user,
        other_user_id=user_id,
    )
    return ConversationHideResponse(
        target_id=user_id,
        chat_id=member.chat_id,
        hidden=bool(member.is_hidden),
    )


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


@router.get("/groups", response_model=List[GroupRoomRead])
async def get_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """لیست گروه‌هایی که کاربر فعلاً عضو فعال آن‌هاست"""
    groups = await list_groups_for_user(db, user_id=current_user.id)
    return [
        GroupRoomRead(
            id=group.id,
            type=group.type,
            title=group.title,
            description=group.description,
            created_by_id=group.created_by_id,
            member_count=group.member_count,
            max_members=group.max_members,
            created_at=group.created_at,
            current_user_role=group.current_user_role.value if group.current_user_role is not None else None,
        )
        for group in groups
    ]


@router.post("/groups", response_model=GroupCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: GroupCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ساخت گروه جدید با سازنده به‌عنوان admin و اعضای اولیه"""
    group = await create_group_chat(
        db,
        creator=current_user,
        title=data.title,
        member_ids=data.member_ids,
    )
    member_count = await count_active_chat_members(db, group.id)
    return GroupCreateResponse(
        group=GroupRoomRead(
            id=group.id,
            type=ChatType.GROUP,
            title=group.title or "",
            description=group.description,
            created_by_id=group.created_by_id,
            member_count=member_count,
            max_members=int(group.max_members or 50),
            created_at=group.created_at,
            current_user_role="admin",
        )
    )


@router.get("/groups/{chat_id}", response_model=GroupDetailRead)
async def get_group_detail(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """جزئیات یک گروه برای اعضای فعال آن"""
    group = await get_group_or_404(db, chat_id)
    member = await get_active_group_member_or_403(db, chat=group, user_id=current_user.id)
    member_count = await count_active_chat_members(db, group.id)
    members = await list_group_members(db, chat=group)
    return GroupDetailRead(
        group=GroupRoomRead(
            id=group.id,
            type=group.type,
            title=group.title or "",
            description=group.description,
            created_by_id=group.created_by_id,
            member_count=member_count,
            max_members=int(group.max_members or 50),
            created_at=group.created_at,
            current_user_role=member.role.value,
        ),
        members=[
            GroupMemberRead(
                user_id=item.user_id,
                account_name=item.account_name,
                full_name=item.full_name,
                mobile_number=item.mobile_number,
                role=item.role.value,
                joined_at=item.joined_at,
                is_group_creator=item.is_group_creator,
            )
            for item in members
        ],
    )


@router.patch("/groups/{chat_id}", response_model=GroupRoomRead)
async def patch_group(
    chat_id: int,
    data: GroupUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تغییر نام گروه توسط یکی از adminهای فعال"""
    group = await get_group_or_404(db, chat_id)
    admin_member = await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    group = await update_group_chat(db, chat=group, title=data.title)
    member_count = await count_active_chat_members(db, group.id)
    return GroupRoomRead(
        id=group.id,
        type=group.type,
        title=group.title or "",
        description=group.description,
        created_by_id=group.created_by_id,
        member_count=member_count,
        max_members=int(group.max_members or 50),
        created_at=group.created_at,
        current_user_role=admin_member.role.value,
    )


@router.post("/groups/{chat_id}/members", response_model=GroupMemberMutationResponse)
async def post_group_member(
    chat_id: int,
    data: GroupMemberAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """افزودن یا re-activate کردن یک عضو گروه توسط admin فعال"""
    group = await get_group_or_404(db, chat_id)
    await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    summary = await add_group_member(db, chat=group, user_id=data.user_id)
    return GroupMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        left=summary.left,
        member_count=summary.member_count,
        unchanged=summary.unchanged,
    )


@router.delete("/groups/{chat_id}/members/{user_id}", response_model=GroupMemberMutationResponse)
async def delete_group_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """حذف یک عضو گروه توسط admin فعال"""
    group = await get_group_or_404(db, chat_id)
    await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    summary = await remove_group_member(db, chat=group, acting_user_id=current_user.id, user_id=user_id)
    return GroupMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        left=summary.left,
        member_count=summary.member_count,
        unchanged=summary.unchanged,
    )


@router.post("/groups/{chat_id}/admins/{user_id}", response_model=GroupMemberMutationResponse)
async def promote_group_admin(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ارتقای یک عضو فعال گروه به admin"""
    group = await get_group_or_404(db, chat_id)
    await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    summary = await update_group_admin_status(db, chat=group, user_id=user_id, make_admin=True)
    return GroupMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        left=summary.left,
        member_count=summary.member_count,
        unchanged=summary.unchanged,
    )


@router.delete("/groups/{chat_id}/admins/{user_id}", response_model=GroupMemberMutationResponse)
async def demote_group_admin(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تنزل یک admin گروه به member با last-admin guard"""
    group = await get_group_or_404(db, chat_id)
    await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    summary = await update_group_admin_status(db, chat=group, user_id=user_id, make_admin=False)
    return GroupMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        left=summary.left,
        member_count=summary.member_count,
        unchanged=summary.unchanged,
    )


@router.post("/groups/{chat_id}/leave", response_model=GroupMemberMutationResponse)
async def post_group_leave(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """خروج عضو فعال از گروه با last-admin guard برای adminها"""
    group = await get_group_or_404(db, chat_id)
    summary = await leave_group_chat(db, chat=group, user_id=current_user.id)
    return GroupMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        left=summary.left,
        member_count=summary.member_count,
        unchanged=summary.unchanged,
    )


@router.post("/rooms/{chat_id}/pin", response_model=ConversationPinResponse)
async def pin_room_conversation(
    chat_id: int,
    data: ConversationPinUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await get_room_or_404(db, chat_id)
    if room.type == ChatType.DIRECT:
        raise HTTPException(status_code=400, detail="Use the direct pin endpoint for personal chats")

    member = await set_room_pin_state(
        db,
        chat=room,
        user_id=current_user.id,
        pinned=data.pinned,
    )
    return ConversationPinResponse(
        target_id=-int(room.id),
        chat_id=room.id,
        is_pinned=bool(member.is_pinned),
        pinned_at=member.pinned_at,
        pin_order=getattr(member, "pin_order", None),
    )


@router.post("/rooms/{chat_id}/pin-order", response_model=ConversationPinReorderResponse)
async def reorder_room_conversation_pin(
    chat_id: int,
    data: ConversationPinReorderUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await get_room_or_404(db, chat_id)
    if room.type == ChatType.DIRECT:
        raise HTTPException(status_code=400, detail="Use the direct pin-order endpoint for personal chats")

    member = await set_room_pin_state(
        db,
        chat=room,
        user_id=current_user.id,
        pinned=True,
    )
    reordered_member = await reorder_chat_member_pin_order(
        db,
        user_id=current_user.id,
        chat_id=member.chat_id,
        direction=data.direction,
    )
    return ConversationPinReorderResponse(
        target_id=-int(room.id),
        chat_id=room.id,
        pin_order=reordered_member.pin_order,
    )


@router.get("/rooms/{chat_id}/pinned-message", response_model=PinnedMessageStateResponse)
async def get_room_pinned_message(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await get_room_or_404(db, chat_id)
    if room.type == ChatType.GROUP:
        await get_active_group_member_or_403(db, chat=room, user_id=current_user.id)
    elif room.type == ChatType.CHANNEL:
        await get_active_channel_member_or_403(db, chat=room, user_id=current_user.id)
    else:
        raise HTTPException(status_code=400, detail="Use the direct pinned-message endpoint for personal chats")

    message = await get_pinned_message_for_chat(db, room)
    return _serialize_pinned_message_state(room_kind=room.type.value, chat=room, message=message)


@router.post("/rooms/{chat_id}/mute", response_model=ConversationMuteResponse)
async def mute_room_conversation(
    chat_id: int,
    data: ConversationMuteUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await get_room_or_404(db, chat_id)
    if room.type == ChatType.DIRECT:
        raise HTTPException(status_code=400, detail="Use the direct mute endpoint for personal chats")

    member = await set_room_mute_state(
        db,
        chat=room,
        user_id=current_user.id,
        muted=data.muted,
    )
    return ConversationMuteResponse(
        target_id=-int(room.id),
        chat_id=room.id,
        is_muted=bool(member.is_muted),
    )


@router.post("/rooms/{chat_id}/mark-unread", response_model=ConversationUnreadResponse)
async def mark_room_conversation_unread(
    chat_id: int,
    data: ConversationUnreadUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    room = await get_room_or_404(db, chat_id)
    if room.type == ChatType.DIRECT:
        raise HTTPException(status_code=400, detail="Use the direct unread endpoint for personal chats")

    member = await set_room_mark_unread_state(
        db,
        chat=room,
        user_id=current_user.id,
        unread=data.unread,
    )
    return ConversationUnreadResponse(
        target_id=-int(room.id),
        chat_id=room.id,
        unread_count=1 if data.unread or getattr(member, "is_marked_unread", False) else 0,
    )


@router.post("/channels/{chat_id}/unfollow", response_model=ChannelMemberMutationResponse)
async def unfollow_channel(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await get_channel_or_404(db, chat_id)
    summary = await leave_channel_chat(db, chat=channel, user_id=current_user.id)
    return ChannelMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        member_count=summary.member_count,
        left=summary.left,
        unchanged=summary.unchanged,
    )


@router.get("/channels", response_model=List[ChannelRoomRead])
async def get_channels(
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """لیست کانال‌های قابل مدیریت برای مدیر ارشد"""
    _ = current_user
    channels = await list_manageable_channels(db)
    return [
        ChannelRoomRead(
            id=channel.id,
            type=channel.type,
            title=channel.title,
            description=channel.description,
            created_by_id=channel.created_by_id,
            is_system=channel.is_system,
            is_mandatory=channel.is_mandatory,
            member_count=channel.member_count,
            created_at=channel.created_at,
        )
        for channel in channels
    ]


@router.post("/channels", response_model=ChannelCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    data: ChannelCreateRequest,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """ساخت کانال اختیاری invite-only برای مدیر ارشد"""
    channel = await create_optional_channel(
        db,
        creator=current_user,
        title=data.title,
        description=data.description,
    )
    member_count = await count_active_chat_members(db, channel.id)
    return ChannelCreateResponse(
        channel=ChannelRoomRead(
            id=channel.id,
            type=ChatType.CHANNEL,
            title=channel.title or "",
            description=channel.description,
            created_by_id=channel.created_by_id,
            is_system=channel.is_system,
            is_mandatory=channel.is_mandatory,
            member_count=member_count,
            created_at=channel.created_at,
        ),
        member_picker_required=True,
    )


@router.patch("/channels/{chat_id}", response_model=ChannelRoomRead)
async def update_channel(
    chat_id: int,
    data: ChannelUpdateRequest,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """ویرایش نام و توضیحات کانال برای مدیر ارشد"""
    _ = current_user
    channel = await get_channel_or_404(db, chat_id)
    channel = await update_manageable_channel_metadata(
        db,
        chat=channel,
        title=data.title,
        description=data.description,
    )
    member_count = await count_active_chat_members(db, channel.id)
    return ChannelRoomRead(
        id=channel.id,
        type=channel.type,
        title=channel.title or "",
        description=channel.description,
        created_by_id=channel.created_by_id,
        is_system=channel.is_system,
        is_mandatory=channel.is_mandatory,
        member_count=member_count,
        created_at=channel.created_at,
    )


@router.get("/channels/{chat_id}/members", response_model=List[ChannelMemberRead])
async def get_channel_members(
    chat_id: int,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """لیست اعضای فعلی کانال اختیاری برای مدیریت ادمین"""
    _ = current_user
    channel = await get_channel_or_404(db, chat_id)
    members = await list_channel_members(db, chat=channel)
    return [
        ChannelMemberRead(
            user_id=member.user_id,
            account_name=member.account_name,
            full_name=member.full_name,
            mobile_number=member.mobile_number,
            role=member.role.value,
            joined_at=member.joined_at,
            is_channel_creator=member.is_channel_creator,
        )
        for member in members
    ]


@router.patch("/channels/{chat_id}/members/{user_id}", response_model=ChannelMemberMutationResponse)
async def patch_channel_member(
    chat_id: int,
    user_id: int,
    data: ChannelMemberUpdateRequest,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """تغییر نقش یا حذف عضو از کانال اختیاری"""
    _ = current_user
    channel = await get_channel_or_404(db, chat_id)
    next_role = None
    if data.role is not None:
        next_role = ChatMemberRole.ADMIN if data.role == "admin" else ChatMemberRole.MEMBER
    summary = await update_channel_member(
        db,
        chat=channel,
        user_id=user_id,
        role=next_role,
        remove_member=data.remove_member,
    )
    return ChannelMemberMutationResponse(
        chat_id=summary.chat_id,
        user_id=summary.user_id,
        role=summary.role.value if summary.role is not None else None,
        removed=summary.removed,
        member_count=summary.member_count,
    )


@router.get("/channels/invite-candidates", response_model=ChannelInviteCandidateListResponse)
async def get_channel_invite_candidates(
    q: Optional[str] = Query(None, min_length=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    exclude_chat_id: Optional[int] = Query(None),
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """لیست کاربران فعال پروژه برای member picker کانال"""
    candidate_page = await list_channel_invite_candidates(
        db,
        query_text=q,
        limit=limit,
        offset=offset,
        exclude_chat_id=exclude_chat_id,
    )
    return ChannelInviteCandidateListResponse(
        items=[
            {
                "user_id": item.user_id,
                "account_name": item.account_name,
                "full_name": item.full_name,
                "mobile_number": item.mobile_number,
                "is_already_member": item.is_already_member,
            }
            for item in candidate_page.items
        ],
        total=candidate_page.total,
        active_total=candidate_page.active_total,
    )


@router.post("/channels/{chat_id}/members/bulk", response_model=ChannelBulkMemberAddResponse)
async def bulk_invite_channel_members(
    chat_id: int,
    data: ChannelBulkMemberAddRequest,
    current_user: User = Depends(verify_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """دعوت/افزودن گروهی اعضا به کانال اختیاری"""
    _ = current_user
    channel = await get_channel_or_404(db, chat_id)
    summary = await bulk_add_channel_members(
        db,
        chat=channel,
        user_ids=data.user_ids,
        select_all_active_users=data.select_all_active_users,
    )
    return ChannelBulkMemberAddResponse(
        chat_id=summary.chat_id,
        processed_user_ids=summary.processed_user_ids,
        added_count=summary.added_count,
        reactivated_count=summary.reactivated_count,
        already_member_count=summary.already_member_count,
        member_count=summary.member_count,
        select_all_active_users=summary.select_all_active_users,
    )


@router.get("/rooms/{chat_id}/messages", response_model=List[MessageRead])
async def get_room_messages(
    chat_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    around_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """تاریخچه پیام‌های room/group/channel روی foundation عمومی chat"""
    chat = await get_room_or_404(db, chat_id)
    if chat.type == ChatType.GROUP:
        await get_active_group_member_or_403(db, chat=chat, user_id=current_user.id)
        messages = await list_group_messages(
            db,
            chat=chat,
            limit=limit,
            before_id=before_id,
            around_id=around_id,
        )
    else:
        await get_active_channel_member_or_403(db, chat=chat, user_id=current_user.id)
        messages = await list_channel_messages(
            db,
            chat=chat,
            limit=limit,
            before_id=before_id,
            around_id=around_id,
        )
    return serialize_direct_messages_for_response(
        messages,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.post("/rooms/{chat_id}/send", response_model=MessageRead)
async def send_room_message(
    chat_id: int,
    data: RoomMessageSend,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ارسال post/message به room/group/channel روی foundation عمومی chat"""
    chat = await get_room_or_404(db, chat_id)
    if chat.type == ChatType.GROUP:
        message = await send_group_message(
            db,
            chat=chat,
            sender=current_user,
            content=data.content,
            message_type=data.message_type,
            reply_to_message_id=data.reply_to_message_id,
            forwarded_from_id=data.forwarded_from_id,
        )
        await publish_group_message_event(
            chat=chat,
            message=message,
            member_user_ids=await list_active_room_member_user_ids(db, chat_id=chat.id),
            serializer=MessageRead.from_orm_with_forwarding,
            publisher=publish_user_event,
        )
    else:
        message = await send_channel_message(
            db,
            chat=chat,
            sender=current_user,
            content=data.content,
            message_type=data.message_type,
            reply_to_message_id=data.reply_to_message_id,
            forwarded_from_id=data.forwarded_from_id,
        )
        await publish_channel_message_event(
            chat=chat,
            message=message,
            member_user_ids=await list_active_channel_member_user_ids(db, chat_id=chat.id),
            serializer=MessageRead.from_orm_with_forwarding,
            publisher=publish_user_event,
        )
    return serialize_direct_message_for_response(
        message,
        serializer=MessageRead.from_orm_with_forwarding,
    )


@router.post("/rooms/{chat_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_room_messages_read(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """علامت‌گذاری room/group/channel فعلی به‌عنوان خوانده‌شده"""
    chat = await get_room_or_404(db, chat_id)
    if chat.type == ChatType.GROUP:
        read_summary = await mark_group_messages_read_with_broadcast(
            db,
            chat=chat,
            user_id=current_user.id,
        )
        await publish_group_read_event(
            chat_id=read_summary.chat_id,
            reader_id=read_summary.reader_id,
            member_user_ids=read_summary.member_user_ids,
            publisher=publish_user_event,
        )
    else:
        read_summary = await mark_channel_messages_read_with_broadcast(
            db,
            chat=chat,
            user_id=current_user.id,
        )
        await publish_channel_read_event(
            chat_id=read_summary.chat_id,
            reader_id=read_summary.reader_id,
            member_user_ids=read_summary.member_user_ids,
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
    reaction_chat = await db.get(Chat, updated_message.chat_id) if updated_message.chat_id else None
    if reaction_chat is not None and reaction_chat.type == ChatType.CHANNEL and not reaction_chat.is_deleted:
        await publish_channel_reaction_event(
            chat=reaction_chat,
            message=updated_message,
            member_user_ids=await list_active_channel_member_user_ids(db, chat_id=reaction_chat.id),
            serializer=MessageRead.from_orm_with_forwarding,
            publisher=publish_user_event,
        )
    elif reaction_chat is not None and reaction_chat.type == ChatType.GROUP and not reaction_chat.is_deleted:
        await publish_group_reaction_event(
            chat=reaction_chat,
            message=updated_message,
            member_user_ids=await list_active_room_member_user_ids(db, chat_id=reaction_chat.id),
            serializer=MessageRead.from_orm_with_forwarding,
            publisher=publish_user_event,
        )
    else:
        await publish_direct_reaction_event(
            message=updated_message,
            serializer=MessageRead.from_orm_with_forwarding,
            publisher=publish_user_event,
        )

    return reaction_payload


@router.post("/messages/{message_id}/pin", response_model=PinnedMessageStateResponse)
async def toggle_message_pin(
    message_id: int,
    data: MessagePinUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chat, pinned_message = await apply_message_pin_state(
        db,
        message_id=message_id,
        actor_id=current_user.id,
        pinned=data.pinned,
    )
    return _serialize_pinned_message_state(
        room_kind=chat.type.value,
        chat=chat,
        message=pinned_message,
    )


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
    conv_stmt = build_direct_conversation_list_stmt(current_user.id)
    result = await db.execute(conv_stmt)
    direct_convs = result.mappings().all()
    group_convs = await list_group_conversations(db, current_user_id=current_user.id)
    channel_convs = await list_channel_conversations(db, current_user_id=current_user.id)
    all_convs = [*direct_convs, *group_convs, *channel_convs]

    def read_field(row, key, default=None):
        if hasattr(row, key):
            return getattr(row, key)
        if isinstance(row, dict):
            return row.get(key, default)
        return default

    unread_convs = [
        row for row in all_convs
        if int(read_field(row, "unread_count", 0) or 0) > 0
    ]
    muted_conversation_ids = sorted(
        {
            int(read_field(row, "other_user_id", 0) or 0)
            for row in all_convs
            if bool(read_field(row, "is_muted", False))
        }
    )

    unread_chats_count = len(unread_convs)
    total_unread = sum(int(read_field(row, "unread_count", 0) or 0) for row in unread_convs)
    conversations_with_unread = [
        {
            "user_id": int(read_field(row, "other_user_id", 0) or 0),
            "user_name": read_field(row, "other_user_name", "") or "",
            "unread_count": int(read_field(row, "unread_count", 0) or 0),
            "is_deleted": bool(read_field(row, "other_user_is_deleted", False)),
        }
        for row in unread_convs
    ]
    
    return PollResponse(
        total_unread=total_unread,
        unread_chats_count=unread_chats_count,
        conversations_with_unread=conversations_with_unread,
        muted_conversation_ids=muted_conversation_ids,
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


