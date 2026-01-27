# api/routers/offers.py
"""
API Router for Offer Management - MiniApp Integration
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.db import get_db
from core.config import settings
from core.trading_settings import get_trading_settings
from core.utils import check_user_limits, increment_user_counter, to_jalali_str
from models.user import User
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from .auth import get_current_user

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/offers",
    tags=["Offers"],
)


# --- Pydantic Schemas ---

class OfferCreate(BaseModel):
    """ایجاد لفظ جدید"""
    offer_type: str = Field(..., pattern="^(buy|sell)$", description="نوع: buy یا sell")
    commodity_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0, le=1000)
    price: int = Field(..., gt=0)
    is_wholesale: bool = Field(default=True, description="یکجا یا خُرد")
    lot_sizes: Optional[List[int]] = Field(default=None, description="بخش‌ها برای فروش خُرد")
    notes: Optional[str] = Field(default=None, max_length=200)
    republished_from_id: Optional[int] = Field(default=None, description="شناسه لفظ قدیمی برای تکرار")


class OfferResponse(BaseModel):
    """پاسخ لفظ"""
    id: int
    user_id: int
    user_account_name: str
    offer_type: str
    commodity_id: int
    commodity_name: str
    quantity: int
    remaining_quantity: int
    price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]]
    original_lot_sizes: Optional[List[int]]
    notes: Optional[str]
    status: str
    channel_message_id: Optional[int]
    created_at: str
    expires_at_ts: Optional[int] = None
    
    class Config:
        from_attributes = True


class ParseOfferRequest(BaseModel):
    """درخواست پارس متن لفظ"""
    text: str = Field(..., min_length=3, max_length=200)


class ParseOfferResponse(BaseModel):
    """پاسخ پارس لفظ"""
    success: bool
    error: Optional[str] = None
    data: Optional[dict] = None


# --- Helper Functions ---

# --- Helper Functions ---

def offer_to_response(offer: Offer, start_settings: Optional['TradingSettings'] = None) -> OfferResponse:
    """تبدیل مدل Offer به پاسخ API"""
    remaining = offer.remaining_quantity or offer.quantity
    
    # محاسبه زمان انقضا
    expires_at_ts = None
    logger.info(f"TRACE EXPIRY: offer.id={offer.id} offer.status={offer.status} offer.status.type={type(offer.status)}")
    if offer.status == OfferStatus.ACTIVE:
        try:
            ts = start_settings or get_trading_settings()
            logger.info(f"TRACE SETTINGS: offer_expiry_minutes={ts.offer_expiry_minutes}")
            # تبدیل created_at به timestamp
            created_ts = offer.created_at.timestamp()
            expiry_seconds = ts.offer_expiry_minutes * 60
            expires_at_ts = int(created_ts + expiry_seconds)
            logger.info(f"TRACE EXPIRY RESULT: ID={offer.id} ExpiresTS={expires_at_ts}")
        except Exception as e:
            logger.error(f"Error calculating expiry for offer {offer.id}: {e}")

    return OfferResponse(
        id=offer.id,
        user_id=offer.user_id,
        user_account_name="",  # نام ثبت‌کننده نمایش داده نشود - فقط در نوتیفیکیشن معامله
        offer_type=offer.offer_type.value,
        commodity_id=offer.commodity_id,
        commodity_name=offer.commodity.name if offer.commodity else "نامشخص",
        quantity=offer.quantity,
        remaining_quantity=remaining,
        price=offer.price,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
        original_lot_sizes=offer.original_lot_sizes,
        notes=offer.notes,
        status=offer.status.value,
        channel_message_id=offer.channel_message_id,
        created_at=to_jalali_str(offer.created_at) or "",
        expires_at_ts=expires_at_ts
    )


async def send_offer_to_channel(offer: Offer, user: User) -> Optional[int]:
    """ارسال لفظ به کانال تلگرام و برگرداندن message_id"""
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    
    if not bot_token or not channel_id:
        return None
    
    # ساخت متن پیام
    trade_emoji = "🟢" if offer.offer_type == OfferType.BUY else "🔴"
    trade_label = "خرید" if offer.offer_type == OfferType.BUY else "فروش"
    invisible_padding = "\u2800" * 35
    
    channel_message = f"{trade_emoji}{trade_label} {offer.commodity.name} {offer.quantity} عدد {offer.price:,}"
    if offer.notes:
        channel_message += f"\nتوضیحات: {offer.notes}"
    channel_message += f"\n{invisible_padding}"
    
    # ساخت دکمه‌ها
    if offer.is_wholesale or not offer.lot_sizes:
        buttons = [[{"text": f"{offer.quantity} عدد", "callback_data": f"channel_trade:{offer.id}:{offer.quantity}"}]]
    else:
        all_amounts = [offer.quantity] + sorted(offer.lot_sizes, reverse=True)
        seen = set()
        unique_amounts = []
        for a in all_amounts:
            if a not in seen:
                seen.add(a)
                unique_amounts.append(a)
        buttons = [[{"text": f"{a} عدد", "callback_data": f"channel_trade:{offer.id}:{a}"} for a in unique_amounts]]
    
    reply_markup = {"inline_keyboard": buttons}
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": channel_id,
        "text": channel_message,
        "reply_markup": reply_markup
    }
    
    logger.info(f"DEBUG CHANNEL: Sending offer {offer.id} to channel {channel_id}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10)
            logger.info(f"DEBUG CHANNEL: Response status={response.status_code}, body={response.text[:500]}")
            if response.status_code == 200:
                result = response.json()
                return result.get("result", {}).get("message_id")
            else:
                logger.error(f"Channel send failed: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error sending to channel: {e}")
    
    return None


# --- Endpoints ---

@router.post("/", response_model=OfferResponse, status_code=status.HTTP_201_CREATED)
async def create_offer(
    offer_data: OfferCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    ثبت لفظ جدید از MiniApp
    - بررسی محدودیت‌ها
    - ذخیره در دیتابیس
    - ارسال به کانال تلگرام
    """
    from core.enums import UserRole
    
    # بررسی نقش
    if current_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )
    
    # بررسی مسدودیت
    if current_user.trading_restricted_until:
        if current_user.trading_restricted_until > datetime.utcnow():
            remaining = current_user.trading_restricted_until - datetime.utcnow()
            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(current_user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"حساب شما مسدود است.\n📅 رفع مسدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
            )
    
    # بررسی محدودیت ارسال لفظ
    allowed, error_msg = check_user_limits(current_user, 'channel_message')
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # بررسی محدودیت معامله و کالا
    allowed, error_msg = check_user_limits(current_user, 'trade', offer_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # بررسی تعداد لفظ‌های فعال (با کش Redis)
    ts = get_trading_settings()
    
    # ===== Redis Cache Check =====
    from core.cache import get_active_offer_count, set_active_offer_count
    
    active_count = await get_active_offer_count(current_user.id)
    if active_count is None:
        # Cache miss - query DB
        active_count = await db.scalar(
            select(func.count(Offer.id)).where(
                Offer.user_id == current_user.id,
                Offer.status == OfferStatus.ACTIVE
            )
        )
        # Cache the result
        await set_active_offer_count(current_user.id, active_count)
    # =============================
    
    if active_count >= ts.max_active_offers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"شما حداکثر {ts.max_active_offers} لفظ فعال دارید. لطفاً ابتدا یکی را منقضی کنید."
        )
    
    # بررسی وجود کالا
    commodity = await db.get(Commodity, offer_data.commodity_id)
    if not commodity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="کالا یافت نشد.")

    # ===== اعتبارسنجی‌های مشترک (Shared Service) =====
    from core.services.trade_service import validate_quantity, validate_price, validate_lot_sizes
    
    # 1. اعتبارسنجی تعداد
    is_valid_qty, err_qty = validate_quantity(offer_data.quantity)
    if not is_valid_qty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_qty)

    # 2. اعتبارسنجی قیمت
    is_valid_price, err_price = validate_price(offer_data.price)
    if not is_valid_price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_price)
    
    # 3. اعتبارسنجی لات‌ها
    if not offer_data.is_wholesale and offer_data.lot_sizes:
        is_valid_lots, err_lots, suggested_lots = validate_lot_sizes(offer_data.quantity, offer_data.lot_sizes)
        if not is_valid_lots:
            detail_msg = err_lots
            # اگر پیشنهاد اصلاح دارد، آن را به کاربر نشان می‌دهیم
            if suggested_lots:
                # اینجا می‌توانیم پیشنهاد را در قالب خاصی بفرستیم، اما فعلاً در متن خطا می‌گذاریم
                pass 
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail_msg)

    

    # مدیریت تکرار لفظ قدیمی
    if offer_data.republished_from_id:
        old_offer = await db.get(Offer, offer_data.republished_from_id)
        if old_offer and old_offer.user_id == current_user.id:
            # اگر لفظ قبلی هنوز فعال باشد، آن را منقضی می‌کنیم
            if old_offer.status == OfferStatus.ACTIVE:
                 old_offer.status = OfferStatus.EXPIRED

    # ایجاد لفظ
    new_offer = Offer(
        user_id=current_user.id,
        offer_type=OfferType.BUY if offer_data.offer_type == "buy" else OfferType.SELL,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        remaining_quantity=offer_data.quantity,
        price=offer_data.price,
        is_wholesale=offer_data.is_wholesale,
        lot_sizes=offer_data.lot_sizes,
        original_lot_sizes=offer_data.lot_sizes,  # ذخیره ترکیب اولیه برای تکرار
        notes=offer_data.notes,
        status=OfferStatus.ACTIVE
    )
    db.add(new_offer)
    await db.commit()
    await db.refresh(new_offer)
    
    # لینک کردن لفظ قدیمی به جدید
    if offer_data.republished_from_id:
        # دوباره لود می‌کنیم چون ممکن است سشن بسته شده باشد یا نیاز به اتچ مجدد باشد، اما اینجا سشن باز است
        # فقط باید مطمئن شویم old_offer در سشن است
        if old_offer: 
            old_offer.republished_offer_id = new_offer.id
            await db.commit()
    
    # بارگذاری روابط
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.user), selectinload(Offer.commodity))
        .where(Offer.id == new_offer.id)
    )
    new_offer = result.scalar_one()
    
    # ارسال به کانال
    message_id = await send_offer_to_channel(new_offer, current_user)
    if message_id:
        new_offer.channel_message_id = message_id
        await db.commit()
    
    # ===== Increment Offer Count Cache =====
    from core.cache import incr_active_offer_count
    await incr_active_offer_count(current_user.id)
    # =======================================
    
    # افزایش شمارنده
    await increment_user_counter(db, current_user, 'channel_message')
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    await publish_event("offer:created", {
        "id": new_offer.id,
        "offer_type": new_offer.offer_type.value,
        "commodity_name": new_offer.commodity.name,
        "quantity": new_offer.quantity,
        "price": new_offer.price,
        "user_account_name": current_user.account_name
    })
    
    # دریافت تنظیمات برای محاسبه انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return offer_to_response(new_offer, ts)


@router.get("/", response_model=List[OfferResponse])
async def get_active_offers(
    offer_type: Optional[str] = Query(None, pattern="^(buy|sell)$"),
    commodity_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    دریافت لیست لفظ‌های فعال
    """
    query = select(Offer).options(
        selectinload(Offer.user),
        selectinload(Offer.commodity)
    ).where(Offer.status == OfferStatus.ACTIVE)
    
    if offer_type:
        query = query.where(Offer.offer_type == (OfferType.BUY if offer_type == "buy" else OfferType.SELL))
    
    if commodity_id:
        query = query.where(Offer.commodity_id == commodity_id)
    
    query = query.order_by(Offer.created_at.asc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    offers = result.scalars().all()
    
    # دریافت تنظیمات برای محاسبه دقیق انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return [offer_to_response(o, ts) for o in offers]


@router.get("/my", response_model=List[OfferResponse])
async def get_my_offers(
    status_filter: Optional[str] = Query(None, pattern="^(active|completed|cancelled|expired)$"),
    since_hours: Optional[int] = Query(None, gt=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    دریافت لفظ‌های کاربر
    """
    query = select(Offer).options(
        selectinload(Offer.user),
        selectinload(Offer.commodity)
    ).where(Offer.user_id == current_user.id)
    
    # فیلتر کردن لفظ‌هایی که دوباره منتشر شده‌اند
    query = query.where(Offer.republished_offer_id.is_(None))
    
    if since_hours:
        from datetime import timedelta
        cutoff_time = datetime.utcnow() - timedelta(hours=since_hours)
        query = query.where(Offer.created_at >= cutoff_time)
        
        if status_filter:
             status_enum = {
                "active": OfferStatus.ACTIVE,
                "completed": OfferStatus.COMPLETED,
                "cancelled": OfferStatus.CANCELLED,
                "expired": OfferStatus.EXPIRED
            }.get(status_filter)
             if status_enum:
                query = query.where(Offer.status == status_enum)
    elif status_filter:
        status_enum = {
            "active": OfferStatus.ACTIVE,
            "completed": OfferStatus.COMPLETED,
            "cancelled": OfferStatus.CANCELLED,
            "expired": OfferStatus.EXPIRED
        }.get(status_filter)
        if status_enum:
            query = query.where(Offer.status == status_enum)
    
    # مرتب‌سازی: جدیدترین‌ها اول
    query = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    offers = result.scalars().all()
    
    # دریافت تنظیمات برای محاسبه انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return [offer_to_response(o, ts) for o in offers]


@router.delete("/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def expire_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    منقضی کردن لفظ
    """
    offer = await db.get(Offer, offer_id)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    if offer.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="شما مالک این لفظ نیستید.")
    
    if offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="این لفظ قبلاً غیرفعال شده است.")
    
    offer.status = OfferStatus.EXPIRED
    await db.commit()
    
    # حذف دکمه‌ها از کانال
    if offer.channel_message_id:
        bot_token = os.getenv("BOT_TOKEN")
        channel_id = settings.channel_id
        if bot_token and channel_id:
            url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
            payload = {
                "chat_id": channel_id,
                "message_id": offer.channel_message_id
            }
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(url, json=payload, timeout=10)
            except Exception as e:
                logger.warning(f"Error removing channel buttons: {e}")
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    await publish_event("offer:expired", {"id": offer_id})
    
    # ===== Decrement Offer Count Cache =====
    from core.cache import decr_active_offer_count
    await decr_active_offer_count(offer.user_id)
    # =======================================
    
    return None


@router.post("/parse", response_model=ParseOfferResponse)
async def parse_offer_text(
    request: ParseOfferRequest,
    current_user: User = Depends(get_current_user)
):
    """
    پارس متن لفظ (مثل بات)
    """
    from bot.utils.offer_parser import parse_offer_text as parser
    
    result, error = await parser(request.text)
    
    if error:
        return ParseOfferResponse(success=False, error=error.message)
    
    if result is None:
        return ParseOfferResponse(success=False, error="متن قابل تشخیص نیست.")
    
    return ParseOfferResponse(
        success=True,
        data={
            "trade_type": result.trade_type,
            "commodity_id": result.commodity_id,
            "commodity_name": result.commodity_name,
            "quantity": result.quantity,
            "price": result.price,
            "is_wholesale": result.is_wholesale,
            "lot_sizes": result.lot_sizes,
            "notes": result.notes
        }
    )
