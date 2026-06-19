import logging
import json
from aiogram import Router, F, types, Bot
from typing import Optional
from sqlalchemy import select
from datetime import datetime
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.offer_request import OfferRequestSourceSurface
from core.config import settings
from core.db import AsyncSessionLocal
from bot.utils.redis_helpers import check_double_click
from core.utils import check_user_limits
from core.enums import UserRole
from bot.callbacks import ChannelTradeCallback
from api.deps import EffectiveOwnerActor
from api.routers.trades import TradeCreate, _execute_trade_authoritatively
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    validate_offer_trade_amount,
)
from core.services.telegram_offer_channel_service import apply_offer_channel_state
from core.server_routing import current_server, is_remote_home
from core.trade_forwarding import forward_trade_to_home_server
from bot.utils.trade_suggestion_messages import (
    PRIVATE_SUGGESTION_CONFIRM_TIMEOUT,
    build_offer_trade_buttons,
    build_trade_amount_buttons,
    remove_trade_suggestion_record,
    schedule_trade_suggestion_cleanup,
    schedule_trade_suggestion_pending_reset,
    upsert_trade_suggestion_record,
)


logger = logging.getLogger(__name__)

router = Router()


async def update_offer_channel_markup(bot: Bot, offer: Offer) -> None:
    """همیشه دکمه‌های کانال را با channel_message_id واقعی به‌روزرسانی کن."""
    if not offer.channel_message_id:
        return

    if offer.remaining_quantity <= 0 or offer.status != OfferStatus.ACTIVE:
        await apply_offer_channel_state(offer, reason="bot_channel_trade")
        return

    new_keyboard = build_offer_trade_buttons(
        offer.id,
        offer.quantity,
        offer.remaining_quantity,
        offer.is_wholesale,
        list(offer.lot_sizes) if offer.lot_sizes else None,
    )
    await bot.edit_message_reply_markup(
        chat_id=settings.channel_id,
        message_id=offer.channel_message_id,
        reply_markup=new_keyboard,
    )


async def send_or_update_trade_suggestion_message(
    callback: types.CallbackQuery,
    bot: Bot,
    target_chat_id: int,
    payload: dict,
) -> None:
    """ارسال یا به‌روزرسانی پیام پیشنهاد معامله با دکمه‌های لات باقی‌مانده."""
    reply_markup = build_trade_amount_buttons(payload["offer_id"], payload["available_lots"])
    callback_message = callback.message
    is_private_suggestion_message = bool(
        callback_message
        and callback_message.chat
        and callback_message.chat.id == target_chat_id
        and callback_message.chat.id != settings.channel_id
    )

    if is_private_suggestion_message:
        try:
            await callback_message.edit_text(payload["message"], reply_markup=reply_markup)
            await upsert_trade_suggestion_record(
                offer_id=int(payload["offer_id"]),
                chat_id=target_chat_id,
                message_id=callback_message.message_id,
                requested_amount=int(payload["requested_amount"]),
            )
            schedule_trade_suggestion_cleanup(bot, int(payload["offer_id"]), target_chat_id, callback_message.message_id)
            return
        except Exception as exc:
            logger.debug(f"Failed to update existing trade suggestion message: {exc}")

    sent_message = await bot.send_message(
        chat_id=target_chat_id,
        text=payload["message"],
        reply_markup=reply_markup,
    )
    await upsert_trade_suggestion_record(
        offer_id=int(payload["offer_id"]),
        chat_id=target_chat_id,
        message_id=sent_message.message_id,
        requested_amount=int(payload["requested_amount"]),
    )
    schedule_trade_suggestion_cleanup(bot, int(payload["offer_id"]), target_chat_id, sent_message.message_id)


def _json_response_body(response: JSONResponse) -> dict:
    try:
        return json.loads(response.body.decode("utf-8"))
    except Exception:
        return {}


async def _execute_confirmed_channel_trade_via_shared_command(
    *,
    callback: types.CallbackQuery,
    callback_data: ChannelTradeCallback,
    user: User,
    bot: Bot,
    session,
    offer: Offer,
    actual_amount: int,
) -> None:
    if not hasattr(user, "role"):
        setattr(user, "role", UserRole.STANDARD)
    if not hasattr(user, "trading_restricted_until"):
        setattr(user, "trading_restricted_until", None)
    background_tasks = BackgroundTasks()
    try:
        result = await _execute_trade_authoritatively(
            trade_data=TradeCreate(
                offer_id=offer.id,
                offer_public_id=getattr(offer, "offer_public_id", None),
                quantity=actual_amount,
                idempotency_key=f"bot:{user.id}:{offer.id}:{actual_amount}:{callback.id}",
            ),
            background_tasks=background_tasks,
            db=session,
            context=EffectiveOwnerActor(
                owner_user=user,
                actor_user=user,
                relation=None,
                is_accountant_context=False,
            ),
            edge_received_at=datetime.utcnow(),
            request_source_surface=OfferRequestSourceSurface.TELEGRAM_BOT,
            request_source_server=current_server(),
        )
    except HTTPException as exc:
        await callback.answer(f"❌ {exc.detail or 'امکان انجام این معامله وجود ندارد.'}", show_alert=True)
        return

    if isinstance(result, JSONResponse):
        body = _json_response_body(result)
        if result.status_code == 409 and body.get("error_code") == "TRADE_LOT_UNAVAILABLE":
            target_chat_id = user.telegram_id or callback.from_user.id
            await send_or_update_trade_suggestion_message(
                callback=callback,
                bot=bot,
                target_chat_id=target_chat_id,
                payload=body,
            )
            await callback.answer("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)
            return
        detail = body.get("detail") if isinstance(body, dict) else None
        await callback.answer(f"❌ {detail or 'امکان انجام این معامله وجود ندارد.'}", show_alert=True)
        return

    try:
        await background_tasks()
    except Exception as exc:
        logger.debug(f"Failed to run channel trade background tasks: {exc}")

    try:
        if callback.message and callback.message.chat.id != settings.channel_id:
            await callback.message.edit_reply_markup(reply_markup=None)
            await remove_trade_suggestion_record(offer.id, callback.message.chat.id, callback.message.message_id)
    except Exception as exc:
        logger.debug(f"Failed to clear private suggestion buttons: {exc}")

    await callback.answer("معامله ثبت شد ✅", show_alert=False)


@router.callback_query(ChannelTradeCallback.filter())
async def handle_channel_trade(callback: types.CallbackQuery, callback_data: ChannelTradeCallback, user: Optional[User], bot: Bot):
    """کلیک روی دکمه پست کانال - دابل‌کلیک برای تایید"""
    if not user:
        await callback.answer()
        return
    
    # ===== بررسی مسدودیت کاربر =====
    if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
        await callback.answer("⛔ حساب شما مسدود است", show_alert=True)
        return
    
    # پارس callback_data
    offer_id = callback_data.offer_id
    trade_amount = callback_data.amount
    
    # ===== بررسی محدودیت کاربر =====
    # باید قبل از قفل offer انجام شود
    allowed, error_msg = check_user_limits(user, 'trade', trade_amount or 1)
    if not allowed:
        await callback.answer(f"⚠️ {error_msg}", show_alert=True)
        return
    
    async with AsyncSessionLocal() as session:
        # اول قفل را بگیر، سپس روابط را بارگذاری کن
        # FOR UPDATE با LEFT OUTER JOIN سازگار نیست
        stmt = select(Offer).where(Offer.id == offer_id).with_for_update()
        offer = (await session.execute(stmt)).scalar_one_or_none()
        
        if offer:
            # بارگذاری روابط بعد از گرفتن قفل
            await session.refresh(offer, ["user", "commodity"])
        
        if not offer:
            await callback.answer()
            return
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.answer()
            return
        
        if offer.user_id == user.id:
            await callback.answer()
            return
        
        # ===== بررسی بلاک =====
        from core.services.block_service import is_blocked
        is_blocked_check, blocker_id = await is_blocked(session, offer.user_id, user.id)
        if is_blocked_check:
            if blocker_id == user.id:
                # کاربر فعلی این لفظ‌دهنده را بلاک کرده
                await callback.answer("⚠️ شما این کاربر را مسدود کرده‌اید!", show_alert=True)
            else:
                # لفظ‌دهنده کاربر فعلی را بلاک کرده
                await callback.answer("❌ این لفظ در دسترس نیست.", show_alert=True)
            return
        # =======================
        
        # تعداد واقعی معامله
        actual_amount = trade_amount or offer.remaining_quantity or offer.quantity
        if is_remote_home(offer.home_server):
            is_confirmed = await check_double_click(user.id, offer_id, actual_amount, timeout=PRIVATE_SUGGESTION_CONFIRM_TIMEOUT)
            local_available_amounts = get_available_trade_amounts(
                quantity=offer.quantity,
                remaining_quantity=offer.remaining_quantity or offer.quantity,
                is_wholesale=offer.is_wholesale,
                lot_sizes=offer.lot_sizes,
            )

            if not is_confirmed:
                if callback.message and callback.message.chat.id != settings.channel_id:
                    try:
                        pending_keyboard = build_trade_amount_buttons(offer.id, local_available_amounts, pending_amount=actual_amount)
                        await callback.message.edit_reply_markup(reply_markup=pending_keyboard)
                        await upsert_trade_suggestion_record(
                            offer_id=offer.id,
                            chat_id=callback.message.chat.id,
                            message_id=callback.message.message_id,
                            requested_amount=actual_amount,
                        )
                        schedule_trade_suggestion_cleanup(bot, offer.id, callback.message.chat.id, callback.message.message_id)
                        schedule_trade_suggestion_pending_reset(bot, offer.id)
                    except Exception as exc:
                        logger.debug(f"Failed to set pending state for remote-home offer: {exc}")
                await callback.answer("برای تایید دوباره روی همان دکمه بزنید ☑️", show_alert=False)
                return

            status_code, body = await forward_trade_to_home_server(
                offer.home_server,
                {
                    "offer_id": offer.id,
                    "offer_public_id": getattr(offer, "offer_public_id", None),
                    "quantity": actual_amount,
                    "responder_user_id": user.id,
                    "edge_received_at": datetime.utcnow().isoformat(),
                    "source_surface": OfferRequestSourceSurface.TELEGRAM_BOT.value,
                    "source_server": current_server(),
                    "idempotency_key": f"bot:{user.id}:{offer.id}:{actual_amount}:{callback.id}",
                },
            )

            if status_code == 409 and isinstance(body, dict) and body.get("error_code") == "TRADE_LOT_UNAVAILABLE":
                target_chat_id = user.telegram_id or callback.from_user.id
                await send_or_update_trade_suggestion_message(
                    callback=callback,
                    bot=bot,
                    target_chat_id=target_chat_id,
                    payload=body,
                )
                await callback.answer("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)
                return

            if 200 <= status_code < 300:
                try:
                    if callback.message and callback.message.chat.id != settings.channel_id:
                        await callback.message.edit_reply_markup(reply_markup=None)
                        await remove_trade_suggestion_record(offer.id, callback.message.chat.id, callback.message.message_id)
                except Exception as exc:
                    logger.debug(f"Failed to clear remote-home suggestion buttons: {exc}")
                await callback.answer("معامله ثبت شد ✅", show_alert=False)
                return

            detail = body.get("detail") if isinstance(body, dict) else None
            await callback.answer(f"❌ {detail or 'امکان انجام این معامله وجود ندارد.'}", show_alert=True)
            return

        
        remaining = offer.remaining_quantity or offer.quantity
        is_valid_amount, amount_error, actual_amount, available_amounts = validate_offer_trade_amount(
            quantity=offer.quantity,
            remaining_quantity=remaining,
            is_wholesale=offer.is_wholesale,
            lot_sizes=offer.lot_sizes,
            requested_amount=actual_amount,
        )
        if not is_valid_amount:
            if not offer.is_wholesale and available_amounts and amount_error == "این لات دیگر موجود نیست.":
                suggestion_payload = build_lot_unavailable_suggestion_payload(
                    offer_id=offer.id,
                    requested_amount=actual_amount,
                    offer_type=offer.offer_type,
                    commodity_name=offer.commodity.name if offer.commodity else None,
                    price=offer.price,
                    remaining_quantity=remaining,
                    available_amounts=available_amounts,
                )
                target_chat_id = user.telegram_id or callback.from_user.id
                await send_or_update_trade_suggestion_message(
                    callback=callback,
                    bot=bot,
                    target_chat_id=target_chat_id,
                    payload=suggestion_payload,
                )
                await callback.answer("پیشنهاد جدید برای شما ارسال شد.", show_alert=False)
            else:
                await callback.answer(f"❌ {amount_error}", show_alert=True)
            return
        
        # بررسی دابل‌کلیک با Redis (0.5 ثانیه)
        is_confirmed = await check_double_click(user.id, offer_id, actual_amount, timeout=PRIVATE_SUGGESTION_CONFIRM_TIMEOUT)
        
        if is_confirmed:
            await _execute_confirmed_channel_trade_via_shared_command(
                callback=callback,
                callback_data=callback_data,
                user=user,
                bot=bot,
                session=session,
                offer=offer,
                actual_amount=actual_amount,
            )
            return
        else:
            # کلیک اول - Redis ثبت کرده، راهنمایی به کاربر
            if callback.message and callback.message.chat.id != settings.channel_id:
                try:
                    pending_keyboard = build_trade_amount_buttons(offer.id, available_amounts, pending_amount=actual_amount)
                    await callback.message.edit_reply_markup(reply_markup=pending_keyboard)
                    await upsert_trade_suggestion_record(
                        offer_id=offer.id,
                        chat_id=callback.message.chat.id,
                        message_id=callback.message.message_id,
                        requested_amount=actual_amount,
                    )
                    schedule_trade_suggestion_cleanup(bot, offer.id, callback.message.chat.id, callback.message.message_id)
                    schedule_trade_suggestion_pending_reset(bot, offer.id)
                except Exception as exc:
                    logger.debug(f"Failed to set pending confirmation state for suggestion message: {exc}")
            await callback.answer("برای تایید دوباره روی همان دکمه بزنید ☑️", show_alert=False)
