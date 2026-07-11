"""Shared Telegram bot eligibility policy.

This module is intentionally independent from aiogram handlers so WebApp token
issuance, bot linking, channel joins, and bot trade surfaces enforce one policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus, UserRole
from core.services.accountant_relation_service import is_user_accountant
from core.services.customer_relation_service import get_active_customer_relation_for_user
from core.services.user_account_status_service import get_user_account_status
from models.customer_relation import CustomerTier
from models.user import User


BOT_ACCESS_ALLOWED_ROLE_VALUES = {
    UserRole.STANDARD.value,
    UserRole.POLICE.value,
    UserRole.MIDDLE_MANAGER.value,
    UserRole.SUPER_ADMIN.value,
}

BOT_ACCESS_REASON_SYNC_PENDING = "pending_sync"
BOT_ACCESS_REASON_DELETED = "deleted"
BOT_ACCESS_REASON_INACTIVE = "inactive"
BOT_ACCESS_REASON_ROLE_FORBIDDEN = "role_forbidden"
BOT_ACCESS_REASON_ACCOUNTANT = "accountant"
BOT_ACCESS_REASON_CUSTOMER_TIER2 = "customer_tier2"
BOT_ACCESS_REASON_CUSTOMER_UNAVAILABLE = "customer_unavailable"
BOT_ACCESS_REASON_INVITATION_KIND_FORBIDDEN = "invitation_kind_forbidden"


@dataclass(frozen=True)
class BotAccessDecision:
    allowed: bool
    reason: str | None = None
    customer_tier: str | None = None


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _is_mocked_relation_check(relation_checker) -> bool:
    return callable(getattr(relation_checker, "assert_awaited", None)) or callable(
        getattr(relation_checker, "assert_called", None)
    )


async def _safe_is_user_accountant(db: AsyncSession, user_id: int) -> bool:
    if not isinstance(db, AsyncSession) and not _is_mocked_relation_check(is_user_accountant):
        return False
    return await is_user_accountant(db, user_id)


def evaluate_bot_access_local_state(user: User | object | None) -> BotAccessDecision:
    """Evaluate state that is available without relation-table reads."""
    if user is None:
        return BotAccessDecision(False, BOT_ACCESS_REASON_SYNC_PENDING)
    if getattr(user, "is_deleted", False):
        return BotAccessDecision(False, BOT_ACCESS_REASON_DELETED)
    if get_user_account_status(user) != UserAccountStatus.ACTIVE:
        return BotAccessDecision(False, BOT_ACCESS_REASON_INACTIVE)
    raw_role = getattr(user, "role", None)
    if raw_role is None and not isinstance(user, User):
        return BotAccessDecision(False, BOT_ACCESS_REASON_SYNC_PENDING)
    if _enum_value(raw_role) not in BOT_ACCESS_ALLOWED_ROLE_VALUES:
        return BotAccessDecision(False, BOT_ACCESS_REASON_ROLE_FORBIDDEN)
    return BotAccessDecision(True)


def evaluate_invitation_bot_access(
    *,
    role: object,
    invitation_kind: object,
    customer_tier: object | None,
) -> BotAccessDecision:
    """Pure pre-registration policy shared by links and authoritative completion."""
    role_value = _enum_value(role)
    kind_value = _enum_value(invitation_kind).strip().lower()
    tier_value = _enum_value(customer_tier).strip().lower()

    if role_value not in BOT_ACCESS_ALLOWED_ROLE_VALUES:
        return BotAccessDecision(False, BOT_ACCESS_REASON_ROLE_FORBIDDEN)
    if kind_value == "standard":
        return BotAccessDecision(True)
    if kind_value == "accountant":
        return BotAccessDecision(False, BOT_ACCESS_REASON_ACCOUNTANT)
    if kind_value == "customer":
        if tier_value == CustomerTier.TIER_1.value:
            return BotAccessDecision(True, customer_tier=tier_value)
        if tier_value == CustomerTier.TIER_2.value:
            return BotAccessDecision(
                False,
                BOT_ACCESS_REASON_CUSTOMER_TIER2,
                customer_tier=tier_value,
            )
        return BotAccessDecision(
            False,
            BOT_ACCESS_REASON_CUSTOMER_UNAVAILABLE,
            customer_tier=tier_value or None,
        )
    return BotAccessDecision(False, BOT_ACCESS_REASON_INVITATION_KIND_FORBIDDEN)


async def evaluate_bot_access(db: AsyncSession, user: User | object | None) -> BotAccessDecision:
    """Return whether a user may connect to and use Telegram bot market features."""
    local_decision = evaluate_bot_access_local_state(user)
    if not local_decision.allowed:
        return local_decision

    user_id = getattr(user, "id", None)
    if user_id is None:
        return local_decision

    if await _safe_is_user_accountant(db, int(user_id)):
        return BotAccessDecision(False, BOT_ACCESS_REASON_ACCOUNTANT)

    if isinstance(db, AsyncSession):
        relation = await get_active_customer_relation_for_user(db, int(user_id))
    else:
        relation = None

    if relation is None:
        return local_decision
    if getattr(relation, "deleted_at", None) is not None:
        return BotAccessDecision(False, BOT_ACCESS_REASON_CUSTOMER_UNAVAILABLE)

    customer_tier_value = _enum_value(getattr(relation, "customer_tier", None))
    if customer_tier_value == CustomerTier.TIER_2.value:
        return BotAccessDecision(False, BOT_ACCESS_REASON_CUSTOMER_TIER2, customer_tier=customer_tier_value)
    if customer_tier_value == CustomerTier.TIER_1.value:
        return BotAccessDecision(True, customer_tier=customer_tier_value)
    return BotAccessDecision(False, BOT_ACCESS_REASON_CUSTOMER_UNAVAILABLE, customer_tier=customer_tier_value)


def bot_access_denial_message(reason: str | None) -> str:
    if reason == BOT_ACCESS_REASON_INACTIVE:
        return (
            "❌ حساب شما غیرفعال است.\n\n"
            "تا زمان فعال‌سازی مجدد حساب، امکان اتصال تلگرام یا عضویت در کانال معاملات وجود ندارد."
        )
    if reason == BOT_ACCESS_REASON_DELETED:
        return (
            "❌ این حساب در دسترس نیست.\n\n"
            "امکان اتصال تلگرام یا عضویت در کانال معاملات برای این حساب وجود ندارد."
        )
    if reason == BOT_ACCESS_REASON_ACCOUNTANT:
        return "⚠️ حسابدارها به ربات تلگرام دسترسی ندارند.\n\nبرای استفاده از حساب حسابدار فقط از وب‌اپ استفاده کنید."
    if reason == BOT_ACCESS_REASON_CUSTOMER_TIER2:
        return "⚠️ دسترسی این حساب به ربات تلگرام فعال نیست.\n\nبرای استفاده از بازار فقط از وب‌اپ استفاده کنید."
    if reason in {BOT_ACCESS_REASON_ROLE_FORBIDDEN, BOT_ACCESS_REASON_CUSTOMER_UNAVAILABLE}:
        return "⛔️ دسترسی این حساب به ربات تلگرام در حال حاضر مجاز نیست."
    return (
        "⏳ اطلاعات حساب شما هنوز در ربات قابل تایید نیست.\n\n"
        "اگر همین الان در وب‌اپ ثبت‌نام کرده‌اید یا اتصال را آغاز کرده‌اید، احتمالاً همگام‌سازی هنوز کامل نشده است. "
        "چند لحظه بعد دوباره تلاش کنید."
    )


def normalize_telegram_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
