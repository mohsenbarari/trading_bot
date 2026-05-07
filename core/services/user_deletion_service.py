from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.utils.redis_helpers import mark_deleted_telegram_user
from core.config import settings
from core.services.session_service import deactivate_active_sessions, publish_session_revocation
from core.utils import send_telegram_notification
from models.invitation import Invitation
from models.offer import Offer, OfferStatus
from models.trade import Trade
from models.user import User


logger = logging.getLogger(__name__)

REMOVAL_TELEGRAM_MESSAGE = (
    "ℹ️ *اطلاعیه*\n\n"
    "حساب کاربری شما از پروژه حذف شده است.\n"
    "دسترسی وب و ربات شما بلافاصله غیرفعال شد.\n\n"
    "اگر در آینده دوباره به پروژه برگردید، مدیر باید مجدداً برای شما دسترسی ایجاد کند."
)


@dataclass(slots=True)
class DeletedUserResult:
    user_id: int
    telegram_id: Optional[int]
    revoked_session_count: int


async def remove_user_from_telegram_channel(telegram_id: int) -> None:
    """Remove a linked user from the Telegram channel without leaving them banned."""
    if not settings.channel_id or not settings.bot_token:
        return

    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.bot_token}/banChatMember",
            json={"chat_id": settings.channel_id, "user_id": telegram_id, "revoke_messages": False},
        )
        await client.post(
            f"https://api.telegram.org/bot{settings.bot_token}/unbanChatMember",
            json={"chat_id": settings.channel_id, "user_id": telegram_id, "only_if_banned": True},
        )


async def delete_user_account(db: AsyncSession, user: User) -> DeletedUserResult:
    """Soft-delete a user and revoke all current access in one shared flow."""
    if user.is_deleted:
        raise ValueError("User already deleted")

    telegram_id = user.telegram_id
    mobile_number = user.mobile_number
    account_name = user.account_name

    try:
        await db.execute(
            update(Trade)
            .where(Trade.offer_user_id == user.id)
            .values(offer_user_mobile=mobile_number)
        )
        await db.execute(
            update(Trade)
            .where(Trade.responder_user_id == user.id)
            .values(responder_user_mobile=mobile_number)
        )
        await db.execute(
            update(Offer)
            .where(Offer.user_id == user.id, Offer.status == OfferStatus.ACTIVE)
            .values(status=OfferStatus.EXPIRED)
        )
        invitation_result = await db.execute(
            select(Invitation).where(
                or_(Invitation.mobile_number == mobile_number, Invitation.account_name == account_name)
            )
        )
        for invitation in invitation_result.scalars().all():
            await db.delete(invitation)

        revoked_sessions = await deactivate_active_sessions(db, user.id)
        user.soft_delete()
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    if telegram_id:
        try:
            await mark_deleted_telegram_user(telegram_id)
        except Exception as exc:
            logger.warning(f"Failed to mark deleted telegram user {telegram_id}: {exc}")

        try:
            await send_telegram_notification(telegram_id, REMOVAL_TELEGRAM_MESSAGE)
        except Exception as exc:
            logger.warning(f"Failed to send deletion notice to telegram user {telegram_id}: {exc}")

    await publish_session_revocation(user.id, revoked_sessions)

    if telegram_id:
        try:
            await remove_user_from_telegram_channel(telegram_id)
        except Exception as exc:
            logger.warning(f"Failed to remove telegram user {telegram_id} from channel: {exc}")

    return DeletedUserResult(
        user_id=user.id,
        telegram_id=telegram_id,
        revoked_session_count=len(revoked_sessions),
    )