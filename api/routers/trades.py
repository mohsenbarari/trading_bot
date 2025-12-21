# api/routers/trades.py
"""
API Router for Trade Management - MiniApp Integration
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
import httpx
import os

from core.db import get_db
from core.config import settings
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.trade import Trade, TradeType, TradeStatus
from models.commodity import Commodity
from .auth import get_current_user
from core.utils import (
    check_user_limits, increment_user_counter, to_jalali_str,
    create_user_notification
)
from core.enums import NotificationLevel, NotificationCategory


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
    except:
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
            buttons = [[{"text": f"{offer.remaining_quantity} عدد", "callback_data": f"channel_trade_{offer.id}_{offer.remaining_quantity}"}]]
        else:
            # فیلتر lot_sizes که بیشتر از remaining نباشند
            valid_lots = [l for l in offer.lot_sizes if l <= offer.remaining_quantity]
            if offer.remaining_quantity not in valid_lots:
                valid_lots = [offer.remaining_quantity] + valid_lots
            valid_lots = sorted(set(valid_lots), reverse=True)
            buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade_{offer.id}_{a}"} for a in valid_lots]]
        
        payload = {
            "chat_id": channel_id,
            "message_id": offer.channel_message_id,
            "reply_markup": {"inline_keyboard": buttons}
        }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            return response.status_code == 200
    except:
        return False


# --- Endpoints ---

@router.post("/", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def create_trade(
    trade_data: TradeCreate,
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
    
    # بررسی مسدودیت
    if current_user.trading_restricted_until:
        if current_user.trading_restricted_until > datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="حساب شما مسدود است."
            )
    
    # بررسی محدودیت معامله
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
    if offer.remaining_quantity <= 0:
        offer.status = OfferStatus.COMPLETED
    
    await db.commit()
    
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
    
    # آپدیت دکمه‌های کانال
    await update_channel_buttons(offer)
    
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
    
    # ارسال پیام تلگرام
    await send_telegram_message(current_user.telegram_id, responder_msg)
    if offer.user:
        await send_telegram_message(offer.user.telegram_id, offer_owner_msg)
    
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
    
    # افزایش شمارنده معامله برای هر دو طرف (پاسخ‌دهنده و صاحب لفظ)
    
    # 1. شمارنده برای پاسخ‌دهنده (current_user)
    user_for_counter = await db.get(User, current_user.id)
    if user_for_counter:
        await increment_user_counter(db, user_for_counter, 'trade', trade_data.quantity)
    
    # 2. شمارنده برای صاحب لفظ (offer.user_id) - اگر متفاوت از پاسخ‌دهنده باشد
    if offer.user_id and offer.user_id != current_user.id:
        offer_owner = await db.get(User, offer.user_id)
        if offer_owner:
            await increment_user_counter(db, offer_owner, 'trade', trade_data.quantity)
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    await publish_event("trade:created", {
        "trade_number": new_trade_number,
        "offer_id": offer.id,
        "quantity": trade_data.quantity,
        "price": offer.price,
        "commodity_name": offer.commodity.name
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
