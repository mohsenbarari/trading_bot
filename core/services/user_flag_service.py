"""Simple, reusable user-review flags and administrator alerts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import NotificationCategory, NotificationLevel, UserAccountStatus
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.utils import create_user_notification, utc_now
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
    enqueue_telegram_action_notification_once,
)
from models.user import User, UserRole
from models.user_flag import UserFlag


logger = logging.getLogger(__name__)

SESSION_REPLACEMENT_FLAG_TYPE = "session_replacement_frequency"
SESSION_REPLACEMENT_REASON_CODE = "repeated_session_replacement"
SESSION_REPLACEMENT_THRESHOLDS = {"daily": 2, "weekly": 5, "monthly": 7}
SESSION_REPLACEMENT_TTLS = {"daily": 86_400, "weekly": 604_800, "monthly": 2_592_000}


@dataclass(frozen=True, slots=True)
class SessionReplacementRiskResult:
    counts: dict[str, int]
    flag: UserFlag | None
    flag_created: bool


def user_flag_labels(flag: UserFlag | object) -> tuple[str, str]:
    flag_type = str(getattr(flag, "flag_type", "") or "")
    reason_code = str(getattr(flag, "reason_code", "") or "")
    type_labels = {
        SESSION_REPLACEMENT_FLAG_TYPE: "جابه‌جایی مکرر نشست",
    }
    reason_labels = {
        SESSION_REPLACEMENT_REASON_CODE: "ورود مکرر روی دستگاه یا مرورگر جدید",
    }
    return (
        type_labels.get(flag_type, "نیازمند بررسی"),
        reason_labels.get(reason_code, "رفتار ثبت‌شده نیازمند بررسی مدیر است"),
    )


async def _increment_session_replacement_counters(user_id: int) -> dict[str, int]:
    from bot.utils.redis_helpers import get_redis

    redis = await get_redis()
    counts: dict[str, int] = {}
    for period, ttl in SESSION_REPLACEMENT_TTLS.items():
        key = f"session_replace:{int(user_id)}:{period}"
        # SET NX creates an expiry-bound fixed window atomically. Subsequent
        # replacements only increment it; they do not silently extend the
        # displayed 24-hour/7-day/30-day window.
        created = await redis.set(key, "1", ex=ttl, nx=True)
        counts[period] = 1 if created else int(await redis.incr(key))
    return counts


def _threshold_reason(counts: dict[str, int]) -> str | None:
    for period in ("daily", "weekly", "monthly"):
        if counts.get(period, 0) >= SESSION_REPLACEMENT_THRESHOLDS[period]:
            return period
    return None


async def notify_super_admins_about_user_flag(
    db: AsyncSession,
    *,
    flag: UserFlag,
    flagged_user: User,
) -> None:
    """Notify every active super admin once on WebApp and Queue-v1 Telegram."""
    admins = list(
        (
            await db.execute(
                select(User).where(
                    and_(
                        User.role == UserRole.SUPER_ADMIN,
                        User.is_deleted == False,
                        User.account_status == UserAccountStatus.ACTIVE,
                    )
                )
            )
        ).scalars().all()
    )
    display_name = (
        str(getattr(flagged_user, "account_name", "") or "").strip()
        or str(getattr(flagged_user, "full_name", "") or "").strip()
        or f"کاربر {flagged_user.id}"
    )
    type_label, _ = user_flag_labels(flag)
    message = f"کاربر «{display_name}» با دلیل «{type_label}» علامت‌گذاری شد."
    payload = {
        "title": "کاربر مشکوک جدید",
        "route": "/admin/users",
        "flag_id": int(flag.id),
        "flag_type": flag.flag_type,
        "flag_reason_code": flag.reason_code,
        "flagged_user_id": int(flagged_user.id),
    }

    for admin in admins:
        await create_user_notification(
            db,
            int(admin.id),
            message,
            NotificationLevel.WARNING,
            NotificationCategory.SYSTEM,
            extra_payload=payload,
            dedupe_key=f"user-flag:{flag.id}:admin:{admin.id}",
        )
        telegram_id = getattr(admin, "telegram_id", None)
        sync_version = int(getattr(admin, "sync_version", 0) or 0)
        if telegram_id is None or sync_version <= 0:
            continue
        await enqueue_telegram_action_notification_once(
            db,
            recipient=TelegramNotificationRecipient(
                user_id=int(admin.id),
                telegram_id=int(telegram_id),
            ),
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            source_id=f"user-flag:{flag.id}",
            text=f"⚠️ {message}\nبرای بررسی، بخش کاربران مشکوک را باز کنید.",
            user_sync_version=sync_version,
        )
    await db.commit()


async def open_or_update_user_flag(
    db: AsyncSession,
    *,
    user: User,
    flag_type: str,
    reason_code: str,
    details: dict[str, Any],
    severity: str = "warning",
    observed_at=None,
) -> tuple[UserFlag, bool]:
    """Generic entry point for every current and future user-flag detector."""
    now = observed_at or utc_now()
    # The user row is the shared lock for all detector types. This prevents two
    # concurrent observations from racing to create the same open case before
    # the partial unique index can arbitrate the insert.
    await db.execute(select(User.id).where(User.id == int(user.id)).with_for_update())
    flag = (
        await db.execute(
            select(UserFlag)
            .where(
                and_(
                    UserFlag.user_id == int(user.id),
                    UserFlag.flag_type == str(flag_type),
                    UserFlag.status == "open",
                )
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    created = flag is None
    if flag is None:
        flag = UserFlag(
            user_id=int(user.id),
            flag_type=str(flag_type),
            reason_code=str(reason_code),
            status="open",
            severity=str(severity),
            details=dict(details),
            trigger_count=1,
            first_flagged_at=now,
            last_flagged_at=now,
        )
        db.add(flag)
    else:
        flag.reason_code = str(reason_code)
        flag.severity = str(severity)
        flag.details = dict(details)
        flag.trigger_count = int(flag.trigger_count or 0) + 1
        flag.last_flagged_at = now

    await db.commit()
    await db.refresh(flag)
    if created:
        await notify_super_admins_about_user_flag(db, flag=flag, flagged_user=user)
    return flag, created


async def record_session_replacement_activity(
    db: AsyncSession,
    *,
    user: User,
    replaced_session_count: int,
    device_name: str,
    device_ip: str | None,
    platform: Any,
    home_server: str,
) -> SessionReplacementRiskResult | None:
    """Count replacements and open/update a review case after a simple threshold.

    Counter or notification failures must never undo a successfully authenticated
    session.  The caller owns that fail-open boundary and logs any exception.
    """
    if replaced_session_count <= 0:
        return None

    counts = await _increment_session_replacement_counters(int(user.id))
    threshold_period = _threshold_reason(counts)
    if threshold_period is None:
        return SessionReplacementRiskResult(counts=counts, flag=None, flag_created=False)

    now = utc_now()
    details = {
        "detector_version": 1,
        "counts": counts,
        "thresholds": dict(SESSION_REPLACEMENT_THRESHOLDS),
        "threshold_period": threshold_period,
        "replaced_session_count": int(replaced_session_count),
        "device_name": str(device_name or "مرورگر ناشناس")[:120],
        "device_ip": str(device_ip)[:64] if device_ip else None,
        "platform": str(getattr(platform, "value", platform) or "web")[:32],
        "home_server": str(home_server or "")[:16],
        "observed_at": now.isoformat(),
    }
    flag, created = await open_or_update_user_flag(
        db,
        user=user,
        flag_type=SESSION_REPLACEMENT_FLAG_TYPE,
        reason_code=SESSION_REPLACEMENT_REASON_CODE,
        severity="warning",
        details=details,
        observed_at=now,
    )
    return SessionReplacementRiskResult(counts=counts, flag=flag, flag_created=created)
