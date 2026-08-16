"""Telegram bot display helpers for customer users."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from sqlalchemy import select

from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User
from core.services.accountant_relation_service import get_active_accountant_relation_for_accountant
from core.services.customer_relation_service import get_active_customer_relation_for_customer


logger = logging.getLogger(__name__)


def user_display_name(user: object | None, fallback: str = "کاربر") -> str:
    if user is None:
        return fallback
    return (
        getattr(user, "customer_management_name", None)
        or getattr(user, "account_name", None)
        or getattr(user, "full_name", None)
        or fallback
    )


async def attach_customer_management_names(session, users: Iterable[User | None]) -> None:
    users_by_id = {
        int(user.id): user
        for user in users
        if user is not None and getattr(user, "id", None) is not None
    }
    if not users_by_id:
        return

    relation_stmt = select(CustomerRelation).where(
        CustomerRelation.customer_user_id.in_(users_by_id.keys()),
        CustomerRelation.status == CustomerRelationStatus.ACTIVE,
        CustomerRelation.deleted_at.is_(None),
    )
    try:
        relation_result = await session.execute(relation_stmt)
        relations = relation_result.scalars().all()
    except Exception as exc:
        logger.debug("customer display enrichment skipped: %s", exc)
        return

    for relation in relations:
        management_name = str(getattr(relation, "management_name", None) or "").strip()
        customer_user_id = getattr(relation, "customer_user_id", None)
        if not customer_user_id or not management_name:
            continue
        user = users_by_id.get(int(customer_user_id))
        if user is not None:
            setattr(user, "customer_management_name", management_name)


async def resolve_customer_display_name_for_viewer(
    session,
    user: User | object | None,
    *,
    viewer_user_id: int,
    fallback: str = "کاربر",
) -> str | None:
    """Return a customer identity only inside the viewer's effective owner scope."""

    if user is None or getattr(user, "id", None) is None:
        return fallback
    target_user_id = int(user.id)
    try:
        relation = await get_active_customer_relation_for_customer(session, target_user_id)
        if relation is None:
            return getattr(user, "account_name", None) or getattr(user, "full_name", None) or fallback
        viewer_accountant_relation = await get_active_accountant_relation_for_accountant(
            session,
            viewer_user_id,
        )
    except Exception as exc:
        logger.debug("viewer-scoped customer display resolution failed closed: %s", exc)
        return None

    viewer_owner_user_id = (
        getattr(viewer_accountant_relation, "owner_user_id", None)
        if viewer_accountant_relation is not None
        else viewer_user_id
    )
    if target_user_id != viewer_user_id and relation.owner_user_id != viewer_owner_user_id:
        return None
    return (
        str(getattr(relation, "management_name", None) or "").strip()
        or getattr(user, "account_name", None)
        or getattr(user, "full_name", None)
        or fallback
    )
