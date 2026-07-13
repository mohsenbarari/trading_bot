"""Registration notification boundaries shared by completion adapters."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import NotificationCategory, NotificationLevel
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
    TelegramNotificationRecipient,
    enqueue_telegram_notifications,
)
from core.utils import create_user_notification
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.user import User


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegistrationNotificationUserSnapshot:
    id: int
    account_name: str
    full_name: str


def should_announce_project_user_registration(
    accountant_relation: AccountantRelation | object | None,
    customer_relation: CustomerRelation | object | None,
) -> bool:
    return accountant_relation is None and customer_relation is None


def project_user_joined_message(user: User | object) -> str:
    display_name = (getattr(user, "account_name", None) or getattr(user, "full_name", "") or "").strip()
    return f"{display_name} به لیست همکاران اضافه شدند."


def project_user_profile_route(user: User | object) -> str:
    account_name = (getattr(user, "account_name", "") or "").strip()
    suffix = f"?account_name={quote(account_name)}" if account_name else ""
    return f"/users/{user.id}{suffix}"


async def enqueue_project_user_joined_telegram_outbox(
    db: AsyncSession,
    *,
    new_user: User,
) -> list[TelegramNotificationOutbox]:
    """Insert the unique Telegram announcement in the registration transaction."""
    customer_exists = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )
    accountant_exists = (
        select(AccountantRelation.id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )
    recipient_stmt = select(User.id, User.telegram_id).where(
        User.is_deleted.is_(False),
        User.id != new_user.id,
        User.telegram_id.is_not(None),
        ~customer_exists,
        ~accountant_exists,
    )
    recipient_rows = list((await db.execute(recipient_stmt)).all())
    recipients = [
        TelegramNotificationRecipient(user_id=int(user_id), telegram_id=int(telegram_id))
        for user_id, telegram_id in recipient_rows
        if telegram_id is not None
    ]
    if not recipients:
        return []

    return await enqueue_telegram_notifications(
        db,
        recipients=recipients,
        text=project_user_joined_message(new_user),
        source_type=TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
        source_id=new_user.id,
        parse_mode=None,
        extra_payload={
            "title": "پیام مدیریت",
            "route": project_user_profile_route(new_user),
            "exclude_customers": True,
        },
    )


async def publish_project_user_joined_web_notifications(
    *,
    new_user_id: int,
    account_name: str,
    full_name: str,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """Publish non-authoritative Web notifications after registration commit."""
    if not new_user_id:
        return
    snapshot = RegistrationNotificationUserSnapshot(
        id=int(new_user_id),
        account_name=str(account_name or ""),
        full_name=str(full_name or ""),
    )
    if session_factory is None:
        from core.db import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    try:
        async with session_factory() as notification_db:
            recipient_stmt = select(User.id).where(
                User.is_deleted.is_(False),
                User.id != snapshot.id,
            )
            try:
                recipient_ids = list(
                    (await notification_db.execute(recipient_stmt)).scalars().all()
                )
            except Exception as exc:
                await notification_db.rollback()
                logger.warning(
                    "Project user joined notification recipient lookup failed",
                    extra={
                        "event": "registration.notification_recipient_lookup_failed",
                        "error_class": type(exc).__name__,
                    },
                )
                return

            message = project_user_joined_message(snapshot)
            route = project_user_profile_route(snapshot)
            for recipient_id in recipient_ids:
                try:
                    await create_user_notification(
                        notification_db,
                        int(recipient_id),
                        message,
                        NotificationLevel.INFO,
                        NotificationCategory.SYSTEM,
                        extra_payload={
                            "title": "پیام مدیریت",
                            "route": route,
                        },
                    )
                except Exception as exc:
                    await notification_db.rollback()
                    logger.warning(
                        "Project user joined Web notification failed",
                        extra={
                            "event": "registration.web_notification_failed",
                            "recipient_id": recipient_id,
                            "new_user_id": snapshot.id,
                            "error_class": type(exc).__name__,
                        },
                    )
    except Exception as exc:
        logger.warning(
            "Project user joined notification session failed",
            extra={
                "event": "registration.notification_session_failed",
                "error_class": type(exc).__name__,
            },
        )
