"""Shared role badges for messenger user identity surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User

ChatRoleKind = Literal["colleague", "accountant", "customer"]

CHAT_ROLE_COLLEAGUE: ChatRoleKind = "colleague"
CHAT_ROLE_ACCOUNTANT: ChatRoleKind = "accountant"
CHAT_ROLE_CUSTOMER: ChatRoleKind = "customer"

CHAT_ROLE_LABELS: dict[ChatRoleKind, str] = {
    CHAT_ROLE_COLLEAGUE: "همکار",
    CHAT_ROLE_ACCOUNTANT: "حسابدار",
    CHAT_ROLE_CUSTOMER: "مشتری",
}


@dataclass(frozen=True)
class ChatRoleBadge:
    kind: ChatRoleKind
    label: str
    accountant_owner_name: str | None = None
    accountant_owner_label: str | None = None


def make_chat_role_badge(
    kind: ChatRoleKind,
    *,
    accountant_owner_name: str | None = None,
) -> ChatRoleBadge:
    owner_name = (accountant_owner_name or "").strip() or None
    owner_label = f"سرگروه: {owner_name}" if kind == CHAT_ROLE_ACCOUNTANT and owner_name else None
    return ChatRoleBadge(
        kind=kind,
        label=CHAT_ROLE_LABELS[kind],
        accountant_owner_name=owner_name,
        accountant_owner_label=owner_label,
    )


async def load_chat_role_badges(db: AsyncSession, user_ids: list[int] | set[int]) -> dict[int, ChatRoleBadge]:
    """Resolve messenger role badges for many users with accountant precedence."""

    if not hasattr(db, "execute"):
        return {}

    normalized_ids = {
        int(user_id)
        for user_id in user_ids
        if isinstance(user_id, int) and user_id > 0
    }
    if not normalized_ids:
        return {}

    badges = {
        user_id: make_chat_role_badge(CHAT_ROLE_COLLEAGUE)
        for user_id in normalized_ids
    }

    customer_rows = (
        await db.execute(
            select(CustomerRelation.customer_user_id).where(
                CustomerRelation.customer_user_id.in_(normalized_ids),
                CustomerRelation.status == CustomerRelationStatus.ACTIVE,
                CustomerRelation.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for user_id in customer_rows:
        if user_id is not None:
            badges[int(user_id)] = make_chat_role_badge(CHAT_ROLE_CUSTOMER)

    accountant_rows = (
        await db.execute(
            select(
                AccountantRelation.accountant_user_id,
                User.account_name,
            )
            .join(User, User.id == AccountantRelation.owner_user_id)
            .where(
                AccountantRelation.accountant_user_id.in_(normalized_ids),
                AccountantRelation.status == AccountantRelationStatus.ACTIVE,
                AccountantRelation.deleted_at.is_(None),
                User.is_deleted.is_(False),
            )
        )
    ).all()
    for user_id, owner_name in accountant_rows:
        if user_id is not None:
            badges[int(user_id)] = make_chat_role_badge(
                CHAT_ROLE_ACCOUNTANT,
                accountant_owner_name=owner_name,
            )

    return badges
