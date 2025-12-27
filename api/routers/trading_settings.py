# api/routers/trading_settings.py
"""API برای مدیریت تنظیمات سیستم معاملاتی"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from .auth import verify_super_admin_or_dev_key
from core.trading_settings import (
    TradingSettings, 
    load_trading_settings_async,
    save_trading_settings_async,
    refresh_settings_cache_async
)


router = APIRouter(
    prefix="/trading-settings",
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


class TradingSettingsResponse(BaseModel):
    """مدل پاسخ تنظیمات (بدون property ها)"""
    invitation_expiry_days: int
    offer_expiry_minutes: int
    offer_min_quantity: int
    offer_max_quantity: int
    max_active_offers: int
    offer_expire_rate_per_minute: int
    offer_expire_daily_limit_after_threshold: int
    
    # مقادیر محاسباتی
    invitation_expiry_minutes: int
    lot_min_size: int
    lot_max_count: int


@router.get("/", response_model=TradingSettingsResponse)
async def get_settings():
    """دریافت تنظیمات فعلی - برای همه کاربران قابل دسترس"""
    settings = await load_trading_settings_async()
    return TradingSettingsResponse(
        invitation_expiry_days=settings.invitation_expiry_days,
        offer_expiry_minutes=settings.offer_expiry_minutes,
        offer_min_quantity=settings.offer_min_quantity,
        offer_max_quantity=settings.offer_max_quantity,
        max_active_offers=settings.max_active_offers,
        offer_expire_rate_per_minute=settings.offer_expire_rate_per_minute,
        offer_expire_daily_limit_after_threshold=settings.offer_expire_daily_limit_after_threshold,
        invitation_expiry_minutes=settings.invitation_expiry_minutes,
        lot_min_size=settings.lot_min_size,
        lot_max_count=settings.lot_max_count,
    )


@router.put("/", response_model=TradingSettingsResponse, dependencies=[Depends(verify_super_admin_or_dev_key)])
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
    
    # ذخیره در دیتابیس
    success = await save_trading_settings_async(current_dict)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="خطا در ذخیره تنظیمات"
        )
    
    # بارگذاری مجدد برای گرفتن مقادیر بروز
    updated = await load_trading_settings_async()
    
    return TradingSettingsResponse(
        invitation_expiry_days=updated.invitation_expiry_days,
        offer_expiry_minutes=updated.offer_expiry_minutes,
        offer_min_quantity=updated.offer_min_quantity,
        offer_max_quantity=updated.offer_max_quantity,
        max_active_offers=updated.max_active_offers,
        offer_expire_rate_per_minute=updated.offer_expire_rate_per_minute,
        offer_expire_daily_limit_after_threshold=updated.offer_expire_daily_limit_after_threshold,
        invitation_expiry_minutes=updated.invitation_expiry_minutes,
        lot_min_size=updated.lot_min_size,
        lot_max_count=updated.lot_max_count,
    )


@router.post("/reset", response_model=TradingSettingsResponse, dependencies=[Depends(verify_super_admin_or_dev_key)])
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
    
    return TradingSettingsResponse(
        invitation_expiry_days=default_settings.invitation_expiry_days,
        offer_expiry_minutes=default_settings.offer_expiry_minutes,
        offer_min_quantity=default_settings.offer_min_quantity,
        offer_max_quantity=default_settings.offer_max_quantity,
        max_active_offers=default_settings.max_active_offers,
        offer_expire_rate_per_minute=default_settings.offer_expire_rate_per_minute,
        offer_expire_daily_limit_after_threshold=default_settings.offer_expire_daily_limit_after_threshold,
        invitation_expiry_minutes=default_settings.invitation_expiry_minutes,
        lot_min_size=default_settings.lot_min_size,
        lot_max_count=default_settings.lot_max_count,
    )
