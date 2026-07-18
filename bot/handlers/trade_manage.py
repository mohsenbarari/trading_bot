import logging
from datetime import datetime
from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm.exc import StaleDataError

from models.user import User
from models.offer import Offer, OfferStatus
from core.config import settings
from core.db import AsyncSessionLocal
from core.trading_settings import get_trading_settings
from core.offer_expiry_forwarding import forward_offer_expiry_to_home_server
from core.offer_expiry_contracts import (
    OfferExpiryCommandIdentityError,
    build_offer_expiry_forward_payload,
)
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
from core.services.offer_expiry_gate import try_acquire_offer_expiry_gate
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.services.telegram_callback_queue_service import (
    enqueue_telegram_callback_answer,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.utils import utc_now
from bot.callbacks import ExpireOfferCallback
from bot.repeat_offer import build_persistent_navigation_keyboard
from core.public_webapp_url import user_facing_webapp_url

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


async def _answer_offer_expiry_callback(
    callback: types.CallbackQuery,
    *,
    received_at: datetime,
    text: str | None = None,
    session=None,
    commit: bool = True,
) -> None:
    """Preserve legacy replies or enqueue one direct M0 callback answer."""
    if (
        configured_telegram_delivery_runtime().mode
        != TelegramDeliveryRuntimeMode.QUEUE_V1
    ):
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text)
        return

    async def _enqueue(db) -> None:
        await enqueue_telegram_callback_answer(
            db,
            current_server=current_server(),
            callback_query_id=callback.id,
            received_at=received_at,
            action=TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
            text=text,
        )
        if commit:
            await db.commit()

    if session is not None:
        await _enqueue(session)
        return
    async with AsyncSessionLocal() as db:
        await _enqueue(db)

@router.callback_query(ExpireOfferCallback.filter())
async def handle_expire_offer(callback: types.CallbackQuery, callback_data: ExpireOfferCallback, user: Optional[User], bot: Bot):
    callback_received_at = utc_now()
    if not user:
        await _answer_offer_expiry_callback(
            callback,
            received_at=callback_received_at,
        )
        return
    
    ts = get_trading_settings()
    # پارس callback_data
    offer_id = callback_data.offer_id

    lease = await try_acquire_offer_expiry_gate(offer_id=offer_id)
    if not lease.acquired:
        await _answer_offer_expiry_callback(
            callback,
            received_at=callback_received_at,
            text=OFFER_EXPIRY_LOCK_BUSY_TEXT,
        )
        return
    try:
        async with AsyncSessionLocal() as session:
            offer = await session.get(Offer, offer_id)

            if not offer:
                await _answer_offer_expiry_callback(
                    callback,
                    received_at=callback_received_at,
                    text="❌ لفظ یافت نشد",
                    session=session,
                )
                return

            if offer.user_id != user.id:
                await _answer_offer_expiry_callback(
                    callback,
                    received_at=callback_received_at,
                    text="❌ شما مالک این لفظ نیستید",
                    session=session,
                )
                return

            if is_remote_home(getattr(offer, "home_server", None)):
                try:
                    payload = build_offer_expiry_forward_payload(
                        offer,
                        owner_user_id=user.id,
                        actor_user_id=user.id,
                        source_surface=OfferExpirySourceSurface.TELEGRAM_BOT,
                        source_server=current_server(),
                        expire_reason=OfferExpiryReason.MANUAL,
                        include_command_identity=bool(
                            settings.offer_expiry_command_receipts_enabled
                        ),
                    )
                except OfferExpiryCommandIdentityError:
                    logger.warning(
                        "offer_expiry_forward_identity_invalid",
                        extra={
                            "event": "offer_expiry_forward.identity_invalid",
                            "offer_id": getattr(offer, "id", None),
                        },
                    )
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ شناسه عمومی لفظ برای انتقال معتبر نیست",
                        session=session,
                    )
                    return
                status_code, body = await forward_offer_expiry_to_home_server(
                    offer.home_server,
                    payload,
                )
                if status_code >= 400:
                    detail = body.get("detail") if isinstance(body, dict) else None
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text=f"❌ {detail or 'خطا در منقضی کردن لفظ'}",
                        session=session,
                    )
                    return
            else:
                if offer.status != OfferStatus.ACTIVE:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ دیگر فعال نیست",
                        session=session,
                    )
                    return
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
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ دیگر فعال نیست",
                        session=session,
                    )
                    return
                except (OperationalError, DBAPIError) as exc:
                    if is_offer_expiry_lock_busy(exc):
                        await _rollback_if_supported(session)
                        await _answer_offer_expiry_callback(
                            callback,
                            received_at=callback_received_at,
                            text=OFFER_EXPIRY_LOCK_BUSY_TEXT,
                            session=session,
                        )
                        return
                    raise
                if not offer:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ لفظ یافت نشد",
                        session=session,
                    )
                    return
                if offer.user_id != user.id:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ شما مالک این لفظ نیستید",
                        session=session,
                    )
                    return
                if offer.status != OfferStatus.ACTIVE:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ دیگر فعال نیست",
                        session=session,
                    )
                    return
                try:
                    await enforce_manual_offer_expire_limits(
                        session,
                        owner_user_id=user.id,
                        trading_settings=ts,
                    )
                except OfferManualExpireLimitError as exc:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text=f"❌ {exc.detail}",
                        session=session,
                    )
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
                        commit=(
                            configured_telegram_delivery_runtime().mode
                            != TelegramDeliveryRuntimeMode.QUEUE_V1
                        ),
                    )
                except OfferNotAuthoritativeError:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ باید روی سرور مرجع منقضی شود",
                        session=session,
                    )
                    return
                except OfferAlreadyInactiveError:
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ دیگر فعال نیست",
                        session=session,
                    )
                    return
                except StaleDataError:
                    await _rollback_if_supported(session)
                    await _answer_offer_expiry_callback(
                        callback,
                        received_at=callback_received_at,
                        text="❌ این لفظ دیگر فعال نیست",
                        session=session,
                    )
                    return

                if offer.channel_message_id:
                    try:
                        await apply_offer_channel_state(offer, reason="manual_expire", timeout=10)
                    except Exception as e:
                        logger.debug(f"Failed to apply channel offer state: {e}")

            await _answer_offer_expiry_callback(
                callback,
                received_at=callback_received_at,
                session=session,
                commit=False,
            )
            if (
                configured_telegram_delivery_runtime().mode
                == TelegramDeliveryRuntimeMode.QUEUE_V1
            ):
                # The local Offer mutation and its callback obligation become
                # visible atomically. Remote-home expiry is already protected
                # by its command receipt and this commits the foreign callback.
                await session.commit()

            # حذف دکمه از پیام کاربر
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(
                "✅ لفظ شما منقضی شد.",
                reply_markup=await build_persistent_navigation_keyboard(
                    user,
                    user_facing_webapp_url(settings_obj=settings),
                ),
            )
    finally:
        await lease.release()
