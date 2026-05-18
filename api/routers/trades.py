# api/routers/trades.py
"""
API Router for Trade Management - MiniApp Integration
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.db import get_db
from core.config import settings
from core.enums import NotificationLevel, NotificationCategory
from core.utils import (
    check_user_limits, increment_user_counter, to_jalali_str,
    create_user_notification, send_telegram_notification
)
from core.services.accountant_relation_service import build_trade_notification_audience_user_ids
from core.services.trade_service import (
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    validate_offer_trade_amount,
)
from core.services.user_account_status_service import is_user_trade_blocked
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity
from api.deps import EffectiveOwnerActor, get_current_user, get_effective_owner_actor_context
from core.server_routing import current_server, is_remote_home, normalize_server
from core.trade_forwarding import forward_trade_to_home_server, verify_internal_signature


logger = logging.getLogger(__name__)


router = APIRouter(
    tags=["Trades"],
)


# --- Pydantic Schemas ---

class TradeCreate(BaseModel):
    """ایجاد معامله جدید"""
    offer_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    idempotency_key: Optional[str] = None


class InternalTradeExecuteRequest(BaseModel):
    """درخواست داخلی اجرای معامله روی سرور مرجع آفر"""
    offer_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    responder_user_id: int = Field(..., gt=0)
    actor_user_id: Optional[int] = Field(None, gt=0)
    edge_received_at: datetime
    source_server: str
    idempotency_key: Optional[str] = None


class TradeResponse(BaseModel):
    """پاسخ معامله"""
    id: int
    trade_number: int
    offer_id: Optional[int]
    trade_type: str
    commodity_id: int
    commodity_name: str
    quantity: int
    price: int
    status: str
    offer_user_id: Optional[int]
    offer_user_name: Optional[str]
    responder_user_id: Optional[int]
    responder_user_name: Optional[str]
    created_at: str
    
    class Config:
        from_attributes = True


# --- Helper Functions ---

def trade_to_response(trade: Trade) -> TradeResponse:
    """تبدیل مدل Trade به پاسخ API"""
    return TradeResponse(
        id=trade.id,
        trade_number=trade.trade_number,
        offer_id=trade.offer_id,
        trade_type=trade.trade_type.value,
        commodity_id=trade.commodity_id,
        commodity_name=trade.commodity.name if trade.commodity else "نامشخص",
        quantity=trade.quantity,
        price=trade.price,
        status=trade.status.value,
        offer_user_id=trade.offer_user_id,
        offer_user_name=trade.offer_user.account_name if trade.offer_user else None,
        responder_user_id=trade.responder_user_id,
        responder_user_name=trade.responder_user.account_name if trade.responder_user else None,
        created_at=to_jalali_str(trade.created_at) or ""
    )


async def send_telegram_message(chat_id: int, text: str) -> bool:
    """ارسال پیام به تلگرام"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending telegram message: {e}")
        return False


async def update_channel_buttons(offer: Offer) -> bool:
    """آپدیت دکمه‌های پست کانال"""
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id or not offer.channel_message_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
    
    if offer.remaining_quantity <= 0 or offer.status != OfferStatus.ACTIVE:
        # حذف دکمه‌ها
        payload = {
            "chat_id": channel_id,
            "message_id": offer.channel_message_id
        }
    else:
        # ساخت دکمه‌های جدید
        if offer.is_wholesale or not offer.lot_sizes:
            buttons = [[{"text": f"{offer.remaining_quantity} عدد", "callback_data": f"channel_trade:{offer.id}:{offer.remaining_quantity}"}]]
        else:
            valid_lots = get_available_trade_amounts(
                quantity=offer.quantity,
                remaining_quantity=offer.remaining_quantity,
                is_wholesale=False,
                lot_sizes=offer.lot_sizes,
            )
            if not valid_lots:
                payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
                buttons = None
            else:
                buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer.id}:{a}"} for a in valid_lots]]
        
        if buttons is not None:
            payload = {
                "chat_id": channel_id,
                "message_id": offer.channel_message_id,
                "reply_markup": {"inline_keyboard": buttons}
            }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error updating channel buttons: {e}")
        return False


# ===== Sync Wrappers for BackgroundTasks =====
# استفاده از httpx sync client به جای asyncio.run برای جلوگیری از مشکلات event loop

def send_telegram_message_sync(chat_id: int, text: str) -> bool:
    """نسخه sync برای استفاده در BackgroundTasks"""
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token or not chat_id:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    
    try:
        # استفاده از httpx sync client به جای asyncio.run
        response = httpx.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"[Background] Error sending telegram message: {e}")
        return False


def update_channel_buttons_sync(offer_id: int, remaining_quantity: int, status, lot_sizes) -> bool:
    """نسخه sync برای استفاده در BackgroundTasks"""
    from core.db import AsyncSessionLocal
    import asyncio
    
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return False
    
    # برای گرفتن channel_message_id باید از DB بخوانیم
    # این کار در یک thread جداگانه انجام می‌شود
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _update_channel_buttons_async(offer_id, remaining_quantity, status, lot_sizes)
            )
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"[Background] Error updating channel buttons: {e}")
        return False


async def _update_channel_buttons_async(offer_id: int, remaining_quantity: int, offer_status, lot_sizes) -> bool:
    """Helper async function - باید offer را از دیتابیس بخواند"""
    from core.db import AsyncSessionLocal
    
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return False
    
    # گرفتن اطلاعات offer از دیتابیس
    async with AsyncSessionLocal() as session:
        offer = await session.get(Offer, offer_id)
        if not offer or not offer.channel_message_id:
            return False
        
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        
        if remaining_quantity <= 0 or offer_status != OfferStatus.ACTIVE:
            payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
        else:
            if offer.is_wholesale or not lot_sizes:
                buttons = [[{"text": f"{remaining_quantity} عدد", "callback_data": f"channel_trade:{offer_id}:{remaining_quantity}"}]]
            else:
                valid_lots = get_available_trade_amounts(
                    quantity=offer.quantity,
                    remaining_quantity=remaining_quantity,
                    is_wholesale=False,
                    lot_sizes=lot_sizes,
                )
                if not valid_lots:
                    payload = {"chat_id": channel_id, "message_id": offer.channel_message_id}
                    buttons = None
                else:
                    buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer_id}:{a}"} for a in valid_lots]]
            
            if buttons is not None:
                payload = {"chat_id": channel_id, "message_id": offer.channel_message_id, "reply_markup": {"inline_keyboard": buttons}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=10)
        return response.status_code == 200


# --- Endpoints ---

def _normalize_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def _is_offer_expired_for_trade(offer: Offer, edge_received_at: Optional[datetime]) -> bool:
    from core.trading_settings import get_trading_settings_async

    ts = await get_trading_settings_async()
    if ts.offer_expiry_minutes <= 0 or not offer.created_at:
        return False

    created_at = _normalize_naive_utc(offer.created_at) or datetime.utcnow()
    expiry_at = created_at + timedelta(minutes=ts.offer_expiry_minutes)
    now = datetime.utcnow()
    edge_at = _normalize_naive_utc(edge_received_at)

    if edge_at and edge_at <= expiry_at:
        transit_seconds = max(0.0, (now - edge_at).total_seconds())
        if transit_seconds <= settings.trade_forward_grace_seconds:
            return False

    return now > expiry_at


async def _forward_trade_if_remote_home(
    db: AsyncSession,
    trade_data: TradeCreate,
    context: EffectiveOwnerActor,
    edge_received_at: datetime,
) -> Optional[JSONResponse]:
    offer = await db.get(Offer, trade_data.offer_id)
    if not offer or not is_remote_home(offer.home_server):
        return None

    owner_user = getattr(context, "owner_user", context)
    actor_user = getattr(context, "actor_user", owner_user)
    payload = {
        "offer_id": trade_data.offer_id,
        "quantity": trade_data.quantity,
        "responder_user_id": owner_user.id,
        "edge_received_at": edge_received_at.isoformat(),
        "source_server": current_server(),
        "idempotency_key": trade_data.idempotency_key,
    }
    if getattr(actor_user, "id", None) != owner_user.id:
        payload["actor_user_id"] = actor_user.id
    status_code, body = await forward_trade_to_home_server(offer.home_server, payload)
    return JSONResponse(status_code=status_code, content=body)


async def _execute_trade_authoritatively(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
    edge_received_at: Optional[datetime] = None,
):
    """
    انجام معامله روی یک لفظ از MiniApp
    """
    from core.enums import UserRole
    import jdatetime
    owner_user = context.owner_user
    actor_user = context.actor_user
    
    # بررسی نقش
    if owner_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )

    if is_user_trade_blocked(owner_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="حساب شما غیرفعال است و امکان انجام معامله ندارید.",
        )
    
    # بررسی مسدودیت (قبل از قفل)
    if owner_user.trading_restricted_until:
        if owner_user.trading_restricted_until > datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="حساب شما مسدود است."
            )
    
    # ===== قفل کاربر برای جلوگیری از Race Condition در محدودیت‌ها =====
    # اگر دو درخواست همزمان بیاید، اولی قفل می‌کند و دومی منتظر می‌ماند
    locked_user = await db.execute(
        select(User).where(User.id == owner_user.id).with_for_update()
    )
    owner_user = locked_user.scalar_one()
    
    # بررسی محدودیت معامله (حالا با قفل)
    allowed, error_msg = check_user_limits(owner_user, 'trade', trade_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # گرفتن لفظ با قفل
    offer = await db.get(Offer, trade_data.offer_id, with_for_update=True)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    expired_for_trade = await _is_offer_expired_for_trade(offer, edge_received_at)

    allow_in_flight_after_auto_expiry = (
        offer.status == OfferStatus.EXPIRED
        and edge_received_at is not None
        and not expired_for_trade
    )
    if (offer.status != OfferStatus.ACTIVE and not allow_in_flight_after_auto_expiry) or expired_for_trade:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ دیگر فعال نیست.")
    
    if offer.user_id == owner_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="نمی‌توانید روی لفظ خودتان معامله کنید.")
    
    # بررسی بلاک بین کاربران (پنهان - کاربر نباید متوجه بلاک شدن بشه)
    from core.services.block_service import is_blocked
    blocked, _ = await is_blocked(db, owner_user.id, offer.user_id)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="امکان انجام این معامله وجود ندارد."
        )
    
    is_valid_amount, amount_error, trade_quantity, available_amounts = validate_offer_trade_amount(
        quantity=offer.quantity,
        remaining_quantity=offer.remaining_quantity,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
        requested_amount=trade_data.quantity,
    )
    if not is_valid_amount:
        if (
            not offer.is_wholesale
            and available_amounts
            and amount_error == "این لات دیگر موجود نیست."
        ):
            await db.refresh(offer, ["commodity"])
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=build_lot_unavailable_suggestion_payload(
                    offer_id=offer.id,
                    requested_amount=trade_data.quantity,
                    offer_type=offer.offer_type,
                    commodity_name=offer.commodity.name if offer.commodity else None,
                    price=offer.price,
                    remaining_quantity=offer.remaining_quantity or offer.quantity,
                    available_amounts=available_amounts,
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=amount_error
        )
    
    # بارگذاری روابط لفظ
    await db.refresh(offer, ["user", "commodity"])

    if trade_data.idempotency_key:
        existing_trade = await db.execute(
            select(Trade)
            .options(
                selectinload(Trade.offer_user),
                selectinload(Trade.responder_user),
                selectinload(Trade.commodity),
            )
            .where(Trade.idempotency_key == trade_data.idempotency_key)
        )
        existing_trade_obj = existing_trade.scalar_one_or_none()
        if existing_trade_obj:
            return trade_to_response(existing_trade_obj)
    
    # گرفتن شماره معامله جدید
    max_trade_number = await db.scalar(select(func.max(Trade.trade_number)))
    new_trade_number = (max_trade_number or 9999) + 1
    
    # نوع معامله از دید پاسخ‌دهنده
    responder_trade_type = TradeType.BUY if offer.offer_type == OfferType.SELL else TradeType.SELL
    
    # ایجاد معامله
    new_trade = Trade(
        trade_number=new_trade_number,
        offer_id=offer.id,
        offer_user_id=offer.user_id,
        offer_user_mobile=offer.user.mobile_number if offer.user else None,
        responder_user_id=owner_user.id,
        responder_user_mobile=owner_user.mobile_number,
        actor_user_id=actor_user.id,
        commodity_id=offer.commodity_id,
        trade_type=responder_trade_type,
        quantity=trade_quantity,
        price=offer.price,
        status=TradeStatus.COMPLETED,
        idempotency_key=trade_data.idempotency_key,
    )
    db.add(new_trade)
    
    # آپدیت لفظ
    offer.remaining_quantity -= trade_quantity
    
    # بروزرسانی لات‌ها - حذف مقدار معامله شده از لیست
    if offer.remaining_quantity <= 0:
        if offer.lot_sizes is not None:
            from sqlalchemy.orm.attributes import flag_modified
            offer.lot_sizes = None
            flag_modified(offer, "lot_sizes")
    elif offer.lot_sizes:
        from sqlalchemy.orm.attributes import flag_modified
        new_lot_sizes = list(offer.lot_sizes)
        if trade_quantity in new_lot_sizes:
            new_lot_sizes.remove(trade_quantity)
        offer.lot_sizes = new_lot_sizes if new_lot_sizes else None
        flag_modified(offer, "lot_sizes")  # اجبار SQLAlchemy برای تشخیص تغییر
    
    if offer.remaining_quantity <= 0:
        offer.status = OfferStatus.COMPLETED
    
    # Commit با محافظت Optimistic Locking
    try:
        await db.commit()
    except Exception as e:
        # بررسی StaleDataError (تغییر همزمان توسط کاربر دیگر)
        if "StaleDataError" in str(type(e).__name__) or "could not update" in str(e).lower():
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="این لفظ توسط کاربر دیگری در حال معامله است. لطفاً دوباره تلاش کنید."
            )
        raise
    
    # بارگذاری روابط معامله
    result = await db.execute(
        select(Trade)
        .options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity)
        )
        .where(Trade.id == new_trade.id)
    )
    new_trade = result.scalar_one()
    
    # ===== ارسال پیام‌های تلگرام در Background (غیر-بلاکینگ) =====
    # این کار باعث می‌شود پاسخ API سریعتر برگردد
    
    # آپدیت دکمه‌های کانال (مستقیم - نه در background)
    try:
        await update_channel_buttons(offer)
    except Exception as e:
        logger.error(f"Failed to update channel buttons: {e}")
    
    # ارسال نوتیفیکیشن‌ها
    now = datetime.utcnow()
    jalali_dt = jdatetime.datetime.fromgregorian(datetime=now)
    trade_datetime = jalali_dt.strftime("%Y/%m/%d   %H:%M")
    
    # تعیین نوع و ایموجی
    if responder_trade_type == TradeType.BUY:
        respond_emoji = "🟢"
        respond_type_fa = "خرید"
        offer_emoji = "🔴"
        offer_type_fa = "فروش"
    else:
        respond_emoji = "🔴"
        respond_type_fa = "فروش"
        offer_emoji = "🟢"
        offer_type_fa = "خرید"
    
    # پیام برای پاسخ‌دهنده
    responder_msg = (
        f"{respond_emoji} <b>{respond_type_fa}</b>\n\n"
        f"💰 فی: {offer.price:,}\n"
        f"📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {offer.user.account_name if offer.user else 'نامشخص'}\n"
        f"🔢 شماره معامله: {new_trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
    )
    
    # پیام برای لفظ‌دهنده
    offer_owner_msg = (
        f"{offer_emoji} <b>{offer_type_fa}</b>\n\n"
        f"💰 فی: {offer.price:,}\n"
        f"📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {owner_user.account_name}\n"
        f"🔢 شماره معامله: {new_trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
    )
    
    # ارسال پیام‌های تلگرام (در background)
    background_tasks.add_task(send_telegram_message_sync, owner_user.telegram_id, responder_msg)
    if offer.user:
        background_tasks.add_task(send_telegram_message_sync, offer.user.telegram_id, offer_owner_msg)
    
    # ارسال نوتیفیکیشن اپ
    notif_msg_responder = (
        f"{respond_emoji} {respond_type_fa}\n"
        f"💰 فی: {offer.price:,} | 📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {offer.user.account_name if offer.user else 'نامشخص'}\n"
        f"🔢 شماره: {new_trade_number}"
    )
    
    notif_msg_owner = (
        f"{offer_emoji} {offer_type_fa}\n"
        f"💰 فی: {offer.price:,} | 📦 تعداد: {trade_quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {owner_user.account_name}\n"
        f"🔢 شماره: {new_trade_number}"
    )
    
    try:
        responder_audience = await build_trade_notification_audience_user_ids(db, [owner_user.id])
        offer_owner_audience = await build_trade_notification_audience_user_ids(db, [offer.user_id])

        for audience_user_id in responder_audience:
            await create_user_notification(
                db, audience_user_id, notif_msg_responder,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.TRADE
            )
        for audience_user_id in offer_owner_audience:
            await create_user_notification(
                db, audience_user_id, notif_msg_owner,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.TRADE
            )
    except:
        pass
    
    # افزایش شمارنده معامله
    # فقط پاسخ‌دهنده (کسی که روی لفظ دیگران معامله می‌کند) شمارنده‌اش افزایش می‌یابد
    # صاحب لفظ شمارنده‌اش افزایش نمی‌یابد (چون او فقط لفظ داده، فعالانه معامله نکرده)
    await increment_user_counter(db, owner_user, 'trade', trade_quantity)
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    await publish_event("trade:created", {
        "trade_number": new_trade_number,
        "offer_id": offer.id,
        "quantity": trade_quantity,
        "price": offer.price,
        "commodity_name": offer.commodity.name,
        "trade_type": responder_trade_type.value,
        "offer_user_id": offer.user_id,
        "offer_user_name": offer.user.account_name if offer.user else "ناشناس",
        "responder_user_id": owner_user.id,
        "responder_user_name": owner_user.account_name
    })
    await publish_event("offer:updated", {
        "id": offer.id,
        "remaining_quantity": offer.remaining_quantity,
        "lot_sizes": offer.lot_sizes,
        "status": offer.status.value
    })
    
    return trade_to_response(new_trade)


@router.post("/", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def create_trade(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    edge_received_at = datetime.utcnow()
    forwarded_response = await _forward_trade_if_remote_home(db, trade_data, context, edge_received_at)
    if forwarded_response is not None:
        return forwarded_response

    return await _execute_trade_authoritatively(
        trade_data=trade_data,
        background_tasks=background_tasks,
        db=db,
        context=context,
        edge_received_at=edge_received_at,
    )


@router.post("/internal/execute", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def execute_trade_internal(
    internal_data: InternalTradeExecuteRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal trade signature")

    offer = await db.get(Offer, internal_data.offer_id)
    if offer and normalize_server(offer.home_server) != current_server():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="این سرور مرجع آفر نیست.")

    responder = await db.get(User, internal_data.responder_user_id)
    if not responder or responder.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر درخواست‌دهنده یافت نشد")

    actor_user = responder
    if internal_data.actor_user_id and internal_data.actor_user_id != responder.id:
        actor_user = await db.get(User, internal_data.actor_user_id)
        if not actor_user or actor_user.is_deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کاربر اجراکننده یافت نشد")

    return await _execute_trade_authoritatively(
        trade_data=TradeCreate(
            offer_id=internal_data.offer_id,
            quantity=internal_data.quantity,
            idempotency_key=internal_data.idempotency_key,
        ),
        background_tasks=background_tasks,
        db=db,
        context=EffectiveOwnerActor(
            owner_user=responder,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=actor_user.id != responder.id,
        ),
        edge_received_at=internal_data.edge_received_at,
    )


@router.get("/my", response_model=List[TradeResponse])
async def get_my_trades(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات کاربر
    """
    from sqlalchemy import or_

    owner_user = context.owner_user
    
    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    ).where(
        or_(
            Trade.offer_user_id == owner_user.id,
            Trade.responder_user_id == owner_user.id
        )
    ).order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return [trade_to_response(t) for t in trades]


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت جزئیات یک معامله
    """
    result = await db.execute(
        select(Trade).options(
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity)
        ).where(Trade.id == trade_id)
    )
    trade = result.scalar_one_or_none()

    owner_user = context.owner_user
    
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="معامله یافت نشد.")
    
    # فقط طرفین معامله می‌توانند جزئیات را ببینند
    if trade.offer_user_id != owner_user.id and trade.responder_user_id != owner_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما به این معامله دسترسی ندارید.")
    
    return trade_to_response(trade)


@router.get("/with/{other_user_id}", response_model=List[TradeResponse])
async def get_trades_with_user(
    other_user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    دریافت تاریخچه معاملات با یک کاربر خاص
    """
    from sqlalchemy import and_, or_

    owner_user = context.owner_user
    
    # کاربر نمی‌تواند معاملات خودش با خودش را بگیرد (که منطقاً وجود ندارد)
    if other_user_id == owner_user.id:
        return []

    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    ).where(
        or_(
            and_(Trade.offer_user_id == owner_user.id, Trade.responder_user_id == other_user_id),
            and_(Trade.offer_user_id == other_user_id, Trade.responder_user_id == owner_user.id)
        )
    ).order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return [trade_to_response(t) for t in trades]
