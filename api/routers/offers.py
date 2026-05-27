# api/routers/offers.py
"""
API Router for Offer Management - Web App Integration
"""
import logging
import os
from datetime import date, datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.db import get_db
from core.config import settings
from core.trading_settings import TradingSettings, get_trading_settings
from core.utils import check_user_limits, increment_user_counter, to_jalali_str
from core.services.market_transition_service import (
    evaluate_current_market_schedule,
    register_market_offer_created,
)
from core.services.trade_service import get_available_trade_amounts
from core.services.customer_relation_service import (
    build_customer_offer_read_model,
    get_active_customer_relation_for_customer,
    load_offer_customer_read_context,
)
from core.services.user_account_status_service import is_user_market_blocked
from models.user import User
from models.customer_relation import CustomerTier
from models.offer import Offer, OfferType, OfferStatus
from models.commodity import Commodity
from api.deps import EffectiveOwnerActor, get_current_user, get_current_user_optional, get_effective_owner_actor_context
from core.server_routing import current_server


logger = logging.getLogger(__name__)


MARKET_CLOSED_DETAIL = "بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید."
ACCOUNTANT_MARKET_BLOCKED_DETAIL = "حسابدار دسترسی به بازار ندارد."


def _resolve_offer_owner_context(
    context: EffectiveOwnerActor | None,
    current_user: Optional[User],
) -> EffectiveOwnerActor:
    if hasattr(context, "owner_user") and hasattr(context, "actor_user"):
        return context
    if current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return EffectiveOwnerActor(
        owner_user=current_user,
        actor_user=current_user,
        relation=None,
        is_accountant_context=False,
    )


def _ensure_accountant_market_access_allowed(context: EffectiveOwnerActor) -> None:
    if getattr(context, "is_accountant_context", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNTANT_MARKET_BLOCKED_DETAIL,
        )


router = APIRouter(
    tags=["Offers"],
)


# --- Pydantic Schemas ---

class OfferCreate(BaseModel):
    """ایجاد لفظ جدید"""
    offer_type: str = Field(..., pattern="^(buy|sell)$", description="نوع: buy یا sell")
    commodity_id: int = Field(..., gt=0)
    quantity: int = Field(..., gt=0)
    price: int = Field(..., gt=0)
    is_wholesale: bool = Field(default=True, description="یکجا یا خُرد")
    lot_sizes: Optional[List[int]] = Field(default=None, description="بخش‌ها برای فروش خُرد")
    notes: Optional[str] = Field(default=None, max_length=200)
    republished_from_id: Optional[int] = Field(default=None, description="شناسه لفظ قدیمی برای تکرار")
    warning_acknowledged: bool = Field(default=False, description="آیا هشدار قیمت غیرعادی توسط کاربر تایید شده است")


class OfferResponse(BaseModel):
    """پاسخ لفظ"""
    id: int
    user_id: Optional[int] = None
    user_account_name: str = ""
    is_own_offer: bool = False
    offer_type: str
    commodity_id: int
    commodity_name: str
    quantity: int
    remaining_quantity: int
    price: int
    raw_price: int
    market_published_price: int
    viewer_effective_price: int
    is_wholesale: bool
    lot_sizes: Optional[List[int]]
    original_lot_sizes: Optional[List[int]]
    notes: Optional[str]
    status: str
    expire_reason: Optional[str] = None
    channel_message_id: Optional[int]
    customer_badge_visible: bool = False
    customer_management_name: Optional[str] = None
    customer_tier: Optional[str] = None
    created_at: str
    expires_at_ts: Optional[int] = None
    
    class Config:
        from_attributes = True


class ParseOfferRequest(BaseModel):
    """درخواست پارس متن لفظ"""
    text: str = Field(..., min_length=3, max_length=500)


class ParseOfferResponse(BaseModel):
    """پاسخ پارس لفظ"""
    success: bool
    error: Optional[str] = None
    data: Optional[dict] = None


# --- Helper Functions ---

# --- Helper Functions ---

def offer_to_response(
    offer: Offer,
    start_settings: Optional['TradingSettings'] = None,
    *,
    viewer_user_id: Optional[int] = None,
    include_owner_identity: bool = False,
    offer_owner_relation: object | None = None,
    viewer_customer_relation: object | None = None,
) -> OfferResponse:
    """تبدیل مدل Offer به پاسخ API"""
    remaining = offer.remaining_quantity or offer.quantity
    is_own_offer = viewer_user_id is not None and offer.user_id == viewer_user_id
    offer_read_model = build_customer_offer_read_model(
        raw_price=offer.price,
        offer_type=offer.offer_type,
        viewer_user_id=viewer_user_id,
        offer_owner_relation=offer_owner_relation,
        viewer_customer_relation=viewer_customer_relation,
    )
    
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
        user_id=offer.user_id if include_owner_identity else None,
        user_account_name=(offer.user.account_name if include_owner_identity and offer.user else ""),
        is_own_offer=is_own_offer,
        offer_type=offer.offer_type.value,
        commodity_id=offer.commodity_id,
        commodity_name=offer.commodity.name if offer.commodity else "نامشخص",
        quantity=offer.quantity,
        remaining_quantity=remaining,
        price=offer_read_model.market_published_price,
        raw_price=offer_read_model.raw_price,
        market_published_price=offer_read_model.market_published_price,
        viewer_effective_price=offer_read_model.viewer_effective_price,
        is_wholesale=offer.is_wholesale,
        lot_sizes=offer.lot_sizes,
        original_lot_sizes=offer.original_lot_sizes,
        notes=offer.notes,
        status=offer.status.value,
        expire_reason=getattr(offer, "expire_reason", None),
        channel_message_id=offer.channel_message_id,
        customer_badge_visible=offer_read_model.customer_badge_visible,
        customer_management_name=offer_read_model.customer_management_name,
        customer_tier=offer_read_model.customer_tier,
        created_at=to_jalali_str(offer.created_at) or "",
        expires_at_ts=expires_at_ts
    )


async def _serialize_offer_responses(
    offers: List[Offer],
    *,
    db: AsyncSession,
    start_settings: Optional['TradingSettings'] = None,
    viewer_user_id: Optional[int] = None,
    include_owner_identity: bool = False,
) -> List[OfferResponse]:
    offer_owner_relation_map, viewer_customer_relation = await load_offer_customer_read_context(
        db,
        offer_owner_user_ids={offer.user_id for offer in offers if getattr(offer, "user_id", None) is not None},
        viewer_user_id=viewer_user_id,
    )
    return [
        offer_to_response(
            offer,
            start_settings,
            viewer_user_id=viewer_user_id,
            include_owner_identity=include_owner_identity,
            offer_owner_relation=offer_owner_relation_map.get(getattr(offer, "user_id", None)),
            viewer_customer_relation=viewer_customer_relation,
        )
        for offer in offers
    ]


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
        all_amounts = get_available_trade_amounts(
            quantity=offer.quantity,
            remaining_quantity=offer.remaining_quantity or offer.quantity,
            is_wholesale=False,
            lot_sizes=sorted(offer.lot_sizes, reverse=True),
        )
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
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    ثبت لفظ جدید از MiniApp
    - بررسی محدودیت‌ها
    - ذخیره در دیتابیس
    - ارسال به کانال تلگرام
    """
    from core.enums import UserRole
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    actor_user = context.actor_user
    
    # بررسی نقش
    if owner_user.role == UserRole.WATCH:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="شما دسترسی به بخش معاملات را ندارید."
        )

    if is_user_market_blocked(owner_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="حساب شما غیرفعال است و دسترسی شما به بازار بسته شده است.",
        )

    market_evaluation = await evaluate_current_market_schedule(db)
    if not market_evaluation.is_open:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=MARKET_CLOSED_DETAIL,
        )
    
    # بررسی مسدودیت
    if owner_user.trading_restricted_until:
        if owner_user.trading_restricted_until > datetime.utcnow():
            remaining = owner_user.trading_restricted_until - datetime.utcnow()
            total_seconds = int(remaining.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            countdown = f"{days:02d}:{hours:02d}:{minutes:02d}"
            expiry_jalali = to_jalali_str(owner_user.trading_restricted_until, "%Y/%m/%d - %H:%M")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"حساب شما مسدود است.\n📅 رفع مسدودیت: {expiry_jalali}\n⏳ زمان باقی‌مانده: {countdown}"
            )
    
    # بررسی محدودیت ارسال لفظ
    allowed, error_msg = check_user_limits(owner_user, 'channel_message')
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
    
    # بررسی محدودیت معامله و کالا
    allowed, error_msg = check_user_limits(owner_user, 'trade', offer_data.quantity)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)

    actor_customer_relation = await get_active_customer_relation_for_customer(db, actor_user.id)
    if actor_customer_relation and actor_customer_relation.customer_tier == CustomerTier.TIER_2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="مشتری سطح 2 مجاز به ثبت لفظ نیست و فقط می‌تواند روی لفظ‌های دیگر درخواست بزند.",
        )
    
    # بررسی تعداد لفظ‌های فعال
    ts = get_trading_settings()

    # Redis count can drift behind cross-surface writes (for example bot-created offers).
    # Use the DB as the authoritative guard here, then repair the shared cache for fast
    # readers and later increment/decrement paths.
    from core.cache import get_active_offer_count, set_active_offer_count

    cached_active_count = await get_active_offer_count(owner_user.id)
    active_count = await db.scalar(
        select(func.count(Offer.id)).where(
            Offer.user_id == owner_user.id,
            Offer.status == OfferStatus.ACTIVE,
        )
    )
    if cached_active_count != active_count:
        await set_active_offer_count(owner_user.id, active_count)
    
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
    from core.services.trade_service import (
        detect_offer_price_warning,
        validate_quantity,
        validate_price,
        validate_lot_sizes,
    )
    
    # 1. اعتبارسنجی تعداد
    is_valid_qty, err_qty = validate_quantity(offer_data.quantity)
    if not is_valid_qty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_qty)

    # 2. اعتبارسنجی قیمت
    is_valid_price, err_price = validate_price(offer_data.price)
    if not is_valid_price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_price)
    
    # 3. اعتبارسنجی لات‌ها
    if not offer_data.is_wholesale:
        if not offer_data.lot_sizes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="برای آفر خُرد باید لات‌ها مشخص شوند.")
        is_valid_lots, err_lots, suggested_lots = validate_lot_sizes(offer_data.quantity, offer_data.lot_sizes)
        if not is_valid_lots:
            detail_msg = err_lots
            # اگر پیشنهاد اصلاح دارد، آن را به کاربر نشان می‌دهیم
            if suggested_lots:
                # اینجا می‌توانیم پیشنهاد را در قالب خاصی بفرستیم، اما فعلاً در متن خطا می‌گذاریم
                pass 
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail_msg)
    
    # 4. اعتبارسنجی قیمت رقابتی
    from core.services.trade_service import validate_competitive_price
    is_valid_comp, err_comp = await validate_competitive_price(
        db=db,
        offer_type=offer_data.offer_type,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        proposed_price=offer_data.price,
        user_id=owner_user.id
    )
    if not is_valid_comp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_comp)

    price_warning = await detect_offer_price_warning(
        db=db,
        offer_type=offer_data.offer_type,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        proposed_price=offer_data.price,
        user_id=owner_user.id,
    )
    if price_warning and not offer_data.warning_acknowledged:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error_code": price_warning["error_code"],
                "detail": price_warning["detail"],
                "warning": price_warning,
            },
        )

    # مدیریت تکرار لفظ قدیمی
    if offer_data.republished_from_id:
        old_offer = await db.get(Offer, offer_data.republished_from_id)
        if old_offer and old_offer.user_id == owner_user.id:
            # اگر لفظ قبلی هنوز فعال باشد، آن را منقضی می‌کنیم
            if old_offer.status == OfferStatus.ACTIVE:
                 old_offer.status = OfferStatus.EXPIRED

    # ایجاد لفظ
    new_offer = Offer(
        user_id=owner_user.id,
        actor_user_id=actor_user.id,
        home_server=getattr(owner_user, "home_server", None) or current_server(),
        offer_type=OfferType.BUY if offer_data.offer_type == "buy" else OfferType.SELL,
        commodity_id=offer_data.commodity_id,
        quantity=offer_data.quantity,
        remaining_quantity=offer_data.quantity,
        price=offer_data.price,
        exclude_from_competitive_price=bool(price_warning),
        price_warning_type=price_warning["warning_type"] if price_warning else None,
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
    message_id = await send_offer_to_channel(new_offer, owner_user)
    if message_id:
        new_offer.channel_message_id = message_id
        await db.commit()
    
    # ===== Increment Offer Count Cache =====
    from core.cache import incr_active_offer_count
    await incr_active_offer_count(owner_user.id)
    # =======================================
    
    # افزایش شمارنده
    await increment_user_counter(db, owner_user, 'channel_message')

    await register_market_offer_created(db)
    
    # دریافت تنظیمات برای محاسبه انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    # ارسال رویداد SSE
    from .realtime import publish_event
    
    # محاسبه expires_at_ts برای SSE event
    sse_expires_at_ts = None
    try:
        created_ts = new_offer.created_at.timestamp()
        sse_expires_at_ts = int(created_ts + ts.offer_expiry_minutes * 60)
    except Exception:
        pass
    
    await publish_event("offer:created", {
        "id": new_offer.id,
        "user_id": None,
        "offer_type": new_offer.offer_type.value,
        "commodity_id": new_offer.commodity_id,
        "commodity_name": new_offer.commodity.name,
        "quantity": new_offer.quantity,
        "remaining_quantity": new_offer.remaining_quantity or new_offer.quantity,
        "price": new_offer.price,
        "status": new_offer.status.value,
        "created_at": to_jalali_str(new_offer.created_at) or "",
        "user_account_name": "",
        "is_own_offer": False,
        "notes": new_offer.notes,
        "is_wholesale": new_offer.is_wholesale,
        "lot_sizes": new_offer.lot_sizes,
        "original_lot_sizes": new_offer.original_lot_sizes,
        "expires_at_ts": sse_expires_at_ts,
    })
    
    return offer_to_response(new_offer, ts, viewer_user_id=owner_user.id, include_owner_identity=True)


@router.get("/", response_model=List[OfferResponse])
async def get_active_offers(
    offer_type: Optional[str] = Query(None, pattern="^(buy|sell)$"),
    commodity_id: Optional[int] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    دریافت لیست لفظ‌های فعال
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user

    query = select(Offer).options(
        selectinload(Offer.user),
        selectinload(Offer.commodity)
    ).where(Offer.status == OfferStatus.ACTIVE)
    
    if offer_type:
        query = query.where(Offer.offer_type == (OfferType.BUY if offer_type == "buy" else OfferType.SELL))
    
    if commodity_id:
        query = query.where(Offer.commodity_id == commodity_id)
    
    query = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    offers = result.scalars().all()
    
    # دریافت تنظیمات برای محاسبه دقیق انقضا
    from core.trading_settings import get_trading_settings_async
    ts = await get_trading_settings_async()
    
    return await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=ts,
        viewer_user_id=owner_user.id,
        include_owner_identity=False,
    )


@router.get("/my", response_model=List[OfferResponse])
async def get_my_offers(
    status_filter: Optional[str] = Query(None, pattern="^(active|completed|cancelled|expired)$"),
    since_hours: Optional[int] = Query(None, gt=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    context: EffectiveOwnerActor | None = Depends(get_effective_owner_actor_context),
):
    """
    دریافت لفظ‌های کاربر
    """
    context = _resolve_offer_owner_context(context, current_user)
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user

    query = select(Offer).options(
        selectinload(Offer.user),
        selectinload(Offer.commodity)
    ).where(Offer.user_id == owner_user.id)
    
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
    
    return await _serialize_offer_responses(
        offers,
        db=db,
        start_settings=ts,
        viewer_user_id=owner_user.id,
        include_owner_identity=True,
    )


@router.delete("/{offer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def expire_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    """
    منقضی کردن لفظ
    """
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    ts = get_trading_settings()

    from bot.utils.redis_helpers import track_daily_expire, track_expire_rate

    rate_count = await track_expire_rate(owner_user.id, window_seconds=60)
    if rate_count > ts.offer_expire_rate_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"حداکثر {ts.offer_expire_rate_per_minute} منقضی در دقیقه مجاز است"
        )

    start_of_day = datetime.combine(date.today(), datetime.min.time())
    total_offers_today = await db.scalar(
        select(func.count(Offer.id)).where(
            Offer.user_id == owner_user.id,
            Offer.created_at >= start_of_day
        )
    ) or 0

    daily_data = await track_daily_expire(owner_user.id, total_offers_today)
    threshold = ts.offer_expire_daily_limit_after_threshold
    if daily_data["count"] >= threshold:
        max_allowed = total_offers_today // 3
        if daily_data["count"] >= max_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"شما امروز {daily_data['count']} لفظ منقضی کرده‌اید. "
                    f"برای منقضی کردن بیشتر، باید لفظ‌های جدید ثبت کنید."
                )
            )

    offer = await db.get(Offer, offer_id)
    
    if not offer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="لفظ یافت نشد.")
    
    if offer.user_id != owner_user.id:
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


@router.post("/cancel-all", status_code=status.HTTP_200_OK)
async def cancel_all_active_offers(
    db: AsyncSession = Depends(get_db),
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context)
):
    """
    منقضی کردن تمام لفظ‌های فعال کاربر (برای دستور نشد)
    """
    _ensure_accountant_market_access_allowed(context)
    owner_user = context.owner_user
    
    query = select(Offer).where(
        Offer.user_id == owner_user.id,
        Offer.status == OfferStatus.ACTIVE
    )
    result = await db.execute(query)
    offers = result.scalars().all()
    
    if not offers:
        return {"cancelled_count": 0}
        
    bot_token = os.getenv("BOT_TOKEN")
    channel_id = settings.channel_id
    from .realtime import publish_event
    from core.cache import decr_active_offer_count
    
    async with httpx.AsyncClient() as client:
        for offer in offers:
            offer.status = OfferStatus.EXPIRED
            
            if offer.channel_message_id and bot_token and channel_id:
                url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
                payload = {
                    "chat_id": channel_id,
                    "message_id": offer.channel_message_id
                }
                try:
                    await client.post(url, json=payload, timeout=5)
                except Exception as e:
                    logger.warning(f"Error removing channel buttons for offer {offer.id}: {e}")
            
            await publish_event("offer:expired", {"id": offer.id})
            await decr_active_offer_count(owner_user.id)
            
    await db.commit()
    
    return {"cancelled_count": len(offers)}


@router.post("/parse", response_model=ParseOfferResponse)
async def parse_offer_text(
    request: ParseOfferRequest,
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
):
    """
    پارس متن لفظ با parser مشترک بات و وب اپ.
    """
    _ensure_accountant_market_access_allowed(context)
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
