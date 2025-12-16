# api/routers/trading_settings.py
"""API برای مدیریت تنظیمات سیستم معاملاتی"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from .auth import verify_super_admin_or_dev_key
from core.trading_settings import (
    TradingSettings, 
    load_trading_settings, 
    save_trading_settings,
    refresh_settings_cache
)


router = APIRouter(
    prefix="/trading-settings",
    tags=["Trading Settings"],
    dependencies=[Depends(verify_super_admin_or_dev_key)]
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


@router.get("/", response_model=TradingSettings)
async def get_settings():
    """دریافت تنظیمات فعلی"""
    return load_trading_settings()


@router.put("/", response_model=TradingSettings)
async def update_settings(updates: TradingSettingsUpdate):
    """بروزرسانی تنظیمات"""
    current = load_trading_settings()
    
    # بروزرسانی فقط مقادیر ارسال شده
    update_data = updates.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if value is not None:
            setattr(current, key, value)
    
    # اعتبارسنجی
    if current.offer_min_quantity > current.offer_max_quantity:
        raise HTTPException(
            status_code=400, 
            detail="حداقل تعداد نمی‌تواند بیشتر از حداکثر باشد"
        )
    
    # ذخیره
    if not save_trading_settings(current):
        raise HTTPException(
            status_code=500,
            detail="خطا در ذخیره تنظیمات"
        )
    
    # بروزرسانی کش
    refresh_settings_cache()
    
    return current


@router.post("/reset", response_model=TradingSettings)
async def reset_settings():
    """بازنشانی تنظیمات به مقادیر پیش‌فرض"""
    default_settings = TradingSettings()
    
    if not save_trading_settings(default_settings):
        raise HTTPException(
            status_code=500,
            detail="خطا در بازنشانی تنظیمات"
        )
    
    refresh_settings_cache()
    
    return default_settings
