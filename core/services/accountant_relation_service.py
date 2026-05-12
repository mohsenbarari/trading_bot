"""Shared accountant-relation helpers for phased delegated-owner rollout."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.user import User


CAPACITY_TRACKED_RELATION_STATUSES = (
    AccountantRelationStatus.PENDING,
    AccountantRelationStatus.ACTIVE,
)


@dataclass(frozen=True)
class EffectiveOwnerActor:
    owner_user: User
    actor_user: User
    relation: AccountantRelation | None
    is_accountant_context: bool


def get_effective_max_accountants(owner_user: User) -> int:
    raw_limit = getattr(owner_user, "max_accountants", 3)
    try:
        normalized_limit = int(raw_limit)
    except (TypeError, ValueError):
        normalized_limit = 3
    return max(0, normalized_limit)


async def get_active_accountant_relation_for_accountant(
    db: AsyncSession,
    accountant_user_id: int,
) -> AccountantRelation | None:
    stmt = (
        select(AccountantRelation)
        .options(
            joinedload(AccountantRelation.owner_user),
            joinedload(AccountantRelation.accountant_user),
        )
        .where(
            AccountantRelation.accountant_user_id == accountant_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.id.asc())
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_active_accountants_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> list[AccountantRelation]:
    stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.created_at.asc(), AccountantRelation.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def count_capacity_tracked_accountants_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> int:
    stmt = select(func.count(AccountantRelation.id)).where(
        AccountantRelation.owner_user_id == owner_user_id,
        AccountantRelation.status.in_(CAPACITY_TRACKED_RELATION_STATUSES),
        AccountantRelation.deleted_at.is_(None),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def validate_accountant_capacity(
    db: AsyncSession,
    owner_user: User,
    *,
    additional_slots: int = 1,
) -> tuple[int, int]:
    current_count = await count_capacity_tracked_accountants_for_owner(db, owner_user.id)
    max_accountants = get_effective_max_accountants(owner_user)
    requested_total = current_count + max(0, additional_slots)
    if requested_total > max_accountants:
        raise HTTPException(
            status_code=400,
            detail="Owner has reached the maximum number of accountants",
        )
    return current_count, max_accountants


async def resolve_effective_owner_actor(
    db: AsyncSession,
    current_user: User,
) -> EffectiveOwnerActor:
    relation = await get_active_accountant_relation_for_accountant(db, current_user.id)
    if not relation or relation.owner_user is None:
        return EffectiveOwnerActor(
            owner_user=current_user,
            actor_user=current_user,
            relation=None,
            is_accountant_context=False,
        )

    return EffectiveOwnerActor(
        owner_user=relation.owner_user,
        actor_user=current_user,
        relation=relation,
        is_accountant_context=relation.owner_user.id != current_user.id,
    )