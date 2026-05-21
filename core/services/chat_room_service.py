"""Generic room helpers for group/channel-oriented chat workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Callable, Sequence, TypeVar
import unicodedata

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.chat import Chat
from models.chat_member import ChatMember
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.message import Message
from models.user import User, UserRole
from core.services.chat_service import get_next_chat_member_pin_order
from core.services.customer_relation_service import is_user_customer

SerializedMessageT = TypeVar("SerializedMessageT")
RoomEventPublisher = Callable[[int, str, dict], object]
MENTION_HANDLE_PATTERN = re.compile(r"(?<![\w@])@([a-zA-Z0-9_\u0600-\u06FF]+)")


@dataclass
class ChannelInviteCandidate:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    avatar_file_id: str | None = None
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
    avatar_file_id: str | None
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
    avatar_file_id: str | None
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
    avatar_file_id: str | None
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
    member_count: int | None = None
    max_members: int | None = None
    is_system: bool = False
    is_mandatory: bool = False
    is_muted: bool = False
    is_pinned: bool = False
    pinned_at: datetime | None = None
    pin_order: int | None = None
    unread_mention_count: int = 0


@dataclass
class ChannelMemberSummary:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    avatar_file_id: str | None
    role: ChatMemberRole
    joined_at: datetime
    is_channel_creator: bool


@dataclass
class GroupMemberSummary:
    user_id: int
    account_name: str
    full_name: str
    mobile_number: str
    avatar_file_id: str | None
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
    left: bool = False
    unchanged: bool = False


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
    cleaned = "".join(
        ch for ch in value if unicodedata.category(ch) != "Cc" or ch in {"\n", "\r", "\t"}
    ).strip()
    return cleaned or None


def _has_disallowed_control_chars(value: str | None) -> bool:
    if not value:
        return False
    return any(unicodedata.category(ch) == "Cc" and ch not in {"\n", "\r", "\t"} for ch in value)


def _unpack_conversation_projection_row(row):
    if len(row) == 11:
        return row
    if len(row) == 10:
        # returns chat, member_role, is_muted, is_pinned, pinned_at, pin_order, last_content, last_type, unread, member_count, unread_mention_count
        return row + (0,)
    if len(row) == 6:
        chat, member_role, last_content, last_type, unread, member_count = row
        return chat, member_role, False, False, None, None, last_content, last_type, unread, member_count, 0
    if len(row) == 9:
        chat, member_role, is_muted, is_pinned, pinned_at, last_content, last_type, unread, member_count = row
        return chat, member_role, is_muted, is_pinned, pinned_at, None, last_content, last_type, unread, member_count, 0
    raise ValueError(f"Unexpected conversation projection row size: {len(row)}")


GROUP_MAX_MEMBERS = 50
MANDATORY_CHANNEL_TITLE = "اطلاع‌رسانی"
MANDATORY_CHANNEL_DESCRIPTION = "کانال اجباری اطلاع‌رسانی سامانه"
MANDATORY_CHANNEL_LOCK_KEY = 362514001


def _membership_status_for_user(user: User) -> ChatMembershipStatus:
    return ChatMembershipStatus.INACTIVE if getattr(user, "is_deleted", False) else ChatMembershipStatus.ACTIVE


def _normalize_positive_user_ids(user_ids: Sequence[object]) -> list[int]:
    normalized: list[int] = []
    seen_user_ids: set[int] = set()
    for raw_user_id in user_ids:
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        if user_id <= 0 or user_id in seen_user_ids:
            continue
        seen_user_ids.add(user_id)
        normalized.append(user_id)
    return normalized


def _extract_unique_mention_handles(content: str | None) -> list[str]:
    if not content:
        return []

    handles: list[str] = []
    seen_handles: set[str] = set()
    for raw_handle in MENTION_HANDLE_PATTERN.findall(content):
        normalized_handle = raw_handle.strip().casefold()
        if not normalized_handle or normalized_handle in seen_handles:
            continue
        seen_handles.add(normalized_handle)
        handles.append(normalized_handle)
    return handles


async def _resolve_room_message_mentions(
    db: AsyncSession,
    *,
    chat: Chat,
    content: str | None,
    message_type: MessageType,
    mentions: Sequence[object] | None,
    mention_all: bool,
) -> tuple[list[int], bool]:
    explicit_user_ids = _normalize_positive_user_ids(mentions or [])
    parsed_handles = _extract_unique_mention_handles(content if message_type == MessageType.TEXT else None)

    resolved_mention_all = bool(mention_all)
    mention_handles: list[str] = []
    for handle in parsed_handles:
        if handle == "all":
            resolved_mention_all = True
            continue
        mention_handles.append(handle)

    if not explicit_user_ids and not mention_handles:
        return [], resolved_mention_all

    result = await db.execute(
        select(ChatMember.user_id, User.account_name).join(User, User.id == ChatMember.user_id).where(
            ChatMember.chat_id == chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
    )

    member_user_ids: set[int] = set()
    member_account_name_map: dict[str, int] = {}
    for user_id, account_name in result.all():
        normalized_user_id = int(user_id)
        member_user_ids.add(normalized_user_id)
        if isinstance(account_name, str) and account_name.strip():
            member_account_name_map[account_name.casefold()] = normalized_user_id

    resolved_user_ids: list[int] = []
    seen_user_ids: set[int] = set()
    for user_id in explicit_user_ids:
        if user_id in member_user_ids and user_id not in seen_user_ids:
            seen_user_ids.add(user_id)
            resolved_user_ids.append(user_id)

    for handle in mention_handles:
        matched_user_id = member_account_name_map.get(handle)
        if matched_user_id is None or matched_user_id in seen_user_ids:
            continue
        seen_user_ids.add(matched_user_id)
        resolved_user_ids.append(matched_user_id)

    return resolved_user_ids, resolved_mention_all


async def _load_active_customer_owner_map(
    db: AsyncSession,
    user_ids: Sequence[object],
) -> dict[int, int]:
    normalized_user_ids = _normalize_positive_user_ids(user_ids)
    if not normalized_user_ids:
        return {}

    stmt = select(CustomerRelation.customer_user_id, CustomerRelation.owner_user_id).where(
        CustomerRelation.customer_user_id.in_(normalized_user_ids),
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.customer_user_id.is_not(None),
    )
    result = await db.execute(stmt)
    return {
        int(customer_user_id): int(owner_user_id)
        for customer_user_id, owner_user_id in result.all()
        if customer_user_id is not None and owner_user_id is not None
    }


async def _load_active_accountant_owner_map(
    db: AsyncSession,
    user_ids: Sequence[object],
) -> dict[int, int]:
    normalized_user_ids = _normalize_positive_user_ids(user_ids)
    if not normalized_user_ids:
        return {}

    stmt = select(AccountantRelation.accountant_user_id, AccountantRelation.owner_user_id).where(
        AccountantRelation.accountant_user_id.in_(normalized_user_ids),
        AccountantRelation.status == AccountantRelationStatus.ACTIVE,
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.accountant_user_id.is_not(None),
    )
    result = await db.execute(stmt)
    return {
        int(accountant_user_id): int(owner_user_id)
        for accountant_user_id, owner_user_id in result.all()
        if accountant_user_id is not None and owner_user_id is not None
    }


async def _load_active_customer_user_ids(
    db: AsyncSession,
    user_ids: Sequence[object],
) -> set[int]:
    return set((await _load_active_customer_owner_map(db, user_ids)).keys())


def _resolve_customer_group_owner_scope(
    user_id: int,
    *,
    customer_owner_by_user_id: dict[int, int],
    accountant_owner_by_user_id: dict[int, int],
) -> int:
    return (
        customer_owner_by_user_id.get(user_id)
        or accountant_owner_by_user_id.get(user_id)
        or user_id
    )


async def _validate_customer_group_membership_context(
    db: AsyncSession,
    member_user_ids: Sequence[object],
) -> None:
    normalized_user_ids = _normalize_positive_user_ids(member_user_ids)
    if not normalized_user_ids:
        return

    customer_owner_by_user_id = await _load_active_customer_owner_map(db, normalized_user_ids)
    customer_user_ids = [user_id for user_id in normalized_user_ids if user_id in customer_owner_by_user_id]
    if not customer_user_ids:
        return

    if len(customer_user_ids) > 1:
        raise HTTPException(
            status_code=400,
            detail="Customer groups may include only one active customer",
        )

    accountant_owner_by_user_id = await _load_active_accountant_owner_map(db, normalized_user_ids)
    allowed_owner_id = customer_owner_by_user_id[customer_user_ids[0]]
    invalid_user_ids = [
        user_id
        for user_id in normalized_user_ids
        if user_id not in customer_owner_by_user_id
        and _resolve_customer_group_owner_scope(
            user_id,
            customer_owner_by_user_id=customer_owner_by_user_id,
            accountant_owner_by_user_id=accountant_owner_by_user_id,
        )
        != allowed_owner_id
    ]
    if invalid_user_ids:
        raise HTTPException(
            status_code=400,
            detail="Customer groups may only include the owner, same-owner accountants, and that customer",
        )


def _is_group_candidate_allowed(
    *,
    candidate_user_id: int,
    context_user_ids: Sequence[object],
    customer_owner_by_user_id: dict[int, int],
    accountant_owner_by_user_id: dict[int, int],
) -> bool:
    normalized_context_user_ids = _normalize_positive_user_ids(context_user_ids)
    context_customer_user_ids = [
        user_id for user_id in normalized_context_user_ids if user_id in customer_owner_by_user_id
    ]

    if len(context_customer_user_ids) > 1:
        return candidate_user_id in context_customer_user_ids

    if context_customer_user_ids:
        if candidate_user_id in customer_owner_by_user_id:
            return candidate_user_id == context_customer_user_ids[0]
        allowed_owner_id = customer_owner_by_user_id[context_customer_user_ids[0]]
        return (
            _resolve_customer_group_owner_scope(
                candidate_user_id,
                customer_owner_by_user_id=customer_owner_by_user_id,
                accountant_owner_by_user_id=accountant_owner_by_user_id,
            )
            == allowed_owner_id
        )

    if candidate_user_id not in customer_owner_by_user_id:
        return True

    candidate_owner_id = customer_owner_by_user_id[candidate_user_id]
    for user_id in normalized_context_user_ids:
        if user_id in customer_owner_by_user_id:
            if user_id != candidate_user_id:
                return False
            continue
        if (
            _resolve_customer_group_owner_scope(
                user_id,
                customer_owner_by_user_id=customer_owner_by_user_id,
                accountant_owner_by_user_id=accountant_owner_by_user_id,
            )
            != candidate_owner_id
        ):
            return False
    return True


async def _list_mandatory_channels(db: AsyncSession) -> list[Chat]:
    stmt = select(Chat).where(
        Chat.type == ChatType.CHANNEL,
        Chat.is_system.is_(True),
        Chat.is_mandatory.is_(True),
        Chat.is_deleted.is_(False),
    ).order_by(Chat.id.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_mandatory_channel(db: AsyncSession) -> Chat | None:
    channels = await _list_mandatory_channels(db)
    return channels[0] if channels else None


def _normalize_mandatory_channel_metadata(chat: Chat) -> bool:
    normalized_title = (
        MANDATORY_CHANNEL_TITLE if _has_disallowed_control_chars(chat.title) else _clean_text(chat.title) or MANDATORY_CHANNEL_TITLE
    )
    normalized_description = (
        MANDATORY_CHANNEL_DESCRIPTION
        if _has_disallowed_control_chars(chat.description)
        else _clean_text(chat.description) or MANDATORY_CHANNEL_DESCRIPTION
    )
    changed = False

    if chat.title != normalized_title:
        chat.title = normalized_title
        changed = True
    if chat.description != normalized_description:
        chat.description = normalized_description
        changed = True

    if changed:
        chat.updated_at = _utcnow()

    return changed


def _soft_delete_duplicate_mandatory_channel(chat: Chat, *, now: datetime) -> bool:
    changed = False

    if not chat.is_deleted:
        chat.is_deleted = True
        changed = True
    if chat.deleted_at != now:
        chat.deleted_at = now
        changed = True
    if chat.updated_at != now:
        chat.updated_at = now
        changed = True

    return changed


async def ensure_mandatory_channel(db: AsyncSession) -> Chat:
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": MANDATORY_CHANNEL_LOCK_KEY})

    channels = await _list_mandatory_channels(db)
    if channels:
        chat = channels[0]
        changed = _normalize_mandatory_channel_metadata(chat)
        if len(channels) > 1:
            now = _utcnow()
            for duplicate in channels[1:]:
                if _soft_delete_duplicate_mandatory_channel(duplicate, now=now):
                    changed = True
        if changed:
            await db.flush()
        return chat

    chat = Chat(
        type=ChatType.CHANNEL,
        title=MANDATORY_CHANNEL_TITLE,
        description=MANDATORY_CHANNEL_DESCRIPTION,
        created_by_id=None,
        is_system=True,
        is_mandatory=True,
        updated_at=_utcnow(),
    )
    db.add(chat)
    await db.flush()
    return chat


async def _get_primary_super_admin_user_id(db: AsyncSession) -> int | None:
    stmt = (
        select(User.id)
        .where(
            User.role == UserRole.SUPER_ADMIN,
            User.is_deleted.is_(False),
        )
        .order_by(User.id.asc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_latest_chat_member(db: AsyncSession, *, chat_id: int, user_id: int) -> ChatMember | None:
    stmt = (
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
        )
        .order_by(ChatMember.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _sync_mandatory_channel_member(
    member: ChatMember | None,
    *,
    chat_id: int,
    user: User,
    make_admin: bool,
    now: datetime,
) -> ChatMember:
    expected_status = _membership_status_for_user(user)

    if member is None:
        return ChatMember(
            chat_id=chat_id,
            user_id=user.id,
            role=ChatMemberRole.ADMIN if make_admin else ChatMemberRole.MEMBER,
            membership_status=expected_status,
            joined_at=now,
            left_at=None if expected_status == ChatMembershipStatus.ACTIVE else now,
            updated_at=now,
        )

    changed = False

    if member.membership_status != expected_status:
        member.membership_status = expected_status
        changed = True

    if expected_status == ChatMembershipStatus.ACTIVE:
        if member.left_at is not None:
            member.left_at = None
            changed = True
        if make_admin and member.role != ChatMemberRole.ADMIN:
            member.role = ChatMemberRole.ADMIN
            changed = True
    else:
        if member.left_at is None:
            member.left_at = now
            changed = True
        if member.role != ChatMemberRole.MEMBER:
            member.role = ChatMemberRole.MEMBER
            changed = True

    if changed:
        member.updated_at = now

    return member


async def ensure_mandatory_channel_membership(
    db: AsyncSession,
    *,
    user: User,
) -> Chat:
    return await ensure_mandatory_channel_rollout(db, users=[user])


async def sync_mandatory_channel_for_user_state_change(
    db: AsyncSession,
    *,
    user: User,
    previous_role: UserRole | None = None,
    previous_is_deleted: bool | None = None,
    previous_deleted_at: datetime | None = None,
) -> Chat | None:
    if previous_role is not None and previous_role != user.role:
        return await ensure_mandatory_channel_rollout(db)

    membership_state_changed = False
    if previous_is_deleted is not None and previous_is_deleted != getattr(user, "is_deleted", False):
        membership_state_changed = True
    if previous_deleted_at is not None and previous_deleted_at != getattr(user, "deleted_at", None):
        membership_state_changed = True

    if membership_state_changed:
        return await ensure_mandatory_channel_membership(db, user=user)

    return None


async def ensure_mandatory_channel_rollout(
    db: AsyncSession,
    *,
    users: Sequence[User] | None = None,
) -> Chat:
    chat = await ensure_mandatory_channel(db)
    now = _utcnow()

    if users is None:
        result = await db.execute(select(User).order_by(User.id.asc()))
        users = list(result.scalars().all())

    primary_super_admin_user_id = await _get_primary_super_admin_user_id(db)
    seen_user_ids: set[int] = set()
    for user in users:
        if getattr(user, "id", None) is None or user.id in seen_user_ids:
            continue
        seen_user_ids.add(user.id)
        member = await _get_latest_chat_member(db, chat_id=chat.id, user_id=user.id)
        synced_member = _sync_mandatory_channel_member(
            member,
            chat_id=chat.id,
            user=user,
            make_admin=user.id == primary_super_admin_user_id,
            now=now,
        )
        if member is None:
            db.add(synced_member)

    return chat


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
    avatar_file_id: str | None = None,
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
        avatar_file_id=avatar_file_id,
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
    description: str | None = None,
    avatar_file_id: str | None = None,
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

    if await is_user_customer(db, creator.id):
        raise HTTPException(status_code=403, detail="Customer users cannot create group chats in this phase")

    await _validate_customer_group_membership_context(db, [creator.id, *normalized_member_ids])

    now = _utcnow()
    chat = Chat(
        type=ChatType.GROUP,
        title=cleaned_title,
        description=_clean_text(description),
        avatar_file_id=avatar_file_id,
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
    description: str | None = None,
    avatar_file_id: str | None = None,
) -> Chat:
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Group title is required")

    chat.title = cleaned_title
    chat.description = _clean_text(description)
    chat.avatar_file_id = avatar_file_id
    chat.updated_at = _utcnow()
    await db.commit()
    await db.refresh(chat)
    return chat


async def update_manageable_channel_metadata(
    db: AsyncSession,
    *,
    chat: Chat,
    title: str,
    description: str | None = None,
    avatar_file_id: str | None = None,
) -> Chat:
    """Update one admin-manageable channel title/description."""
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Channel title is required")

    chat.title = cleaned_title
    chat.description = _clean_text(description)
    chat.avatar_file_id = avatar_file_id
    chat.updated_at = _utcnow()
    await db.commit()
    await db.refresh(chat)
    return chat


async def list_manageable_channels(db: AsyncSession) -> list[ChannelRoomSummary]:
    """List active channels that the admin UI can manage."""
    member_count_expr = func.count(ChatMember.id).filter(
        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
    )
    stmt = (
        select(Chat, member_count_expr.label("member_count"))
        .outerjoin(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            Chat.type == ChatType.CHANNEL,
            Chat.is_deleted.is_(False),
        )
        .group_by(Chat.id)
        .order_by(
            Chat.is_mandatory.desc(),
            Chat.is_system.desc(),
            Chat.created_at.desc(),
            Chat.id.desc(),
        )
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
                avatar_file_id=getattr(chat, "avatar_file_id", None),
                created_by_id=chat.created_by_id,
                is_system=chat.is_system,
                is_mandatory=chat.is_mandatory,
                member_count=int(member_count or 0),
                created_at=chat.created_at,
            )
        )
    return summaries


async def update_optional_channel(
    db: AsyncSession,
    *,
    chat: Chat,
    title: str,
    description: str | None = None,
    avatar_file_id: str | None = None,
) -> Chat:
    return await update_manageable_channel_metadata(
        db,
        chat=chat,
        title=title,
        description=description,
        avatar_file_id=avatar_file_id,
    )


async def list_optional_channels(db: AsyncSession) -> list[ChannelRoomSummary]:
    return await list_manageable_channels(db)


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
                avatar_file_id=getattr(chat, "avatar_file_id", None),
                created_by_id=chat.created_by_id,
                member_count=int(active_member_count or 0),
                max_members=int(chat.max_members or GROUP_MAX_MEMBERS),
                created_at=chat.created_at,
                current_user_role=member_role,
            )
        )
    return items


async def list_group_conversations(
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
    unread_mention_count = (
        select(func.count(Message.id))
        .where(
            Message.chat_id == Chat.id,
            Message.sender_id != current_user_id,
            Message.is_deleted.is_(False),
            Message.id > func.coalesce(current_member.last_read_message_id, 0),
            or_(
                Message.mention_all.is_(True),
                text("messages.mentions::jsonb @> CAST(:user_id_str AS jsonb)").bindparams(user_id_str=str(current_user_id))
            )
        )
        .correlate(Chat, current_member)
        .scalar_subquery()
    )
    manual_unread_count = case(
        (
            (current_member.is_marked_unread.is_(True)) & (Chat.last_message_id.is_not(None)),
            1,
        ),
        else_=0,
    )
    active_member_count = (
        select(func.count(ChatMember.id))
        .where(
            ChatMember.chat_id == Chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .correlate(Chat)
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
            current_member.is_muted.label("is_muted"),
            current_member.is_pinned.label("is_pinned"),
            current_member.pinned_at.label("pinned_at"),
            current_member.pin_order.label("pin_order"),
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            func.greatest(func.coalesce(unread_count, 0), manual_unread_count).label("unread_count"),
            active_member_count.label("member_count"),
            func.coalesce(unread_mention_count, 0).label("unread_mention_count"),
        )
        .join(
            current_member,
            (current_member.chat_id == Chat.id)
            & (current_member.user_id == current_user_id)
            & (current_member.membership_status == ChatMembershipStatus.ACTIVE),
        )
        .outerjoin(last_message_alias, last_message_alias.id == Chat.last_message_id)
        .where(
            Chat.type == ChatType.GROUP,
            Chat.is_deleted.is_(False),
        )
        .order_by(Chat.last_message_at.desc().nullslast(), Chat.id.desc())
    )
    result = await db.execute(stmt)

    rows: list[ChannelConversationSummary] = []
    for row in result.all():
        chat, member_role, is_muted, is_pinned, pinned_at, pin_order, last_content, last_type, unread, member_count, unread_mentions = _unpack_conversation_projection_row(row)
        synthetic_room_id = -int(chat.id)
        rows.append(
            ChannelConversationSummary(
                id=synthetic_room_id,
                other_user_id=synthetic_room_id,
                other_user_name=chat.title or f"گروه {chat.id}",
                avatar_file_id=getattr(chat, "avatar_file_id", None),
                other_user_is_deleted=False,
                last_message_content=last_content,
                last_message_type=last_type,
                last_message_at=chat.last_message_at,
                unread_count=int(unread or 0),
                other_user_last_seen_at=None,
                room_kind="group",
                chat_id=chat.id,
                can_send=True,
                member_role=member_role.value if member_role is not None else None,
                member_count=int(member_count or 0),
                max_members=int(chat.max_members or GROUP_MAX_MEMBERS),
                is_system=bool(chat.is_system),
                is_mandatory=bool(chat.is_mandatory),
                is_muted=bool(is_muted),
                is_pinned=bool(is_pinned),
                pinned_at=pinned_at,
                pin_order=pin_order,
                unread_mention_count=int(unread_mentions or 0),
            )
        )
    return rows


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
                avatar_file_id=getattr(user, "avatar_file_id", None),
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

    active_member_user_ids = await list_active_room_member_user_ids(db, chat_id=chat.id)
    await _validate_customer_group_membership_context(db, [*active_member_user_ids, user_id])

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
    member.is_pinned = False
    member.pinned_at = None
    member.pin_order = None
    member.is_hidden = False
    member.hidden_at = None
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
    member.is_pinned = False
    member.pinned_at = None
    member.pin_order = None
    member.is_hidden = False
    member.hidden_at = None
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
    unread_mention_count = (
        select(func.count(Message.id))
        .where(
            Message.chat_id == Chat.id,
            Message.sender_id != current_user_id,
            Message.is_deleted.is_(False),
            Message.id > func.coalesce(current_member.last_read_message_id, 0),
            or_(
                Message.mention_all.is_(True),
                text("messages.mentions::jsonb @> CAST(:user_id_str AS jsonb)").bindparams(user_id_str=str(current_user_id))
            )
        )
        .correlate(Chat, current_member)
        .scalar_subquery()
    )
    manual_unread_count = case(
        (
            (current_member.is_marked_unread.is_(True)) & (Chat.last_message_id.is_not(None)),
            1,
        ),
        else_=0,
    )
    active_member_count = (
        select(func.count(ChatMember.id))
        .where(
            ChatMember.chat_id == Chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .correlate(Chat)
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
            current_member.is_muted.label("is_muted"),
            current_member.is_pinned.label("is_pinned"),
            current_member.pinned_at.label("pinned_at"),
            current_member.pin_order.label("pin_order"),
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            func.greatest(func.coalesce(unread_count, 0), manual_unread_count).label("unread_count"),
            active_member_count.label("member_count"),
            func.coalesce(unread_mention_count, 0).label("unread_mention_count"),
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
    for row in result.all():
        chat, member_role, is_muted, is_pinned, pinned_at, pin_order, last_content, last_type, unread, member_count, unread_mentions = _unpack_conversation_projection_row(row)
        synthetic_room_id = -int(chat.id)
        rows.append(
            ChannelConversationSummary(
                id=synthetic_room_id,
                other_user_id=synthetic_room_id,
                other_user_name=chat.title or f"کانال {chat.id}",
                avatar_file_id=getattr(chat, "avatar_file_id", None),
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
                member_count=int(member_count or 0),
                max_members=None,
                is_system=bool(chat.is_system),
                is_mandatory=bool(chat.is_mandatory),
                is_muted=bool(is_muted),
                is_pinned=bool(is_pinned),
                pinned_at=pinned_at,
                pin_order=pin_order,
                unread_mention_count=int(unread_mentions or 0),
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
                avatar_file_id=getattr(user, "avatar_file_id", None),
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


def build_room_activity_event_payload(
    *,
    chat: Chat,
    sender_id: int,
    sender_name: str | None,
    activity: str,
    active: bool,
) -> dict[str, object]:
    room_kind = "group" if chat.type == ChatType.GROUP else "channel"
    payload: dict[str, object] = {
        "room_kind": room_kind,
        "chat_id": chat.id,
        "sender_id": sender_id,
        "activity": activity,
        "active": active,
        "conversation_id": -int(chat.id),
        "conversation_other_user_id": -int(chat.id),
        "conversation_title": chat.title or (f"گروه {chat.id}" if room_kind == "group" else f"کانال {chat.id}"),
    }
    if sender_name is not None:
        payload["sender_name"] = sender_name
    return payload


async def publish_room_activity_event(
    *,
    chat: Chat,
    sender_id: int,
    sender_name: str | None,
    member_user_ids: list[int],
    activity: str,
    active: bool,
    publisher: RoomEventPublisher,
) -> None:
    payload = build_room_activity_event_payload(
        chat=chat,
        sender_id=sender_id,
        sender_name=sender_name,
        activity=activity,
        active=active,
    )
    for user_id in member_user_ids:
        if user_id == sender_id:
            continue
        await publisher(user_id, "chat:activity", payload)


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
    active_customer_user_ids = select(CustomerRelation.customer_user_id).where(
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.customer_user_id.is_not(None),
    )
    active_total_stmt = select(func.count(User.id)).where(
        User.is_deleted.is_(False),
        ~User.id.in_(active_customer_user_ids),
    )
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

    filters = [
        User.is_deleted.is_(False),
        ~User.id.in_(active_customer_user_ids),
    ]
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
                avatar_file_id=getattr(user, "avatar_file_id", None),
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
        active_customer_user_ids = select(CustomerRelation.customer_user_id).where(
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.customer_user_id.is_not(None),
        )
        target_stmt = select(User.id).where(
            User.is_deleted.is_(False),
            ~User.id.in_(active_customer_user_ids),
        ).order_by(User.id.asc())
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

        customer_user_ids = await _load_active_customer_user_ids(db, target_user_ids)
        if customer_user_ids:
            raise HTTPException(
                status_code=400,
                detail="Customer users cannot join channels in this phase",
            )

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


async def list_group_member_candidates(
    db: AsyncSession,
    *,
    current_user: User,
    query_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
    exclude_chat_id: int | None = None,
    selected_user_ids: Sequence[object] | None = None,
) -> ChannelInviteCandidatePage:
    """List eligible project users for group member selection under customer membership rules."""
    normalized_query = _clean_text(query_text)
    normalized_selected_user_ids = _normalize_positive_user_ids(selected_user_ids or [])

    excluded_member_user_ids: set[int] = set()
    if exclude_chat_id is not None:
        excluded_member_user_ids = set(await list_active_room_member_user_ids(db, chat_id=exclude_chat_id))

    filters = [
        User.is_deleted.is_(False),
        User.id != current_user.id,
    ]
    if excluded_member_user_ids:
        filters.append(~User.id.in_(excluded_member_user_ids))
    if normalized_query:
        search_pattern = f"%{normalized_query}%"
        filters.append(
            or_(
                User.full_name.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern),
            )
        )

    result = await db.execute(select(User).where(*filters).order_by(User.id.desc()))
    users = list(result.scalars().all())
    context_user_ids = _normalize_positive_user_ids([
        current_user.id,
        *excluded_member_user_ids,
        *normalized_selected_user_ids,
    ])
    scoped_user_ids = _normalize_positive_user_ids([
        *context_user_ids,
        *(user.id for user in users),
    ])
    customer_owner_by_user_id = await _load_active_customer_owner_map(db, scoped_user_ids)
    accountant_owner_by_user_id = await _load_active_accountant_owner_map(db, scoped_user_ids)
    selected_user_id_set = set(normalized_selected_user_ids)

    filtered_users = [
        user
        for user in users
        if user.id in selected_user_id_set
        or _is_group_candidate_allowed(
            candidate_user_id=user.id,
            context_user_ids=context_user_ids,
            customer_owner_by_user_id=customer_owner_by_user_id,
            accountant_owner_by_user_id=accountant_owner_by_user_id,
        )
    ]

    paged_users = filtered_users[offset: offset + limit]
    total = len(filtered_users)
    return ChannelInviteCandidatePage(
        items=[
            ChannelInviteCandidate(
                user_id=user.id,
                account_name=user.account_name,
                full_name=user.full_name,
                mobile_number=user.mobile_number,
                avatar_file_id=getattr(user, "avatar_file_id", None),
                is_already_member=False,
            )
            for user in paged_users
        ],
        total=total,
        active_total=total,
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
        member.is_pinned = False
        member.pinned_at = None
        member.pin_order = None
        member.is_hidden = False
        member.hidden_at = None
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


async def delete_optional_channel(
    db: AsyncSession,
    *,
    chat: Chat,
    actor_user_id: int,
) -> ChannelMemberMutationSummary:
    if chat.is_system or chat.is_mandatory:
        raise HTTPException(status_code=400, detail="Mandatory/system channels cannot be deleted here")

    now = _utcnow()
    members_result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat.id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
    )
    active_members = members_result.scalars().all()

    for member in active_members:
        member.membership_status = ChatMembershipStatus.REMOVED
        member.left_at = now
        member.is_pinned = False
        member.pinned_at = None
        member.pin_order = None
        member.is_hidden = False
        member.hidden_at = None
        member.updated_at = now

    chat.is_deleted = True
    chat.deleted_at = now
    chat.updated_at = now
    await db.commit()

    return ChannelMemberMutationSummary(
        chat_id=chat.id,
        user_id=actor_user_id,
        role=None,
        removed=True,
        member_count=0,
        left=False,
    )


async def leave_channel_chat(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
) -> ChannelMemberMutationSummary:
    if chat.is_system or chat.is_mandatory:
        raise HTTPException(status_code=400, detail="Mandatory/system channels cannot be unfollowed")

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

    is_admin_leave = member.role == ChatMemberRole.ADMIN
    if member.user_id == chat.created_by_id and is_admin_leave:
        return await delete_optional_channel(db, chat=chat, actor_user_id=user_id)

    if is_admin_leave:
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
    member.membership_status = ChatMembershipStatus.LEFT
    member.left_at = now
    member.is_pinned = False
    member.pinned_at = None
    member.pin_order = None
    member.is_hidden = False
    member.hidden_at = None
    member.updated_at = now
    chat.updated_at = now
    await db.commit()

    member_count = await count_active_chat_members(db, chat.id)
    return ChannelMemberMutationSummary(
        chat_id=chat.id,
        user_id=user_id,
        role=None,
        removed=False,
        member_count=member_count,
        left=True,
    )


async def set_room_pin_state(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
    pinned: bool,
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
        raise HTTPException(status_code=403, detail="Room membership not found")

    now = _utcnow()
    member.is_pinned = pinned
    member.pinned_at = now if pinned else None
    if pinned:
        if member.pin_order is None:
            try:
                member.pin_order = await get_next_chat_member_pin_order(db, user_id=user_id)
            except AssertionError:
                member.pin_order = 1
        member.is_hidden = False
        member.hidden_at = None
    else:
        member.pin_order = None
    member.updated_at = now
    chat.updated_at = now
    await db.commit()
    return member


async def send_channel_message(
    db: AsyncSession,
    *,
    chat: Chat,
    sender: User,
    content: str,
    message_type: MessageType,
    reply_to_message_id: int | None = None,
    forwarded_from_id: int | None = None,
    mentions: list[int] | None = None,
    mention_all: bool = False,
) -> Message:
    member = await get_active_channel_member_or_403(db, chat=chat, user_id=sender.id)
    if member.role != ChatMemberRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only channel admins can post messages")

    if reply_to_message_id is not None:
        reply_to_message = await db.get(Message, reply_to_message_id)
        if reply_to_message is None or reply_to_message.chat_id != chat.id:
            raise HTTPException(status_code=400, detail="Reply target is not part of this channel")

    resolved_mentions, resolved_mention_all = await _resolve_room_message_mentions(
        db,
        chat=chat,
        content=content,
        message_type=message_type,
        mentions=mentions,
        mention_all=mention_all,
    )

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
        mentions=resolved_mentions,
        mention_all=resolved_mention_all,
    )
    db.add(message)
    await db.flush()

    chat.last_message_id = message.id
    chat.last_message_at = now
    chat.updated_at = now
    member.last_read_message_id = message.id
    member.last_read_at = now
    member.is_marked_unread = False
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
    mentions: list[int] | None = None,
    mention_all: bool = False,
) -> Message:
    member = await get_active_group_member_or_403(db, chat=chat, user_id=sender.id)

    if reply_to_message_id is not None:
        reply_to_message = await db.get(Message, reply_to_message_id)
        if reply_to_message is None or reply_to_message.chat_id != chat.id:
            raise HTTPException(status_code=400, detail="Reply target is not part of this group")

    resolved_mentions, resolved_mention_all = await _resolve_room_message_mentions(
        db,
        chat=chat,
        content=content,
        message_type=message_type,
        mentions=mentions,
        mention_all=mention_all,
    )

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
        mentions=resolved_mentions,
        mention_all=resolved_mention_all,
    )
    db.add(message)
    await db.flush()

    chat.last_message_id = message.id
    chat.last_message_at = now
    chat.updated_at = now
    member.last_read_message_id = message.id
    member.last_read_at = now
    member.is_marked_unread = False
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
    member.is_marked_unread = False
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
    member.is_marked_unread = False
    member.updated_at = now
    await db.commit()


async def set_room_mute_state(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
    muted: bool,
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
        raise HTTPException(status_code=403, detail="Room membership not found")

    now = _utcnow()
    member.is_muted = muted
    member.updated_at = now
    chat.updated_at = now
    await db.commit()
    return member


async def set_room_mark_unread_state(
    db: AsyncSession,
    *,
    chat: Chat,
    user_id: int,
    unread: bool,
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
        raise HTTPException(status_code=403, detail="Room membership not found")

    if unread:
        latest_message_id_result = await db.execute(
            select(func.max(Message.id)).where(
                Message.chat_id == chat.id,
                Message.is_deleted.is_(False),
            )
        )
        latest_message_id = latest_message_id_result.scalar_one_or_none()
        if latest_message_id is None:
            raise HTTPException(status_code=400, detail="Conversation has no messages to mark unread")

    now = _utcnow()
    member.is_marked_unread = unread
    member.updated_at = now
    chat.updated_at = now
    await db.commit()
    return member


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