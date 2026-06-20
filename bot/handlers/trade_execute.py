import asyncio
import logging
import json
import hashlib
from aiogram import Router, F, types, Bot
from typing import Optional, Mapping
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus
from models.trade import Trade
from core.config import settings
from core.db import AsyncSessionLocal
from bot.utils.redis_helpers import check_double_click
from core.utils import check_user_limits, to_jalali_str, utc_now
from core.enums import UserRole
from bot.callbacks import ChannelTradeCallback, ChannelTradePublicCallback
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
from core.trading_observability import log_trading_event
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

OFFER_UNAVAILABLE_CALLBACK_MESSAGE = "این لفظ دیگر در دسترس نیست."
OFFER_INACTIVE_CALLBACK_MESSAGE = "این لفظ دیگر فعال نیست."
BOT_REMOTE_HOME_FORWARD_TIMEOUT_SECONDS = 2.0


def _callback_offer_public_id(callback_data) -> str | None:
    public_id = getattr(callback_data, "offer_public_id", None)
    if public_id is None:
        return None
    public_id = str(public_id).strip()
    return public_id or None


def _callback_offer_id(callback_data) -> int | None:
    try:
        offer_id = int(getattr(callback_data, "offer_id", 0))
    except (TypeError, ValueError):
        return None
    return offer_id if offer_id > 0 else None


async def _load_callback_offer(session, callback_data) -> Offer | None:
    public_id = _callback_offer_public_id(callback_data)
    if public_id:
        stmt = select(Offer).where(Offer.offer_public_id == public_id).with_for_update()
    else:
        offer_id = _callback_offer_id(callback_data)
        if offer_id is None:
            return None
        stmt = select(Offer).where(Offer.id == offer_id).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def _compact_idempotency_key(prefix: str, raw_value: object, *, max_length: int = 64) -> str:
    raw_text = str(raw_value or "").strip()
    candidate = f"{prefix}:{raw_text}"
    if len(candidate) <= max_length:
        return candidate
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"[:max_length]


def _channel_trade_idempotency_key(
    *,
    callback: types.CallbackQuery,
    user: User,
    offer: Offer,
    actual_amount: int,
) -> str:
    callback_message = getattr(callback, "message", None)
    message_id = getattr(callback_message, "message_id", None)
    offer_ref = getattr(offer, "offer_public_id", None) or f"legacy_offer:{getattr(offer, 'id', '')}"
    remaining = getattr(offer, "remaining_quantity", None)
    if remaining is None:
        remaining = getattr(offer, "quantity", "")
    return _compact_idempotency_key(
        "telegram_callback",
        f"{getattr(user, 'id', '')}:{offer_ref}:{actual_amount}:remaining:{remaining}:{message_id or 'no_message'}",
    )


def _snapshot_get(snapshot: object, key: str, default: object = None) -> object:
    if isinstance(snapshot, Mapping):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


def _safe_offer_snapshot_id(snapshot: object) -> object:
    if isinstance(snapshot, Mapping):
        return snapshot.get("id")
    return getattr(snapshot, "__dict__", {}).get("id")


def _safe_enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _remote_trade_offer_snapshot(offer: Offer | object) -> dict[str, object]:
    """Capture offer fields while the ORM object is still attached and loaded."""
    commodity = getattr(offer, "commodity", None)
    offer_user = getattr(offer, "user", None)
    return {
        "id": getattr(offer, "id", None),
        "offer_type": _safe_enum_value(getattr(offer, "offer_type", None)),
        "price": getattr(offer, "price", None),
        "notes": getattr(offer, "notes", None),
        "commodity_name": getattr(commodity, "name", None),
        "counterparty_name": getattr(offer_user, "account_name", None),
    }


def _build_remote_trade_success_message(body: object, fallback_offer: object, amount: int) -> str:
    fallback_notes = _snapshot_get(fallback_offer, "notes")
    if isinstance(body, dict):
        trade_type = str(body.get("trade_type") or "").lower()
        if trade_type == "buy":
            trade_emoji = "🟢"
            trade_label = "خرید"
        elif trade_type == "sell":
            trade_emoji = "🔴"
            trade_label = "فروش"
        else:
            trade_emoji = "✅"
            trade_label = "معامله"

        price = body.get("price") or _snapshot_get(fallback_offer, "price", 0) or 0
        quantity = body.get("quantity") or amount
        commodity_name = body.get("commodity_name") or _snapshot_get(fallback_offer, "commodity_name") or "نامشخص"
        counterparty_name = (
            body.get("counterparty_name")
            or body.get("offer_user_name")
            or _snapshot_get(fallback_offer, "counterparty_name")
            or "نامشخص"
        )
        trade_number = body.get("trade_number")
        created_at = body.get("created_at")
        offer_notes = body.get("offer_notes") or fallback_notes

        lines = [
            f"{trade_emoji} {trade_label}",
            "",
            f"💰 فی: {int(price):,}" if isinstance(price, (int, float)) else f"💰 فی: {price}",
            f"📦 تعداد: {quantity}",
            f"🏷️ کالا: {commodity_name}",
            f"👤 طرف معامله: {counterparty_name}",
        ]
        if trade_number:
            lines.append(f"🔢 شماره معامله: {trade_number}")
        if created_at:
            lines.append(f"🕐 زمان معامله: {created_at}")
        normalized_notes = " ".join(str(offer_notes or "").split())
        if normalized_notes:
            lines.append(f"📝 توضیحات: {normalized_notes}")
        return "\n".join(lines)

    fallback_offer_type = str(_safe_enum_value(_snapshot_get(fallback_offer, "offer_type")) or "").lower()
    if fallback_offer_type == OfferType.SELL.value:
        fallback_trade_emoji = "🟢"
        fallback_trade_label = "خرید"
    elif fallback_offer_type == OfferType.BUY.value:
        fallback_trade_emoji = "🔴"
        fallback_trade_label = "فروش"
    else:
        fallback_trade_emoji = "✅"
        fallback_trade_label = "معامله"

    lines = [
        f"{fallback_trade_emoji} {fallback_trade_label}",
        "",
        f"💰 فی: {(_snapshot_get(fallback_offer, 'price', 0) or 0):,}",
        f"📦 تعداد: {amount}",
        f"🏷️ کالا: {_snapshot_get(fallback_offer, 'commodity_name') or 'نامشخص'}",
    ]
    fallback_counterparty = _snapshot_get(fallback_offer, "counterparty_name")
    if fallback_counterparty:
        lines.append(f"👤 طرف معامله: {fallback_counterparty}")
    normalized_notes = " ".join(str(fallback_notes or "").split())
    if normalized_notes:
        lines.append(f"📝 توضیحات: {normalized_notes}")
    return "\n".join(lines)


async def _notify_remote_trade_success(
    bot: Bot,
    user: User,
    offer: object,
    amount: int,
    body: object,
    *,
    fallback_chat_id: int | None = None,
    idempotency_key: str | None = None,
) -> None:
    target_chat_id = fallback_chat_id or getattr(user, "telegram_id", None)
    if not target_chat_id:
        log_trading_event(
            logger,
            "remote_home_trade_success_message.skipped",
            level="warning",
            action="trading_side_effect",
            result="noop",
            side_effect="telegram_message",
            offer_id=_safe_offer_snapshot_id(offer),
            has_idempotency_key=bool(idempotency_key),
            reason="missing_actor",
        )
        return
    try:
        await bot.send_message(
            chat_id=target_chat_id,
            text=_build_remote_trade_success_message(body, offer, amount),
        )
        log_trading_event(
            logger,
            "remote_home_trade_success_message.sent",
            action="trading_side_effect",
            result="success",
            side_effect="telegram_message",
            offer_id=_safe_offer_snapshot_id(offer),
            has_idempotency_key=bool(idempotency_key),
        )
    except Exception as exc:
        log_trading_event(
            logger,
            "remote_home_trade_success_message.failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            offer_id=_safe_offer_snapshot_id(offer),
            has_idempotency_key=bool(idempotency_key),
            error_class=type(exc).__name__,
        )


async def _notify_remote_trade_success_when_recovered(
    *,
    bot: Bot,
    user: User,
    offer_snapshot: object,
    amount: int,
    idempotency_key: str,
    fallback_chat_id: int | None,
) -> None:
    try:
        recovered_body = await _wait_for_forwarded_trade_completion(
            idempotency_key,
            grace_seconds=max(settings.trade_forward_grace_seconds, 8),
        )
        if not recovered_body:
            log_trading_event(
                logger,
                "remote_home_trade_success_message.recovery_missed",
                level="warning",
                action="trading_side_effect",
                result="noop",
                side_effect="telegram_message",
                offer_id=_safe_offer_snapshot_id(offer_snapshot),
                has_idempotency_key=bool(idempotency_key),
            )
            return
        await _notify_remote_trade_success(
            bot,
            user,
            offer_snapshot,
            amount,
            recovered_body,
            fallback_chat_id=fallback_chat_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        log_trading_event(
            logger,
            "remote_home_trade_success_message.recovery_failed",
            level="warning",
            action="trading_side_effect",
            result="failure",
            side_effect="telegram_message",
            offer_id=_safe_offer_snapshot_id(offer_snapshot),
            has_idempotency_key=bool(idempotency_key),
            error_class=type(exc).__name__,
        )


def _schedule_remote_trade_success_recovery(
    *,
    bot: Bot,
    user: User,
    offer_snapshot: object,
    amount: int,
    idempotency_key: str,
    fallback_chat_id: int | None,
) -> None:
    asyncio.create_task(
        _notify_remote_trade_success_when_recovered(
            bot=bot,
            user=user,
            offer_snapshot=offer_snapshot,
            amount=amount,
            idempotency_key=idempotency_key,
            fallback_chat_id=fallback_chat_id,
        )
    )


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
        offer_public_id=getattr(offer, "offer_public_id", None),
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
    reply_markup = build_trade_amount_buttons(
        payload["offer_id"],
        payload["available_lots"],
        offer_public_id=payload.get("offer_public_id"),
    )
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


def _trade_model_to_remote_home_body(trade: Trade | object) -> dict[str, object | None]:
    commodity = getattr(trade, "commodity", None)
    offer_user = getattr(trade, "offer_user", None)
    offer = getattr(trade, "offer", None)
    return {
        "trade_number": getattr(trade, "trade_number", None),
        "trade_type": getattr(getattr(trade, "trade_type", None), "value", getattr(trade, "trade_type", None)),
        "commodity_name": getattr(commodity, "name", None),
        "quantity": getattr(trade, "quantity", None),
        "price": getattr(trade, "price", None),
        "created_at": to_jalali_str(getattr(trade, "created_at", None)) or "",
        "counterparty_name": getattr(offer_user, "full_name", None) or getattr(offer_user, "account_name", None),
        "offer_notes": getattr(offer, "notes", None),
    }


async def _wait_for_forwarded_trade_completion(
    idempotency_key: str | None,
    *,
    grace_seconds: int | float | None = None,
) -> dict[str, object | None] | None:
    if not idempotency_key:
        return None
    wait_seconds = max(grace_seconds if grace_seconds is not None else settings.trade_forward_grace_seconds, 1)
    attempts = max(1, int(wait_seconds / 0.25))
    for attempt in range(attempts):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(OfferRequest)
                .where(
                    OfferRequest.idempotency_key == idempotency_key,
                    OfferRequest.result_status == OfferRequestStatus.COMPLETED_TRADE,
                )
                .order_by(OfferRequest.id.desc())
            )
            ledger = result.scalar_one_or_none()
            trade_id = getattr(ledger, "resulting_trade_id", None) if ledger else None
            if trade_id:
                trade_result = await session.execute(
                    select(Trade)
                    .options(
                        selectinload(Trade.offer_user),
                        selectinload(Trade.responder_user),
                        selectinload(Trade.commodity),
                        selectinload(Trade.offer),
                    )
                    .where(Trade.id == trade_id)
                )
                trade = trade_result.scalar_one_or_none()
                if trade:
                    return _trade_model_to_remote_home_body(trade)
        if attempt < attempts - 1:
            await asyncio.sleep(0.25)
    return None


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
                idempotency_key=_channel_trade_idempotency_key(
                    callback=callback,
                    user=user,
                    offer=offer,
                    actual_amount=actual_amount,
                ),
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
    await _handle_channel_trade(callback, callback_data, user, bot)


@router.callback_query(ChannelTradePublicCallback.filter())
async def handle_channel_trade_public(
    callback: types.CallbackQuery,
    callback_data: ChannelTradePublicCallback,
    user: Optional[User],
    bot: Bot,
):
    await _handle_channel_trade(callback, callback_data, user, bot)


async def _handle_channel_trade(callback: types.CallbackQuery, callback_data, user: Optional[User], bot: Bot):
    """کلیک روی دکمه پست کانال - دابل‌کلیک برای تایید"""
    if not user:
        await callback.answer()
        return
    
    # ===== بررسی مسدودیت کاربر =====
    if user.trading_restricted_until and user.trading_restricted_until > datetime.utcnow():
        await callback.answer("⛔ حساب شما مسدود است", show_alert=True)
        return
    
    # پارس callback_data
    offer_id = _callback_offer_id(callback_data)
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
        offer = await _load_callback_offer(session, callback_data)
        
        if offer:
            # بارگذاری روابط بعد از گرفتن قفل
            await session.refresh(offer, ["user", "commodity"])
        
        if not offer:
            await callback.answer(OFFER_UNAVAILABLE_CALLBACK_MESSAGE, show_alert=True)
            return

        resolved_offer_id = getattr(offer, "id", None)
        if resolved_offer_id is not None:
            offer_id = resolved_offer_id
        
        if offer.status != OfferStatus.ACTIVE:
            await callback.answer(OFFER_INACTIVE_CALLBACK_MESSAGE, show_alert=True)
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
                        pending_keyboard = build_trade_amount_buttons(
                            offer.id,
                            local_available_amounts,
                            pending_amount=actual_amount,
                            offer_public_id=getattr(offer, "offer_public_id", None),
                        )
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

            idempotency_key = _channel_trade_idempotency_key(
                callback=callback,
                user=user,
                offer=offer,
                actual_amount=actual_amount,
            )
            offer_snapshot = _remote_trade_offer_snapshot(offer)
            callback_chat_id = getattr(getattr(callback, "from_user", None), "id", None)
            forward_payload = {
                "offer_id": offer.id,
                "offer_public_id": getattr(offer, "offer_public_id", None),
                "quantity": actual_amount,
                "responder_user_id": user.id,
                "edge_received_at": utc_now().isoformat(),
                "source_surface": OfferRequestSourceSurface.TELEGRAM_BOT.value,
                "source_server": current_server(),
                "idempotency_key": idempotency_key,
            }
            configured_forward_timeout = getattr(
                settings,
                "trade_forward_timeout_seconds",
                BOT_REMOTE_HOME_FORWARD_TIMEOUT_SECONDS,
            )
            status_code, body = await forward_trade_to_home_server(
                offer.home_server,
                forward_payload,
                timeout_seconds=min(configured_forward_timeout, BOT_REMOTE_HOME_FORWARD_TIMEOUT_SECONDS),
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
                await _notify_remote_trade_success(
                    bot,
                    user,
                    offer_snapshot,
                    actual_amount,
                    body,
                    fallback_chat_id=callback_chat_id,
                    idempotency_key=idempotency_key,
                )
                await callback.answer("معامله ثبت شد ✅", show_alert=False)
                return

            if status_code == 504:
                try:
                    await session.rollback()
                except Exception as exc:
                    logger.debug(f"Failed to rollback before remote-home completion recovery: {exc}")
                _schedule_remote_trade_success_recovery(
                    bot=bot,
                    user=user,
                    offer_snapshot=offer_snapshot,
                    amount=actual_amount,
                    idempotency_key=idempotency_key,
                    fallback_chat_id=callback_chat_id,
                )
                await callback.answer(
                    "درخواست معامله ارسال شد؛ نتیجه تا چند لحظه دیگر همگام می‌شود.",
                    show_alert=False,
                )
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
                    offer_public_id=getattr(offer, "offer_public_id", None),
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
                    pending_keyboard = build_trade_amount_buttons(
                        offer.id,
                        available_amounts,
                        pending_amount=actual_amount,
                        offer_public_id=getattr(offer, "offer_public_id", None),
                    )
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
