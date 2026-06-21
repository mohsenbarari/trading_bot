import logging
from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from models.user import User
from models.offer import Offer, OfferStatus
from core.db import AsyncSessionLocal
from core.trading_settings import get_trading_settings
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.server_routing import current_server, is_remote_home
from core.services.offer_expiry_service import (
    OfferAlreadyInactiveError,
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    OfferNotAuthoritativeError,
    expire_offer_authoritatively,
    is_offer_expiry_lock_busy,
)
from core.services.offer_expiry_limits import OfferManualExpireLimitError, enforce_manual_offer_expire_limits
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from bot.callbacks import ExpireOfferCallback

logger = logging.getLogger(__name__)

router = Router()

OFFER_EXPIRY_LOCK_BUSY_TEXT = "❌ این لفظ در حال غیرفعال شدن است"


def _expunge_if_supported(session, obj) -> None:
    expunge = getattr(session, "expunge", None)
    if callable(expunge):
        expunge(obj)


async def _rollback_if_supported(session) -> None:
    rollback = getattr(session, "rollback", None)
    if callable(rollback):
        await rollback()

@router.callback_query(ExpireOfferCallback.filter())
async def handle_expire_offer(callback: types.CallbackQuery, callback_data: ExpireOfferCallback, user: Optional[User], bot: Bot):
    if not user:
        await callback.answer()
        return
    
    ts = get_trading_settings()
    # پارس callback_data
    offer_id = callback_data.offer_id
    
    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id)
        
        if not offer:
            await callback.answer("❌ لفظ یافت نشد")
            return
        
        if offer.user_id != user.id:
            await callback.answer("❌ شما مالک این لفظ نیستید")
            return
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.answer("❌ این لفظ دیگر فعال نیست")
            return

        if is_remote_home(getattr(offer, "home_server", None)):
            status_code, body = await forward_offer_expiry_to_home_server(
                offer.home_server,
                {
                    "offer_id": getattr(offer, "id", offer_id),
                    "offer_public_id": getattr(offer, "offer_public_id", None),
                    "owner_user_id": user.id,
                    "actor_user_id": user.id,
                    "source_surface": OfferExpirySourceSurface.TELEGRAM_BOT.value,
                    "source_server": current_server(),
                    "expire_reason": OfferExpiryReason.MANUAL,
                },
            )
            if status_code >= 400:
                detail = body.get("detail") if isinstance(body, dict) else None
                await callback.answer(f"❌ {detail or 'خطا در منقضی کردن لفظ'}")
                return
        else:
            _expunge_if_supported(session, offer)
            try:
                offer = await session.get(
                    Offer,
                    offer_id,
                    with_for_update={"nowait": True},
                    populate_existing=True,
                )
            except StaleDataError:
                await _rollback_if_supported(session)
                await callback.answer("❌ این لفظ دیگر فعال نیست")
                return
            except OperationalError as exc:
                if is_offer_expiry_lock_busy(exc):
                    await _rollback_if_supported(session)
                    await callback.answer(OFFER_EXPIRY_LOCK_BUSY_TEXT)
                    return
                raise
            if not offer:
                await callback.answer("❌ لفظ یافت نشد")
                return
            if offer.user_id != user.id:
                await callback.answer("❌ شما مالک این لفظ نیستید")
                return
            if offer.status != OfferStatus.ACTIVE:
                await callback.answer("❌ این لفظ دیگر فعال نیست")
                return
            try:
                await enforce_manual_offer_expire_limits(
                    session,
                    owner_user_id=user.id,
                    trading_settings=ts,
                )
            except OfferManualExpireLimitError as exc:
                await callback.answer(f"❌ {exc.detail}")
                return
            try:
                await expire_offer_authoritatively(
                    session,
                    offer,
                    OfferExpiryCommand(
                        reason=OfferExpiryReason.MANUAL,
                        source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
                        source_server=current_server(),
                        expired_by_user_id=user.id,
                        expired_by_actor_user_id=user.id,
                    ),
                )
            except OfferNotAuthoritativeError:
                await callback.answer("❌ این لفظ باید روی سرور مرجع منقضی شود")
                return
            except OfferAlreadyInactiveError:
                await callback.answer("❌ این لفظ دیگر فعال نیست")
                return
            except StaleDataError:
                await _rollback_if_supported(session)
                await callback.answer("❌ این لفظ دیگر فعال نیست")
                return

            if offer.channel_message_id:
                try:
                    await apply_offer_channel_state(offer, reason="manual_expire", timeout=10)
                except Exception as e:
                    logger.debug(f"Failed to apply channel offer state: {e}")

        # حذف دکمه از پیام کاربر
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ لفظ شما منقضی شد")
