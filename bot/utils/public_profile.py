"""Safe Telegram bot public-profile helpers."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ProfileTradePdfCallback
from bot.utils.customer_display import attach_customer_management_names, user_display_name
from core.services.accountant_relation_service import get_active_accountant_relation_for_accountant
from core.services.customer_relation_service import (
    build_allowed_customer_chat_targets,
    get_active_customer_relation_for_customer,
)
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation
from models.user import User, UserRole


PUBLIC_PROFILE_USERNAME_UNAVAILABLE_CALLBACK = "public_profile_username_unavailable"


@dataclass(frozen=True, slots=True)
class BotPublicProfileAccountant:
    display_name: str
    mobile_number: str | None


@dataclass(frozen=True, slots=True)
class BotPublicProfile:
    target_user: User
    display_name: str
    accountants: tuple[BotPublicProfileAccountant, ...]
    is_self: bool = False


def _is_super_admin(user: User | object | None) -> bool:
    return getattr(user, "role", None) == UserRole.SUPER_ADMIN


def _is_deleted(user: User | object | None) -> bool:
    return bool(getattr(user, "is_deleted", False))


def _coerce_user_id(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


async def _load_user(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id).limit(1))
    return result.scalar_one_or_none()


async def _load_active_accountants_for_owner(
    db: AsyncSession,
    owner_user_id: int,
) -> tuple[BotPublicProfileAccountant, ...]:
    result = await db.execute(
        select(AccountantRelation)
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.accountant_user_id.is_not(None),
        )
        .order_by(AccountantRelation.created_at.asc(), AccountantRelation.id.asc())
    )
    relations = list(result.scalars().all())
    accountant_user_ids = [
        int(relation.accountant_user_id)
        for relation in relations
        if _coerce_user_id(getattr(relation, "accountant_user_id", None)) is not None
    ]
    if not accountant_user_ids:
        return ()

    users_result = await db.execute(select(User).where(User.id.in_(accountant_user_ids)))
    users_by_id = {
        int(user.id): user
        for user in users_result.scalars().all()
        if _coerce_user_id(getattr(user, "id", None)) is not None and not _is_deleted(user)
    }
    summaries: list[BotPublicProfileAccountant] = []
    for relation in relations:
        accountant_user_id = _coerce_user_id(getattr(relation, "accountant_user_id", None))
        accountant_user = users_by_id.get(accountant_user_id) if accountant_user_id is not None else None
        if accountant_user is None:
            continue
        display_name = str(getattr(relation, "relation_display_name", None) or "").strip()
        if not display_name:
            display_name = user_display_name(accountant_user, "حسابدار")
        summaries.append(
            BotPublicProfileAccountant(
                display_name=display_name,
                mobile_number=getattr(accountant_user, "mobile_number", None),
            )
        )
    return tuple(summaries)


async def _viewer_can_access_target(
    db: AsyncSession,
    *,
    viewer: User,
    target_user: User,
    target_customer_relation: CustomerRelation | None,
    original_target_user_id: int,
) -> bool:
    if viewer.id == target_user.id or viewer.id == original_target_user_id:
        return True
    if _is_super_admin(viewer):
        return True

    viewer_accountant_relation = await get_active_accountant_relation_for_accountant(db, viewer.id)
    if target_customer_relation is not None:
        if viewer.id == target_customer_relation.owner_user_id:
            return True
        return (
            viewer_accountant_relation is not None
            and getattr(viewer_accountant_relation, "owner_user_id", None) == target_customer_relation.owner_user_id
        )

    viewer_customer_relation = await get_active_customer_relation_for_customer(db, viewer.id)
    if viewer_customer_relation is not None:
        allowed_target_ids = set(await build_allowed_customer_chat_targets(db, viewer.id))
        return target_user.id in allowed_target_ids or original_target_user_id in allowed_target_ids

    if viewer_accountant_relation is not None:
        return getattr(viewer_accountant_relation, "owner_user_id", None) == target_user.id

    return True


async def load_bot_public_profile(
    db: AsyncSession,
    *,
    viewer: User | None,
    target_user_id: int,
) -> BotPublicProfile | None:
    """Load a bot-visible profile while enforcing project customer/accountant visibility policy."""
    if viewer is None:
        return None

    normalized_target_user_id = _coerce_user_id(target_user_id)
    if normalized_target_user_id is None:
        return None

    target_user = await _load_user(db, normalized_target_user_id)
    if target_user is None or _is_deleted(target_user):
        return None

    accountant_relation = await get_active_accountant_relation_for_accountant(db, normalized_target_user_id)
    if (
        accountant_relation is not None
        and getattr(accountant_relation, "owner_user", None) is not None
        and not _is_deleted(accountant_relation.owner_user)
    ):
        target_user = accountant_relation.owner_user

    target_customer_relation = await get_active_customer_relation_for_customer(db, target_user.id)
    if not await _viewer_can_access_target(
        db,
        viewer=viewer,
        target_user=target_user,
        target_customer_relation=target_customer_relation,
        original_target_user_id=normalized_target_user_id,
    ):
        return None

    await attach_customer_management_names(db, [target_user])
    if target_customer_relation is not None:
        display_name = str(getattr(target_customer_relation, "management_name", None) or "").strip()
    else:
        display_name = ""
    if not display_name:
        display_name = user_display_name(target_user, "کاربر")

    accountants: tuple[BotPublicProfileAccountant, ...] = ()
    if target_customer_relation is None:
        accountants = await _load_active_accountants_for_owner(db, target_user.id)

    return BotPublicProfile(
        target_user=target_user,
        display_name=display_name,
        accountants=accountants,
        is_self=viewer.id == target_user.id,
    )


def build_bot_public_profile_text(profile: BotPublicProfile) -> str:
    target_user = profile.target_user
    lines = [
        "👤 پروفایل",
        "",
        f"🔸 نام: {profile.display_name}",
        f"📞 شماره تماس: {getattr(target_user, 'mobile_number', None) or 'ثبت نشده'}",
        f"📍 آدرس: {getattr(target_user, 'address', None) or 'ثبت نشده'}",
    ]
    if profile.accountants:
        lines.extend(["", "👥 حسابداران:"])
        for index, accountant in enumerate(profile.accountants, start=1):
            mobile = accountant.mobile_number or "ثبت نشده"
            lines.append(f"{index}. {accountant.display_name} - {mobile}")
    return "\n".join(lines)


def build_bot_public_profile_keyboard(profile: BotPublicProfile) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📄 معاملات ۳ ماه اخیر",
                callback_data=ProfileTradePdfCallback(target_user_id=profile.target_user.id).pack(),
            )
        ]
    ]
    if not profile.is_self:
        raw_username = getattr(profile.target_user, "username", None)
        username = str(raw_username or "").strip().lstrip("@").strip()
        if username:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="💬 ارسال پیام",
                        url=f"https://t.me/{quote(username, safe='')}",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⚠️ عدم شناسایی کاربر",
                        callback_data=PUBLIC_PROFILE_USERNAME_UNAVAILABLE_CALLBACK,
                    )
                ]
            )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )
