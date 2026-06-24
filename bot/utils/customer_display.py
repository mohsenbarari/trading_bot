"""Telegram bot display helpers for customer users."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from sqlalchemy import select

from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.user import User


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
        management_name = str(relation.management_name or "").strip()
        if not relation.customer_user_id or not management_name:
            continue
        user = users_by_id.get(int(relation.customer_user_id))
        if user is not None:
            setattr(user, "customer_management_name", management_name)
