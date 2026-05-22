# api/routers/trading_settings.py
"""API برای مدیریت تنظیمات سیستم معاملاتی"""

from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from core.db import get_db
from core.services.market_transition_service import get_market_runtime_view
from api.deps import verify_super_admin
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType
from models.user import User

from core.trading_settings import (
    TradingSettings, 
    load_trading_settings_async,
    save_trading_settings_async,
    refresh_settings_cache_async
)


router = APIRouter(
    tags=["Trading Settings"],
)


class TradingSettingsUpdate(BaseModel):
    """مدل بروزرسانی تنظیمات"""
    invitation_expiry_days: Optional[int] = None
    offer_expiry_minutes: Optional[int] = None
    offer_min_quantity: Optional[int] = None
    offer_max_quantity: Optional[int] = None
    max_active_offers: Optional[int] = None
    offer_expire_rate_per_minute: Optional[int] = None
    offer_expire_daily_limit_after_threshold: Optional[int] = None
    anti_abuse_daily_base: Optional[int] = None
    anti_abuse_weekly_base: Optional[int] = None
    anti_abuse_monthly_base: Optional[int] = None
    market_schedule_enabled: Optional[bool] = None
    market_open_time_local: Optional[str] = None
    market_close_time_local: Optional[str] = None
    market_closed_weekdays: Optional[list[int]] = None


class TradingSettingsResponse(BaseModel):
    """مدل پاسخ تنظیمات (بدون property ها)"""
    invitation_expiry_days: int
    offer_expiry_minutes: int
    offer_min_quantity: int
    offer_max_quantity: int
    max_active_offers: int
    offer_expire_rate_per_minute: int
    offer_expire_daily_limit_after_threshold: int
    anti_abuse_daily_base: int
    anti_abuse_weekly_base: int
    anti_abuse_monthly_base: int
    market_schedule_enabled: bool
    market_timezone: str
    market_open_time_local: str
    market_close_time_local: str
    market_closed_weekdays: list[int]
    
    # مقادیر محاسباتی
    invitation_expiry_minutes: int
    lot_min_size: int
    lot_max_count: int


class MarketRuntimeStateResponse(BaseModel):
    is_open: bool
    active_web_notice_visible: bool
    offers_since_last_open: int
    last_transition_at: Optional[datetime] = None
    next_transition_at: Optional[datetime] = None


class MarketScheduleOverrideUpsert(BaseModel):
    date: str
    override_type: MarketScheduleOverrideType
    open_time_local: Optional[str] = None
    close_time_local: Optional[str] = None
    note: Optional[str] = None


class MarketScheduleOverrideResponse(BaseModel):
    id: int
    date: str
    override_type: MarketScheduleOverrideType
    open_time_local: Optional[str] = None
    close_time_local: Optional[str] = None
    note: Optional[str] = None


def _serialize_trading_settings(settings: TradingSettings) -> TradingSettingsResponse:
    return TradingSettingsResponse(
        invitation_expiry_days=settings.invitation_expiry_days,
        offer_expiry_minutes=settings.offer_expiry_minutes,
        offer_min_quantity=settings.offer_min_quantity,
        offer_max_quantity=settings.offer_max_quantity,
        max_active_offers=settings.max_active_offers,
        offer_expire_rate_per_minute=settings.offer_expire_rate_per_minute,
        offer_expire_daily_limit_after_threshold=settings.offer_expire_daily_limit_after_threshold,
        anti_abuse_daily_base=settings.anti_abuse_daily_base,
        anti_abuse_weekly_base=settings.anti_abuse_weekly_base,
        anti_abuse_monthly_base=settings.anti_abuse_monthly_base,
        market_schedule_enabled=settings.market_schedule_enabled,
        market_timezone=settings.market_timezone,
        market_open_time_local=settings.market_open_time_local,
        market_close_time_local=settings.market_close_time_local,
        market_closed_weekdays=list(settings.market_closed_weekdays),
        invitation_expiry_minutes=settings.invitation_expiry_minutes,
        lot_min_size=settings.lot_min_size,
        lot_max_count=settings.lot_max_count,
    )


def _format_local_time(value: Optional[time]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%H:%M")


def _serialize_market_schedule_override(override: MarketScheduleOverride) -> MarketScheduleOverrideResponse:
    return MarketScheduleOverrideResponse(
        id=override.id,
        date=override.date.isoformat(),
        override_type=override.override_type,
        open_time_local=_format_local_time(override.open_time_local),
        close_time_local=_format_local_time(override.close_time_local),
        note=override.note,
    )


def _parse_local_time(value: str, field_name: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name} نامعتبر است") from exc
    return parsed.replace(second=0, microsecond=0)


def _normalize_closed_weekdays(values: Optional[list[int]]) -> list[int]:
    normalized: list[int] = []
    for raw_value in values or []:
        try:
            weekday = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="روزهای بسته بازار نامعتبر هستند") from exc
        if weekday < 0 or weekday > 6:
            raise HTTPException(status_code=400, detail="روزهای بسته بازار باید بین 0 تا 6 باشند")
        if weekday not in normalized:
            normalized.append(weekday)
    return sorted(normalized)


def _validate_market_hours(open_time_value: str, close_time_value: str) -> tuple[str, str]:
    open_time = _parse_local_time(open_time_value, "ساعت شروع بازار")
    close_time = _parse_local_time(close_time_value, "ساعت پایان بازار")
    if open_time >= close_time:
        raise HTTPException(status_code=400, detail="ساعت شروع بازار باید قبل از ساعت پایان باشد")
    return open_time.strftime("%H:%M"), close_time.strftime("%H:%M")


def _prepare_override_payload(payload: MarketScheduleOverrideUpsert) -> tuple[date, Optional[time], Optional[time], Optional[str]]:
    try:
        parsed_date = date.fromisoformat(payload.date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="تاریخ استثنا نامعتبر است") from exc

    note = (payload.note or "").strip() or None

    if payload.override_type == MarketScheduleOverrideType.CUSTOM_HOURS:
        if not payload.open_time_local or not payload.close_time_local:
            raise HTTPException(status_code=400, detail="برای ساعت سفارشی باید ساعت شروع و پایان تعیین شود")
        open_time = _parse_local_time(payload.open_time_local, "ساعت شروع استثنا")
        close_time = _parse_local_time(payload.close_time_local, "ساعت پایان استثنا")
        if open_time >= close_time:
            raise HTTPException(status_code=400, detail="ساعت شروع استثنا باید قبل از ساعت پایان باشد")
        return parsed_date, open_time, close_time, note

    return parsed_date, None, None, note


@router.get("/", response_model=TradingSettingsResponse)
async def get_settings():
    """دریافت تنظیمات فعلی - برای همه کاربران قابل دسترس"""
    settings = await load_trading_settings_async()
    return _serialize_trading_settings(settings)


@router.get("/market-state", response_model=MarketRuntimeStateResponse)
async def get_market_state(db: AsyncSession = Depends(get_db)):
    state = await get_market_runtime_view(db)
    return MarketRuntimeStateResponse(
        is_open=state.is_open,
        active_web_notice_visible=state.active_web_notice_visible,
        offers_since_last_open=state.offers_since_last_open,
        last_transition_at=state.last_transition_at,
        next_transition_at=state.next_transition_at,
    )


@router.get("/market-overrides", response_model=list[MarketScheduleOverrideResponse], dependencies=[Depends(verify_super_admin)])
async def list_market_overrides(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MarketScheduleOverride).order_by(MarketScheduleOverride.date.asc())
    )
    return [_serialize_market_schedule_override(item) for item in result.scalars().all()]


@router.post("/market-overrides", response_model=MarketScheduleOverrideResponse)
async def create_market_override(
    payload: MarketScheduleOverrideUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
):
    parsed_date, open_time_local, close_time_local, note = _prepare_override_payload(payload)
    existing = await db.execute(
        select(MarketScheduleOverride).where(MarketScheduleOverride.date == parsed_date)
    )
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=400, detail="برای این تاریخ قبلاً استثنا ثبت شده است")

    override = MarketScheduleOverride(
        date=parsed_date,
        override_type=payload.override_type,
        open_time_local=open_time_local,
        close_time_local=close_time_local,
        note=note,
        created_by_user_id=current_user.id,
    )
    db.add(override)
    await db.commit()
    await db.refresh(override)
    return _serialize_market_schedule_override(override)


@router.put("/market-overrides/{override_id}", response_model=MarketScheduleOverrideResponse)
async def update_market_override(
    override_id: int,
    payload: MarketScheduleOverrideUpsert,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(verify_super_admin),
):
    override = await db.get(MarketScheduleOverride, override_id)
    if override is None:
        raise HTTPException(status_code=404, detail="استثنای زمان‌بندی پیدا نشد")

    parsed_date, open_time_local, close_time_local, note = _prepare_override_payload(payload)
    duplicate = await db.execute(
        select(MarketScheduleOverride).where(
            MarketScheduleOverride.date == parsed_date,
            MarketScheduleOverride.id != override_id,
        )
    )
    if duplicate.scalars().first() is not None:
        raise HTTPException(status_code=400, detail="برای این تاریخ قبلاً استثنا ثبت شده است")

    override.date = parsed_date
    override.override_type = payload.override_type
    override.open_time_local = open_time_local
    override.close_time_local = close_time_local
    override.note = note
    await db.commit()
    await db.refresh(override)
    return _serialize_market_schedule_override(override)


@router.delete("/market-overrides/{override_id}")
async def delete_market_override(
    override_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(verify_super_admin),
):
    override = await db.get(MarketScheduleOverride, override_id)
    if override is None:
        raise HTTPException(status_code=404, detail="استثنای زمان‌بندی پیدا نشد")

    await db.delete(override)
    await db.commit()
    return {"success": True}


@router.put("/", response_model=TradingSettingsResponse, dependencies=[Depends(verify_super_admin)])
async def update_settings(updates: TradingSettingsUpdate):
    """بروزرسانی تنظیمات - فقط ادمین ارشد"""
    current = await load_trading_settings_async()
    
    # بروزرسانی فقط مقادیر ارسال شده
    update_data = updates.model_dump(exclude_unset=True)
    current_dict = current.model_dump()
    
    for key, value in update_data.items():
        if value is not None:
            current_dict[key] = value
    
    # اعتبارسنجی
    if current_dict['offer_min_quantity'] > current_dict['offer_max_quantity']:
        raise HTTPException(
            status_code=400, 
            detail="حداقل تعداد نمی‌تواند بیشتر از حداکثر باشد"
        )

    current_dict['market_closed_weekdays'] = _normalize_closed_weekdays(current_dict.get('market_closed_weekdays'))
    current_dict['market_open_time_local'], current_dict['market_close_time_local'] = _validate_market_hours(
        current_dict['market_open_time_local'],
        current_dict['market_close_time_local'],
    )
    
    # ذخیره در دیتابیس
    success = await save_trading_settings_async(current_dict)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="خطا در ذخیره تنظیمات"
        )
    
    # بارگذاری مجدد برای گرفتن مقادیر بروز
    updated = await load_trading_settings_async()

    return _serialize_trading_settings(updated)


@router.post("/reset", response_model=TradingSettingsResponse, dependencies=[Depends(verify_super_admin)])
async def reset_settings():
    """بازنشانی تنظیمات به مقادیر پیش‌فرض - فقط ادمین ارشد"""
    default_settings = TradingSettings()
    
    success = await save_trading_settings_async(default_settings.model_dump())
    if not success:
        raise HTTPException(
            status_code=500,
            detail="خطا در بازنشانی تنظیمات"
        )
    
    await refresh_settings_cache_async()

    return _serialize_trading_settings(default_settings)
