"""Telegram admin broadcast recipient resolution and queue creation."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus
from core.utils import unique_user_ids, utc_now
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import User, UserRole


TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY = "ordinary"
TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS = "managers"
TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS = "tier1_customers"
SUPPORTED_TELEGRAM_ADMIN_BROADCAST_GROUPS = frozenset(
    {
        TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY,
        TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS,
        TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS,
    }
)

TELEGRAM_BROADCAST_TEXT_MAX_LENGTH = 4096
TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP = 500
TELEGRAM_BROADCAST_RECEIPT_INSERT_CHUNK_SIZE = 500

BOT_BROADCAST_ALLOWED_ROLES = (
    UserRole.STANDARD,
    UserRole.POLICE,
    UserRole.MIDDLE_MANAGER,
    UserRole.SUPER_ADMIN,
)


class TelegramAdminBroadcastValidationError(ValueError):
    """Raised when a broadcast request is invalid before queue creation."""


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastRecipient:
    user_id: int
    telegram_id: int
    account_name: str | None = None
    full_name: str | None = None
    username: str | None = None
    mobile_number: str | None = None
    role: str | None = None
    customer_tier: str | None = None
    customer_management_name: str | None = None

    @property
    def display_name(self) -> str:
        return (
            self.customer_management_name
            or self.account_name
            or self.full_name
            or self.username
            or self.mobile_number
            or f"User {self.user_id}"
        )


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastQueueResult:
    broadcast: TelegramAdminBroadcast
    recipients: tuple[TelegramAdminBroadcastRecipient, ...]
    receipt_count: int


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _normalize_audience_type(value: TelegramAdminBroadcastAudienceType | str) -> TelegramAdminBroadcastAudienceType:
    if isinstance(value, TelegramAdminBroadcastAudienceType):
        return value
    normalized = str(value or "").strip()
    try:
        return TelegramAdminBroadcastAudienceType(normalized)
    except ValueError as exc:
        raise TelegramAdminBroadcastValidationError("unsupported_audience_type") from exc


def _normalize_groups(groups: Iterable[str] | None) -> list[str]:
    normalized = [str(group or "").strip() for group in (groups or ()) if str(group or "").strip()]
    unsupported = sorted(set(normalized) - SUPPORTED_TELEGRAM_ADMIN_BROADCAST_GROUPS)
    if unsupported:
        raise TelegramAdminBroadcastValidationError(f"unsupported_group:{unsupported[0]}")
    return normalized


def validate_telegram_admin_broadcast_content(content: str) -> str:
    cleaned = str(content or "").strip()
    if not cleaned:
        raise TelegramAdminBroadcastValidationError("content_required")
    if len(cleaned) > TELEGRAM_BROADCAST_TEXT_MAX_LENGTH:
        raise TelegramAdminBroadcastValidationError("content_too_long")
    return cleaned


def telegram_admin_broadcast_dedupe_key(*, broadcast_id: int, recipient_user_id: int) -> str:
    return f"telegram-admin-broadcast:{int(broadcast_id)}:{int(recipient_user_id)}"


def _active_accountants_subquery():
    return select(AccountantRelation.accountant_user_id).where(
        AccountantRelation.status == AccountantRelationStatus.ACTIVE,
        AccountantRelation.deleted_at.is_(None),
        AccountantRelation.accountant_user_id.is_not(None),
    )


def _active_customers_subquery(*, tier: CustomerTier | None = None):
    stmt = select(CustomerRelation.customer_user_id).where(
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
        CustomerRelation.customer_user_id.is_not(None),
    )
    if tier is not None:
        stmt = stmt.where(CustomerRelation.customer_tier == tier)
    return stmt


def _base_bot_recipient_stmt():
    active_accountants = _active_accountants_subquery()
    active_tier2_customers = _active_customers_subquery(tier=CustomerTier.TIER_2)
    return select(User).where(
        User.is_deleted.is_(False),
        User.account_status == UserAccountStatus.ACTIVE,
        User.telegram_id.is_not(None),
        User.role.in_(BOT_BROADCAST_ALLOWED_ROLES),
        ~User.id.in_(active_accountants),
        ~User.id.in_(active_tier2_customers),
    )


def _apply_group_filters(stmt, groups: Sequence[str]):
    if not groups:
        raise TelegramAdminBroadcastValidationError("groups_required")

    active_customers = _active_customers_subquery()
    active_tier1_customers = _active_customers_subquery(tier=CustomerTier.TIER_1)
    group_conditions = []
    if TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY in groups:
        group_conditions.append(
            (User.role == UserRole.STANDARD)
            & ~User.id.in_(active_customers)
        )
    if TELEGRAM_ADMIN_BROADCAST_GROUP_MANAGERS in groups:
        group_conditions.append(User.role.in_((UserRole.MIDDLE_MANAGER, UserRole.SUPER_ADMIN)))
    if TELEGRAM_ADMIN_BROADCAST_GROUP_TIER1_CUSTOMERS in groups:
        group_conditions.append(User.id.in_(active_tier1_customers))

    if not group_conditions:
        raise TelegramAdminBroadcastValidationError("groups_required")
    return stmt.where(or_(*group_conditions))


async def _customer_metadata_by_user_id(db: AsyncSession, user_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
    normalized_ids = unique_user_ids(user_ids)
    if not normalized_ids:
        return {}
    result = await db.execute(
        select(CustomerRelation.customer_user_id, CustomerRelation.customer_tier, CustomerRelation.management_name).where(
            CustomerRelation.customer_user_id.in_(normalized_ids),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    metadata: dict[int, tuple[str, str]] = {}
    for user_id, tier, management_name in result.all():
        if user_id is None:
            continue
        metadata[int(user_id)] = (_enum_value(tier), str(management_name or "").strip())
    return metadata


async def _recipients_from_users(
    db: AsyncSession,
    users: Iterable[User],
) -> tuple[TelegramAdminBroadcastRecipient, ...]:
    user_list = [user for user in users if getattr(user, "id", None) is not None]
    customer_metadata = await _customer_metadata_by_user_id(db, [int(user.id) for user in user_list])
    recipients: list[TelegramAdminBroadcastRecipient] = []
    seen_ids: set[int] = set()
    for user in user_list:
        user_id = int(user.id)
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        telegram_id = getattr(user, "telegram_id", None)
        if telegram_id is None:
            continue
        customer_tier, management_name = customer_metadata.get(user_id, (None, None))
        recipients.append(
            TelegramAdminBroadcastRecipient(
                user_id=user_id,
                telegram_id=int(telegram_id),
                account_name=getattr(user, "account_name", None),
                full_name=getattr(user, "full_name", None),
                username=getattr(user, "username", None),
                mobile_number=getattr(user, "mobile_number", None),
                role=_enum_value(getattr(user, "role", None)),
                customer_tier=customer_tier,
                customer_management_name=management_name or None,
            )
        )
    return tuple(recipients)


async def resolve_telegram_admin_broadcast_recipients(
    db: AsyncSession,
    *,
    audience_type: TelegramAdminBroadcastAudienceType | str,
    target_groups: Iterable[str] | None = None,
    selected_user_ids: Iterable[int] | None = None,
    sender_user_id: int | None = None,
) -> tuple[TelegramAdminBroadcastRecipient, ...]:
    audience = _normalize_audience_type(audience_type)
    stmt = _base_bot_recipient_stmt()

    if audience == TelegramAdminBroadcastAudienceType.ALL:
        if sender_user_id is not None:
            stmt = stmt.where(User.id != int(sender_user_id))
    elif audience == TelegramAdminBroadcastAudienceType.GROUP:
        groups = _normalize_groups(target_groups)
        stmt = _apply_group_filters(stmt, groups)
        if sender_user_id is not None:
            stmt = stmt.where(User.id != int(sender_user_id))
    elif audience == TelegramAdminBroadcastAudienceType.SELECTED:
        selected_ids = unique_user_ids(selected_user_ids or [])
        if len(selected_ids) > TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP:
            raise TelegramAdminBroadcastValidationError("too_many_selected_recipients")
        if not selected_ids:
            return ()
        stmt = stmt.where(User.id.in_(selected_ids))
    else:
        raise TelegramAdminBroadcastValidationError("unsupported_audience_type")

    result = await db.execute(stmt.order_by(User.id.asc()))
    return await _recipients_from_users(db, result.scalars().all())


async def search_telegram_admin_broadcast_recipients(
    db: AsyncSession,
    *,
    query: str,
    limit: int = 10,
    offset: int = 0,
) -> tuple[TelegramAdminBroadcastRecipient, ...]:
    cleaned = str(query or "").strip()
    if not cleaned:
        return ()
    safe_limit = max(1, min(int(limit or 10), 20))
    safe_offset = max(0, int(offset or 0))
    pattern = f"%{cleaned}%"
    stmt = (
        _base_bot_recipient_stmt()
        .where(
            or_(
                User.account_name.ilike(pattern),
                User.full_name.ilike(pattern),
                User.username.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )
        .order_by(User.account_name.asc(), User.id.asc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
    result = await db.execute(stmt)
    return await _recipients_from_users(db, result.scalars().all())


async def create_telegram_admin_broadcast(
    db: AsyncSession,
    *,
    actor: User | object,
    content: str,
    audience_type: TelegramAdminBroadcastAudienceType | str,
    target_groups: Iterable[str] | None = None,
    selected_user_ids: Iterable[int] | None = None,
) -> TelegramAdminBroadcastQueueResult:
    if _enum_value(getattr(actor, "role", None)) != UserRole.SUPER_ADMIN.value:
        raise TelegramAdminBroadcastValidationError("superadmin_required")

    cleaned_content = validate_telegram_admin_broadcast_content(content)
    audience = _normalize_audience_type(audience_type)
    groups = _normalize_groups(target_groups) if audience == TelegramAdminBroadcastAudienceType.GROUP else []
    actor_id = int(getattr(actor, "id"))

    recipients = await resolve_telegram_admin_broadcast_recipients(
        db,
        audience_type=audience,
        target_groups=groups,
        selected_user_ids=selected_user_ids,
        sender_user_id=actor_id if audience in {TelegramAdminBroadcastAudienceType.ALL, TelegramAdminBroadcastAudienceType.GROUP} else None,
    )

    current_time = utc_now()
    broadcast = TelegramAdminBroadcast(
        content=cleaned_content,
        created_by_id=actor_id,
        audience_type=audience,
        target_groups=list(groups),
        recipient_count=0,
        status=TelegramAdminBroadcastStatus.QUEUED if recipients else TelegramAdminBroadcastStatus.COMPLETED,
        queued_at=current_time,
        completed_at=None if recipients else current_time,
        created_at=current_time,
    )
    db.add(broadcast)
    await db.flush()

    receipts: list[TelegramAdminBroadcastReceipt] = []
    for recipient in recipients:
        receipts.append(
            TelegramAdminBroadcastReceipt(
                broadcast_id=int(broadcast.id),
                recipient_user_id=recipient.user_id,
                telegram_id_at_enqueue=recipient.telegram_id,
                dedupe_key=telegram_admin_broadcast_dedupe_key(
                    broadcast_id=int(broadcast.id),
                    recipient_user_id=recipient.user_id,
                ),
                status=TelegramAdminBroadcastReceiptStatus.PENDING,
                attempt_count=0,
                created_at=current_time,
            )
        )

    for start in range(0, len(receipts), TELEGRAM_BROADCAST_RECEIPT_INSERT_CHUNK_SIZE):
        db.add_all(receipts[start : start + TELEGRAM_BROADCAST_RECEIPT_INSERT_CHUNK_SIZE])
        await db.flush()

    broadcast.recipient_count = len(receipts)
    await db.flush()
    return TelegramAdminBroadcastQueueResult(
        broadcast=broadcast,
        recipients=recipients,
        receipt_count=len(receipts),
    )
