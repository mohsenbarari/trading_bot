# core/trading_settings.py
"""
مدیریت تنظیمات قابل تغییر سیستم معاملاتی

این ماژول تنظیمات را از دیتابیس می‌خواند و در حافظه کش می‌کند.
برای سازگاری با کد قدیمی، اگر تنظیمات در دیتابیس نباشد، از JSON فایل می‌خواند.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel

# مسیر فایل تنظیمات (fallback)
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


# ===== CACHE =====
_cached_settings: Optional[TradingSettings] = None
_cache_timestamp: float = 0
CACHE_TTL_SECONDS = 60  # کش هر 60 ثانیه منقضی می‌شود


def _load_from_json() -> dict:
    """خواندن تنظیمات از فایل JSON (fallback)"""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _load_from_db_sync() -> dict:
    """
    خواندن تنظیمات از دیتابیس (sync برای استفاده در context های غیر async).
    اگر دیتابیس در دسترس نباشد، دیکشنری خالی برمی‌گرداند.
    """
    try:
        from sqlalchemy import create_engine, select
        from core.config import settings as app_settings
        from models.trading_setting import TradingSetting
        
        # استفاده از sync database URL
        engine = create_engine(app_settings.sync_database_url)
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text("SELECT key, value FROM trading_settings"))
            rows = result.fetchall()
            
            data = {}
            for row in rows:
                try:
                    data[row[0]] = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    data[row[0]] = row[1]
            return data
    except Exception as e:
        # اگر دیتابیس در دسترس نیست، از fallback استفاده کن
        return {}


def load_trading_settings() -> TradingSettings:
    """خواندن تنظیمات (اول از DB، بعد از JSON)"""
    # اول از دیتابیس
    db_data = _load_from_db_sync()
    
    if db_data:
        return TradingSettings(**db_data)
    
    # اگر در DB نبود، از JSON
    json_data = _load_from_json()
    if json_data:
        return TradingSettings(**json_data)
    
    # مقادیر پیش‌فرض
    return TradingSettings()


def get_trading_settings() -> TradingSettings:
    """گرفتن تنظیمات با کش (بررسی TTL)"""
    global _cached_settings, _cache_timestamp
    
    import time
    current_time = time.time()
    
    # اگر کش معتبر است، از آن استفاده کن
    if _cached_settings is not None and (current_time - _cache_timestamp) < CACHE_TTL_SECONDS:
        return _cached_settings
    
    # بارگذاری مجدد
    try:
        _cached_settings = load_trading_settings()
        _cache_timestamp = current_time
    except Exception:
        if _cached_settings is None:
            _cached_settings = TradingSettings()
    
    return _cached_settings


def refresh_settings_cache():
    """بروزرسانی فوری کش تنظیمات"""
    global _cached_settings, _cache_timestamp
    import time
    _cached_settings = load_trading_settings()
    _cache_timestamp = time.time()


async def save_trading_settings_async(settings_dict: dict) -> bool:
    """
    ذخیره تنظیمات در دیتابیس (async).
    
    Args:
        settings_dict: دیکشنری تنظیمات
        
    Returns:
        True اگر موفق بود
    """
    try:
        from sqlalchemy import text
        from core.db import AsyncSessionLocal
        from models.trading_setting import TradingSetting
        
        async with AsyncSessionLocal() as session:
            for key, value in settings_dict.items():
                # حذف @property ها و فیلدهای غیرقابل ذخیره
                if key.startswith('_') or callable(value):
                    continue
                
                value_json = json.dumps(value)
                
                # Upsert
                stmt = text("""
                    INSERT INTO trading_settings (key, value, updated_at) 
                    VALUES (:key, :value, :updated_at)
                    ON CONFLICT (key) DO UPDATE SET 
                        value = :value, 
                        updated_at = :updated_at
                """)
                await session.execute(stmt, {
                    'key': key,
                    'value': value_json,
                    'updated_at': datetime.utcnow()
                })
            
            await session.commit()
        
        # بروزرسانی کش
        refresh_settings_cache()
        return True
        
    except Exception as e:
        print(f"Error saving trading settings: {e}")
        return False


def get_setting(key: str) -> Any:
    """گرفتن یک تنظیم خاص"""
    settings = get_trading_settings()
    return getattr(settings, key, None)


def update_setting(key: str, value: Any) -> bool:
    """بروزرسانی یک تنظیم خاص (sync - برای سازگاری)"""
    import asyncio
    settings = get_trading_settings()
    if hasattr(settings, key):
        data = settings.model_dump()
        data[key] = value
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(save_trading_settings_async(data))
        except Exception:
            return False
    return False
