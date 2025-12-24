# core/trading_settings.py
"""
مدیریت تنظیمات قابل تغییر سیستم معاملاتی

این ماژول تنظیمات را از دیتابیس می‌خواند و در Redis کش می‌کند.
استفاده از Redis به جای متغیر global باعث می‌شود همه workerها کش یکسان داشته باشند.

برای سازگاری با کد قدیمی، اگر تنظیمات در دیتابیس نباشد، از JSON فایل می‌خواند.
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

__all__ = [
    "TradingSettings",
    "get_trading_settings",
    "load_trading_settings",
    "refresh_settings_cache",
    "save_trading_settings_async",
    "get_setting",
    "update_setting_async",
]

logger = logging.getLogger(__name__)

# مسیر فایل تنظیمات (fallback)
SETTINGS_FILE = Path(__file__).parent.parent / "trading_settings.json"

# کلید کش در Redis
REDIS_CACHE_KEY = "trading_settings:cache"
CACHE_TTL_SECONDS = 60  # کش هر 60 ثانیه منقضی می‌شود


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


# ===== IN-MEMORY FALLBACK CACHE =====
# این کش فقط زمانی استفاده می‌شود که Redis در دسترس نباشد
_fallback_cache: Optional[TradingSettings] = None
_fallback_timestamp: float = 0


def _load_from_json() -> dict:
    """خواندن تنظیمات از فایل JSON (fallback)"""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load settings from JSON: {e}")
    return {}


async def _load_from_db_async() -> dict:
    """
    خواندن تنظیمات از دیتابیس (async).
    این روش best practice است و از blocking جلوگیری می‌کند.
    """
    try:
        from sqlalchemy import text
        from core.db import AsyncSessionLocal
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT key, value FROM trading_settings")
            )
            rows = result.fetchall()
            
            data = {}
            for row in rows:
                try:
                    data[row[0]] = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    data[row[0]] = row[1]
            return data
            
    except Exception as e:
        logger.warning(f"Failed to load settings from DB: {e}")
        return {}


async def _get_from_redis_cache() -> Optional[TradingSettings]:
    """خواندن تنظیمات از کش Redis"""
    try:
        from core.redis import get_redis_client
        
        redis_client = get_redis_client()
        cached_data = await redis_client.get(REDIS_CACHE_KEY)
        
        if cached_data:
            data = json.loads(cached_data)
            return TradingSettings(**data)
            
    except Exception as e:
        logger.debug(f"Redis cache miss or error: {e}")
    
    return None


async def _set_redis_cache(settings: TradingSettings) -> None:
    """ذخیره تنظیمات در کش Redis"""
    try:
        from core.redis import get_redis_client
        
        redis_client = get_redis_client()
        data = settings.model_dump()
        await redis_client.setex(
            REDIS_CACHE_KEY,
            CACHE_TTL_SECONDS,
            json.dumps(data)
        )
        
    except Exception as e:
        logger.debug(f"Failed to set Redis cache: {e}")


async def load_trading_settings_async() -> TradingSettings:
    """خواندن تنظیمات (async) - اول از DB، بعد از JSON"""
    # اول از دیتابیس
    db_data = await _load_from_db_async()
    
    if db_data:
        return TradingSettings(**db_data)
    
    # اگر در DB نبود، از JSON
    json_data = _load_from_json()
    if json_data:
        return TradingSettings(**json_data)
    
    # مقادیر پیش‌فرض
    return TradingSettings()


def load_trading_settings() -> TradingSettings:
    """
    خواندن تنظیمات (sync fallback).
    
    توجه: این تابع برای سازگاری با کد قدیمی است.
    در کد جدید از get_trading_settings_async() استفاده کنید.
    """
    # اول از JSON (غیر blocking)
    json_data = _load_from_json()
    if json_data:
        return TradingSettings(**json_data)
    
    # مقادیر پیش‌فرض
    return TradingSettings()


async def get_trading_settings_async() -> TradingSettings:
    """
    گرفتن تنظیمات با کش Redis (async - توصیه شده).
    
    این تابع برای استفاده در context های async است.
    """
    # اول از کش Redis
    cached = await _get_from_redis_cache()
    if cached is not None:
        return cached
    
    # بارگذاری از DB
    settings = await load_trading_settings_async()
    
    # ذخیره در کش Redis
    await _set_redis_cache(settings)
    
    return settings


def get_trading_settings() -> TradingSettings:
    """
    گرفتن تنظیمات با کش (sync - برای سازگاری).
    
    این تابع از fallback cache استفاده می‌کند.
    برای بهترین عملکرد، از get_trading_settings_async() استفاده کنید.
    """
    global _fallback_cache, _fallback_timestamp
    
    current_time = time.time()
    
    # اگر کش معتبر است، از آن استفاده کن
    if _fallback_cache is not None and (current_time - _fallback_timestamp) < CACHE_TTL_SECONDS:
        return _fallback_cache
    
    # بارگذاری مجدد (sync fallback)
    try:
        _fallback_cache = load_trading_settings()
        _fallback_timestamp = current_time
    except Exception:
        if _fallback_cache is None:
            _fallback_cache = TradingSettings()
    
    return _fallback_cache


async def refresh_settings_cache_async() -> None:
    """بروزرسانی فوری کش تنظیمات (async)"""
    global _fallback_cache, _fallback_timestamp
    
    settings = await load_trading_settings_async()
    
    # بروزرسانی Redis cache
    await _set_redis_cache(settings)
    
    # بروزرسانی fallback cache
    _fallback_cache = settings
    _fallback_timestamp = time.time()


def refresh_settings_cache() -> None:
    """بروزرسانی فوری کش تنظیمات (sync fallback)"""
    global _fallback_cache, _fallback_timestamp
    
    _fallback_cache = load_trading_settings()
    _fallback_timestamp = time.time()


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
        await refresh_settings_cache_async()
        return True
        
    except Exception as e:
        logger.error(f"Error saving trading settings: {e}")
        return False


def get_setting(key: str) -> Any:
    """گرفتن یک تنظیم خاص"""
    settings = get_trading_settings()
    return getattr(settings, key, None)


async def update_setting_async(key: str, value: Any) -> bool:
    """
    بروزرسانی یک تنظیم خاص (async).
    
    این تابع جایگزین update_setting() قدیمی است که از deprecated API استفاده می‌کرد.
    """
    settings = await get_trading_settings_async()
    if hasattr(settings, key):
        data = settings.model_dump()
        data[key] = value
        return await save_trading_settings_async(data)
    return False
