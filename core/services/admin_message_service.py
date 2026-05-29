"""Service helpers for Super Admin market and broadcast management messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType, UserAccountStatus
from core.utils import unique_user_ids
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.admin_message import AdminBroadcastMessage, AdminMarketMessage
from models.chat import Chat
from models.chat_member import ChatMember
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.message import Message
from models.user import User, UserRole


MANAGEMENT_MESSAGE_TITLE = "پیام مدیریت"
MANAGEMENT_BROADCAST_ROOM_DESCRIPTION = "admin_broadcast"

TARGET_USERS = "users"
TARGET_MANAGERS = "managers"
TARGET_ACCOUNTANTS = "accountants"
TARGET_CUSTOMERS = "customers"
SUPPORTED_BROADCAST_TARGET_GROUPS = {
    TARGET_USERS,
    TARGET_MANAGERS,
    TARGET_ACCOUNTANTS,
    TARGET_CUSTOMERS,
}


@dataclass(frozen=True)
class CreatedBroadcastMessage:
    recipient_user_id: int
    chat: Chat
    message: Message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_content(content: str) -> str:
    cleaned = (content or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Message content is required")
    return cleaned


async def _active_accountant_user_ids_stmt():
    return select(AccountantRelation.accountant_user_id).where(
        AccountantRelation.status == AccountantRelationStatus.ACTIVE,
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.accountant_user_id.is_not(None),
    )


async def _active_customer_user_ids_stmt():
    return select(CustomerRelation.customer_user_id).where(
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.customer_user_id.is_not(None),
    )


def _active_user_filters():
    return [
        User.is_deleted.is_(False),
        User.account_status == UserAccountStatus.ACTIVE,
    ]


async def list_market_management_recipient_user_ids(
    db: AsyncSession,
    *,
    exclude_user_ids: Iterable[int] = (),
) -> list[int]:
    """Eligible market-notification recipients: market users except active accountants."""
    active_accountants = await _active_accountant_user_ids_stmt()
    stmt = (
        select(User.id)
        .where(
            *_active_user_filters(),
            ~User.id.in_(active_accountants),
        )
        .order_by(User.id.asc())
    )
    result = await db.execute(stmt)
    excluded = set(unique_user_ids(list(exclude_user_ids)))
    return [user_id for user_id in unique_user_ids(result.scalars().all()) if user_id not in excluded]


async def list_broadcast_recipient_user_ids(
    db: AsyncSession,
    *,
    target_groups: Iterable[str],
    exclude_user_ids: Iterable[int] = (),
) -> list[int]:
    normalized_groups = [str(group).strip() for group in target_groups if str(group).strip()]
    unsupported_groups = sorted(set(normalized_groups) - SUPPORTED_BROADCAST_TARGET_GROUPS)
    if unsupported_groups:
        raise HTTPException(status_code=400, detail=f"Unsupported target group: {unsupported_groups[0]}")
    if not normalized_groups:
        raise HTTPException(status_code=400, detail="At least one target group is required")

    active_accountants = await _active_accountant_user_ids_stmt()
    active_customers = await _active_customer_user_ids_stmt()
    candidate_ids: list[int] = []

    if TARGET_USERS in normalized_groups:
        stmt = (
            select(User.id)
            .where(
                *_active_user_filters(),
                User.role.in_((UserRole.WATCH, UserRole.STANDARD)),
                ~User.id.in_(active_accountants),
                ~User.id.in_(active_customers),
            )
            .order_by(User.id.asc())
        )
        result = await db.execute(stmt)
        candidate_ids.extend(result.scalars().all())

    if TARGET_MANAGERS in normalized_groups:
        stmt = (
            select(User.id)
            .where(
                *_active_user_filters(),
                User.role.in_((UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER)),
            )
            .order_by(User.id.asc())
        )
        result = await db.execute(stmt)
        candidate_ids.extend(result.scalars().all())

    if TARGET_ACCOUNTANTS in normalized_groups:
        stmt = (
            select(User.id)
            .join(AccountantRelation, AccountantRelation.accountant_user_id == User.id)
            .where(
                *_active_user_filters(),
                AccountantRelation.status == AccountantRelationStatus.ACTIVE,
                AccountantRelation.deleted_at.is_(None),
            )
            .order_by(User.id.asc())
        )
        result = await db.execute(stmt)
        candidate_ids.extend(result.scalars().all())

    if TARGET_CUSTOMERS in normalized_groups:
        stmt = (
            select(User.id)
            .join(CustomerRelation, CustomerRelation.customer_user_id == User.id)
            .where(
                *_active_user_filters(),
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                CustomerRelation.deleted_at.is_(None),
            )
            .order_by(User.id.asc())
        )
        result = await db.execute(stmt)
        candidate_ids.extend(result.scalars().all())

    excluded = set(unique_user_ids(list(exclude_user_ids)))
    return [user_id for user_id in unique_user_ids(candidate_ids) if user_id not in excluded]


async def create_market_management_message(
    db: AsyncSession,
    *,
    actor: User,
    content: str,
    reused_from_id: int | None = None,
) -> AdminMarketMessage:
    cleaned_content = _clean_content(content)
    if reused_from_id is not None:
        source = await db.get(AdminMarketMessage, reused_from_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Source market message not found")

    now = _utcnow()
    await db.execute(
        update(AdminMarketMessage)
        .where(AdminMarketMessage.is_active.is_(True))
        .values(is_active=False, updated_at=now)
    )
    message = AdminMarketMessage(
        content=cleaned_content,
        created_by_id=actor.id,
        reused_from_id=reused_from_id,
        is_active=True,
        published_at=now,
        updated_at=now,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def set_market_message_recipient_count(
    db: AsyncSession,
    *,
    message_id: int,
    recipient_count: int,
) -> AdminMarketMessage:
    message = await db.get(AdminMarketMessage, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Market message not found")
    message.notified_recipients_count = max(0, int(recipient_count))
    message.updated_at = _utcnow()
    await db.commit()
    await db.refresh(message)
    return message


async def deactivate_current_market_management_message(db: AsyncSession) -> AdminMarketMessage | None:
    message = await get_current_market_management_message(db)
    if message is None:
        return None
    message.is_active = False
    message.updated_at = _utcnow()
    await db.commit()
    await db.refresh(message)
    return message


async def get_current_market_management_message(db: AsyncSession) -> AdminMarketMessage | None:
    result = await db.execute(
        select(AdminMarketMessage)
        .options(joinedload(AdminMarketMessage.created_by))
        .where(AdminMarketMessage.is_active.is_(True))
        .order_by(AdminMarketMessage.published_at.desc(), AdminMarketMessage.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_market_management_history(db: AsyncSession, *, limit: int = 50) -> list[AdminMarketMessage]:
    result = await db.execute(
        select(AdminMarketMessage)
        .options(joinedload(AdminMarketMessage.created_by))
        .order_by(AdminMarketMessage.published_at.desc(), AdminMarketMessage.id.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    return list(result.scalars().all())


async def get_or_create_management_broadcast_room(
    db: AsyncSession,
    *,
    recipient_user_id: int,
    actor_user_id: int,
) -> Chat:
    result = await db.execute(
        select(Chat)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            Chat.type == ChatType.GROUP,
            Chat.is_system.is_(True),
            Chat.is_mandatory.is_(False),
            Chat.is_deleted.is_(False),
            Chat.description == MANAGEMENT_BROADCAST_ROOM_DESCRIPTION,
            ChatMember.user_id == recipient_user_id,
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(Chat.id.asc())
        .limit(1)
    )
    existing_chat = result.scalar_one_or_none()
    if existing_chat is not None:
        return existing_chat

    now = _utcnow()
    chat = Chat(
        type=ChatType.GROUP,
        title=MANAGEMENT_MESSAGE_TITLE,
        description=MANAGEMENT_BROADCAST_ROOM_DESCRIPTION,
        created_by_id=actor_user_id,
        is_system=True,
        is_mandatory=False,
        max_members=1,
        updated_at=now,
    )
    db.add(chat)
    await db.flush()
    db.add(
        ChatMember(
            chat_id=chat.id,
            user_id=recipient_user_id,
            role=ChatMemberRole.MEMBER,
            membership_status=ChatMembershipStatus.ACTIVE,
            joined_at=now,
        )
    )
    await db.flush()
    return chat


async def create_management_broadcast(
    db: AsyncSession,
    *,
    actor: User,
    content: str,
    target_groups: Iterable[str],
    recipient_user_ids: list[int],
) -> tuple[AdminBroadcastMessage, list[CreatedBroadcastMessage]]:
    cleaned_content = _clean_content(content)
    normalized_groups = [str(group).strip() for group in target_groups if str(group).strip()]
    unsupported_groups = sorted(set(normalized_groups) - SUPPORTED_BROADCAST_TARGET_GROUPS)
    if unsupported_groups:
        raise HTTPException(status_code=400, detail=f"Unsupported target group: {unsupported_groups[0]}")

    recipients = unique_user_ids(recipient_user_ids)
    now = _utcnow()
    broadcast = AdminBroadcastMessage(
        content=cleaned_content,
        created_by_id=actor.id,
        target_groups=normalized_groups,
        recipient_count=len(recipients),
        published_at=now,
    )
    db.add(broadcast)
    await db.flush()

    created_messages: list[CreatedBroadcastMessage] = []
    for recipient_user_id in recipients:
        chat = await get_or_create_management_broadcast_room(
            db,
            recipient_user_id=recipient_user_id,
            actor_user_id=actor.id,
        )
        message = Message(
            chat_id=chat.id,
            sender_id=actor.id,
            receiver_id=recipient_user_id,
            actor_user_id=actor.id,
            content=cleaned_content,
            message_type=MessageType.TEXT,
            is_read=False,
            created_at=now,
        )
        db.add(message)
        await db.flush()
        chat.last_message_id = message.id
        chat.last_message_at = now
        chat.updated_at = now
        created_messages.append(CreatedBroadcastMessage(recipient_user_id=recipient_user_id, chat=chat, message=message))

    await db.commit()
    await db.refresh(broadcast)
    for created in created_messages:
        await db.refresh(created.chat)
        await db.refresh(created.message)
    return broadcast, created_messages


async def list_management_broadcast_history(db: AsyncSession, *, limit: int = 50) -> list[AdminBroadcastMessage]:
    result = await db.execute(
        select(AdminBroadcastMessage)
        .options(joinedload(AdminBroadcastMessage.created_by))
        .order_by(AdminBroadcastMessage.published_at.desc(), AdminBroadcastMessage.id.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    return list(result.scalars().all())