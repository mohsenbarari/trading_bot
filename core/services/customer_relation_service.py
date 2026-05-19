"""Shared customer-relation helpers for the phased customer rollout."""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.utils import utc_now
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User


CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES = (
    CustomerRelationStatus.PENDING,
    CustomerRelationStatus.ACTIVE,
)
CUSTOMER_INVITATION_PREFIX = "CUST-"
CUSTOMER_PENDING_LIFETIME = timedelta(days=2)


def _utcnow_naive():
    return utc_now().replace(tzinfo=None)


def is_customer_invitation_token(token: str | None) -> bool:
    return bool((token or "").startswith(CUSTOMER_INVITATION_PREFIX))


def get_effective_max_customers(owner_user: User) -> int:
    raw_limit = getattr(owner_user, "max_customers", 5)
    try:
        normalized_limit = int(raw_limit)
    except (TypeError, ValueError):
        normalized_limit = 5
    return max(0, normalized_limit)


async def sweep_expired_pending_customer_relations(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    invitation_token: str | None = None,
) -> list[CustomerRelation]:
    now = _utcnow_naive()
    stmt = (
        select(CustomerRelation)
        .where(
            CustomerRelation.status == CustomerRelationStatus.PENDING,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.expires_at.is_not(None),
            CustomerRelation.expires_at <= now,
        )
        .order_by(CustomerRelation.id.asc())
    )
    if owner_user_id is not None:
        stmt = stmt.where(CustomerRelation.owner_user_id == owner_user_id)
    if invitation_token is not None:
        stmt = stmt.where(CustomerRelation.invitation_token == invitation_token)

    expired_relations = list((await db.execute(stmt)).scalars().all())
    for relation in expired_relations:
        relation.status = CustomerRelationStatus.EXPIRED
        relation.deleted_at = now

    return expired_relations


async def get_customer_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> CustomerRelation | None:
    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(CustomerRelation.invitation_token == invitation_token)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_pending_customer_relation_by_invitation_token(
    db: AsyncSession,
    invitation_token: str,
) -> CustomerRelation | None:
    expired_relations = await sweep_expired_pending_customer_relations(db, invitation_token=invitation_token)
    if expired_relations:
        await db.commit()

    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(
            CustomerRelation.invitation_token == invitation_token,
            CustomerRelation.status == CustomerRelationStatus.PENDING,
            CustomerRelation.deleted_at.is_(None),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_active_customer_relation_for_customer(
    db: AsyncSession,
    customer_user_id: int,
) -> CustomerRelation | None:
    stmt = (
        select(CustomerRelation)
        .options(
            joinedload(CustomerRelation.owner_user),
            joinedload(CustomerRelation.customer_user),
        )
        .where(
            CustomerRelation.customer_user_id == customer_user_id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.id.asc())
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def is_user_customer(db: AsyncSession, user_id: int) -> bool:
    return await get_active_customer_relation_for_customer(db, user_id) is not None


async def list_active_customers_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> list[CustomerRelation]:
    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == owner_user_id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.created_at.asc(), CustomerRelation.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_owner_customer_relations(
    db: AsyncSession,
    owner_user_id: int,
) -> list[CustomerRelation]:
    expired_relations = await sweep_expired_pending_customer_relations(db, owner_user_id=owner_user_id)
    if expired_relations:
        await db.commit()

    stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == owner_user_id,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.status.in_(CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES),
        )
        .order_by(CustomerRelation.created_at.desc(), CustomerRelation.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def count_capacity_tracked_customers_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> int:
    stmt = select(func.count(CustomerRelation.id)).where(
        CustomerRelation.owner_user_id == owner_user_id,
        CustomerRelation.status.in_(CAPACITY_TRACKED_CUSTOMER_RELATION_STATUSES),
        CustomerRelation.deleted_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def validate_customer_capacity(
    db: AsyncSession,
    owner_user: User,
    *,
    additional_slots: int = 1,
) -> tuple[int, int]:
    await sweep_expired_pending_customer_relations(db, owner_user_id=owner_user.id)
    current_count = await count_capacity_tracked_customers_for_owner(db, owner_user.id)
    max_customers = get_effective_max_customers(owner_user)
    requested_total = current_count + max(0, additional_slots)
    if requested_total > max_customers:
        raise HTTPException(
            status_code=400,
            detail="Owner has reached the maximum number of customers",
        )
    return current_count, max_customers