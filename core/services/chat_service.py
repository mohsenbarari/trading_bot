"""Direct-chat service helpers for the legacy Conversation-based messenger flow."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Collection, TypeVar

import aiofiles
import httpx
import sqlalchemy as sa
from fastapi.encoders import jsonable_encoder
from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType
from core.services.accountant_relation_service import get_active_accountant_relation_for_accountant
from core.services.customer_relation_service import (
    build_allowed_customer_chat_targets,
    get_active_customer_relation_for_customer,
)
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.chat import Chat
from models.chat_file import ChatFile
from models.chat_member import ChatMember
from models.conversation import Conversation
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.message import Message
from models.user import User, UserRole


SerializedMessageT = TypeVar("SerializedMessageT")
DirectEventPublisher = Callable[[int, str, dict[str, object]], Awaitable[object]]


logger = logging.getLogger(__name__)


def build_direct_conversation_other_user_id_expr(current_user_id: int):
    """Return the counterparty user-id expression for a legacy direct row."""
    return case(
        (Conversation.user1_id == current_user_id, Conversation.user2_id),
        else_=Conversation.user1_id,
    )


async def _ensure_direct_chat_initiation_allowed(
    db: AsyncSession,
    *,
    sender: User,
    receiver_id: int,
) -> None:
    accountant_relation = await get_active_accountant_relation_for_accountant(db, sender.id)
    customer_permission = await _resolve_customer_direct_chat_initiation_permission(
        db,
        sender=sender,
        receiver_id=receiver_id,
        accountant_relation=accountant_relation,
    )
    if customer_permission is True:
        return

    if customer_permission is False:
        raise HTTPException(
            status_code=403,
            detail="کاربر مشتری در این فاز اجازه شروع گفتگوی مستقیم با این کاربر را ندارد",
        )


async def _resolve_customer_direct_chat_initiation_permission(
    db: AsyncSession,
    *,
    sender: User,
    receiver_id: int,
    accountant_relation: AccountantRelation | None,
) -> bool | None:
    sender_customer_relation = await get_active_customer_relation_for_customer(db, sender.id)
    receiver_customer_relation = await get_active_customer_relation_for_customer(db, receiver_id)

    if sender_customer_relation is None and receiver_customer_relation is None:
        return None

    if sender_customer_relation is not None:
        allowed_target_ids = await build_allowed_customer_chat_targets(db, sender.id)
        return receiver_id in allowed_target_ids

    if receiver_customer_relation is None:
        return None

    if sender.id == receiver_customer_relation.owner_user_id:
        return True
    if accountant_relation is not None and accountant_relation.owner_user_id == receiver_customer_relation.owner_user_id:
        return True
    return False

COMMON_MESSAGE_REACTIONS = (
    "👍",
    "👎",
    "❤️",
    "🔥",
    "😂",
    "😮",
    "😢",
    "🙏",
    "👏",
    "😁",
    "🤔",
    "🤯",
    "😡",
    "🎉",
    "💯",
    "👌",
    "😍",
    "🥰",
    "🤝",
    "🤩",
    "👀",
    "💔",
    "🤣",
    "🫡",
)
COMMON_MESSAGE_REACTION_SET = set(COMMON_MESSAGE_REACTIONS)
COMMON_MESSAGE_REACTION_ORDER = {
    emoji: index for index, emoji in enumerate(COMMON_MESSAGE_REACTIONS)
}


def normalize_message_reactions(raw_reactions: object) -> list[dict[str, object]]:
    """Normalize one raw reaction payload into the stable direct-chat schema format."""
    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()

    if not isinstance(raw_reactions, list):
        return normalized

    for reaction in raw_reactions:
        if not isinstance(reaction, dict):
            continue

        emoji = reaction.get("emoji")
        if emoji not in COMMON_MESSAGE_REACTION_SET:
            continue

        try:
            user_id = int(reaction.get("user_id"))
        except (TypeError, ValueError):
            continue

        if user_id <= 0:
            continue

        reaction_key = (emoji, user_id)
        if reaction_key in seen:
            continue

        seen.add(reaction_key)
        normalized.append({"emoji": emoji, "user_id": user_id})

    normalized.sort(
        key=lambda item: (
            COMMON_MESSAGE_REACTION_ORDER.get(str(item["emoji"]), 999),
            int(item["user_id"]),
        )
    )
    return normalized


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
    user1_relation_alias = aliased(AccountantRelation)
    user2_relation_alias = aliased(AccountantRelation)
    user1_customer_relation_alias = aliased(CustomerRelation)
    user2_customer_relation_alias = aliased(CustomerRelation)
    user1_owner_alias = aliased(User)
    user2_owner_alias = aliased(User)
    last_message_alias = aliased(Message)
    current_direct_member_alias = aliased(ChatMember)
    other_direct_member_alias = aliased(ChatMember)
    current_member_alias = aliased(ChatMember)
    direct_chat_lookup = (
        select(
            Chat.id.label("chat_id"),
            func.least(
                current_direct_member_alias.user_id,
                other_direct_member_alias.user_id,
            ).label("user1_id"),
            func.greatest(
                current_direct_member_alias.user_id,
                other_direct_member_alias.user_id,
            ).label("user2_id"),
            Chat.last_message_id.label("chat_last_message_id"),
            Chat.last_message_at.label("chat_last_message_at"),
        )
        .join(
            current_direct_member_alias,
            sa.and_(
                current_direct_member_alias.chat_id == Chat.id,
                current_direct_member_alias.user_id == current_user_id,
                current_direct_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .join(
            other_direct_member_alias,
            sa.and_(
                other_direct_member_alias.chat_id == Chat.id,
                other_direct_member_alias.user_id != current_user_id,
                other_direct_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .where(Chat.type == ChatType.DIRECT, Chat.is_deleted.is_(False))
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
    other_user_id = build_direct_conversation_other_user_id_expr(current_user_id).label("other_user_id")
    user1_relation_display_name = func.nullif(func.btrim(user1_relation_alias.relation_display_name), "")
    user2_relation_display_name = func.nullif(func.btrim(user2_relation_alias.relation_display_name), "")
    user1_customer_management_name = func.nullif(func.btrim(user1_customer_relation_alias.management_name), "")
    user2_customer_management_name = func.nullif(func.btrim(user2_customer_relation_alias.management_name), "")
    user1_display_name = func.coalesce(
        user1_relation_display_name,
        user1_customer_management_name,
        user1_owner_alias.account_name,
        user1_alias.account_name,
    )
    user2_display_name = func.coalesce(
        user2_relation_display_name,
        user2_customer_management_name,
        user2_owner_alias.account_name,
        user2_alias.account_name,
    )
    other_user_name = case(
        (Conversation.user1_id == current_user_id, user2_display_name),
        else_=user1_display_name,
    ).label("other_user_name")
    other_user_profile_user_id = case(
        (Conversation.user1_id == current_user_id, func.coalesce(user2_owner_alias.id, user2_alias.id)),
        else_=func.coalesce(user1_owner_alias.id, user1_alias.id),
    ).label("profile_user_id")
    other_user_profile_account_name = case(
        (Conversation.user1_id == current_user_id, func.coalesce(user2_owner_alias.account_name, user2_alias.account_name)),
        else_=func.coalesce(user1_owner_alias.account_name, user1_alias.account_name),
    ).label("profile_account_name")
    resolved_from_accountant_id = case(
        (Conversation.user1_id == current_user_id, user2_relation_alias.accountant_user_id),
        else_=user1_relation_alias.accountant_user_id,
    ).label("resolved_from_accountant_id")
    highlight_accountant_user_id = case(
        (Conversation.user1_id == current_user_id, user2_relation_alias.accountant_user_id),
        else_=user1_relation_alias.accountant_user_id,
    ).label("highlight_accountant_user_id")
    highlight_accountant_relation_display_name = case(
        (Conversation.user1_id == current_user_id, user2_relation_alias.relation_display_name),
        else_=user1_relation_alias.relation_display_name,
    ).label("highlight_accountant_relation_display_name")
    user1_chat_role_kind = case(
        (user1_relation_alias.accountant_user_id.is_not(None), "accountant"),
        (user1_customer_relation_alias.customer_user_id.is_not(None), "customer"),
        else_="colleague",
    )
    user2_chat_role_kind = case(
        (user2_relation_alias.accountant_user_id.is_not(None), "accountant"),
        (user2_customer_relation_alias.customer_user_id.is_not(None), "customer"),
        else_="colleague",
    )
    user1_chat_role_label = case(
        (user1_relation_alias.accountant_user_id.is_not(None), "حسابدار"),
        (user1_customer_relation_alias.customer_user_id.is_not(None), "مشتری"),
        else_="همکار",
    )
    user2_chat_role_label = case(
        (user2_relation_alias.accountant_user_id.is_not(None), "حسابدار"),
        (user2_customer_relation_alias.customer_user_id.is_not(None), "مشتری"),
        else_="همکار",
    )
    other_user_chat_role_kind = case(
        (Conversation.user1_id == current_user_id, user2_chat_role_kind),
        else_=user1_chat_role_kind,
    ).label("chat_role_kind")
    other_user_chat_role_label = case(
        (Conversation.user1_id == current_user_id, user2_chat_role_label),
        else_=user1_chat_role_label,
    ).label("chat_role_label")
    user1_accountant_owner_name = case(
        (user1_relation_alias.accountant_user_id.is_not(None), user1_owner_alias.account_name),
        else_=None,
    )
    user2_accountant_owner_name = case(
        (user2_relation_alias.accountant_user_id.is_not(None), user2_owner_alias.account_name),
        else_=None,
    )
    other_user_accountant_owner_name = case(
        (Conversation.user1_id == current_user_id, user2_accountant_owner_name),
        else_=user1_accountant_owner_name,
    ).label("chat_accountant_owner_name")
    user1_accountant_owner_label = case(
        (user1_relation_alias.accountant_user_id.is_not(None), sa.literal("سرگروه: ") + user1_owner_alias.account_name),
        else_=None,
    )
    user2_accountant_owner_label = case(
        (user2_relation_alias.accountant_user_id.is_not(None), sa.literal("سرگروه: ") + user2_owner_alias.account_name),
        else_=None,
    )
    other_user_accountant_owner_label = case(
        (Conversation.user1_id == current_user_id, user2_accountant_owner_label),
        else_=user1_accountant_owner_label,
    ).label("chat_accountant_owner_label")
    other_user_avatar_file_id = case(
        (Conversation.user1_id == current_user_id, user2_alias.avatar_file_id),
        else_=user1_alias.avatar_file_id,
    ).label("avatar_file_id")
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
            Message.id > func.coalesce(current_member_alias.last_read_message_id, 0),
        )
        .correlate(direct_chat_lookup, current_member_alias)
        .scalar_subquery()
    )
    computed_unread_count = case(
        (
            sa.and_(
                direct_chat_lookup.c.chat_id.is_not(None),
                current_member_alias.last_read_message_id.is_not(None),
            ),
            generic_unread_count,
        ),
        else_=legacy_unread_count,
    )
    manual_unread_count = case(
        (
            sa.and_(
                current_member_alias.is_marked_unread.is_(True),
                resolved_last_message_id.is_not(None),
            ),
            1,
        ),
        else_=0,
    )
    unread_count = func.greatest(
        func.coalesce(computed_unread_count, 0),
        manual_unread_count,
    ).label("unread_count")
    is_muted = func.coalesce(current_member_alias.is_muted, False).label("is_muted")
    is_pinned = func.coalesce(current_member_alias.is_pinned, False).label("is_pinned")
    pinned_at = current_member_alias.pinned_at.label("pinned_at")
    pin_order = current_member_alias.pin_order.label("pin_order")
    hidden_flag = case(
        (current_member_alias.is_hidden.is_(True), 1),
        else_=0,
    ).label("is_hidden_flag")
    last_message_content = case(
        (last_message_alias.is_deleted.is_(True), "پیام حذف شد"),
        (last_message_alias.message_type == MessageType.TEXT, last_message_alias.content),
        (last_message_alias.message_type.in_((MessageType.IMAGE, MessageType.VIDEO)), last_message_alias.content),
        else_=None,
    ).label("last_message_content")

    stmt = (
        select(
            Conversation.id.label("id"),
            direct_chat_lookup.c.chat_id.label("chat_id"),
            other_user_id,
            other_user_name,
            other_user_avatar_file_id,
            other_user_profile_user_id,
            other_user_profile_account_name,
            resolved_from_accountant_id,
            other_user_chat_role_kind,
            other_user_chat_role_label,
            other_user_accountant_owner_name,
            other_user_accountant_owner_label,
            highlight_accountant_user_id,
            highlight_accountant_relation_display_name,
            other_user_is_deleted,
            last_message_content,
            last_message_alias.message_type.label("last_message_type"),
            resolved_last_message_at,
            unread_count,
            other_user_last_seen_at,
            is_muted,
            is_pinned,
            pinned_at,
            pin_order,
        )
        .select_from(Conversation)
        .join(user1_alias, Conversation.user1_id == user1_alias.id)
        .join(user2_alias, Conversation.user2_id == user2_alias.id)
        .outerjoin(
            user1_relation_alias,
            sa.and_(
                user1_relation_alias.accountant_user_id == user1_alias.id,
                user1_relation_alias.status == AccountantRelationStatus.ACTIVE,
                user1_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user2_relation_alias,
            sa.and_(
                user2_relation_alias.accountant_user_id == user2_alias.id,
                user2_relation_alias.status == AccountantRelationStatus.ACTIVE,
                user2_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user1_customer_relation_alias,
            sa.and_(
                user1_customer_relation_alias.customer_user_id == user1_alias.id,
                user1_customer_relation_alias.status == CustomerRelationStatus.ACTIVE,
                user1_customer_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user2_customer_relation_alias,
            sa.and_(
                user2_customer_relation_alias.customer_user_id == user2_alias.id,
                user2_customer_relation_alias.status == CustomerRelationStatus.ACTIVE,
                user2_customer_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user1_owner_alias,
            sa.and_(
                user1_owner_alias.id == user1_relation_alias.owner_user_id,
                user1_owner_alias.is_deleted.is_(False),
            ),
        )
        .outerjoin(
            user2_owner_alias,
            sa.and_(
                user2_owner_alias.id == user2_relation_alias.owner_user_id,
                user2_owner_alias.is_deleted.is_(False),
            ),
        )
        .outerjoin(
            direct_chat_lookup,
            sa.and_(
                direct_chat_lookup.c.user1_id == Conversation.user1_id,
                direct_chat_lookup.c.user2_id == Conversation.user2_id,
            ),
        )
        .outerjoin(
            current_member_alias,
            sa.and_(
                current_member_alias.chat_id == direct_chat_lookup.c.chat_id,
                current_member_alias.user_id == current_user_id,
                current_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .outerjoin(last_message_alias, last_message_alias.id == resolved_last_message_id)
    )
    return stmt, unread_count, resolved_last_message_at, hidden_flag


def build_direct_conversation_scope_condition(current_user_id: int):
    """Restrict direct conversation projections to rows owned by the current user."""
    return sa.or_(
        Conversation.user1_id == current_user_id,
        Conversation.user2_id == current_user_id,
    )


def build_direct_conversation_list_stmt(
    current_user_id: int,
    *,
    allowed_target_ids: Collection[int] | None = None,
):
    """Build the ordered direct-conversation list query for the current user."""
    stmt, _, conversation_order_at, hidden_flag = build_direct_conversation_projection_stmt(current_user_id)
    if allowed_target_ids is not None:
        target_ids = tuple(sorted({int(item) for item in allowed_target_ids}))
        if not target_ids:
            stmt = stmt.where(sa.false())
        else:
            stmt = stmt.where(build_direct_conversation_other_user_id_expr(current_user_id).in_(target_ids))
    return (
        stmt
        .where(build_direct_conversation_scope_condition(current_user_id))
        .where(func.coalesce(hidden_flag, 0) == 0)
        .order_by(conversation_order_at.desc().nullslast())
    )


def build_direct_unread_poll_stmt(current_user_id: int):
    """Build the unread-only direct conversation poll query for the current user."""
    stmt, unread_count, _, hidden_flag = build_direct_conversation_projection_stmt(current_user_id)
    return (
        stmt
        .where(build_direct_conversation_scope_condition(current_user_id))
        .where(func.coalesce(hidden_flag, 0) == 0)
        .where(func.coalesce(unread_count, 0) > 0)
    )


def build_direct_poll_summary_stmt(current_user_id: int):
    """Build the lightweight direct poll summary query for unread and muted state."""
    user1_alias = aliased(User)
    user2_alias = aliased(User)
    user1_relation_alias = aliased(AccountantRelation)
    user2_relation_alias = aliased(AccountantRelation)
    user1_customer_relation_alias = aliased(CustomerRelation)
    user2_customer_relation_alias = aliased(CustomerRelation)
    user1_owner_alias = aliased(User)
    user2_owner_alias = aliased(User)
    current_direct_member_alias = aliased(ChatMember)
    other_direct_member_alias = aliased(ChatMember)
    current_member_alias = aliased(ChatMember)
    direct_chat_lookup = (
        select(
            Chat.id.label("chat_id"),
            func.least(
                current_direct_member_alias.user_id,
                other_direct_member_alias.user_id,
            ).label("user1_id"),
            func.greatest(
                current_direct_member_alias.user_id,
                other_direct_member_alias.user_id,
            ).label("user2_id"),
            Chat.last_message_id.label("chat_last_message_id"),
        )
        .join(
            current_direct_member_alias,
            sa.and_(
                current_direct_member_alias.chat_id == Chat.id,
                current_direct_member_alias.user_id == current_user_id,
                current_direct_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .join(
            other_direct_member_alias,
            sa.and_(
                other_direct_member_alias.chat_id == Chat.id,
                other_direct_member_alias.user_id != current_user_id,
                other_direct_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .where(Chat.type == ChatType.DIRECT, Chat.is_deleted.is_(False))
        .subquery()
    )
    resolved_last_message_id = func.coalesce(
        direct_chat_lookup.c.chat_last_message_id,
        Conversation.last_message_id,
    )
    other_user_id = case(
        (Conversation.user1_id == current_user_id, Conversation.user2_id),
        else_=Conversation.user1_id,
    ).label("other_user_id")
    user1_relation_display_name = func.nullif(func.btrim(user1_relation_alias.relation_display_name), "")
    user2_relation_display_name = func.nullif(func.btrim(user2_relation_alias.relation_display_name), "")
    user1_customer_management_name = func.nullif(func.btrim(user1_customer_relation_alias.management_name), "")
    user2_customer_management_name = func.nullif(func.btrim(user2_customer_relation_alias.management_name), "")
    user1_display_name = func.coalesce(
        user1_relation_display_name,
        user1_customer_management_name,
        user1_owner_alias.account_name,
        user1_alias.account_name,
    )
    user2_display_name = func.coalesce(
        user2_relation_display_name,
        user2_customer_management_name,
        user2_owner_alias.account_name,
        user2_alias.account_name,
    )
    other_user_name = case(
        (Conversation.user1_id == current_user_id, user2_display_name),
        else_=user1_display_name,
    ).label("other_user_name")
    other_user_is_deleted = case(
        (Conversation.user1_id == current_user_id, user2_alias.is_deleted),
        else_=user1_alias.is_deleted,
    ).label("other_user_is_deleted")
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
            Message.id > func.coalesce(current_member_alias.last_read_message_id, 0),
        )
        .correlate(direct_chat_lookup, current_member_alias)
        .scalar_subquery()
    )
    computed_unread_count = case(
        (
            sa.and_(
                direct_chat_lookup.c.chat_id.is_not(None),
                current_member_alias.last_read_message_id.is_not(None),
            ),
            generic_unread_count,
        ),
        else_=legacy_unread_count,
    )
    manual_unread_count = case(
        (
            sa.and_(
                current_member_alias.is_marked_unread.is_(True),
                resolved_last_message_id.is_not(None),
            ),
            1,
        ),
        else_=0,
    )
    unread_count = func.greatest(
        func.coalesce(computed_unread_count, 0),
        manual_unread_count,
    ).label("unread_count")
    is_muted = func.coalesce(current_member_alias.is_muted, False).label("is_muted")
    hidden_flag = case(
        (current_member_alias.is_hidden.is_(True), 1),
        else_=0,
    )

    return (
        select(
            Conversation.id.label("id"),
            direct_chat_lookup.c.chat_id.label("chat_id"),
            other_user_id,
            other_user_name,
            other_user_is_deleted,
            unread_count,
            is_muted,
            sa.literal(0).label("unread_mention_count"),
        )
        .select_from(Conversation)
        .join(user1_alias, Conversation.user1_id == user1_alias.id)
        .join(user2_alias, Conversation.user2_id == user2_alias.id)
        .outerjoin(
            user1_relation_alias,
            sa.and_(
                user1_relation_alias.accountant_user_id == user1_alias.id,
                user1_relation_alias.status == AccountantRelationStatus.ACTIVE,
                user1_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user2_relation_alias,
            sa.and_(
                user2_relation_alias.accountant_user_id == user2_alias.id,
                user2_relation_alias.status == AccountantRelationStatus.ACTIVE,
                user2_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user1_customer_relation_alias,
            sa.and_(
                user1_customer_relation_alias.customer_user_id == user1_alias.id,
                user1_customer_relation_alias.status == CustomerRelationStatus.ACTIVE,
                user1_customer_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user2_customer_relation_alias,
            sa.and_(
                user2_customer_relation_alias.customer_user_id == user2_alias.id,
                user2_customer_relation_alias.status == CustomerRelationStatus.ACTIVE,
                user2_customer_relation_alias.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            user1_owner_alias,
            sa.and_(
                user1_owner_alias.id == user1_relation_alias.owner_user_id,
                user1_owner_alias.is_deleted.is_(False),
            ),
        )
        .outerjoin(
            user2_owner_alias,
            sa.and_(
                user2_owner_alias.id == user2_relation_alias.owner_user_id,
                user2_owner_alias.is_deleted.is_(False),
            ),
        )
        .outerjoin(
            direct_chat_lookup,
            sa.and_(
                direct_chat_lookup.c.user1_id == Conversation.user1_id,
                direct_chat_lookup.c.user2_id == Conversation.user2_id,
            ),
        )
        .outerjoin(
            current_member_alias,
            sa.and_(
                current_member_alias.chat_id == direct_chat_lookup.c.chat_id,
                current_member_alias.user_id == current_user_id,
                current_member_alias.membership_status == ChatMembershipStatus.ACTIVE,
            ),
        )
        .where(
            build_direct_conversation_scope_condition(current_user_id),
            func.coalesce(hidden_flag, 0) == 0,
            sa.or_(
                func.coalesce(unread_count, 0) > 0,
                is_muted.is_(True),
            ),
        )
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


async def get_existing_direct_chat(
    db: AsyncSession,
    user1_id: int,
    user2_id: int,
) -> Chat | None:
    chat_id = await _find_existing_direct_chat_id(db, user1_id, user2_id)
    if chat_id is None:
        return None
    return await db.get(Chat, chat_id)


async def get_next_chat_member_pin_order(
    db: AsyncSession,
    *,
    user_id: int,
) -> int:
    result = await db.execute(
        select(func.max(ChatMember.pin_order))
        .select_from(ChatMember)
        .join(Chat, Chat.id == ChatMember.chat_id)
        .where(
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
            ChatMember.is_pinned.is_(True),
            Chat.is_deleted.is_(False),
            Chat.is_mandatory.is_(False),
        )
    )
    current_max = result.scalar_one_or_none()
    return int(current_max or 0) + 1


async def reorder_chat_member_pin_order(
    db: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    direction: str,
) -> ChatMember:
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="Direction must be up or down")

    result = await db.execute(
        select(ChatMember, Chat)
        .join(Chat, Chat.id == ChatMember.chat_id)
        .where(
            ChatMember.user_id == user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
            ChatMember.is_pinned.is_(True),
            Chat.is_deleted.is_(False),
            Chat.is_mandatory.is_(False),
        )
        .order_by(
            ChatMember.pin_order.desc().nullslast(),
            ChatMember.pinned_at.desc().nullslast(),
            ChatMember.id.desc(),
        )
    )
    rows = result.all()
    members = [member for member, _chat in rows]
    target_index = next((index for index, member in enumerate(members) if member.chat_id == chat_id), -1)
    if target_index == -1:
        raise HTTPException(status_code=404, detail="Pinned conversation not found")

    if normalized_direction == "up" and target_index > 0:
        members[target_index - 1], members[target_index] = members[target_index], members[target_index - 1]
    elif normalized_direction == "down" and target_index < len(members) - 1:
        members[target_index + 1], members[target_index] = members[target_index], members[target_index + 1]

    now = datetime.now(timezone.utc)
    next_pin_order = len(members)
    for member in members:
        if member.pin_order != next_pin_order:
            member.pin_order = next_pin_order
            member.updated_at = now
        next_pin_order -= 1

    await db.commit()

    target_member = next((member for member in members if member.chat_id == chat_id), None)
    if target_member is None:
        raise HTTPException(status_code=404, detail="Pinned conversation not found")
    return target_member


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
    created_member = False
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
            created_member = True
            continue

        if member.membership_status != expected_status:
            member.membership_status = expected_status
        if not _same_datetime(member.left_at, expected_left_at):
            member.left_at = expected_left_at

    if created_member:
        await db.flush()

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
    for member in members.values():
        if getattr(member, "is_hidden", False):
            member.is_hidden = False
            member.hidden_at = None
            member.updated_at = message.created_at or datetime.now(timezone.utc)

    sender_member = members.get(sender.id)
    if sender_member is not None:
        sender_member.last_read_message_id = message.id
        sender_member.last_read_at = message.created_at or datetime.now(timezone.utc)
        sender_member.is_marked_unread = False

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


async def _get_direct_state_member(
    db: AsyncSession,
    *,
    actor: User,
    other_user_id: int,
) -> tuple[User, ChatMember]:
    other_user = await db.get(User, other_user_id)
    if other_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    direct_chat = await get_or_create_direct_chat(db, actor, other_user)
    members = await _load_direct_chat_members(db, direct_chat.id, actor.id, other_user.id)
    actor_member = members.get(actor.id)
    if actor_member is None:
        direct_chat = await get_or_create_direct_chat(db, actor, other_user)
        members = await _load_direct_chat_members(db, direct_chat.id, actor.id, other_user.id)
        actor_member = members.get(actor.id)

    if actor_member is None:
        raise HTTPException(status_code=500, detail="Failed to update direct conversation state")

    return other_user, actor_member


async def set_direct_chat_pin_state(
    db: AsyncSession,
    *,
    actor: User,
    other_user_id: int,
    pinned: bool,
) -> ChatMember:
    if other_user_id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot pin a conversation with yourself")

    _, actor_member = await _get_direct_state_member(db, actor=actor, other_user_id=other_user_id)

    now = datetime.now(timezone.utc)
    actor_member.is_pinned = pinned
    actor_member.pinned_at = now if pinned else None
    if pinned:
        if actor_member.pin_order is None:
            actor_member.pin_order = await get_next_chat_member_pin_order(db, user_id=actor.id)
        actor_member.is_hidden = False
        actor_member.hidden_at = None
    else:
        actor_member.pin_order = None
    actor_member.updated_at = now
    await db.commit()
    return actor_member


async def hide_direct_conversation(
    db: AsyncSession,
    *,
    actor: User,
    other_user_id: int,
) -> ChatMember:
    if other_user_id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot delete a conversation with yourself")

    _, actor_member = await _get_direct_state_member(db, actor=actor, other_user_id=other_user_id)

    now = datetime.now(timezone.utc)
    actor_member.is_hidden = True
    actor_member.hidden_at = now
    actor_member.is_pinned = False
    actor_member.pinned_at = None
    actor_member.pin_order = None
    actor_member.updated_at = now
    await db.commit()
    return actor_member


async def set_direct_chat_mute_state(
    db: AsyncSession,
    *,
    actor: User,
    other_user_id: int,
    muted: bool,
) -> ChatMember:
    if other_user_id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot mute a conversation with yourself")

    _, actor_member = await _get_direct_state_member(db, actor=actor, other_user_id=other_user_id)

    actor_member.is_muted = muted
    actor_member.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return actor_member


async def set_direct_chat_mark_unread_state(
    db: AsyncSession,
    *,
    actor: User,
    other_user_id: int,
    unread: bool,
) -> ChatMember:
    if other_user_id == actor.id:
        raise HTTPException(status_code=400, detail="Cannot mark your own conversation unread")

    _, actor_member = await _get_direct_state_member(db, actor=actor, other_user_id=other_user_id)

    latest_message = await _get_latest_direct_message(db, actor.id, other_user_id)
    if unread and latest_message is None:
        raise HTTPException(status_code=400, detail="Conversation has no messages to mark unread")

    actor_member.is_marked_unread = unread
    actor_member.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return actor_member


async def sync_direct_read_state(
    db: AsyncSession,
    *,
    reader: User,
    other_user_id: int,
) -> Conversation | None:
    """Sync direct-chat read state across legacy Conversation rows and generic ChatMember cursors."""
    unread_messages = list(
        (
            await db.execute(
                select(Message).where(
                    Message.sender_id == other_user_id,
                    Message.receiver_id == reader.id,
                    Message.is_read.is_(False),
                )
            )
        ).scalars().all()
    )
    for message in unread_messages:
        message.is_read = True

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
        reader_member.is_marked_unread = False

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


async def generate_direct_location_snapshot(
    db: AsyncSession,
    uploader_id: int,
    lat: float,
    lng: float,
) -> str | None:
    """Generate and persist one static location snapshot for a direct location message."""
    try:
        tile_url = f"http://tileserver:8080/styles/basic-preview/static/{lng},{lat},15/600x400.png"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(tile_url)
            if response.status_code != 200:
                logger.warning(
                    f"Tileserver returned {response.status_code} for location snapshot"
                )
                return None

        file_id = str(uuid.uuid4())
        chat_file = ChatFile(
            id=file_id,
            uploader_id=uploader_id,
            s3_key="pending-content-address",
            file_name=f"location_preview_{file_id[:8]}.png",
            mime_type="image/png",
            size=len(response.content),
        )
        db.add(chat_file)
        from core.dr_blob_plane import bind_chat_file_blob

        await bind_chat_file_blob(db, chat_file=chat_file, contents=response.content)
        await db.flush()
        return file_id
    except Exception as exc:
        logger.warning(f"Failed to generate location snapshot: {exc}")
        return None


async def prepare_direct_message_send(
    db: AsyncSession,
    *,
    sender: User,
    receiver_id: int,
    content: str,
    message_type: MessageType,
) -> tuple[User, str]:
    """Validate direct-message preconditions and normalize any send-time payload state."""
    if receiver_id == sender.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")

    receiver = await db.get(User, receiver_id)
    if receiver is None:
        raise HTTPException(status_code=404, detail="Receiver not found")
    if receiver.is_deleted:
        raise HTTPException(status_code=400, detail="امکان ارسال پیام به کاربر غیرفعال وجود ندارد")

    await _ensure_direct_chat_initiation_allowed(
        db,
        sender=sender,
        receiver_id=receiver_id,
    )

    prepared_content = content
    if message_type == MessageType.LOCATION:
        prepared_content = await prepare_direct_location_content(
            db,
            uploader_id=sender.id,
            content=content,
        )

    return receiver, prepared_content


async def prepare_direct_location_content(
    db: AsyncSession,
    *,
    uploader_id: int,
    content: str,
) -> str:
    """Normalize one location payload and attach a snapshot id when available."""
    try:
        location_payload = json.loads(content)
        lat = float(location_payload.get("lat", location_payload.get("latitude")))
        lng = float(location_payload.get("lng", location_payload.get("longitude")))
        location_payload["lat"] = lat
        location_payload["lng"] = lng

        snapshot_id = await generate_direct_location_snapshot(db, uploader_id, lat, lng)
        if snapshot_id:
            location_payload["snapshot_id"] = snapshot_id

        return json.dumps(location_payload)
    except Exception as exc:
        logger.warning(f"Failed to generate location snapshot: {exc}")
        return content


def serialize_direct_message_for_response(
    message: Message,
    *,
    serializer: Callable[[Message], SerializedMessageT],
) -> SerializedMessageT:
    """Serialize one direct message using the API layer's existing schema callback."""
    return serializer(message)


def serialize_direct_messages_for_response(
    messages: list[Message],
    *,
    serializer: Callable[[Message], SerializedMessageT],
) -> list[SerializedMessageT]:
    """Serialize a direct-message collection without duplicating list assembly in the router."""
    return [serializer(message) for message in messages]


def build_direct_message_event_payload(
    message: Message,
    *,
    serializer: Callable[[Message], SerializedMessageT],
    sender_name: str | None = None,
) -> dict[str, object]:
    """Build a realtime-safe payload for one direct message event."""
    payload = jsonable_encoder(serializer(message))
    if sender_name is not None:
        payload["sender_name"] = sender_name
    return payload


async def publish_direct_typing_event(
    *,
    receiver_id: int,
    sender_id: int,
    publisher: DirectEventPublisher,
    sender_name: str | None = None,
) -> None:
    """Publish one direct-chat typing signal if the sender is not targeting themselves."""
    await publish_direct_activity_event(
        receiver_id=receiver_id,
        sender_id=sender_id,
        activity="typing",
        active=True,
        publisher=publisher,
        sender_name=sender_name,
    )


async def publish_direct_activity_event(
    *,
    receiver_id: int,
    sender_id: int,
    activity: str,
    active: bool,
    publisher: DirectEventPublisher,
    sender_name: str | None = None,
) -> None:
    """Publish one direct-chat activity update if the sender is not targeting themselves."""
    if receiver_id == sender_id:
        return

    payload: dict[str, object] = {
        "room_kind": "direct",
        "sender_id": sender_id,
        "activity": activity,
        "active": active,
    }
    if sender_name is not None:
        payload["sender_name"] = sender_name

    await publisher(receiver_id, "chat:activity", payload)


async def publish_direct_message_event(
    *,
    receiver_id: int,
    message: Message,
    serializer: Callable[[Message], SerializedMessageT],
    publisher: DirectEventPublisher,
    sender_name: str | None = None,
) -> None:
    """Publish the standard direct-message delivery event."""
    payload = build_direct_message_event_payload(
        message,
        serializer=serializer,
        sender_name=sender_name,
    )
    await publisher(receiver_id, "chat:message", payload)


async def publish_direct_reaction_event(
    *,
    message: Message,
    serializer: Callable[[Message], SerializedMessageT],
    publisher: DirectEventPublisher,
) -> None:
    """Publish one direct-message reaction update to both conversation participants."""
    payload = build_direct_message_event_payload(message, serializer=serializer)
    await publisher(message.sender_id, "chat:reaction", payload)
    if message.receiver_id != message.sender_id:
        await publisher(message.receiver_id, "chat:reaction", payload)


async def publish_direct_read_event(
    *,
    other_user_id: int,
    reader_id: int,
    publisher: DirectEventPublisher,
) -> None:
    """Publish the standard direct-chat read receipt."""
    await publisher(other_user_id, "chat:read", {"reader_id": reader_id})


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
) -> Message:
    """Toggle one user's reaction state on a direct message."""
    normalized_reactions = normalize_message_reactions(message.reactions)
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
                COMMON_MESSAGE_REACTION_ORDER.get(str(item["emoji"]), 999),
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


async def get_pinned_message_for_chat(
    db: AsyncSession,
    chat: Chat,
) -> Message | None:
    pinned_message_id = getattr(chat, "pinned_message_id", None)
    if not pinned_message_id:
        return None
    message = await reload_direct_message(db, pinned_message_id, include_sender=True)
    if message is None or message.is_deleted:
        return None
    return message


async def get_pinnable_message(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
) -> tuple[Message, Chat]:
    message = await db.get(Message, message_id)
    if message is None or message.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")

    if message.chat_id is None:
        if actor_id not in (message.sender_id, message.receiver_id):
            raise HTTPException(status_code=403, detail="You can only pin messages in your own conversations")
        await ensure_direct_message_chat_link(db, message)

    chat = await db.get(Chat, message.chat_id) if message.chat_id is not None else None
    if chat is None or chat.is_deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.type == ChatType.DIRECT:
        if actor_id not in (message.sender_id, message.receiver_id):
            raise HTTPException(status_code=403, detail="You can only pin messages in your own conversations")
        return message, chat

    member_result = await db.execute(
        select(ChatMember)
        .where(
            ChatMember.chat_id == chat.id,
            ChatMember.user_id == actor_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(ChatMember.id.desc())
    )
    member = member_result.scalars().first()
    if member is None:
        raise HTTPException(status_code=403, detail="Room membership not found")

    if chat.type in (ChatType.GROUP, ChatType.CHANNEL) and member.role != ChatMemberRole.ADMIN:
        raise HTTPException(status_code=403, detail="Only room admins can pin messages")

    return message, chat


async def apply_message_pin_state(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
    pinned: bool,
) -> tuple[Chat, Message | None]:
    message, chat = await get_pinnable_message(
        db,
        message_id=message_id,
        actor_id=actor_id,
    )

    now = datetime.now(timezone.utc)
    if pinned:
        chat.pinned_message_id = message.id
        chat.pinned_message_at = now
        chat.pinned_message_by_id = actor_id
    else:
        if chat.pinned_message_id != message.id:
            raise HTTPException(status_code=400, detail="Only the current pinned message can be unpinned")
        chat.pinned_message_id = None
        chat.pinned_message_at = None
        chat.pinned_message_by_id = None
    chat.updated_at = now
    await db.commit()

    if chat.pinned_message_id is None:
        return chat, None
    return chat, await get_pinned_message_for_chat(db, chat)


async def persist_sent_direct_message(
    db: AsyncSession,
    *,
    sender: User,
    receiver: User,
    content: str,
    message_type: MessageType,
    reply_to_message_id: int | None = None,
    forwarded_from_id: int | None = None,
    forwarded_from_name_override: str | None = None,
) -> Message | None:
    """Persist one new direct message and sync both legacy and generic chat state."""
    message = Message(
        sender_id=sender.id,
        receiver_id=receiver.id,
        content=content,
        message_type=message_type,
        reply_to_message_id=reply_to_message_id,
        forwarded_from_id=forwarded_from_id,
        forwarded_from_name_override=forwarded_from_name_override,
        is_read=False,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    if message.reply_to_message_id or message.forwarded_from_id or message.forwarded_from_name_override:
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
    """Load one message and enforce direct/group/channel reaction preconditions."""
    message = await db.get(Message, message_id)
    if message is None or message.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    if actor_id not in (message.sender_id, message.receiver_id):
        if message.chat_id is not None:
            chat = await db.get(Chat, message.chat_id)
            if chat is not None and chat.type in (ChatType.CHANNEL, ChatType.GROUP) and not chat.is_deleted:
                member_result = await db.execute(
                    select(ChatMember.id).where(
                        ChatMember.chat_id == chat.id,
                        ChatMember.user_id == actor_id,
                        ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
                    )
                )
                if member_result.scalar_one_or_none() is not None:
                    return message
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


async def apply_direct_message_edit(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
    content: str,
) -> Message | None:
    """Apply the full edit workflow for one direct text message."""
    now = datetime.now(timezone.utc)
    message = await get_editable_direct_message(
        db,
        message_id=message_id,
        actor_id=actor_id,
        now=now,
    )
    update_direct_message_content(message, content=content, updated_at=now)
    return await persist_direct_message_change(db, message)


async def apply_direct_message_reaction_toggle(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
    emoji: str,
) -> Message | None:
    """Apply the full reaction-toggle workflow for one direct message."""
    message = await get_reactable_direct_message(
        db,
        message_id=message_id,
        actor_id=actor_id,
    )
    toggle_direct_message_reaction_state(
        message,
        acting_user_id=actor_id,
        emoji=emoji,
    )
    return await persist_direct_message_change(db, message, include_sender=True)


async def apply_direct_message_delete(
    db: AsyncSession,
    *,
    message_id: int,
    actor_id: int,
) -> None:
    """Apply the full soft-delete workflow for one direct message."""
    now = datetime.now(timezone.utc)
    message = await get_deletable_direct_message(
        db,
        message_id=message_id,
        actor_id=actor_id,
        now=now,
    )
    message_chat_id = getattr(message, "chat_id", None)
    if message_chat_id:
        chat = await db.get(Chat, message_chat_id)
        if chat is not None and chat.pinned_message_id == message.id:
            chat.pinned_message_id = None
            chat.pinned_message_at = None
            chat.pinned_message_by_id = None
            chat.updated_at = now
    mark_direct_message_deleted(message, deleted_at=now)
    await persist_direct_message_change(db, message)
