"""Direct-chat service helpers for the legacy Conversation-based messenger flow."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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