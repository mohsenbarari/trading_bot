"""Direct-chat service helpers for the legacy Conversation-based messenger flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType
from models.chat import Chat
from models.chat_member import ChatMember
from models.conversation import Conversation
from models.message import Message
from models.user import User


def get_direct_conversation_key(user1_id: int, user2_id: int) -> tuple[int, int]:
    """Return the canonical ordering for a two-user direct conversation."""
    return (min(user1_id, user2_id), max(user1_id, user2_id))


def _membership_status_for_user(user: User) -> ChatMembershipStatus:
    return ChatMembershipStatus.INACTIVE if user.is_deleted else ChatMembershipStatus.ACTIVE


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _same_datetime(left: datetime | None, right: datetime | None) -> bool:
    return _normalize_datetime(left) == _normalize_datetime(right)


def build_direct_conversation_projection_stmt(current_user_id: int):
    """Project only the fields needed by direct-chat list and poll endpoints."""
    user1_alias = aliased(User)
    user2_alias = aliased(User)
    last_message_alias = aliased(Message)
    direct_chat_lookup = (
        select(
            Chat.id.label("chat_id"),
            func.min(ChatMember.user_id).label("user1_id"),
            func.max(ChatMember.user_id).label("user2_id"),
            Chat.last_message_id.label("chat_last_message_id"),
            Chat.last_message_at.label("chat_last_message_at"),
        )
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(Chat.type == ChatType.DIRECT, Chat.is_deleted.is_(False))
        .group_by(Chat.id, Chat.last_message_id, Chat.last_message_at)
        .having(func.count(sa.distinct(ChatMember.user_id)) == 2)
        .subquery()
    )
    resolved_last_message_id = func.coalesce(
        direct_chat_lookup.c.chat_last_message_id,
        Conversation.last_message_id,
    )
    resolved_last_message_at = func.coalesce(
        direct_chat_lookup.c.chat_last_message_at,
        Conversation.last_message_at,
    ).label("last_message_at")
    current_member_lookup = (
        select(
            ChatMember.chat_id.label("chat_id"),
            func.max(ChatMember.last_read_message_id).label("last_read_message_id"),
            func.max(ChatMember.last_read_at).label("last_read_at"),
        )
        .where(ChatMember.user_id == current_user_id)
        .group_by(ChatMember.chat_id)
        .subquery()
    )

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
    legacy_unread_count = case(
        (Conversation.user1_id == current_user_id, Conversation.unread_count_user1),
        else_=Conversation.unread_count_user2,
    )
    generic_unread_count = (
        select(func.count(Message.id))
        .where(
            Message.chat_id == direct_chat_lookup.c.chat_id,
            Message.sender_id != current_user_id,
            Message.is_deleted.is_(False),
            Message.id > func.coalesce(current_member_lookup.c.last_read_message_id, 0),
        )
        .correlate(direct_chat_lookup, current_member_lookup)
        .scalar_subquery()
    )
    unread_count = case(
        (
            sa.and_(
                direct_chat_lookup.c.chat_id.is_not(None),
                current_member_lookup.c.last_read_message_id.is_not(None),
            ),
            generic_unread_count,
        ),
        else_=legacy_unread_count,
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
            resolved_last_message_at,
            unread_count,
            other_user_last_seen_at,
        )
        .select_from(Conversation)
        .join(user1_alias, Conversation.user1_id == user1_alias.id)
        .join(user2_alias, Conversation.user2_id == user2_alias.id)
        .outerjoin(
            direct_chat_lookup,
            sa.and_(
                direct_chat_lookup.c.user1_id == Conversation.user1_id,
                direct_chat_lookup.c.user2_id == Conversation.user2_id,
            ),
        )
        .outerjoin(
            current_member_lookup,
            current_member_lookup.c.chat_id == direct_chat_lookup.c.chat_id,
        )
        .outerjoin(last_message_alias, last_message_alias.id == resolved_last_message_id)
    )
    return stmt, unread_count, resolved_last_message_at


def build_direct_conversation_scope_condition(current_user_id: int):
    """Restrict direct conversation projections to rows owned by the current user."""
    return sa.or_(
        Conversation.user1_id == current_user_id,
        Conversation.user2_id == current_user_id,
    )


def build_direct_conversation_list_stmt(current_user_id: int):
    """Build the ordered direct-conversation list query for the current user."""
    stmt, _, conversation_order_at = build_direct_conversation_projection_stmt(current_user_id)
    return (
        stmt
        .where(build_direct_conversation_scope_condition(current_user_id))
        .order_by(conversation_order_at.desc().nullslast())
    )


def build_direct_unread_poll_stmt(current_user_id: int):
    """Build the unread-only direct conversation poll query for the current user."""
    stmt, unread_count, _ = build_direct_conversation_projection_stmt(current_user_id)
    return (
        stmt
        .where(build_direct_conversation_scope_condition(current_user_id))
        .where(func.coalesce(unread_count, 0) > 0)
    )


async def get_existing_direct_conversation(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Conversation | None:
    """Load the legacy Conversation row for a direct chat pair if it already exists."""
    ordered_user1_id, ordered_user2_id = get_direct_conversation_key(user1_id, user2_id)

    stmt = select(Conversation).where(
        Conversation.user1_id == ordered_user1_id,
        Conversation.user2_id == ordered_user2_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_direct_conversation(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Conversation:
    """Load or create the legacy Conversation row for a direct chat pair."""
    conversation = await get_existing_direct_conversation(db, user1_id, user2_id)

    if conversation is None:
        ordered_user1_id, ordered_user2_id = get_direct_conversation_key(user1_id, user2_id)
        conversation = Conversation(
            user1_id=ordered_user1_id,
            user2_id=ordered_user2_id,
            unread_count_user1=0,
            unread_count_user2=0,
        )
        db.add(conversation)
        await db.flush()

    return conversation


def build_legacy_direct_message_condition(user1_id: int, user2_id: int):
    """Match the existing direct-message pair using the legacy sender/receiver fields."""
    ordered_user1_id, ordered_user2_id = get_direct_conversation_key(user1_id, user2_id)
    return sa.or_(
        sa.and_(Message.sender_id == ordered_user1_id, Message.receiver_id == ordered_user2_id),
        sa.and_(Message.sender_id == ordered_user2_id, Message.receiver_id == ordered_user1_id),
    )


async def build_direct_message_lookup_condition(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
):
    """Bridge direct-message reads to chat_id while keeping legacy pair fallback."""
    legacy_condition = build_legacy_direct_message_condition(user1_id, user2_id)
    existing_chat_id = await _find_existing_direct_chat_id(db, user1_id, user2_id)
    if existing_chat_id is None:
        return legacy_condition
    return sa.or_(Message.chat_id == existing_chat_id, legacy_condition)


def build_direct_message_read_options(*, include_sender: bool = True):
    """Load the message relations needed by the current direct-chat serializers."""
    options = [
        joinedload(Message.reply_to_message),
        joinedload(Message.forwarded_from),
    ]
    if include_sender:
        options.append(joinedload(Message.sender))
    return tuple(options)


async def build_direct_message_search_stmt(
    db: AsyncSession,
    *,
    current_user_id: int,
    query_text: str,
    other_user_id: int | None = None,
    limit: int = 50,
):
    """Build the direct-message search query for one user or one direct thread."""
    stmt = (
        select(Message)
        .where(
            sa.or_(
                Message.sender_id == current_user_id,
                Message.receiver_id == current_user_id,
            ),
            Message.content.ilike(f"%{query_text}%"),
            Message.is_deleted.is_(False),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
        .options(*build_direct_message_read_options())
    )
    if other_user_id is None:
        return stmt

    direct_message_condition = await build_direct_message_lookup_condition(
        db,
        current_user_id,
        other_user_id,
    )
    return stmt.where(direct_message_condition)


async def build_direct_message_history_statements(
    db: AsyncSession,
    *,
    current_user_id: int,
    other_user_id: int,
    limit: int = 50,
    before_id: int | None = None,
    around_id: int | None = None,
):
    """Build direct-message history statements for pagination or around-message loading."""
    direct_message_condition = await build_direct_message_lookup_condition(
        db,
        current_user_id,
        other_user_id,
    )
    base_conditions = [direct_message_condition, Message.is_deleted.is_(False)]
    read_options = build_direct_message_read_options()

    if around_id is not None:
        stmt_older = (
            select(Message)
            .options(*read_options)
            .where(*base_conditions, Message.id < around_id)
            .order_by(Message.created_at.desc())
            .limit(limit // 2)
        )
        stmt_newer = (
            select(Message)
            .options(*read_options)
            .where(*base_conditions, Message.id >= around_id)
            .order_by(Message.created_at.asc())
            .limit(limit // 2 + 1)
        )
        return stmt_older, stmt_newer

    conditions = list(base_conditions)
    if before_id is not None:
        conditions.append(Message.id < before_id)

    stmt = (
        select(Message)
        .options(*read_options)
        .where(*conditions)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return stmt, None


async def _get_latest_direct_message(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Message | None:
    direct_message_condition = await build_direct_message_lookup_condition(db, user1_id, user2_id)
    stmt = (
        select(Message)
        .where(direct_message_condition)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _find_existing_direct_chat_id(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> int | None:
    member_ids = get_direct_conversation_key(user1_id, user2_id)
    stmt = (
        select(Chat.id)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(Chat.type == ChatType.DIRECT, Chat.is_deleted.is_(False))
        .group_by(Chat.id)
        .having(
            func.count(sa.distinct(ChatMember.user_id)) == 2,
            func.count(sa.distinct(ChatMember.user_id)).filter(ChatMember.user_id.in_(member_ids)) == 2,
        )
        .order_by(Chat.id.asc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _load_direct_chat_members(
    db: AsyncSession,
    chat_id: int,
    user1_id: int,
    user2_id: int,
) -> dict[int, ChatMember]:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id.in_((user1_id, user2_id)),
        )
        .order_by(ChatMember.id.asc())
    )
    result = await db.execute(stmt)
    members: dict[int, ChatMember] = {}
    for member in result.scalars().all():
        members.setdefault(member.user_id, member)
    return members


async def get_or_create_direct_chat(
    db: AsyncSession,
    user1: User,
    user2: User,
) -> Chat:
    """Load or create the generic direct Chat row for a user pair."""
    chat_id = await _find_existing_direct_chat_id(db, user1.id, user2.id)
    chat = await db.get(Chat, chat_id) if chat_id is not None else None

    if chat is None:
        chat = Chat(
            type=ChatType.DIRECT,
            created_by_id=None,
        )
        db.add(chat)
        await db.flush()

    existing_members = await _load_direct_chat_members(db, chat.id, user1.id, user2.id)
    for user in (user1, user2):
        expected_status = _membership_status_for_user(user)
        expected_left_at = user.deleted_at if user.is_deleted else None
        member = existing_members.get(user.id)

        if member is None:
            db.add(
                ChatMember(
                    chat_id=chat.id,
                    user_id=user.id,
                    role=ChatMemberRole.MEMBER,
                    membership_status=expected_status,
                    left_at=expected_left_at,
                )
            )
            continue

        if member.membership_status != expected_status:
            member.membership_status = expected_status
        if not _same_datetime(member.left_at, expected_left_at):
            member.left_at = expected_left_at

    return chat


async def sync_direct_message_threading(
    db: AsyncSession,
    *,
    sender: User,
    receiver: User,
    message: Message,
) -> Conversation:
    """Sync legacy Conversation state and generic direct Chat state for one sent message."""
    conversation = await get_or_create_direct_conversation(db, sender.id, receiver.id)
    direct_chat = await get_or_create_direct_chat(db, sender, receiver)

    if message.chat_id != direct_chat.id:
        message.chat_id = direct_chat.id

    members = await _load_direct_chat_members(db, direct_chat.id, sender.id, receiver.id)
    sender_member = members.get(sender.id)
    if sender_member is not None:
        sender_member.last_read_message_id = message.id
        sender_member.last_read_at = message.created_at or datetime.now(timezone.utc)

    conversation.last_message_id = message.id
    conversation.last_message_at = message.created_at
    direct_chat.last_message_id = message.id
    direct_chat.last_message_at = message.created_at

    ordered_user1_id, _ = get_direct_conversation_key(sender.id, receiver.id)
    if receiver.id == ordered_user1_id:
        conversation.unread_count_user1 += 1
    else:
        conversation.unread_count_user2 += 1

    return conversation


async def sync_direct_read_state(
    db: AsyncSession,
    *,
    reader: User,
    other_user_id: int,
) -> Conversation | None:
    """Sync direct-chat read state across legacy Conversation rows and generic ChatMember cursors."""
    await db.execute(
        update(Message)
        .where(
            Message.sender_id == other_user_id,
            Message.receiver_id == reader.id,
            Message.is_read.is_(False),
        )
        .values(is_read=True)
    )

    conversation = await get_existing_direct_conversation(db, reader.id, other_user_id)
    ordered_user1_id, _ = get_direct_conversation_key(reader.id, other_user_id)
    if conversation is not None:
        if reader.id == ordered_user1_id:
            conversation.unread_count_user1 = 0
        else:
            conversation.unread_count_user2 = 0

    existing_chat_id = await _find_existing_direct_chat_id(db, reader.id, other_user_id)
    if conversation is None and existing_chat_id is None:
        return conversation

    other_user = await db.get(User, other_user_id)
    if other_user is None:
        return conversation

    direct_chat = await get_or_create_direct_chat(db, reader, other_user)
    latest_message = await _get_latest_direct_message(db, reader.id, other_user_id)
    if latest_message is None:
        return conversation

    if latest_message.chat_id != direct_chat.id:
        latest_message.chat_id = direct_chat.id

    members = await _load_direct_chat_members(db, direct_chat.id, reader.id, other_user_id)
    reader_member = members.get(reader.id)
    if reader_member is None:
        direct_chat = await get_or_create_direct_chat(db, reader, other_user)
        members = await _load_direct_chat_members(db, direct_chat.id, reader.id, other_user_id)
        reader_member = members.get(reader.id)

    if reader_member is not None:
        reader_member.last_read_message_id = latest_message.id
        reader_member.last_read_at = datetime.now(timezone.utc)

    return conversation


async def ensure_direct_message_chat_link(
    db: AsyncSession,
    message: Message,
) -> Message:
    """Ensure a direct message is linked into the generic direct-chat foundation."""
    if message.chat_id is not None:
        return message

    sender = await db.get(User, message.sender_id)
    receiver = await db.get(User, message.receiver_id)
    if sender is None or receiver is None:
        return message

    direct_chat = await get_or_create_direct_chat(db, sender, receiver)
    message.chat_id = direct_chat.id

    if direct_chat.last_message_id is None:
        direct_chat.last_message_id = message.id
        direct_chat.last_message_at = message.created_at

    return message


def update_direct_message_content(
    message: Message,
    *,
    content: str,
    updated_at: datetime,
) -> Message:
    """Apply the content mutation for a direct text message."""
    history = list(message.edit_history) if message.edit_history else []
    history.append(
        {
            "content": message.content,
            "updated_at": str(updated_at),
        }
    )
    if len(history) > 3:
        history.pop(0)

    message.edit_history = history
    message.content = content
    message.updated_at = updated_at
    return message


def mark_direct_message_deleted(
    message: Message,
    *,
    deleted_at: datetime,
) -> Message:
    """Apply the soft-delete mutation for a direct message."""
    message.is_deleted = True
    message.updated_at = deleted_at
    return message


def toggle_direct_message_reaction_state(
    message: Message,
    *,
    acting_user_id: int,
    emoji: str,
    normalize_reactions: Callable[[object], list[dict[str, object]]],
    reaction_order: dict[str, int],
) -> Message:
    """Toggle one user's reaction state on a direct message."""
    normalized_reactions = normalize_reactions(message.reactions)
    has_same_reaction = any(
        reaction["user_id"] == acting_user_id and reaction["emoji"] == emoji
        for reaction in normalized_reactions
    )

    if has_same_reaction:
        normalized_reactions = [
            reaction
            for reaction in normalized_reactions
            if not (reaction["user_id"] == acting_user_id and reaction["emoji"] == emoji)
        ]
    else:
        normalized_reactions = [
            reaction
            for reaction in normalized_reactions
            if reaction["user_id"] != acting_user_id
        ]
        normalized_reactions.append({"emoji": emoji, "user_id": acting_user_id})
        normalized_reactions.sort(
            key=lambda item: (
                reaction_order.get(str(item["emoji"]), 999),
                int(item["user_id"]),
            )
        )

    message.reactions = normalized_reactions
    return message


async def reload_direct_message(
    db: AsyncSession,
    message_id: int,
    *,
    include_sender: bool = False,
) -> Message | None:
    """Reload a direct message with the relationships needed by the current API serializers."""
    options = [joinedload(Message.reply_to_message), joinedload(Message.forwarded_from)]
    if include_sender:
        options.append(joinedload(Message.sender))

    result = await db.execute(
        select(Message)
        .options(*options)
        .where(Message.id == message_id)
    )
    return result.scalars().first()


async def persist_sent_direct_message(
    db: AsyncSession,
    *,
    sender: User,
    receiver: User,
    content: str,
    message_type: MessageType,
    reply_to_message_id: int | None = None,
    forwarded_from_id: int | None = None,
) -> Message | None:
    """Persist one new direct message and sync both legacy and generic chat state."""
    message = Message(
        sender_id=sender.id,
        receiver_id=receiver.id,
        content=content,
        message_type=message_type,
        reply_to_message_id=reply_to_message_id,
        forwarded_from_id=forwarded_from_id,
        is_read=False,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    if message.reply_to_message_id or message.forwarded_from_id:
        reloaded_message = await reload_direct_message(db, message.id)
        if reloaded_message is not None:
            message = reloaded_message

    await sync_direct_message_threading(
        db,
        sender=sender,
        receiver=receiver,
        message=message,
    )
    await db.commit()
    return await reload_direct_message(db, message.id)


async def persist_direct_message_change(
    db: AsyncSession,
    message: Message,
    *,
    include_sender: bool = False,
) -> Message | None:
    """Persist a direct-message mutation while preserving generic chat linkage."""
    await ensure_direct_message_chat_link(db, message)
    await db.commit()
    return await reload_direct_message(db, message.id, include_sender=include_sender)


async def commit_direct_read_state(
    db: AsyncSession,
    *,
    reader: User,
    other_user_id: int,
) -> Conversation | None:
    """Persist the direct-thread read-state transition for one user pair."""
    conversation = await sync_direct_read_state(
        db,
        reader=reader,
        other_user_id=other_user_id,
    )
    await db.commit()
    return conversation


async def get_editable_direct_message(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
    now: datetime,
) -> Message:
    """Load one direct message and enforce edit preconditions."""
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != actor_id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
    if message.message_type != MessageType.TEXT:
        raise HTTPException(status_code=400, detail="Only text messages can be edited")
    if getattr(message, "forwarded_from_id", None):
        raise HTTPException(status_code=400, detail="Forwarded messages cannot be edited")
    if message.created_at < now - timedelta(hours=48):
        raise HTTPException(status_code=400, detail="Message is too old to edit")
    return message


async def get_reactable_direct_message(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
) -> Message:
    """Load one direct message and enforce reaction preconditions."""
    message = await db.get(Message, message_id)
    if message is None or message.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    if actor_id not in (message.sender_id, message.receiver_id):
        raise HTTPException(
            status_code=403,
            detail="You can only react to messages in your own conversations",
        )
    return message


async def get_deletable_direct_message(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
    now: datetime,
) -> Message:
    """Load one direct message and enforce delete preconditions."""
    if message_id <= 0 or message_id > 2147483647:
        raise HTTPException(status_code=404, detail="Message not found")

    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != actor_id:
        raise HTTPException(status_code=403, detail="You can only delete your own messages")
    if message.created_at < now - timedelta(hours=48):
        raise HTTPException(status_code=400, detail="Message is too old to delete")
    return message