from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.utils.channel_invites import build_channel_join_request_line
from core.config import settings
from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.server_routing import SERVER_FOREIGN, current_server
from core.services.accountant_relation_service import list_active_accountants_for_owner
from core.services.session_service import force_clear_sessions
from core.services.user_deletion_service import remove_user_from_telegram_channel
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.utils import create_user_notification, send_telegram_notification, utc_now
from models.user import User


logger = logging.getLogger(__name__)

INACTIVE_GLOBAL_LOCK_GRACE_PERIOD = timedelta(days=2)
GLOBAL_LOCK_LOOP_INTERVAL_SECONDS = 300

# Backward-compatible names for the existing DB fields and older imports. The
# policy is now global web lock after grace; column names remain unchanged.
INACTIVE_MESSENGER_GRACE_PERIOD = INACTIVE_GLOBAL_LOCK_GRACE_PERIOD
MESSENGER_BLOCK_LOOP_INTERVAL_SECONDS = GLOBAL_LOCK_LOOP_INTERVAL_SECONDS

INACTIVE_USER_NOTIFICATION = (
    "ℹ️ *اطلاعیه*\n\n"
    "حساب کاربری شما غیرفعال شد. دسترسی شما به بازار بسته شد. "
    "اگر حساب تا 2 روز آینده دوباره فعال نشود، همه نشست‌های وب و پیام‌رسان شما تا زمان فعال‌سازی مجدد بسته می‌شود."
)

REACTIVATED_USER_NOTIFICATION = (
    "✅ *اطلاعیه*\n\n"
    "حساب کاربری شما مجدداً فعال شد.\n"
    "دسترسی شما به بازار دوباره باز شد."
)

GLOBAL_LOCKED_NOTIFICATION = (
    "⛔ *اطلاعیه*\n\n"
    "به دلیل فعال نشدن حساب در مهلت دو روزه، دسترسی وب و پیام‌رسان شما تا زمان فعال‌سازی مجدد حساب بسته شد."
)

MESSENGER_BLOCKED_NOTIFICATION = GLOBAL_LOCKED_NOTIFICATION


@dataclass(slots=True)
class UserAccountStatusTransitionResult:
    changed: bool
    user_id: int
    account_status: UserAccountStatus
    deactivated_at: datetime | None
    messenger_grace_expires_at: datetime | None
    messenger_blocked_at: datetime | None


def _utcnow_naive() -> datetime:
    return utc_now().replace(tzinfo=None)


def _normalize_comparable_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_user_account_status(user: User | object | None) -> UserAccountStatus:
    raw_status = getattr(user, "account_status", None)
    if isinstance(raw_status, UserAccountStatus):
        return raw_status
    if raw_status is None:
        return UserAccountStatus.ACTIVE

    normalized_value = getattr(raw_status, "value", raw_status)
    try:
        return UserAccountStatus(normalized_value)
    except ValueError:
        logger.warning("Unknown user account status %r, defaulting to active", normalized_value)
        return UserAccountStatus.ACTIVE


def is_user_market_blocked(user: User | object | None) -> bool:
    return get_user_account_status(user) == UserAccountStatus.INACTIVE


def is_user_trade_blocked(user: User | object | None) -> bool:
    return is_user_market_blocked(user)


def is_user_global_web_locked(user: User | object | None, *, now: datetime | None = None) -> bool:
    if get_user_account_status(user) != UserAccountStatus.INACTIVE:
        return False

    if getattr(user, "messenger_blocked_at", None) is not None:
        return True

    grace_expires_at = _normalize_comparable_datetime(getattr(user, "messenger_grace_expires_at", None))
    if grace_expires_at is None:
        return False

    comparison_now = _normalize_comparable_datetime(now or _utcnow_naive())
    return grace_expires_at <= comparison_now


def is_user_messenger_blocked(user: User | object | None, *, now: datetime | None = None) -> bool:
    return is_user_global_web_locked(user, now=now)


async def _build_activation_join_line(user_id: int) -> str | None:
    bot: Bot | None = None
    try:
        if current_server() == SERVER_FOREIGN and settings.bot_token:
            bot = Bot(token=settings.bot_token)
        return await build_channel_join_request_line(bot, user_id=user_id)
    except Exception:
        logger.exception("Failed to build channel join line for reactivated user %s", user_id)
        return None
    finally:
        if bot is not None:
            await bot.session.close()


async def _send_or_enqueue_account_status_telegram(
    db: AsyncSession,
    *,
    user: User | object,
    message: str,
    source_id: str,
    account_status: UserAccountStatus,
    messenger_blocked: bool,
    queue_action: TelegramDeliveryAction = TelegramDeliveryAction.ACCOUNT_STATUS,
) -> None:
    user_id = int(getattr(user, "id"))
    telegram_id = int(getattr(user, "telegram_id"))
    if (
        configured_telegram_delivery_runtime().mode
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        # Local import avoids a cycle through bot_access_policy, which reads
        # the account-status helpers during notification freshness checks.
        from core.services.telegram_notification_outbox_service import (
            TelegramNotificationRecipient,
            enqueue_account_status_telegram_notification_once,
        )

        flush = getattr(db, "flush", None)
        if callable(flush):
            await flush()
        user_sync_version = int(getattr(user, "sync_version", 0) or 0)

        await enqueue_account_status_telegram_notification_once(
            db,
            recipient=TelegramNotificationRecipient(
                user_id=int(user_id),
                telegram_id=int(telegram_id),
            ),
            source_id=source_id,
            text=message,
            account_status=account_status,
            messenger_blocked=messenger_blocked,
            user_sync_version=user_sync_version,
            action=queue_action,
        )
    else:
        await send_telegram_notification(telegram_id, message)


async def _notify_user_and_optional_telegram(
    db: AsyncSession,
    user: User | object,
    message: str,
    *,
    level: NotificationLevel,
    source_id: str,
    account_status: UserAccountStatus,
    messenger_blocked: bool,
    include_telegram: bool = True,
    queue_action: TelegramDeliveryAction = TelegramDeliveryAction.ACCOUNT_STATUS,
) -> None:
    user_id = int(getattr(user, "id"))
    telegram_id = getattr(user, "telegram_id", None)
    await create_user_notification(db, user_id, message, level, NotificationCategory.SYSTEM)
    if include_telegram and telegram_id is not None:
        await _send_or_enqueue_account_status_telegram(
            db,
            user=user,
            message=message,
            source_id=source_id,
            account_status=account_status,
            messenger_blocked=messenger_blocked,
            queue_action=queue_action,
        )


async def transition_user_account_status(
    db: AsyncSession,
    user: User,
    target_status: UserAccountStatus,
) -> UserAccountStatusTransitionResult:
    current_status = get_user_account_status(user)
    now = _utcnow_naive()

    if target_status == UserAccountStatus.INACTIVE:
        if current_status == UserAccountStatus.INACTIVE:
            return UserAccountStatusTransitionResult(
                changed=False,
                user_id=user.id,
                account_status=current_status,
                deactivated_at=getattr(user, "deactivated_at", None),
                messenger_grace_expires_at=getattr(user, "messenger_grace_expires_at", None),
                messenger_blocked_at=getattr(user, "messenger_blocked_at", None),
            )

        user.account_status = UserAccountStatus.INACTIVE
        user.deactivated_at = now
        user.messenger_grace_expires_at = now + INACTIVE_GLOBAL_LOCK_GRACE_PERIOD
        user.messenger_blocked_at = None

        await _notify_user_and_optional_telegram(
            db,
            user,
            INACTIVE_USER_NOTIFICATION,
            level=NotificationLevel.WARNING,
            source_id=f"account-inactive:{user.id}:{now.isoformat()}",
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked=False,
        )

        if (
            user.telegram_id is not None
            and configured_telegram_delivery_runtime().mode
            != TelegramDeliveryRuntimeMode.QUEUE_V1
        ):
            try:
                await remove_user_from_telegram_channel(user.telegram_id)
            except Exception:
                logger.exception("Failed to remove inactive user %s from Telegram channel", user.id)

        return UserAccountStatusTransitionResult(
            changed=True,
            user_id=user.id,
            account_status=user.account_status,
            deactivated_at=user.deactivated_at,
            messenger_grace_expires_at=user.messenger_grace_expires_at,
            messenger_blocked_at=user.messenger_blocked_at,
        )

    stale_status_fields_present = any(
        getattr(user, field_name, None) is not None
        for field_name in ("deactivated_at", "messenger_grace_expires_at", "messenger_blocked_at")
    )
    if current_status == UserAccountStatus.ACTIVE and not stale_status_fields_present:
        return UserAccountStatusTransitionResult(
            changed=False,
            user_id=user.id,
            account_status=current_status,
            deactivated_at=None,
            messenger_grace_expires_at=None,
            messenger_blocked_at=None,
        )

    user.account_status = UserAccountStatus.ACTIVE
    user.deactivated_at = None
    user.messenger_grace_expires_at = None
    user.messenger_blocked_at = None

    await _notify_user_and_optional_telegram(
        db,
        user,
        REACTIVATED_USER_NOTIFICATION,
        level=NotificationLevel.SUCCESS,
        source_id=f"account-reactivated-web:{user.id}:{now.isoformat()}",
        account_status=UserAccountStatus.ACTIVE,
        messenger_blocked=False,
        include_telegram=False,
    )

    if user.telegram_id is not None:
        telegram_message = REACTIVATED_USER_NOTIFICATION
        join_line = await _build_activation_join_line(user.id)
        if join_line:
            telegram_message = f"{telegram_message}\n\n{join_line}"
        await _send_or_enqueue_account_status_telegram(
            db,
            user=user,
            message=telegram_message,
            source_id=f"account-reactivated:{user.id}:{now.isoformat()}",
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked=False,
        )

    return UserAccountStatusTransitionResult(
        changed=True,
        user_id=user.id,
        account_status=user.account_status,
        deactivated_at=None,
        messenger_grace_expires_at=None,
        messenger_blocked_at=None,
    )


async def mark_due_users_globally_locked(db: AsyncSession, *, limit: int = 100) -> int:
    now = _utcnow_naive()
    stmt = (
        select(User)
        .where(
            User.is_deleted == False,
            User.account_status == UserAccountStatus.INACTIVE,
            User.messenger_grace_expires_at.is_not(None),
            User.messenger_grace_expires_at <= now,
            User.messenger_blocked_at.is_(None),
        )
        .order_by(User.messenger_grace_expires_at.asc(), User.id.asc())
        .limit(limit)
    )
    due_users = list((await db.execute(stmt)).scalars().all())
    if not due_users:
        return 0

    for user in due_users:
        user.messenger_blocked_at = now
        await _notify_user_and_optional_telegram(
            db,
            user,
            GLOBAL_LOCKED_NOTIFICATION,
            level=NotificationLevel.WARNING,
            source_id=f"account-locked:{user.id}:{now.isoformat()}",
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked=True,
            queue_action=TelegramDeliveryAction.TIMED_SECURITY,
        )

        accountant_relations = await list_active_accountants_for_owner(db, user.id)
        for relation in accountant_relations:
            accountant_user = getattr(relation, "accountant_user", None)
            if accountant_user is None:
                continue
            await _notify_user_and_optional_telegram(
                db,
                accountant_user,
                GLOBAL_LOCKED_NOTIFICATION,
                level=NotificationLevel.WARNING,
                source_id=(
                    f"owner-account-locked:{user.id}:{accountant_user.id}:"
                    f"{now.isoformat()}"
                ),
                account_status=get_user_account_status(accountant_user),
                messenger_blocked=bool(
                    getattr(accountant_user, "messenger_blocked_at", None)
                ),
                queue_action=TelegramDeliveryAction.TIMED_SECURITY,
            )

        try:
            await force_clear_sessions(db, user.id)
        except Exception:
            logger.exception("Failed to revoke active sessions for messenger-blocked user %s", user.id)

    return len(due_users)


async def mark_due_users_messenger_blocked(db: AsyncSession, *, limit: int = 100) -> int:
    return await mark_due_users_globally_locked(db, limit=limit)
