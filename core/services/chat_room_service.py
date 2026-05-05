"""Generic room helpers for group/channel-oriented chat workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType
from models.chat import Chat
from models.chat_member import ChatMember
from models.user import User


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
class ChannelMemberSummary:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    role: ChatMemberRole
    joined_at: datetime
    is_channel_creator: bool

@dataclass
class ChannelMemberMutationSummary:
    chat_id: int
    user_id: int
    role: ChatMemberRole | None
    removed: bool
    member_count: int


def _utcnow() -> datetime:
    return datetime.utcnow()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


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


async def count_active_chat_members(db: AsyncSession, chat_id: int) -> int:
    """Count active members for one chat room."""
    stmt = select(func.count(ChatMember.id)).where(
        ChatMember.chat_id == chat_id,
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


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