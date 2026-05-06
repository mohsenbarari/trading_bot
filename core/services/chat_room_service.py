"""Generic room helpers for group/channel-oriented chat workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType
from models.chat import Chat
from models.chat_member import ChatMember
from models.message import Message
from models.user import User

SerializedMessageT = TypeVar("SerializedMessageT")
RoomEventPublisher = Callable[[int, str, dict], object]


@dataclass
class ChannelInviteCandidate:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    is_already_member: bool = False


@dataclass
class ChannelInviteCandidatePage:
    items: list[ChannelInviteCandidate]
    total: int
    active_total: int


@dataclass
class ChannelBulkAddSummary:
    chat_id: int
    processed_user_ids: list[int]
    added_count: int
    reactivated_count: int
    already_member_count: int
    member_count: int
    select_all_active_users: bool


@dataclass
class ChannelRoomSummary:
    id: int
    type: ChatType
    title: str
    description: str | None
    created_by_id: int | None
    is_system: bool
    is_mandatory: bool
    member_count: int
    created_at: datetime


@dataclass
class GroupRoomSummary:
    id: int
    type: ChatType
    title: str
    description: str | None
    created_by_id: int | None
    member_count: int
    max_members: int
    created_at: datetime
    current_user_role: ChatMemberRole | None


@dataclass
class ChannelConversationSummary:
    id: int
    other_user_id: int
    other_user_name: str
    other_user_is_deleted: bool
    last_message_content: str | None
    last_message_type: MessageType | None
    last_message_at: datetime | None
    unread_count: int
    other_user_last_seen_at: datetime | None
    room_kind: str
    chat_id: int
    can_send: bool
    member_role: str | None


@dataclass
class ChannelMemberSummary:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    role: ChatMemberRole
    joined_at: datetime
    is_channel_creator: bool


@dataclass
class GroupMemberSummary:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    role: ChatMemberRole
    joined_at: datetime
    is_group_creator: bool


@dataclass
class GroupMemberMutationSummary:
    chat_id: int
    user_id: int
    role: ChatMemberRole | None
    removed: bool
    left: bool
    member_count: int
    unchanged: bool


@dataclass
class ChannelMemberMutationSummary:
    chat_id: int
    user_id: int
    role: ChatMemberRole | None
    removed: bool
    member_count: int


@dataclass
class ChannelReadBroadcastSummary:
    member_user_ids: list[int]
    reader_id: int
    chat_id: int


@dataclass
class GroupReadBroadcastSummary:
    member_user_ids: list[int]
    reader_id: int
    chat_id: int


def _utcnow() -> datetime:
    return datetime.utcnow()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


GROUP_MAX_MEMBERS = 50


async def _reload_channel_message(db: AsyncSession, message_id: int) -> Message | None:
    result = await db.execute(
        select(Message)
        .options(
            joinedload(Message.reply_to_message),
            joinedload(Message.forwarded_from),
            joinedload(Message.sender),
        )
        .where(Message.id == message_id)
    )
    return result.scalars().first()


async def get_channel_or_404(db: AsyncSession, chat_id: int) -> Chat:
    """Load one active channel room or raise a 404."""
    stmt = select(Chat).where(
        Chat.id == chat_id,
        Chat.type == ChatType.CHANNEL,
        Chat.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return chat


async def get_group_or_404(db: AsyncSession, chat_id: int) -> Chat:
    """Load one active group room or raise a 404."""
    stmt = select(Chat).where(
        Chat.id == chat_id,
        Chat.type == ChatType.GROUP,
        Chat.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return chat


async def get_room_or_404(db: AsyncSession, chat_id: int) -> Chat:
    stmt = select(Chat).where(
        Chat.id == chat_id,
        Chat.type.in_((ChatType.CHANNEL, ChatType.GROUP)),
        Chat.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    chat = result.scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return chat


async def count_active_chat_members(db: AsyncSession, chat_id: int) -> int:
    """Count active members for one chat room."""
    stmt = select(func.count(ChatMember.id)).where(
        ChatMember.chat_id == chat_id,
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def count_active_chat_admins(db: AsyncSession, chat_id: int) -> int:
    stmt = select(func.count(ChatMember.id)).where(
        ChatMember.chat_id == chat_id,
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        ChatMember.role == ChatMemberRole.ADMIN,
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def list_active_channel_member_user_ids(db: AsyncSession, *, chat_id: int) -> list[int]:
    return await list_active_room_member_user_ids(db, chat_id=chat_id)


async def list_active_room_member_user_ids(db: AsyncSession, *, chat_id: int) -> list[int]:
    stmt = select(ChatMember.user_id).where(
        ChatMember.chat_id == chat_id,
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
    )
    result = await db.execute(stmt)
    return [int(user_id) for user_id in result.scalars().all()]


async def create_optional_channel(
    db: AsyncSession,
    *,
    creator: User,
    title: str,
    description: str | None = None,
) -> Chat:
    """Create one optional invite-only channel and add the creator as admin."""
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Channel title is required")

    now = _utcnow()
    chat = Chat(
        type=ChatType.CHANNEL,
        title=cleaned_title,
        description=_clean_text(description),
        created_by_id=creator.id,
        is_system=False,
        is_mandatory=False,
        updated_at=now,
    )
    db.add(chat)
    await db.flush()

    db.add(
        ChatMember(
            chat_id=chat.id,
            user_id=creator.id,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
        )
    )
    await db.commit()
    await db.refresh(chat)
    return chat


async def create_group_chat(
    db: AsyncSession,
    *,
    creator: User,
    title: str,
    member_ids: list[int],
) -> Chat:
    """Create one group chat with the creator as admin and initial active members."""
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Group title is required")

    normalized_member_ids: list[int] = []
    seen_user_ids: set[int] = {creator.id}
    for user_id in member_ids:
        if user_id <= 0 or user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        normalized_member_ids.append(user_id)

    total_members = 1 + len(normalized_member_ids)
    if total_members > GROUP_MAX_MEMBERS:
        raise HTTPException(
            status_code=400,
            detail=f"Group member limit exceeded ({GROUP_MAX_MEMBERS})",
        )

    member_users: list[User] = []
    if normalized_member_ids:
        result = await db.execute(
            select(User).where(
                User.id.in_(normalized_member_ids),
                User.is_deleted.is_(False),
            )
        )
        member_users = list(result.scalars().all())
        if len(member_users) != len(normalized_member_ids):
            raise HTTPException(status_code=400, detail="Some selected group members are invalid")

    now = _utcnow()
    chat = Chat(
        type=ChatType.GROUP,
        title=cleaned_title,
        description=None,
        created_by_id=creator.id,
        is_system=False,
        is_mandatory=False,
        max_members=GROUP_MAX_MEMBERS,
        updated_at=now,
    )
    db.add(chat)
    await db.flush()

    db.add(
        ChatMember(
            chat_id=chat.id,
            user_id=creator.id,
            role=ChatMemberRole.ADMIN,
            membership_status=ChatMembershipStatus.ACTIVE,
            joined_at=now,
            updated_at=now,
        )
    )
    for user in member_users:
        db.add(
            ChatMember(
                chat_id=chat.id,
                user_id=user.id,
                role=ChatMemberRole.MEMBER,
                membership_status=ChatMembershipStatus.ACTIVE,
                joined_at=now,
                updated_at=now,
            )
        )

    await db.commit()
    await db.refresh(chat)
    return chat


async def update_group_chat(
    db: AsyncSession,
    *,
    chat: Chat,
    title: str,
) -> Chat:
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Group title is required")

    chat.title = cleaned_title
    chat.updated_at = _utcnow()
    await db.commit()
    await db.refresh(chat)
    return chat


async def update_optional_channel(
    db: AsyncSession,
    *,
    chat: Chat,
    title: str,
    description: str | None = None,
) -> Chat:
    """Update one optional channel title/description for admin-side management."""
    if chat.is_system or chat.is_mandatory:
        raise HTTPException(status_code=400, detail="Only optional channels can be updated")

    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Channel title is required")

    chat.title = cleaned_title
    chat.description = _clean_text(description)
    chat.updated_at = _utcnow()
    await db.commit()
    await db.refresh(chat)
    return chat


async def list_optional_channels(db: AsyncSession) -> list[ChannelRoomSummary]:
    """List active optional channels for admin-side management."""
    member_count_expr = func.count(ChatMember.id).filter(
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
    )
    stmt = (
        select(Chat, member_count_expr.label("member_count"))
        .outerjoin(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            Chat.type == ChatType.CHANNEL,
            Chat.is_deleted.is_(False),
            Chat.is_mandatory.is_(False),
        )
        .group_by(Chat.id)
        .order_by(Chat.created_at.desc(), Chat.id.desc())
    )
    result = await db.execute(stmt)

    summaries: list[ChannelRoomSummary] = []
    for chat, member_count in result.all():
        summaries.append(
            ChannelRoomSummary(
                id=chat.id,
                type=chat.type,
                title=chat.title or "",
                description=chat.description,
                created_by_id=chat.created_by_id,
                is_system=chat.is_system,
                is_mandatory=chat.is_mandatory,
                member_count=int(member_count or 0),
                created_at=chat.created_at,
            )
        )
    return summaries


async def list_groups_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> list[GroupRoomSummary]:
    """List active groups that the current user belongs to."""
    current_member = aliased(ChatMember)
    member_count = (
        select(func.count(ChatMember.id))
        .where(
            ChatMember.chat_id == Chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .correlate(Chat)
        .scalar_subquery()
    )
    stmt = (
        select(
            Chat,
            current_member.role.label("member_role"),
            member_count.label("member_count"),
        )
        .join(
            current_member,
            (current_member.chat_id == Chat.id)
            & (current_member.user_id == user_id)
            & (current_member.membership_status == ChatMembershipStatus.ACTIVE),
        )
        .where(
            Chat.type == ChatType.GROUP,
            Chat.is_deleted.is_(False),
        )
        .order_by(
            Chat.last_message_at.desc().nullslast(),
            Chat.updated_at.desc().nullslast(),
            Chat.created_at.desc(),
            Chat.id.desc(),
        )
    )
    result = await db.execute(stmt)

    items: list[GroupRoomSummary] = []
    for chat, member_role, active_member_count in result.all():
        items.append(
            GroupRoomSummary(
                id=chat.id,
                type=chat.type,
                title=chat.title or "",
                description=chat.description,
                created_by_id=chat.created_by_id,
                member_count=int(active_member_count or 0),
                max_members=int(chat.max_members or GROUP_MAX_MEMBERS),
                created_at=chat.created_at,
                current_user_role=member_role,
            )
        )
    return items


async def list_group_members(db: AsyncSession, *, chat: Chat) -> list[GroupMemberSummary]:
    """List active members of one group for the group detail view."""
    stmt = (
        select(ChatMember, User)
        .join(User, User.id == ChatMember.user_id)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.role.asc(), ChatMember.joined_at.asc(), ChatMember.user_id.asc())
    )
    result = await db.execute(stmt)

    items: list[GroupMemberSummary] = []
    for member, user in result.all():
        items.append(
            GroupMemberSummary(
                user_id=user.id,
                account_name=user.account_name,
                full_name=user.full_name,
                mobile_number=user.mobile_number,
                role=member.role,
                joined_at=member.joined_at,
                is_group_creator=chat.created_by_id == user.id,
            )
        )
    return items


async def list_group_messages(
    db: AsyncSession,
    *,
    chat: Chat,
    limit: int = 50,
    before_id: int | None = None,
    around_id: int | None = None,
) -> list[Message]:
    return await list_channel_messages(
        db,
        chat=chat,
        limit=limit,
        before_id=before_id,
        around_id=around_id,
    )


async def get_active_channel_member_or_403(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> ChatMember:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=403, detail="You are not a member of this channel")
    return member


async def get_active_group_member_or_403(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> ChatMember:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return member


async def get_active_group_admin_or_403(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> ChatMember:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=user_id)
    if member.role != ChatMemberRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only group admins can manage this group")
    return member


async def add_group_member(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> GroupMemberMutationSummary:
    user = await db.get(User, user_id)
    if user is None or user.is_deleted:
        raise HTTPException(status_code=404, detail="User not found")

    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()

    if member is not None and member.membership_status == ChatMembershipStatus.ACTIVE:
        member_count = await count_active_chat_members(db, chat.id)
        return GroupMemberMutationSummary(
            chat_id=chat.id,
            user_id=user_id,
            role=member.role,
            removed=False,
            left=False,
            member_count=member_count,
            unchanged=True,
        )

    member_count = await count_active_chat_members(db, chat.id)
    max_members = int(chat.max_members or GROUP_MAX_MEMBERS)
    if member_count >= max_members:
        raise HTTPException(status_code=400, detail=f"Group member limit exceeded ({max_members})")

    now = _utcnow()
    if member is None:
        db.add(
            ChatMember(
                chat_id=chat.id,
                user_id=user_id,
                role=ChatMemberRole.MEMBER,
                membership_status=ChatMembershipStatus.ACTIVE,
                joined_at=now,
                updated_at=now,
            )
        )
    else:
        member.role = ChatMemberRole.MEMBER
        member.membership_status = ChatMembershipStatus.ACTIVE
        member.left_at = None
        member.joined_at = now
        member.updated_at = now

    chat.updated_at = now
    await db.commit()

    next_member_count = await count_active_chat_members(db, chat.id)
    return GroupMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=ChatMemberRole.MEMBER,
        removed=False,
        left=False,
        member_count=next_member_count,
        unchanged=False,
    )


async def update_group_admin_status(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
    make_admin: bool,
) -> GroupMemberMutationSummary:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=404, detail="Group member not found")

    target_role = ChatMemberRole.ADMIN if make_admin else ChatMemberRole.MEMBER
    if member.role == target_role:
        member_count = await count_active_chat_members(db, chat.id)
        return GroupMemberMutationSummary(
            chat_id=chat.id,
            user_id=user_id,
            role=member.role,
            removed=False,
            left=False,
            member_count=member_count,
            unchanged=True,
        )

    if not make_admin:
        admin_count = await count_active_chat_admins(db, chat.id)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Group must keep at least one active admin")

    now = _utcnow()
    member.role = target_role
    member.updated_at = now
    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return GroupMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=target_role,
        removed=False,
        left=False,
        member_count=member_count,
        unchanged=False,
    )


async def remove_group_member(
    db: AsyncSession,
    *,
    chat: Chat,
    acting_user_id: int,
    user_id: int,
) -> GroupMemberMutationSummary:
    if acting_user_id == user_id:
        raise HTTPException(status_code=400, detail="Use the leave endpoint to leave a group")

    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=404, detail="Group member not found")

    if member.role == ChatMemberRole.ADMIN:
        admin_count = await count_active_chat_admins(db, chat.id)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Group must keep at least one active admin")

    now = _utcnow()
    member.membership_status = ChatMembershipStatus.REMOVED
    member.left_at = now
    member.updated_at = now
    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return GroupMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=None,
        removed=True,
        left=False,
        member_count=member_count,
        unchanged=False,
    )


async def leave_group_chat(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> GroupMemberMutationSummary:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=user_id)
    if member.role == ChatMemberRole.ADMIN:
        admin_count = await count_active_chat_admins(db, chat.id)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Group must keep at least one active admin")

    now = _utcnow()
    member.membership_status = ChatMembershipStatus.LEFT
    member.left_at = now
    member.updated_at = now
    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return GroupMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=None,
        removed=False,
        left=True,
        member_count=member_count,
        unchanged=False,
    )


async def list_channel_conversations(
    db: AsyncSession,
    *,
    current_user_id: int,
) -> list[ChannelConversationSummary]:
    current_member = aliased(ChatMember)
    last_message_alias = aliased(Message)

    unread_count = (
        select(func.count(Message.id))
        .where(
            Message.chat_id == Chat.id,
            Message.sender_id != current_user_id,
            Message.is_deleted.is_(False),
            Message.id > func.coalesce(current_member.last_read_message_id, 0),
        )
        .correlate(Chat, current_member)
        .scalar_subquery()
    )
    last_message_content = case(
        (last_message_alias.is_deleted.is_(True), "پیام حذف شد"),
        (last_message_alias.message_type == MessageType.TEXT, last_message_alias.content),
        else_=None,
    ).label("last_message_content")

    stmt = (
        select(
            Chat,
            current_member.role.label("member_role"),
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            unread_count.label("unread_count"),
        )
        .join(
            current_member,
            (current_member.chat_id == Chat.id)
            & (current_member.user_id == current_user_id)
            & (current_member.membership_status == ChatMembershipStatus.ACTIVE),
        )
        .outerjoin(last_message_alias, last_message_alias.id == Chat.last_message_id)
        .where(
            Chat.type == ChatType.CHANNEL,
            Chat.is_deleted.is_(False),
        )
        .order_by(Chat.last_message_at.desc().nullslast(), Chat.id.desc())
    )
    result = await db.execute(stmt)

    rows: list[ChannelConversationSummary] = []
    for chat, member_role, last_content, last_type, unread in result.all():
        synthetic_room_id = -int(chat.id)
        rows.append(
            ChannelConversationSummary(
                id=synthetic_room_id,
                other_user_id=synthetic_room_id,
                other_user_name=chat.title or f"کانال {chat.id}",
                other_user_is_deleted=False,
                last_message_content=last_content,
                last_message_type=last_type,
                last_message_at=chat.last_message_at,
                unread_count=int(unread or 0),
                other_user_last_seen_at=None,
                room_kind="channel",
                chat_id=chat.id,
                can_send=member_role == ChatMemberRole.ADMIN,
                member_role=member_role.value if member_role is not None else None,
            )
        )
    return rows
async def list_channel_members(db: AsyncSession, *, chat: Chat) -> list[ChannelMemberSummary]:
    """List active members of one optional channel for admin-side management."""
    stmt = (
        select(ChatMember, User)
        .join(User, User.id == ChatMember.user_id)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.role.asc(), ChatMember.joined_at.asc(), ChatMember.user_id.asc())
    )
    result = await db.execute(stmt)
    
    items: list[ChannelMemberSummary] = []
    for member, user in result.all():
        items.append(
            ChannelMemberSummary(
                user_id=user.id,
                account_name=user.account_name,
                full_name=user.full_name,
                mobile_number=user.mobile_number,
                role=member.role,
                joined_at=member.joined_at,
                is_channel_creator=chat.created_by_id == user.id,
            )
        )
    return items


async def list_channel_messages(
    db: AsyncSession,
    *,
    chat: Chat,
    limit: int = 50,
    before_id: int | None = None,
    around_id: int | None = None,
) -> list[Message]:
    base_stmt = (
        select(Message)
        .options(
            joinedload(Message.reply_to_message),
            joinedload(Message.forwarded_from),
            joinedload(Message.sender),
        )
        .where(Message.chat_id == chat.id)
    )

    if around_id:
        older_limit = max(limit // 2, 1)
        newer_limit = max(limit - older_limit, 1)
        older_result = await db.execute(
            base_stmt.where(Message.id < around_id).order_by(Message.id.desc()).limit(older_limit)
        )
        newer_result = await db.execute(
            base_stmt.where(Message.id >= around_id).order_by(Message.id.asc()).limit(newer_limit)
        )
        older_messages = list(reversed(older_result.scalars().all()))
        newer_messages = newer_result.scalars().all()
        return [*older_messages, *newer_messages]

    stmt = base_stmt.order_by(Message.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)

    result = await db.execute(stmt)
    return list(reversed(result.scalars().all()))


def build_channel_message_event_payload(
    message: Message,
    *,
    chat: Chat,
    serializer: Callable[[Message], SerializedMessageT],
) -> dict[str, object]:
    payload = jsonable_encoder(serializer(message))
    payload["room_kind"] = "channel"
    payload["chat_id"] = chat.id
    payload["conversation_id"] = -int(chat.id)
    payload["conversation_other_user_id"] = -int(chat.id)
    payload["conversation_title"] = chat.title or f"کانال {chat.id}"
    payload["can_send"] = None
    return payload


async def publish_channel_message_event(
    *,
    chat: Chat,
    message: Message,
    member_user_ids: list[int],
    serializer: Callable[[Message], SerializedMessageT],
    publisher: RoomEventPublisher,
) -> None:
    payload = build_channel_message_event_payload(
        message,
        chat=chat,
        serializer=serializer,
    )
    for user_id in member_user_ids:
        if user_id == message.sender_id:
            continue
        await publisher(user_id, "chat:message", payload)


async def publish_channel_read_event(
    *,
    chat_id: int,
    reader_id: int,
    member_user_ids: list[int],
    publisher: RoomEventPublisher,
) -> None:
    payload = {
        "room_kind": "channel",
        "chat_id": chat_id,
        "reader_id": reader_id,
    }
    for user_id in member_user_ids:
        if user_id == reader_id:
            continue
        await publisher(user_id, "chat:read", payload)


async def publish_channel_reaction_event(
    *,
    chat: Chat,
    message: Message,
    member_user_ids: list[int],
    serializer: Callable[[Message], SerializedMessageT],
    publisher: RoomEventPublisher,
) -> None:
    payload = build_channel_message_event_payload(
        message,
        chat=chat,
        serializer=serializer,
    )
    for user_id in member_user_ids:
        await publisher(user_id, "chat:reaction", payload)


def build_group_message_event_payload(
    message: Message,
    *,
    chat: Chat,
    serializer: Callable[[Message], SerializedMessageT],
) -> dict[str, object]:
    payload = jsonable_encoder(serializer(message))
    payload["room_kind"] = "group"
    payload["chat_id"] = chat.id
    payload["conversation_id"] = -int(chat.id)
    payload["conversation_other_user_id"] = -int(chat.id)
    payload["conversation_title"] = chat.title or f"گروه {chat.id}"
    payload["can_send"] = True
    return payload


async def publish_group_message_event(
    *,
    chat: Chat,
    message: Message,
    member_user_ids: list[int],
    serializer: Callable[[Message], SerializedMessageT],
    publisher: RoomEventPublisher,
) -> None:
    payload = build_group_message_event_payload(
        message,
        chat=chat,
        serializer=serializer,
    )
    for user_id in member_user_ids:
        if user_id == message.sender_id:
            continue
        await publisher(user_id, "chat:message", payload)


async def publish_group_read_event(
    *,
    chat_id: int,
    reader_id: int,
    member_user_ids: list[int],
    publisher: RoomEventPublisher,
) -> None:
    payload = {
        "room_kind": "group",
        "chat_id": chat_id,
        "reader_id": reader_id,
    }
    for user_id in member_user_ids:
        if user_id == reader_id:
            continue
        await publisher(user_id, "chat:read", payload)


async def publish_group_reaction_event(
    *,
    chat: Chat,
    message: Message,
    member_user_ids: list[int],
    serializer: Callable[[Message], SerializedMessageT],
    publisher: RoomEventPublisher,
) -> None:
    payload = build_group_message_event_payload(
        message,
        chat=chat,
        serializer=serializer,
    )
    for user_id in member_user_ids:
        await publisher(user_id, "chat:reaction", payload)


async def list_channel_invite_candidates(
    db: AsyncSession,
    *,
    query_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
    exclude_chat_id: int | None = None,
) -> ChannelInviteCandidatePage:
    """List active project users for the channel member picker."""
    normalized_query = _clean_text(query_text)
    active_total_stmt = select(func.count(User.id)).where(User.is_deleted.is_(False))
    active_total_result = await db.execute(active_total_stmt)
    active_total = int(active_total_result.scalar_one() or 0)

    active_member_user_ids = None
    if exclude_chat_id is not None:
        await get_channel_or_404(db, exclude_chat_id)
        active_member_user_ids = (
            select(ChatMember.user_id)
            .where(
                ChatMember.chat_id == exclude_chat_id,
                ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
            )
        )

    filters = [User.is_deleted.is_(False)]
    if normalized_query:
        search_pattern = f"%{normalized_query}%"
        filters.append(
            or_(
                User.full_name.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern),
            )
        )
    if active_member_user_ids is not None:
        filters.append(~User.id.in_(active_member_user_ids))

    total_stmt = select(func.count(User.id)).where(*filters)
    total_result = await db.execute(total_stmt)
    total = int(total_result.scalar_one() or 0)

    items_stmt = (
        select(User)
        .where(*filters)
        .order_by(User.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items_result = await db.execute(items_stmt)
    users = items_result.scalars().all()

    return ChannelInviteCandidatePage(
        items=[
            ChannelInviteCandidate(
                user_id=user.id,
                account_name=user.account_name,
                full_name=user.full_name,
                mobile_number=user.mobile_number,
                is_already_member=False,
            )
            for user in users
        ],
        total=total,
        active_total=active_total,
    )


async def bulk_add_channel_members(
    db: AsyncSession,
    *,
    chat: Chat,
    user_ids: list[int] | None,
    select_all_active_users: bool,
) -> ChannelBulkAddSummary:
    """Add or reactivate many channel members in one idempotent operation."""
    if chat.is_mandatory:
        raise HTTPException(
            status_code=400,
            detail="Bulk membership changes are not supported for the mandatory system channel",
        )

    if select_all_active_users:
        target_stmt = select(User.id).where(User.is_deleted.is_(False)).order_by(User.id.asc())
        target_result = await db.execute(target_stmt)
        target_user_ids = [int(user_id) for user_id in target_result.scalars().all()]
    else:
        target_user_ids = []
        seen_user_ids: set[int] = set()
        for user_id in user_ids or []:
            if user_id <= 0 or user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            target_user_ids.append(user_id)

    if not target_user_ids:
        raise HTTPException(status_code=400, detail="No users selected")

    active_users_stmt = select(User).where(
        User.id.in_(target_user_ids),
        User.is_deleted.is_(False),
    )
    active_users_result = await db.execute(active_users_stmt)
    active_users = {user.id: user for user in active_users_result.scalars().all()}
    processed_user_ids = [user_id for user_id in target_user_ids if user_id in active_users]
    if not processed_user_ids:
        raise HTTPException(status_code=400, detail="No active users available to add")

    existing_members_stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id.in_(processed_user_ids),
        )
        .order_by(ChatMember.user_id.asc(), ChatMember.id.desc())
    )
    existing_members_result = await db.execute(existing_members_stmt)
    existing_members: dict[int, ChatMember] = {}
    for member in existing_members_result.scalars().all():
        existing_members.setdefault(member.user_id, member)

    now = _utcnow()
    added_count = 0
    reactivated_count = 0
    already_member_count = 0

    for user_id in processed_user_ids:
        member = existing_members.get(user_id)
        if member is None:
            db.add(
                ChatMember(
                    chat_id=chat.id,
                    user_id=user_id,
                    role=ChatMemberRole.MEMBER,
                    membership_status=ChatMembershipStatus.ACTIVE,
                )
            )
            added_count += 1
            continue

        if member.membership_status == ChatMembershipStatus.ACTIVE:
            already_member_count += 1
            continue

        member.role = ChatMemberRole.MEMBER
        member.membership_status = ChatMembershipStatus.ACTIVE
        member.left_at = None
        member.joined_at = now
        member.updated_at = now
        reactivated_count += 1

    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return ChannelBulkAddSummary(
        chat_id=chat.id,
        processed_user_ids=processed_user_ids,
        added_count=added_count,
        reactivated_count=reactivated_count,
        already_member_count=already_member_count,
        member_count=member_count,
        select_all_active_users=select_all_active_users,
    )


async def update_channel_member(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
    role: ChatMemberRole | None = None,
    remove_member: bool = False,
) -> ChannelMemberMutationSummary:
    """Promote/demote or remove one active member from an optional channel."""
    if chat.is_system or chat.is_mandatory:
        raise HTTPException(status_code=400, detail="Mandatory/system channels cannot be changed here")

    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    result = await db.execute(stmt)
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=404, detail="Channel member not found")

    if not remove_member and role is None:
        raise HTTPException(status_code=400, detail="No membership change requested")

    is_admin_downgrade = member.role == ChatMemberRole.ADMIN and (remove_member or role == ChatMemberRole.MEMBER)
    if member.user_id == chat.created_by_id and is_admin_downgrade:
        raise HTTPException(status_code=400, detail="Channel creator must remain an active admin")

    if is_admin_downgrade:
        admin_count_result = await db.execute(
            select(func.count(ChatMember.id)).where(
                ChatMember.chat_id == chat.id,
                ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
                ChatMember.role == ChatMemberRole.ADMIN,
            )
        )
        admin_count = int(admin_count_result.scalar_one() or 0)
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Channel must keep at least one active admin")

    now = _utcnow()
    removed = False
    next_role = role

    if remove_member:
        member.membership_status = ChatMembershipStatus.REMOVED
        member.left_at = now
        member.updated_at = now
        removed = True
        next_role = None
    else:
        member.role = role or member.role
        member.updated_at = now
        next_role = member.role

    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return ChannelMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=next_role,
        removed=removed,
        member_count=member_count,
    )


async def send_channel_message(
    db: AsyncSession,
    *,
    chat: Chat,
    sender: User,
    content: str,
    message_type: MessageType,
    reply_to_message_id: int | None = None,
    forwarded_from_id: int | None = None,
) -> Message:
    member = await get_active_channel_member_or_403(db, chat=chat, user_id=sender.id)
    if member.role != ChatMemberRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only channel admins can post messages")

    if reply_to_message_id is not None:
        reply_to_message = await db.get(Message, reply_to_message_id)
        if reply_to_message is None or reply_to_message.chat_id != chat.id:
            raise HTTPException(status_code=400, detail="Reply target is not part of this channel")

    now = _utcnow()
    message = Message(
        chat_id=chat.id,
        sender_id=sender.id,
        receiver_id=sender.id,
        content=content,
        message_type=message_type,
        reply_to_message_id=reply_to_message_id,
        forwarded_from_id=forwarded_from_id,
        is_read=True,
        created_at=now,
    )
    db.add(message)
    await db.flush()

    chat.last_message_id = message.id
    chat.last_message_at = now
    chat.updated_at = now
    member.last_read_message_id = message.id
    member.last_read_at = now
    member.updated_at = now

    await db.commit()
    reloaded_message = await _reload_channel_message(db, message.id)
    if reloaded_message is None:
        raise HTTPException(status_code=500, detail="Failed to reload channel message")
    return reloaded_message


async def send_group_message(
    db: AsyncSession,
    *,
    chat: Chat,
    sender: User,
    content: str,
    message_type: MessageType,
    reply_to_message_id: int | None = None,
    forwarded_from_id: int | None = None,
) -> Message:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=sender.id)

    if reply_to_message_id is not None:
        reply_to_message = await db.get(Message, reply_to_message_id)
        if reply_to_message is None or reply_to_message.chat_id != chat.id:
            raise HTTPException(status_code=400, detail="Reply target is not part of this group")

    now = _utcnow()
    message = Message(
        chat_id=chat.id,
        sender_id=sender.id,
        receiver_id=sender.id,
        content=content,
        message_type=message_type,
        reply_to_message_id=reply_to_message_id,
        forwarded_from_id=forwarded_from_id,
        is_read=True,
        created_at=now,
    )
    db.add(message)
    await db.flush()

    chat.last_message_id = message.id
    chat.last_message_at = now
    chat.updated_at = now
    member.last_read_message_id = message.id
    member.last_read_at = now
    member.updated_at = now

    await db.commit()
    reloaded_message = await _reload_channel_message(db, message.id)
    if reloaded_message is None:
        raise HTTPException(status_code=500, detail="Failed to reload group message")
    return reloaded_message


async def mark_channel_messages_read(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> None:
    member = await get_active_channel_member_or_403(db, chat=chat, user_id=user_id)
    latest_message_id_result = await db.execute(
        select(func.max(Message.id)).where(
            Message.chat_id == chat.id,
            Message.is_deleted.is_(False),
        )
    )
    latest_message_id = latest_message_id_result.scalar_one_or_none()
    now = _utcnow()
    member.last_read_message_id = latest_message_id
    member.last_read_at = now
    member.updated_at = now
    await db.commit()


async def mark_channel_messages_read_with_broadcast(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> ChannelReadBroadcastSummary:
    await mark_channel_messages_read(db, chat=chat, user_id=user_id)
    return ChannelReadBroadcastSummary(
        member_user_ids=await list_active_channel_member_user_ids(db, chat_id=chat.id),
        reader_id=user_id,
        chat_id=chat.id,
    )


async def mark_group_messages_read(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> None:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=user_id)
    latest_message_id_result = await db.execute(
        select(func.max(Message.id)).where(
            Message.chat_id == chat.id,
            Message.is_deleted.is_(False),
        )
    )
    latest_message_id = latest_message_id_result.scalar_one_or_none()
    now = _utcnow()
    member.last_read_message_id = latest_message_id
    member.last_read_at = now
    member.updated_at = now
    await db.commit()


async def mark_group_messages_read_with_broadcast(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> GroupReadBroadcastSummary:
    await mark_group_messages_read(db, chat=chat, user_id=user_id)
    return GroupReadBroadcastSummary(
        member_user_ids=await list_active_room_member_user_ids(db, chat_id=chat.id),
        reader_id=user_id,
        chat_id=chat.id,
    )