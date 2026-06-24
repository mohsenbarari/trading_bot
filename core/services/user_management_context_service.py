"""Shared helpers for admin user-list relation context and ordering."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import case, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User


def _result_scalars_all(result) -> list:
    scalars = getattr(result, "scalars", None)
    if not callable(scalars):
        return []
    all_items = getattr(scalars(), "all", None)
    if not callable(all_items):
        return []
    return list(all_items())


def _active_customer_exists():
    return (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )


def _active_accountant_exists():
    return (
        select(AccountantRelation.id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )


def user_management_group_rank_expr():
    """Return ordering rank: ordinary users, then customers, then accountants."""

    return case(
        (_active_customer_exists(), 1),
        (_active_accountant_exists(), 2),
        else_=0,
    )


def apply_user_management_order(stmt):
    return stmt.order_by(user_management_group_rank_expr().asc(), User.id.desc())


def build_user_management_search_filter(search_pattern: str):
    accountant_owner = aliased(User)
    customer_match = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.management_name.ilike(search_pattern),
        )
        .exists()
    )
    accountant_match = (
        select(AccountantRelation.id)
        .join(accountant_owner, accountant_owner.id == AccountantRelation.owner_user_id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
            or_(
                AccountantRelation.relation_display_name.ilike(search_pattern),
                AccountantRelation.global_account_name.ilike(search_pattern),
                accountant_owner.account_name.ilike(search_pattern),
                accountant_owner.full_name.ilike(search_pattern),
                accountant_owner.mobile_number.ilike(search_pattern),
            ),
        )
        .exists()
    )
    return or_(
        User.full_name.ilike(search_pattern),
        User.account_name.ilike(search_pattern),
        User.mobile_number.ilike(search_pattern),
        customer_match,
        accountant_match,
    )


async def attach_user_management_relation_context(
    db: AsyncSession,
    users: Iterable[User | None],
) -> list[User]:
    users_by_id = {
        int(user.id): user
        for user in users
        if user is not None and getattr(user, "id", None) is not None
    }
    if not users_by_id:
        return []

    for user in users_by_id.values():
        setattr(user, "is_customer", False)
        setattr(user, "customer_tier", None)
        setattr(user, "customer_owner_user_id", None)
        setattr(user, "customer_owner_account_name", None)
        setattr(user, "customer_management_name", None)
        setattr(user, "is_accountant", False)
        setattr(user, "accountant_owner_user_id", None)
        setattr(user, "accountant_owner_account_name", None)

    if not hasattr(db, "execute"):
        return list(users_by_id.values())

    customer_stmt = (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.owner_user))
        .where(
            CustomerRelation.customer_user_id.in_(users_by_id.keys()),
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .order_by(CustomerRelation.id.asc())
    )
    customer_relations = _result_scalars_all(await db.execute(customer_stmt))
    for relation in customer_relations:
        customer_user_id = getattr(relation, "customer_user_id", None)
        if customer_user_id is None:
            continue
        user = users_by_id.get(int(customer_user_id))
        if user is None:
            continue
        owner_user = getattr(relation, "owner_user", None)
        setattr(user, "is_customer", True)
        setattr(user, "customer_tier", getattr(relation, "customer_tier", None))
        setattr(user, "customer_owner_user_id", getattr(owner_user, "id", None))
        setattr(user, "customer_owner_account_name", getattr(owner_user, "account_name", None))
        setattr(user, "customer_management_name", getattr(relation, "management_name", None))

    accountant_stmt = (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.owner_user))
        .where(
            AccountantRelation.accountant_user_id.in_(users_by_id.keys()),
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .order_by(AccountantRelation.id.asc())
    )
    accountant_relations = _result_scalars_all(await db.execute(accountant_stmt))
    for relation in accountant_relations:
        accountant_user_id = getattr(relation, "accountant_user_id", None)
        if accountant_user_id is None:
            continue
        user = users_by_id.get(int(accountant_user_id))
        if user is None:
            continue
        owner_user = getattr(relation, "owner_user", None)
        setattr(user, "is_accountant", True)
        setattr(user, "accountant_owner_user_id", getattr(owner_user, "id", None))
        setattr(user, "accountant_owner_account_name", getattr(owner_user, "account_name", None))

    return list(users_by_id.values())
