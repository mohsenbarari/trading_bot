# core/trading_settings.py
"""مدیریت تنظیمات قابل تغییر سیستم معاملاتی"""

import json
import os
from pathlib import Path
from typing import Any
from pydantic import BaseModel

# مسیر فایل تنظیمات
SETTINGS_FILE = Path(__file__).parent.parent / "trading_settings.json"


class TradingSettings(BaseModel):
    """تنظیمات سیستم معاملاتی"""
    
    # تایم منقضی شدن لینک دعوت (روز) - پیش‌فرض 2 روز
    invitation_expiry_days: int = 2
    
    # تایم منقضی شدن لفظ (دقیقه) - پیش‌فرض 2 دقیقه
    offer_expiry_minutes: int = 2
    
    # حداقل تعداد کالا در لفظ - پیش‌فرض 5
    offer_min_quantity: int = 5
    
    # حداکثر تعداد کالا در لفظ - پیش‌فرض 50
    offer_max_quantity: int = 50
    
    # حداکثر تعداد لفظ‌های فعال همزمان - پیش‌فرض 4
    max_active_offers: int = 4
    
    # تعداد دفعات منقضی کردن لفظ در دقیقه - پیش‌فرض 2
    offer_expire_rate_per_minute: int = 2
    
    # آستانه تعداد منقضی شدن در روز که بعد از آن محدودیت 1/3 اعمال می‌شود
    offer_expire_daily_limit_after_threshold: int = 10
    
    # --- مقادیر محاسباتی ---
    @property
    def invitation_expiry_minutes(self) -> int:
        """تبدیل روز به دقیقه برای استفاده در کد"""
        return self.invitation_expiry_days * 24 * 60
    
    @property
    def lot_min_size(self) -> int:
        """حداقل لات = حداقل تعداد کالا"""
        return self.offer_min_quantity
    
    @property
    def lot_max_count(self) -> int:
        """حداکثر تعداد بخش‌ها = ثابت 3"""
        return 3


def load_trading_settings() -> TradingSettings:
    """خواندن تنظیمات از فایل"""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return TradingSettings(**data)
        except Exception:
            pass
    return TradingSettings()


def save_trading_settings(settings: TradingSettings) -> bool:
    """ذخیره تنظیمات در فایل"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings.model_dump(), f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def get_setting(key: str) -> Any:
    """گرفتن یک تنظیم خاص"""
    settings = load_trading_settings()
    return getattr(settings, key, None)


def update_setting(key: str, value: Any) -> bool:
    """بروزرسانی یک تنظیم خاص"""
    settings = load_trading_settings()
    if hasattr(settings, key):
        setattr(settings, key, value)
        return save_trading_settings(settings)
    return False


# متغیر سراسری برای کش کردن تنظیمات
_cached_settings: TradingSettings = None
_last_file_mtime: float = 0


def get_trading_settings() -> TradingSettings:
    """گرفتن تنظیمات با کش هوشمند (بررسی تغییر فایل)"""
    global _cached_settings, _last_file_mtime
    
    try:
        # بررسی زمان آخرین تغییر فایل
        current_mtime = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0
        
        # اگر فایل تغییر کرده یا کش خالی است، بارگذاری مجدد
        if _cached_settings is None or current_mtime != _last_file_mtime:
            _cached_settings = load_trading_settings()
            _last_file_mtime = current_mtime
    except Exception:
        if _cached_settings is None:
            _cached_settings = TradingSettings()
    
    return _cached_settings


def refresh_settings_cache():
    """بروزرسانی فوری کش تنظیمات"""
    global _cached_settings, _last_file_mtime
    _cached_settings = load_trading_settings()
    _last_file_mtime = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0

