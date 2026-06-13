# trading_bot/api/routers/chat.py
"""
API endpoints for in-app messaging system
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import inspect
import os
import uuid
import asyncio
import aiofiles
import magic
from jose import jwt, JWTError

from core.db import AsyncSessionLocal, get_db
from core.config import settings
from models.chat import Chat
from models.message import Message
from models.upload_session import UploadSession, UploadSessionStatus, UploadRoomKind
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
    DirectChatActivitySignal,
    GroupMessageSeenRead,
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
    RoomChatActivitySignal,
    RoomMessageSend,
    StickerPack,
    TypingSignal,
    UploadBatchCancelResponse,
    UploadBatchCommitResponse,
    UploadBatchCreateRequest,
    UploadBatchCreateResponse,
    UploadSessionChunkAppendResponse,
    UploadSessionCreateRequest,
    UploadSessionCreateResponse,
    UploadSessionStateRead,
    UploadSessionStatusChangeResponse,
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
    list_group_message_seen_members,
    list_group_members,
    list_group_messages,
    list_groups_for_user,
    list_room_conversations,
    list_active_room_member_user_ids,
    list_channel_conversations,
    list_active_channel_member_user_ids,
    list_channel_invite_candidates,
    list_group_member_candidates,
    list_channel_members,
    list_channel_messages,
    list_manageable_channels,
    list_room_poll_summaries,
    mark_channel_messages_read,
    mark_channel_messages_read_with_broadcast,
    mark_group_messages_read_with_broadcast,
    publish_group_message_event,
    publish_room_activity_event,
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
from core.services.chat_role_badge_service import load_chat_role_badges
from core.services.avatar_service import resolve_owned_avatar_file_id
from core.services.accountant_relation_service import is_user_accountant
from core.services.customer_relation_service import (
    build_allowed_customer_chat_targets,
    get_active_customer_relation_for_customer,
    is_user_customer,
)
from core.services.accountant_chat_contract import (
    apply_accountant_identity_to_direct_conversation_row,
    apply_accountant_identity_to_message_payload,
    collect_message_identity_user_ids,
    load_accountant_chat_identity_map,
    resolve_direct_sender_display_name,
    resolve_relation_aware_sender_display_name,
)
from core.services.single_session_recovery_service import (
    build_recovery_action_map_for_admin_messages,
)
from core.services.chat_service import (
    apply_direct_message_delete,
    apply_direct_message_edit,
    apply_message_pin_state,
    apply_direct_message_reaction_toggle,
    build_direct_conversation_list_stmt,
    build_direct_message_history_statements,
    build_direct_message_search_stmt,
    build_direct_poll_summary_stmt,
    commit_direct_read_state,
    get_existing_direct_chat,
    get_pinned_message_for_chat,
    hide_direct_conversation,
    persist_sent_direct_message,
    publish_direct_message_event,
    publish_direct_activity_event,
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
from core.services.chat_upload_session_service import (
    append_upload_chunk,
    cancel_upload_batch,
    cancel_upload_session,
    commit_upload_batch,
    create_upload_batch,
    create_upload_session,
    finalize_upload_session,
    get_upload_batch_for_current_user,
    get_upload_session_for_current_user,
    persist_chat_media_file_bytes,
    read_upload_chunk_bytes,
)
from core.redis import get_redis_client
from core.utils import publish_user_event
import logging

logger = logging.getLogger(__name__)

CHAT_MEDIA_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHAT_MEDIA_MAX_UPLOAD_LABEL = "50MB"

router = APIRouter(
    tags=["Chat"]
)

ACTIVE_UPLOAD_SESSION_STATUSES = {
    UploadSessionStatus.CREATED,
    UploadSessionStatus.UPLOADING,
    UploadSessionStatus.UPLOADED,
    UploadSessionStatus.FINALIZING,
}

UPLOAD_SESSION_OBSERVABILITY_COUNTERS_KEY = "chat:upload_session:counters"


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _build_upload_session_event_payload(
    *,
    event_name: str,
    session: UploadSession | object,
    batch_status: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event": event_name,
        "session_id": getattr(session, "id"),
        "batch_id": getattr(session, "batch_id", None),
        "room_kind": _enum_value(getattr(session, "room_kind", None)),
        "target_id": getattr(session, "target_id", None),
        "media_type": _enum_value(getattr(session, "media_type", None)),
        "status": _enum_value(getattr(session, "status", None)),
        "received_bytes": getattr(session, "received_bytes", 0),
        "total_bytes": getattr(session, "total_bytes", 0),
        "next_offset": getattr(session, "next_offset", 0),
        "final_chat_file_id": getattr(session, "final_chat_file_id", None),
        "retry_count": getattr(session, "retry_count", 0),
    }
    last_error = getattr(session, "last_error", None)
    if last_error:
        payload["last_error"] = last_error
    preview_metadata = getattr(session, "preview_metadata", None)
    if preview_metadata:
        payload["preview_metadata"] = preview_metadata
    if batch_status is not None:
        payload["batch_status"] = _enum_value(batch_status)
    return payload


async def _increment_upload_session_observability_counters(
    *,
    event_name: str,
    room_kind: object,
    media_type: object,
) -> None:
    try:
        redis = get_redis_client()
        normalized_room_kind = _enum_value(room_kind) or "unknown"
        normalized_media_type = _enum_value(media_type) or "unknown"
        await redis.hincrby(UPLOAD_SESSION_OBSERVABILITY_COUNTERS_KEY, f"event:{event_name}", 1)
        await redis.hincrby(
            UPLOAD_SESSION_OBSERVABILITY_COUNTERS_KEY,
            f"room:{normalized_room_kind}:event:{event_name}",
            1,
        )
        await redis.hincrby(
            UPLOAD_SESSION_OBSERVABILITY_COUNTERS_KEY,
            f"media:{normalized_media_type}:event:{event_name}",
            1,
        )
    except Exception:
        logger.debug("upload session observability counter update skipped", exc_info=True)


async def _has_active_upload_sessions_for_room(
    db: AsyncSession,
    *,
    owner_user_id: int,
    room_kind: UploadRoomKind | str,
    target_id: int,
) -> bool:
    normalized_room_kind = room_kind
    if isinstance(room_kind, str):
        normalized_room_kind = UploadRoomKind(room_kind)

    result = await db.execute(
        select(UploadSession.id)
        .where(
            UploadSession.owner_user_id == owner_user_id,
            UploadSession.room_kind == normalized_room_kind,
            UploadSession.target_id == target_id,
            UploadSession.status.in_(tuple(ACTIVE_UPLOAD_SESSION_STATUSES)),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _publish_upload_session_runtime_event(
    *,
    db: AsyncSession,
    current_user: User,
    session: UploadSession | object,
    event_name: str,
    batch_status: object | None = None,
) -> None:
    room_kind = _enum_value(getattr(session, "room_kind", None))
    target_id = int(getattr(session, "target_id"))
    payload = _build_upload_session_event_payload(
        event_name=event_name,
        session=session,
        batch_status=batch_status,
    )

    await publish_user_event(current_user.id, "chat:upload_session", payload)
    await _increment_upload_session_observability_counters(
        event_name=event_name,
        room_kind=room_kind,
        media_type=getattr(session, "media_type", None),
    )

    logger.info(
        "chat_upload_session_event",
        extra={
            "event": event_name,
            "session_id": getattr(session, "id", None),
            "batch_id": getattr(session, "batch_id", None),
            "owner_user_id": getattr(session, "owner_user_id", current_user.id),
            "actor_user_id": getattr(session, "actor_user_id", current_user.id),
            "room_kind": room_kind,
            "target_id": target_id,
            "media_type": _enum_value(getattr(session, "media_type", None)),
            "status": _enum_value(getattr(session, "status", None)),
            "received_bytes": getattr(session, "received_bytes", 0),
            "total_bytes": getattr(session, "total_bytes", 0),
            "retry_count": getattr(session, "retry_count", 0),
            "last_error": getattr(session, "last_error", None),
        },
    )

    has_active_uploads = await _has_active_upload_sessions_for_room(
        db,
        owner_user_id=current_user.id,
        room_kind=room_kind,
        target_id=target_id,
    )

    if room_kind == UploadRoomKind.DIRECT.value:
        await publish_direct_activity_event(
            receiver_id=target_id,
            sender_id=current_user.id,
            sender_name=await resolve_direct_sender_display_name(db, user=current_user),
            activity="uploading_file",
            active=has_active_uploads,
            publisher=publish_user_event,
        )
        return

    chat = await db.get(Chat, target_id)
    if chat is None or chat.is_deleted:
        return

    await publish_room_activity_event(
        chat=chat,
        sender_id=current_user.id,
        sender_name=await resolve_relation_aware_sender_display_name(db, user=current_user),
        member_user_ids=await list_active_room_member_user_ids(db, chat_id=chat.id),
        activity="uploading_file",
        active=has_active_uploads,
        publisher=publish_user_event,
    )


async def _list_batch_upload_sessions(db: AsyncSession, *, batch_id: str) -> list[UploadSession]:
    result = await db.execute(
        select(UploadSession)
        .where(UploadSession.batch_id == batch_id)
        .order_by(UploadSession.id.asc())
    )
    return list(result.scalars().all())


async def _publish_upload_batch_commit_message_events(
    *,
    target: object,
    messages: list[Message],
    serialized_messages: list[MessageRead],
    sender_name: str | None,
    group_member_user_ids: list[int] | None = None,
) -> None:
    try:
        serialized_by_id = {message.id: message for message in serialized_messages}

        def _serializer(message: Message) -> MessageRead:
            serialized = serialized_by_id.get(message.id)
            if serialized is None:
                return MessageRead.from_orm_with_forwarding(message)
            return serialized

        receiver = getattr(target, "receiver", None)
        if receiver is not None:
            receiver_id = getattr(receiver, "id", None)
            if receiver_id is None:
                return
            for message in messages:
                await publish_direct_message_event(
                    receiver_id=int(receiver_id),
                    message=message,
                    serializer=_serializer,
                    publisher=publish_user_event,
                    sender_name=sender_name,
                )
            return

        chat = getattr(target, "chat", None)
        if chat is None or group_member_user_ids is None:
            return

        for message in messages:
            await publish_group_message_event(
                chat=chat,
                message=message,
                member_user_ids=group_member_user_ids,
                serializer=_serializer,
                publisher=publish_user_event,
            )
    except Exception:
        logger.warning("chat upload batch commit message fanout skipped", exc_info=True)


async def _publish_upload_batch_commit_runtime_events(
    *,
    batch_id: str,
    current_user_id: int,
    batch_status: object,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            current_user = await db.get(User, current_user_id)
            if current_user is None:
                return
            for session in await _list_batch_upload_sessions(db, batch_id=batch_id):
                await _publish_upload_session_runtime_event(
                    db=db,
                    current_user=current_user,
                    session=session,
                    event_name="committed",
                    batch_status=batch_status,
                )
    except Exception:
        logger.warning("chat upload batch commit runtime fanout skipped", exc_info=True)


async def _serialize_pinned_message_state(
    *,
    db: AsyncSession,
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
            await _serialize_direct_message_with_accountant_contract(db, message)
            if message is not None
            else None
        ),
    )


async def _get_customer_visible_direct_target_ids(
    db: AsyncSession,
    *,
    current_user: User,
) -> set[int] | None:
    if not hasattr(db, "execute"):
        return None
    relation = await get_active_customer_relation_for_customer(db, current_user.id)
    if relation is None:
        return None
    return set(await build_allowed_customer_chat_targets(db, current_user.id))


async def _ensure_customer_can_access_direct_target(
    db: AsyncSession,
    *,
    current_user: User,
    target_user_id: int,
    allowed_target_ids: set[int] | None = None,
) -> set[int] | None:
    if allowed_target_ids is None:
        allowed_target_ids = await _get_customer_visible_direct_target_ids(db, current_user=current_user)
    if allowed_target_ids is None:
        return None
    if target_user_id not in allowed_target_ids:
        raise HTTPException(status_code=404, detail="User not found")
    return allowed_target_ids


def _get_direct_message_counterparty_id(message: Message | object, *, viewer_user_id: int) -> int | None:
    sender_id = getattr(message, "sender_id", None)
    receiver_id = getattr(message, "receiver_id", None)
    if sender_id == viewer_user_id:
        return receiver_id
    if receiver_id == viewer_user_id:
        return sender_id
    return None


# ===== Endpoints =====

@router.get("/conversations", response_model=List[ConversationRead])
async def get_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """لیست مکالمات کاربر"""
    allowed_direct_target_ids = await _get_customer_visible_direct_target_ids(db, current_user=current_user)
    if allowed_direct_target_ids is None:
        stmt = build_direct_conversation_list_stmt(current_user.id)
    else:
        stmt = build_direct_conversation_list_stmt(
            current_user.id,
            allowed_target_ids=allowed_direct_target_ids,
        )
    result = await db.execute(stmt)
    direct_conversations = [ConversationRead(**row) for row in result.mappings().all()]
    if allowed_direct_target_ids is not None:
        direct_conversations = [
            row for row in direct_conversations if row.other_user_id in allowed_direct_target_ids
        ]
    room_conversations = [
        ConversationRead(
            id=row.id,
            other_user_id=row.other_user_id,
            other_user_name=row.other_user_name,
            avatar_file_id=getattr(row, "avatar_file_id", None),
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
            unread_mention_count=getattr(row, "unread_mention_count", 0),
        )
        for row in await list_room_conversations(db, current_user_id=current_user.id)
    ]
    conversations = [*direct_conversations, *room_conversations]
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
    return await _serialize_pinned_message_state(db=db, room_kind="direct", chat=chat, message=message)


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
    allowed_direct_target_ids = await _get_customer_visible_direct_target_ids(db, current_user=current_user)
    if chat_id is not None:
        await _ensure_customer_can_access_direct_target(
            db,
            current_user=current_user,
            target_user_id=chat_id,
            allowed_target_ids=allowed_direct_target_ids,
        )

    query = await build_direct_message_search_stmt(
        db,
        current_user_id=current_user.id,
        query_text=q,
        other_user_id=chat_id,
        limit=limit,
    )
    result = await db.execute(query)
    messages = result.scalars().all()
    if allowed_direct_target_ids is not None and chat_id is None:
        messages = [
            message
            for message in messages
            if _get_direct_message_counterparty_id(message, viewer_user_id=current_user.id) in allowed_direct_target_ids
        ]
    return await _serialize_direct_messages_with_accountant_contract(
        db,
        messages,
        viewer_user_id=current_user.id,
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

    await _ensure_customer_can_access_direct_target(
        db,
        current_user=current_user,
        target_user_id=user_id,
    )

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
        return await _serialize_direct_messages_with_accountant_contract(
            db,
            messages,
            viewer_user_id=current_user.id,
        )

    result = await db.execute(stmt_older)
    messages = result.scalars().all()
    
    # معکوس کردن برای نمایش صعودی
    messages = list(reversed(messages))
    return await _serialize_direct_messages_with_accountant_contract(
        db,
        messages,
        viewer_user_id=current_user.id,
    )


@router.post("/typing", status_code=status.HTTP_204_NO_CONTENT)
async def send_typing_signal(
    data: TypingSignal,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ارسال سیگنال تایپ کردن به گیرنده"""
    await _ensure_customer_can_access_direct_target(
        db,
        current_user=current_user,
        target_user_id=data.receiver_id,
    )
    await publish_direct_typing_event(
        receiver_id=data.receiver_id,
        sender_id=current_user.id,
        sender_name=await resolve_direct_sender_display_name(db, user=current_user),
        publisher=publish_user_event,
    )
    return None


@router.post("/activity", status_code=status.HTTP_204_NO_CONTENT)
async def send_direct_activity_signal(
    data: DirectChatActivitySignal,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ارسال سیگنال activity برای گفتگو مستقیم."""
    await _ensure_customer_can_access_direct_target(
        db,
        current_user=current_user,
        target_user_id=data.receiver_id,
    )
    await publish_direct_activity_event(
        receiver_id=data.receiver_id,
        sender_id=current_user.id,
        sender_name=await resolve_direct_sender_display_name(db, user=current_user),
        activity=data.activity,
        active=data.active,
        publisher=publish_user_event,
    )
    return None


@router.post("/rooms/{chat_id}/activity", status_code=status.HTTP_204_NO_CONTENT)
async def send_room_activity_signal(
    chat_id: int,
    data: RoomChatActivitySignal,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ارسال سیگنال activity برای roomهای گروه/کانال."""
    chat = await get_room_or_404(db, chat_id)

    if chat.type == ChatType.GROUP:
        await get_active_group_member_or_403(db, chat=chat, user_id=current_user.id)
    else:
        await get_active_channel_member_or_403(db, chat=chat, user_id=current_user.id)

    await publish_room_activity_event(
        chat=chat,
        sender_id=current_user.id,
        sender_name=await resolve_relation_aware_sender_display_name(db, user=current_user),
        member_user_ids=await list_active_room_member_user_ids(db, chat_id=chat.id),
        activity=data.activity,
        active=data.active,
        publisher=publish_user_event,
    )
    return None


def _optional_attr(obj: object, name: str):
    return getattr(obj, name, None)


async def _enrich_direct_message_reads(
    db: AsyncSession,
    messages: list[MessageRead],
    *,
    viewer_user_id: int | None = None,
) -> list[MessageRead]:
    if not messages:
        return messages

    # Fetch mention details
    all_mentioned_ids = set()
    for msg in messages:
        if getattr(msg, "mentions", None):
            all_mentioned_ids.update(msg.mentions)

    mention_details_map = {}
    if all_mentioned_ids:
        from models.user import User
        stmt = select(User.id, User.account_name).where(User.id.in_(list(all_mentioned_ids)))
        result = await db.execute(stmt)
        for row in result.all():
            mention_details_map[row.id] = row.account_name

    identity_map = await load_accountant_chat_identity_map(
        db,
        collect_message_identity_user_ids(messages),
    )
    normalized_messages = []
    for message in messages:
        payload = apply_accountant_identity_to_message_payload(message.model_dump(), identity_map)
        mentions = payload.get("mentions") or []
        payload["mention_details"] = [
            {"user_id": uid, "account_name": mention_details_map.get(uid, f"user_{uid}")}
            for uid in mentions
        ]
        normalized_messages.append(payload)

    recovery_action_map: dict[int, dict[str, object]] = {}
    if viewer_user_id is not None and hasattr(db, "execute"):
        recovery_action_map = await build_recovery_action_map_for_admin_messages(
            db,
            admin_user_id=viewer_user_id,
            message_ids=[message.id for message in messages],
        )

    return [
        MessageRead(
            **{
                **payload,
                "recovery_action": recovery_action_map.get(int(payload["id"])),
            }
        )
        for payload in normalized_messages
    ]


async def _serialize_direct_message_with_accountant_contract(
    db: AsyncSession,
    message: Message,
    *,
    viewer_user_id: int | None = None,
) -> MessageRead:
    return (
        await _enrich_direct_message_reads(
            db,
            [
                serialize_direct_message_for_response(
                    message,
                    serializer=MessageRead.from_orm_with_forwarding,
                )
            ],
            viewer_user_id=viewer_user_id,
        )
    )[0]


async def _serialize_direct_messages_with_accountant_contract(
    db: AsyncSession,
    messages: list[Message],
    *,
    viewer_user_id: int | None = None,
) -> list[MessageRead]:
    serialized_messages = serialize_direct_messages_for_response(
        messages,
        serializer=MessageRead.from_orm_with_forwarding,
    )
    return await _enrich_direct_message_reads(
        db,
        serialized_messages,
        viewer_user_id=viewer_user_id,
    )


def _build_direct_message_accountant_serializer(identity_map):
    def serializer(message: Message) -> MessageRead:
        base_payload = MessageRead.from_orm_with_forwarding(message)
        return MessageRead(
            **apply_accountant_identity_to_message_payload(base_payload.model_dump(), identity_map)
        )

    return serializer


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
            avatar_file_id=_optional_attr(group, "avatar_file_id"),
            created_by_id=group.created_by_id,
            member_count=group.member_count,
            max_members=group.max_members,
            created_at=group.created_at,
            current_user_role=group.current_user_role.value if group.current_user_role is not None else None,
        )
        for group in groups
    ]


@router.get("/groups/member-candidates", response_model=ChannelInviteCandidateListResponse)
async def get_group_member_candidates(
    query_text: Optional[str] = Query(default=None, alias="q"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    exclude_chat_id: Optional[int] = Query(default=None),
    selected_user_ids: List[int] = Query(default=[]),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List eligible group member candidates under customer membership rules."""
    if await is_user_customer(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="مشتری در این فاز اجازه ساخت یا مدیریت اعضای گروه را ندارد",
        )
    if exclude_chat_id is not None:
        group = await get_group_or_404(db, exclude_chat_id)
        await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)

    page = await list_group_member_candidates(
        db,
        current_user=current_user,
        query_text=query_text,
        limit=limit,
        offset=offset,
        exclude_chat_id=exclude_chat_id,
        selected_user_ids=selected_user_ids,
    )
    role_badges = await load_chat_role_badges(db, {item.user_id for item in page.items})
    return ChannelInviteCandidateListResponse(
        items=[
            {
                "user_id": item.user_id,
                "account_name": item.account_name,
                "full_name": item.full_name,
                "mobile_number": item.mobile_number,
                "avatar_file_id": _optional_attr(item, "avatar_file_id"),
                "is_already_member": item.is_already_member,
                "chat_role_kind": role_badges[item.user_id].kind if item.user_id in role_badges else None,
                "chat_role_label": role_badges[item.user_id].label if item.user_id in role_badges else None,
                "chat_accountant_owner_name": role_badges[item.user_id].accountant_owner_name if item.user_id in role_badges else None,
                "chat_accountant_owner_label": role_badges[item.user_id].accountant_owner_label if item.user_id in role_badges else None,
            }
            for item in page.items
        ],
        total=page.total,
        active_total=page.active_total,
    )


@router.post("/groups", response_model=GroupCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: GroupCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ساخت گروه جدید با سازنده به‌عنوان admin و اعضای اولیه"""
    if await is_user_customer(db, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="مشتری در این فاز اجازه ساخت گروه جدید را ندارد",
        )

    create_kwargs = {
        "creator": current_user,
        "title": data.title,
        "member_ids": data.member_ids,
    }
    if hasattr(data, "description"):
        create_kwargs["description"] = data.description
    if hasattr(data, "avatar_file_id"):
        create_kwargs["avatar_file_id"] = await resolve_owned_avatar_file_id(
            db,
            actor_id=current_user.id,
            avatar_file_id=data.avatar_file_id,
        )
    group = await create_group_chat(db, **create_kwargs)
    member_count = await count_active_chat_members(db, group.id)
    return GroupCreateResponse(
        group=GroupRoomRead(
            id=group.id,
            type=ChatType.GROUP,
            title=group.title or "",
            description=group.description,
            avatar_file_id=_optional_attr(group, "avatar_file_id"),
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
    role_badges = await load_chat_role_badges(db, {item.user_id for item in members})
    return GroupDetailRead(
        group=GroupRoomRead(
            id=group.id,
            type=group.type,
            title=group.title or "",
            description=group.description,
            avatar_file_id=_optional_attr(group, "avatar_file_id"),
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
                avatar_file_id=_optional_attr(item, "avatar_file_id"),
                role=item.role.value,
                joined_at=item.joined_at,
                is_group_creator=item.is_group_creator,
                chat_role_kind=role_badges[item.user_id].kind if item.user_id in role_badges else None,
                chat_role_label=role_badges[item.user_id].label if item.user_id in role_badges else None,
                chat_accountant_owner_name=role_badges[item.user_id].accountant_owner_name if item.user_id in role_badges else None,
                chat_accountant_owner_label=role_badges[item.user_id].accountant_owner_label if item.user_id in role_badges else None,
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
    """تغییر نام و توضیحات گروه توسط یکی از adminهای فعال"""
    group = await get_group_or_404(db, chat_id)
    admin_member = await get_active_group_admin_or_403(db, chat=group, user_id=current_user.id)
    update_kwargs = {
        "chat": group,
        "title": data.title,
    }
    if hasattr(data, "description"):
        update_kwargs["description"] = data.description
    if hasattr(data, "avatar_file_id"):
        update_kwargs["avatar_file_id"] = await resolve_owned_avatar_file_id(
            db,
            actor_id=current_user.id,
            avatar_file_id=data.avatar_file_id,
        )
    group = await update_group_chat(db, **update_kwargs)
    member_count = await count_active_chat_members(db, group.id)
    return GroupRoomRead(
        id=group.id,
        type=group.type,
        title=group.title or "",
        description=group.description,
        avatar_file_id=_optional_attr(group, "avatar_file_id"),
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
    return await _serialize_pinned_message_state(db=db, room_kind=room.type.value, chat=room, message=message)


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
            avatar_file_id=_optional_attr(channel, "avatar_file_id"),
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
    create_kwargs = {
        "creator": current_user,
        "title": data.title,
        "description": data.description,
    }
    if hasattr(data, "avatar_file_id"):
        create_kwargs["avatar_file_id"] = await resolve_owned_avatar_file_id(
            db,
            actor_id=current_user.id,
            avatar_file_id=data.avatar_file_id,
        )
    channel = await create_optional_channel(db, **create_kwargs)
    member_count = await count_active_chat_members(db, channel.id)
    return ChannelCreateResponse(
        channel=ChannelRoomRead(
            id=channel.id,
            type=ChatType.CHANNEL,
            title=channel.title or "",
            description=channel.description,
            avatar_file_id=_optional_attr(channel, "avatar_file_id"),
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
    update_kwargs = {
        "chat": channel,
        "title": data.title,
        "description": data.description,
    }
    if hasattr(data, "avatar_file_id"):
        update_kwargs["avatar_file_id"] = await resolve_owned_avatar_file_id(
            db,
            actor_id=current_user.id,
            avatar_file_id=data.avatar_file_id,
        )
    channel = await update_manageable_channel_metadata(db, **update_kwargs)
    member_count = await count_active_chat_members(db, channel.id)
    return ChannelRoomRead(
        id=channel.id,
        type=channel.type,
        title=channel.title or "",
        description=channel.description,
        avatar_file_id=_optional_attr(channel, "avatar_file_id"),
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
    role_badges = await load_chat_role_badges(db, {member.user_id for member in members})
    return [
        ChannelMemberRead(
            user_id=member.user_id,
            account_name=member.account_name,
            full_name=member.full_name,
            mobile_number=member.mobile_number,
            avatar_file_id=_optional_attr(member, "avatar_file_id"),
            role=member.role.value,
            joined_at=member.joined_at,
            is_channel_creator=member.is_channel_creator,
            chat_role_kind=role_badges[member.user_id].kind if member.user_id in role_badges else None,
            chat_role_label=role_badges[member.user_id].label if member.user_id in role_badges else None,
            chat_accountant_owner_name=role_badges[member.user_id].accountant_owner_name if member.user_id in role_badges else None,
            chat_accountant_owner_label=role_badges[member.user_id].accountant_owner_label if member.user_id in role_badges else None,
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
    role_badges = await load_chat_role_badges(db, {item.user_id for item in candidate_page.items})
    return ChannelInviteCandidateListResponse(
        items=[
            {
                "user_id": item.user_id,
                "account_name": item.account_name,
                "full_name": item.full_name,
                "mobile_number": item.mobile_number,
                "avatar_file_id": _optional_attr(item, "avatar_file_id"),
                "is_already_member": item.is_already_member,
                "chat_role_kind": role_badges[item.user_id].kind if item.user_id in role_badges else None,
                "chat_role_label": role_badges[item.user_id].label if item.user_id in role_badges else None,
                "chat_accountant_owner_name": role_badges[item.user_id].accountant_owner_name if item.user_id in role_badges else None,
                "chat_accountant_owner_label": role_badges[item.user_id].accountant_owner_label if item.user_id in role_badges else None,
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
    return await _serialize_direct_messages_with_accountant_contract(
        db,
        messages,
        viewer_user_id=current_user.id,
    )


@router.get("/rooms/{chat_id}/messages/{message_id}/seen", response_model=List[GroupMessageSeenRead])
async def get_group_message_seen_members(
    chat_id: int,
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    chat = await get_room_or_404(db, chat_id)
    if chat.type != ChatType.GROUP:
        raise HTTPException(status_code=404, detail="Message seen list is available for groups only")
    items = await list_group_message_seen_members(
        db,
        chat=chat,
        message_id=message_id,
        requester_id=current_user.id,
    )
    return [GroupMessageSeenRead.model_validate(item) for item in items]


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
        room_mentions = getattr(data, "mentions", [])
        room_mention_all = getattr(data, "mention_all", False)
        message = await send_group_message(
            db,
            chat=chat,
            sender=current_user,
            content=data.content,
            message_type=data.message_type,
            reply_to_message_id=data.reply_to_message_id,
            forwarded_from_id=data.forwarded_from_id,
            forwarded_from_name_override=data.forwarded_from_name_override,
            mentions=room_mentions,
            mention_all=room_mention_all,
        )
        
        # Resolve mentions for real-time broadcast and payload
        all_mentioned_ids = getattr(message, "mentions", None) or []
        mention_details = []
        if all_mentioned_ids:
            stmt = select(User.id, User.account_name).where(User.id.in_(all_mentioned_ids))
            result = await db.execute(stmt)
            mention_details = [
                {"user_id": row.id, "account_name": row.account_name}
                for row in result.all()
            ]
        message.mention_details = mention_details

        response_message = await _serialize_direct_message_with_accountant_contract(
            db,
            message,
            viewer_user_id=current_user.id,
        )
        identity_map = await load_accountant_chat_identity_map(
            db,
            collect_message_identity_user_ids([response_message]),
        )
        await publish_group_message_event(
            chat=chat,
            message=message,
            member_user_ids=await list_active_room_member_user_ids(db, chat_id=chat.id),
            serializer=_build_direct_message_accountant_serializer(identity_map),
            publisher=publish_user_event,
        )
    else:
        room_mentions = getattr(data, "mentions", [])
        room_mention_all = getattr(data, "mention_all", False)
        message = await send_channel_message(
            db,
            chat=chat,
            sender=current_user,
            content=data.content,
            message_type=data.message_type,
            reply_to_message_id=data.reply_to_message_id,
            forwarded_from_id=data.forwarded_from_id,
            forwarded_from_name_override=data.forwarded_from_name_override,
            mentions=room_mentions,
            mention_all=room_mention_all,
        )
        
        # Resolve mentions for real-time broadcast and payload
        all_mentioned_ids = getattr(message, "mentions", None) or []
        mention_details = []
        if all_mentioned_ids:
            stmt = select(User.id, User.account_name).where(User.id.in_(all_mentioned_ids))
            result = await db.execute(stmt)
            mention_details = [
                {"user_id": row.id, "account_name": row.account_name}
                for row in result.all()
            ]
        message.mention_details = mention_details

        response_message = await _serialize_direct_message_with_accountant_contract(
            db,
            message,
            viewer_user_id=current_user.id,
        )
        identity_map = await load_accountant_chat_identity_map(
            db,
            collect_message_identity_user_ids([response_message]),
        )
        await publish_channel_message_event(
            chat=chat,
            message=message,
            member_user_ids=await list_active_channel_member_user_ids(db, chat_id=chat.id),
            serializer=_build_direct_message_accountant_serializer(identity_map),
            publisher=publish_user_event,
        )
    return response_message


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
        forwarded_from_name_override=data.forwarded_from_name_override,
    )
    if message is None:
        raise HTTPException(status_code=500, detail="Failed to persist message")
    response_message = await _serialize_direct_message_with_accountant_contract(
        db,
        message,
        viewer_user_id=current_user.id,
    )
    identity_map = await load_accountant_chat_identity_map(
        db,
        collect_message_identity_user_ids([response_message]),
    )
    sender_name = await resolve_direct_sender_display_name(db, user=current_user)
    
    # انتشار پیام برای گیرنده (Real-time update)
    # استفاده از MessageRead برای سریالایز کردن مناسب
    await publish_direct_message_event(
        receiver_id=data.receiver_id,
        message=message,
        serializer=_build_direct_message_accountant_serializer(identity_map),
        publisher=publish_user_event,
        sender_name=sender_name,
    )

    return response_message


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
    return await _serialize_direct_message_with_accountant_contract(
        db,
        msg,
        viewer_user_id=current_user.id,
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
    identity_map = await load_accountant_chat_identity_map(
        db,
        collect_message_identity_user_ids([reaction_payload]),
    )
    reaction_payload = MessageRead(
        **apply_accountant_identity_to_message_payload(reaction_payload.model_dump(), identity_map)
    )
    reaction_payload = (
        await _enrich_direct_message_reads(
            db,
            [reaction_payload],
            viewer_user_id=current_user.id,
        )
    )[0]
    reaction_chat = await db.get(Chat, updated_message.chat_id) if updated_message.chat_id else None
    if reaction_chat is not None and reaction_chat.type == ChatType.CHANNEL and not reaction_chat.is_deleted:
        await publish_channel_reaction_event(
            chat=reaction_chat,
            message=updated_message,
            member_user_ids=await list_active_channel_member_user_ids(db, chat_id=reaction_chat.id),
            serializer=_build_direct_message_accountant_serializer(identity_map),
            publisher=publish_user_event,
        )
    elif reaction_chat is not None and reaction_chat.type == ChatType.GROUP and not reaction_chat.is_deleted:
        await publish_group_reaction_event(
            chat=reaction_chat,
            message=updated_message,
            member_user_ids=await list_active_room_member_user_ids(db, chat_id=reaction_chat.id),
            serializer=_build_direct_message_accountant_serializer(identity_map),
            publisher=publish_user_event,
        )
    else:
        await publish_direct_reaction_event(
            message=updated_message,
            serializer=_build_direct_message_accountant_serializer(identity_map),
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
    return await _serialize_pinned_message_state(
        db=db,
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
    await _ensure_customer_can_access_direct_target(
        db,
        current_user=current_user,
        target_user_id=user_id,
    )
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
    allowed_direct_target_ids = await _get_customer_visible_direct_target_ids(db, current_user=current_user)
    direct_poll_result = await db.execute(build_direct_poll_summary_stmt(current_user.id))
    direct_poll_rows = direct_poll_result.mappings().all()
    room_poll_summaries = await list_room_poll_summaries(db, current_user_id=current_user.id)

    def read_field(row, key, default=None):
        if hasattr(row, key):
            return getattr(row, key)
        if isinstance(row, dict):
            return row.get(key, default)
        return default

    if allowed_direct_target_ids is not None:
        direct_poll_rows = [
            row
            for row in direct_poll_rows
            if int(read_field(row, "other_user_id", 0) or 0) in allowed_direct_target_ids
        ]

    unread_convs = [
        *[
            row
            for row in direct_poll_rows
            if int(read_field(row, "unread_count", 0) or 0) > 0
        ],
        *[
            row
            for row in room_poll_summaries
            if int(read_field(row, "unread_count", 0) or 0) > 0
        ],
    ]
    muted_conversation_ids = sorted(
        {
            int(read_field(row, "other_user_id", 0) or 0)
            for row in [*direct_poll_rows, *room_poll_summaries]
            if bool(read_field(row, "is_muted", False))
        }
    )

    unread_chats_count = len(unread_convs)
    total_unread = sum(int(read_field(row, "unread_count", 0) or 0) for row in unread_convs)
    total_unread_mentions = sum(
        int(read_field(row, "unread_mention_count", 0) or 0)
        for row in room_poll_summaries
    )
    conversations_with_unread = [
        {
            "user_id": int(read_field(row, "other_user_id", 0) or 0),
            "user_name": read_field(row, "other_user_name", "") or "",
            "unread_count": int(read_field(row, "unread_count", 0) or 0),
            "unread_mention_count": int(read_field(row, "unread_mention_count", 0) or 0),
            "is_deleted": bool(read_field(row, "other_user_is_deleted", False)),
        }
        for row in unread_convs
    ]
    
    return PollResponse(
        total_unread=total_unread,
        unread_chats_count=unread_chats_count,
        conversations_with_unread=conversations_with_unread,
        muted_conversation_ids=muted_conversation_ids,
        total_unread_mentions=total_unread_mentions,
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
    try:
        contents = await file.read()
        file_result = await persist_chat_media_file_bytes(
            db,
            uploader_id=current_user.id,
            file_name=file.filename or f"upload_{uuid.uuid4()}",
            declared_content_type=file.content_type or "application/octet-stream",
            contents=contents,
            thumbnail=thumbnail,
        )
        await db.commit()

        # برگرداندن شناسه، تامنیل و ابعاد تصویر
        result = {
            "file_id": file_result.chat_file.id,
            "thumbnail": file_result.chat_file.thumbnail,
            "file_name": file_result.chat_file.file_name,
            "mime_type": file_result.chat_file.mime_type,
            "size": file_result.chat_file.size,
        }
        if file_result.width and file_result.height:
            result["width"] = file_result.width
            result["height"] = file_result.height
        return result
    finally:
        close_file = getattr(file, "close", None)
        if callable(close_file):
            close_result = close_file()
            if inspect.isawaitable(close_result):
                await close_result


@router.post("/upload-batches", response_model=UploadBatchCreateResponse, status_code=status.HTTP_201_CREATED)
async def post_upload_batch(
    data: UploadBatchCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    batch = await create_upload_batch(
        db,
        current_user=current_user,
        room_kind=data.room_kind,
        target_id=data.target_id,
        message_kind=data.message_kind,
        expected_items=data.expected_items,
        caption_policy=data.caption_policy,
        idempotency_key=data.idempotency_key,
    )
    return UploadBatchCreateResponse(
        batch_id=batch.id,
        status=batch.status,
        expires_at=batch.expires_at,
    )


@router.post("/upload-sessions", response_model=UploadSessionCreateResponse, status_code=status.HTTP_201_CREATED)
async def post_upload_session(
    data: UploadSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await create_upload_session(
        db,
        current_user=current_user,
        batch_id=data.batch_id,
        room_kind=data.room_kind,
        target_id=data.target_id,
        media_type=data.media_type,
        file_name=data.file_name,
        mime_type=data.mime_type,
        total_bytes=data.total_bytes,
        chunk_size=data.chunk_size,
        preview_metadata=data.preview_metadata.model_dump(exclude_none=True),
        sha256_full=data.sha256_full,
    )
    await _publish_upload_session_runtime_event(
        db=db,
        current_user=current_user,
        session=session,
        event_name="created",
    )
    return UploadSessionCreateResponse(
        session_id=session.id,
        resume_token=session.resume_token,
        next_offset=session.next_offset,
        chunk_size=session.chunk_size,
        expires_at=session.expires_at,
        status=session.status,
    )


@router.patch("/upload-sessions/{session_id}/chunk", response_model=UploadSessionChunkAppendResponse)
async def patch_upload_session_chunk(
    session_id: str,
    resume_token: str = Form(...),
    offset: int = Form(...),
    is_last_chunk: bool = Form(False),
    chunk: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_upload_session_for_current_user(
        db,
        session_id=session_id,
        current_user=current_user,
    )
    chunk_bytes = await read_upload_chunk_bytes(chunk)
    session = await append_upload_chunk(
        db,
        session=session,
        resume_token=resume_token,
        offset=offset,
        chunk_bytes=chunk_bytes,
        is_last_chunk=is_last_chunk,
    )
    await _publish_upload_session_runtime_event(
        db=db,
        current_user=current_user,
        session=session,
        event_name="progress",
    )
    return UploadSessionChunkAppendResponse(
        session_id=session.id,
        received_bytes=session.received_bytes,
        next_offset=session.next_offset,
        status=session.status,
    )


@router.get("/upload-sessions/{session_id}", response_model=UploadSessionStateRead)
async def get_upload_session_state(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_upload_session_for_current_user(
        db,
        session_id=session_id,
        current_user=current_user,
    )
    return UploadSessionStateRead(
        session_id=session.id,
        status=session.status,
        next_offset=session.next_offset,
        received_bytes=session.received_bytes,
        total_bytes=session.total_bytes,
        preview_metadata=session.preview_metadata or {},
        final_chat_file_id=session.final_chat_file_id,
    )


@router.post("/upload-sessions/{session_id}/finalize", response_model=UploadSessionStatusChangeResponse)
async def finalize_upload_session_endpoint(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_upload_session_for_current_user(
        db,
        session_id=session_id,
        current_user=current_user,
    )
    try:
        session = await finalize_upload_session(
            db,
            session=session,
            current_user=current_user,
        )
    except HTTPException:
        if getattr(session, "status", None) == UploadSessionStatus.FAILED:
            await _publish_upload_session_runtime_event(
                db=db,
                current_user=current_user,
                session=session,
                event_name="failed",
            )
        raise
    await _publish_upload_session_runtime_event(
        db=db,
        current_user=current_user,
        session=session,
        event_name="ready",
    )
    return UploadSessionStatusChangeResponse(
        session_id=session.id,
        status=session.status,
        final_chat_file_id=session.final_chat_file_id,
    )


@router.post("/upload-batches/{batch_id}/commit", response_model=UploadBatchCommitResponse)
async def commit_upload_batch_endpoint(
    batch_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    batch = await get_upload_batch_for_current_user(
        db,
        batch_id=batch_id,
        current_user=current_user,
    )
    commit_result = await commit_upload_batch(
        db,
        batch=batch,
        current_user=current_user,
    )
    serialized_messages = await _serialize_direct_messages_with_accountant_contract(
        db,
        commit_result.messages,
        viewer_user_id=current_user.id,
    )
    sender_name = await resolve_direct_sender_display_name(db, user=current_user)
    group_member_user_ids = None

    if commit_result.target.receiver is None:
        group_member_user_ids = await list_active_room_member_user_ids(db, chat_id=commit_result.target.target_id)

    background_tasks.add_task(
        _publish_upload_batch_commit_message_events,
        target=commit_result.target,
        messages=commit_result.messages,
        serialized_messages=serialized_messages,
        sender_name=sender_name,
        group_member_user_ids=group_member_user_ids,
    )
    background_tasks.add_task(
        _publish_upload_batch_commit_runtime_events,
        batch_id=commit_result.batch.id,
        current_user_id=current_user.id,
        batch_status=commit_result.batch.status,
    )

    return UploadBatchCommitResponse(
        batch_id=commit_result.batch.id,
        status=commit_result.batch.status,
        committed_items=commit_result.batch.committed_items,
        messages=serialized_messages,
    )


@router.post("/upload-sessions/{session_id}/cancel", response_model=UploadSessionStatusChangeResponse)
async def cancel_upload_session_endpoint(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_upload_session_for_current_user(
        db,
        session_id=session_id,
        current_user=current_user,
    )
    session = await cancel_upload_session(db, session=session)
    await _publish_upload_session_runtime_event(
        db=db,
        current_user=current_user,
        session=session,
        event_name="cancelled",
    )
    return UploadSessionStatusChangeResponse(
        session_id=session.id,
        status=session.status,
        final_chat_file_id=session.final_chat_file_id,
    )


@router.post("/upload-batches/{batch_id}/cancel", response_model=UploadBatchCancelResponse)
async def cancel_upload_batch_endpoint(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    batch = await get_upload_batch_for_current_user(
        db,
        batch_id=batch_id,
        current_user=current_user,
    )
    batch = await cancel_upload_batch(db, batch=batch)
    for session in await _list_batch_upload_sessions(db, batch_id=batch.id):
        await _publish_upload_session_runtime_event(
            db=db,
            current_user=current_user,
            session=session,
            event_name="cancelled",
            batch_status=batch.status,
        )
    return UploadBatchCancelResponse(batch_id=batch.id, status=batch.status)

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
