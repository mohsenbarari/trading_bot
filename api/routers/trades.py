# api/routers/trades.py
"""
API Router for Trade Management - MiniApp Integration
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
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
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity
from api.deps import get_current_user


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/trades",
    tags=["Trades"],
)


# --- Pydantic Schemas ---

class TradeCreate(BaseModel):
    """ایجاد معامله جدید"""
    offer_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)


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
    if not bot_token:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
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
            # فیلتر lot_sizes که بیشتر از remaining نباشند
            valid_lots = [l for l in offer.lot_sizes if l <= offer.remaining_quantity]
            if offer.remaining_quantity not in valid_lots:
                valid_lots = [offer.remaining_quantity] + valid_lots
            valid_lots = sorted(set(valid_lots), reverse=True)
            buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer.id}:{a}"} for a in valid_lots]]
        
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
    if not bot_token:
        return False
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    
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
                valid_lots = [l for l in lot_sizes if l <= remaining_quantity]
                if remaining_quantity not in valid_lots:
                    valid_lots = [remaining_quantity] + valid_lots
                valid_lots = sorted(set(valid_lots), reverse=True)
                buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer_id}:{a}"} for a in valid_lots]]
            
            payload = {"chat_id": channel_id, "message_id": offer.channel_message_id, "reply_markup": {"inline_keyboard": buttons}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=10)
        return response.status_code == 200


# --- Endpoints ---

@router.post("/", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def create_trade(
    trade_data: TradeCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    انجام معامله روی یک لفظ از MiniApp
    """
    from core.enums import UserRole
    import jdatetime
    
    # بررسی نقش
    if current_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )
    
    # بررسی مسدودیت (قبل از قفل)
    if current_user.trading_restricted_until:
        if current_user.trading_restricted_until > datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="حساب شما مسدود است."
            )
    
    # ===== قفل کاربر برای جلوگیری از Race Condition در محدودیت‌ها =====
    # اگر دو درخواست همزمان بیاید، اولی قفل می‌کند و دومی منتظر می‌ماند
    locked_user = await db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    )
    current_user = locked_user.scalar_one()
    
    # بررسی محدودیت معامله (حالا با قفل)
    allowed, error_msg = check_user_limits(current_user, 'trade', trade_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # گرفتن لفظ با قفل
    offer = await db.get(Offer, trade_data.offer_id, with_for_update=True)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    if offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ دیگر فعال نیست.")
    
    if offer.user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="نمی‌توانید روی لفظ خودتان معامله کنید.")
    
    # بررسی بلاک بین کاربران (پنهان - کاربر نباید متوجه بلاک شدن بشه)
    from core.services.block_service import is_blocked
    blocked, _ = await is_blocked(db, current_user.id, offer.user_id)
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="امکان انجام این معامله وجود ندارد."
        )
    
    if trade_data.quantity > offer.remaining_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"تعداد درخواستی بیشتر از موجودی است. موجودی: {offer.remaining_quantity}"
        )
    
    # بارگذاری روابط لفظ
    await db.refresh(offer, ["user", "commodity"])
    
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
        responder_user_id=current_user.id,
        responder_user_mobile=current_user.mobile_number,
        commodity_id=offer.commodity_id,
        trade_type=responder_trade_type,
        quantity=trade_data.quantity,
        price=offer.price,
        status=TradeStatus.COMPLETED
    )
    db.add(new_trade)
    
    # آپدیت لفظ
    offer.remaining_quantity -= trade_data.quantity
    
    # بروزرسانی لات‌ها - حذف مقدار معامله شده از لیست
    if offer.lot_sizes:
        from sqlalchemy.orm.attributes import flag_modified
        new_lot_sizes = list(offer.lot_sizes)
        if trade_data.quantity in new_lot_sizes:
            new_lot_sizes.remove(trade_data.quantity)
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
        f"{respond_emoji} **{respond_type_fa}**\n\n"
        f"💰 فی: {offer.price:,}\n"
        f"📦 تعداد: {trade_data.quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {offer.user.account_name if offer.user else 'نامشخص'}\n"
        f"🔢 شماره معامله: {new_trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
    )
    
    # پیام برای لفظ‌دهنده
    offer_owner_msg = (
        f"{offer_emoji} **{offer_type_fa}**\n\n"
        f"💰 فی: {offer.price:,}\n"
        f"📦 تعداد: {trade_data.quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {current_user.account_name}\n"
        f"🔢 شماره معامله: {new_trade_number}\n"
        f"🕐 زمان معامله: {trade_datetime}"
    )
    
    # ارسال پیام‌های تلگرام (در background)
    background_tasks.add_task(send_telegram_message_sync, current_user.telegram_id, responder_msg)
    if offer.user:
        background_tasks.add_task(send_telegram_message_sync, offer.user.telegram_id, offer_owner_msg)
    
    # ارسال نوتیفیکیشن اپ
    notif_msg_responder = (
        f"{respond_emoji} {respond_type_fa}\n"
        f"💰 فی: {offer.price:,} | 📦 تعداد: {trade_data.quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {offer.user.account_name if offer.user else 'نامشخص'}\n"
        f"🔢 شماره: {new_trade_number}"
    )
    
    notif_msg_owner = (
        f"{offer_emoji} {offer_type_fa}\n"
        f"💰 فی: {offer.price:,} | 📦 تعداد: {trade_data.quantity}\n"
        f"🏷️ کالا: {offer.commodity.name}\n"
        f"👤 طرف معامله: {current_user.account_name}\n"
        f"🔢 شماره: {new_trade_number}"
    )
    
    try:
        await create_user_notification(
            db, current_user.id, notif_msg_responder,
            level=NotificationLevel.SUCCESS,
            category=NotificationCategory.TRADE
        )
        if offer.user_id:
            await create_user_notification(
                db, offer.user_id, notif_msg_owner,
                level=NotificationLevel.SUCCESS,
                category=NotificationCategory.TRADE
            )
    except:
        pass
    
    # افزایش شمارنده معامله
    # فقط پاسخ‌دهنده (کسی که روی لفظ دیگران معامله می‌کند) شمارنده‌اش افزایش می‌یابد
    # صاحب لفظ شمارنده‌اش افزایش نمی‌یابد (چون او فقط لفظ داده، فعالانه معامله نکرده)
    user_for_counter = await db.get(User, current_user.id)
    if user_for_counter:
        await increment_user_counter(db, user_for_counter, 'trade', trade_data.quantity)
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    await publish_event("trade:created", {
        "trade_number": new_trade_number,
        "offer_id": offer.id,
        "quantity": trade_data.quantity,
        "price": offer.price,
        "commodity_name": offer.commodity.name,
        "trade_type": responder_trade_type.value,
        "offer_user_id": offer.user_id,
        "offer_user_name": offer.user.account_name if offer.user else "ناشناس",
        "responder_user_id": current_user.id,
        "responder_user_name": current_user.account_name
    })
    await publish_event("offer:updated", {
        "id": offer.id,
        "remaining_quantity": offer.remaining_quantity,
        "status": offer.status.value
    })
    
    return trade_to_response(new_trade)


@router.get("/my", response_model=List[TradeResponse])
async def get_my_trades(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    دریافت تاریخچه معاملات کاربر
    """
    from sqlalchemy import or_
    
    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    ).where(
        or_(
            Trade.offer_user_id == current_user.id,
            Trade.responder_user_id == current_user.id
        )
    ).order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return [trade_to_response(t) for t in trades]


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
    
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="معامله یافت نشد.")
    
    # فقط طرفین معامله می‌توانند جزئیات را ببینند
    if trade.offer_user_id != current_user.id and trade.responder_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما به این معامله دسترسی ندارید.")
    
    return trade_to_response(trade)


@router.get("/with/{other_user_id}", response_model=List[TradeResponse])
async def get_trades_with_user(
    other_user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    دریافت تاریخچه معاملات با یک کاربر خاص
    """
    from sqlalchemy import and_, or_
    
    # کاربر نمی‌تواند معاملات خودش با خودش را بگیرد (که منطقاً وجود ندارد)
    if other_user_id == current_user.id:
        return []

    query = select(Trade).options(
        selectinload(Trade.offer_user),
        selectinload(Trade.responder_user),
        selectinload(Trade.commodity)
    ).where(
        or_(
            and_(Trade.offer_user_id == current_user.id, Trade.responder_user_id == other_user_id),
            and_(Trade.offer_user_id == other_user_id, Trade.responder_user_id == current_user.id)
        )
    ).order_by(Trade.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    trades = result.scalars().all()
    
    return [trade_to_response(t) for t in trades]
