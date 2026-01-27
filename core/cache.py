# core/cache.py
"""
Redis Cache Service - لایه کش مرکزی

این ماژول توابع کمکی برای کش کردن داده‌ها در Redis ارائه می‌دهد.
همه توابع دارای fallback هستند و در صورت خرابی Redis، خطا نمی‌دهند.
"""

import json
import logging
from typing import Any, Optional, TypeVar, Callable
from datetime import datetime

logger = logging.getLogger(__name__)

T = TypeVar('T')

# ===== Cache Keys =====
class CacheKeys:
    """کلیدهای کش"""
    
    # User cache: user:telegram_id:{telegram_id}
    @staticmethod
    def user_by_telegram_id(telegram_id: int) -> str:
        return f"user:telegram_id:{telegram_id}"
    
    # Active offer count: user:{user_id}:active_offer_count
    @staticmethod
    def active_offer_count(user_id: int) -> str:
        return f"user:{user_id}:active_offer_count"
    
    # Commodities cache (shared with bot)
    COMMODITIES_ALL = "cache:commodities:all"
    
    # Price average: price_avg:{commodity_id}:{offer_type}:{quantity_range}
    @staticmethod
    def price_average(commodity_id: int, offer_type: str, quantity_range: str) -> str:
        return f"price_avg:{commodity_id}:{offer_type}:{quantity_range}"


# ===== TTL Constants (seconds) =====
class CacheTTL:
    """زمان انقضای کش"""
    USER = 300           # 5 minutes
    OFFER_COUNT = 30     # 30 seconds
    COMMODITIES = 300    # 5 minutes
    PRICE_AVG = 60       # 1 minute


# ===== Helper Functions =====

async def _get_redis():
    """دریافت کلاینت Redis"""
    try:
        from core.redis import get_redis_client
        return get_redis_client()
    except Exception as e:
        logger.debug(f"Redis not available: {e}")
        return None


async def cache_get(key: str) -> Optional[Any]:
    """
    خواندن از کش
    
    Returns:
        مقدار کش شده یا None
    """
    redis = await _get_redis()
    if not redis:
        return None
    
    try:
        data = await redis.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.debug(f"Cache get error for {key}: {e}")
        return None


async def cache_set(key: str, value: Any, ttl: int = 60) -> bool:
    """
    نوشتن در کش
    
    Args:
        key: کلید کش
        value: مقدار (باید JSON serializable باشد)
        ttl: زمان انقضا (ثانیه)
    
    Returns:
        True اگر موفق
    """
    redis = await _get_redis()
    if not redis:
        return False
    
    try:
        await redis.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
        return True
    except Exception as e:
        logger.debug(f"Cache set error for {key}: {e}")
        return False


async def cache_delete(key: str) -> bool:
    """
    حذف از کش
    
    Returns:
        True اگر موفق
    """
    redis = await _get_redis()
    if not redis:
        return False
    
    try:
        await redis.delete(key)
        return True
    except Exception as e:
        logger.debug(f"Cache delete error for {key}: {e}")
        return False


async def cache_incr(key: str, ttl: int = 60) -> Optional[int]:
    """
    افزایش مقدار عدد صحیح
    
    Returns:
        مقدار جدید یا None
    """
    redis = await _get_redis()
    if not redis:
        return None
    
    try:
        value = await redis.incr(key)
        await redis.expire(key, ttl)
        return value
    except Exception as e:
        logger.debug(f"Cache incr error for {key}: {e}")
        return None


async def cache_decr(key: str, min_value: int = 0) -> Optional[int]:
    """
    کاهش مقدار عدد صحیح (حداقل min_value)
    
    Returns:
        مقدار جدید یا None
    """
    redis = await _get_redis()
    if not redis:
        return None
    
    try:
        value = await redis.decr(key)
        # اطمینان از منفی نشدن
        if value < min_value:
            await redis.set(key, min_value)
            return min_value
        return value
    except Exception as e:
        logger.debug(f"Cache decr error for {key}: {e}")
        return None


async def cache_delete_pattern(pattern: str) -> int:
    """
    حذف کلیدهای مطابق الگو
    
    Args:
        pattern: الگوی glob (مثل "price_avg:*")
    
    Returns:
        تعداد کلیدهای حذف شده
    """
    redis = await _get_redis()
    if not redis:
        return 0
    
    try:
        keys = []
        async for key in redis.scan_iter(match=pattern):
            keys.append(key)
        
        if keys:
            await redis.delete(*keys)
            return len(keys)
        return 0
    except Exception as e:
        logger.debug(f"Cache delete pattern error for {pattern}: {e}")
        return 0


# ===== High-Level Cache Functions =====

async def get_cached_user_by_telegram_id(telegram_id: int) -> Optional[dict]:
    """دریافت کاربر از کش"""
    key = CacheKeys.user_by_telegram_id(telegram_id)
    return await cache_get(key)


async def set_cached_user(telegram_id: int, user_data: dict) -> bool:
    """ذخیره کاربر در کش"""
    key = CacheKeys.user_by_telegram_id(telegram_id)
    return await cache_set(key, user_data, CacheTTL.USER)


async def invalidate_user_cache(telegram_id: int) -> bool:
    """پاک کردن کش کاربر"""
    key = CacheKeys.user_by_telegram_id(telegram_id)
    return await cache_delete(key)


async def get_active_offer_count(user_id: int) -> Optional[int]:
    """دریافت تعداد لفظ‌های فعال از کش"""
    key = CacheKeys.active_offer_count(user_id)
    return await cache_get(key)


async def set_active_offer_count(user_id: int, count: int) -> bool:
    """ذخیره تعداد لفظ‌های فعال"""
    key = CacheKeys.active_offer_count(user_id)
    return await cache_set(key, count, CacheTTL.OFFER_COUNT)


async def incr_active_offer_count(user_id: int) -> Optional[int]:
    """افزایش تعداد لفظ‌های فعال"""
    key = CacheKeys.active_offer_count(user_id)
    return await cache_incr(key, CacheTTL.OFFER_COUNT)


async def decr_active_offer_count(user_id: int) -> Optional[int]:
    """کاهش تعداد لفظ‌های فعال"""
    key = CacheKeys.active_offer_count(user_id)
    return await cache_decr(key, min_value=0)


async def get_price_average(commodity_id: int, offer_type: str, quantity_range: str) -> Optional[float]:
    """دریافت میانگین قیمت از کش"""
    key = CacheKeys.price_average(commodity_id, offer_type, quantity_range)
    return await cache_get(key)


async def set_price_average(commodity_id: int, offer_type: str, quantity_range: str, avg_price: float) -> bool:
    """ذخیره میانگین قیمت"""
    key = CacheKeys.price_average(commodity_id, offer_type, quantity_range)
    return await cache_set(key, avg_price, CacheTTL.PRICE_AVG)


async def invalidate_price_averages(commodity_id: int = None) -> int:
    """پاک کردن کش میانگین قیمت‌ها"""
    if commodity_id:
        pattern = f"price_avg:{commodity_id}:*"
    else:
        pattern = "price_avg:*"
    return await cache_delete_pattern(pattern)


async def get_cached_commodities() -> Optional[list]:
    """دریافت لیست کالاها از کش"""
    return await cache_get(CacheKeys.COMMODITIES_ALL)


async def set_cached_commodities(commodities: list) -> bool:
    """ذخیره لیست کالاها در کش"""
    return await cache_set(CacheKeys.COMMODITIES_ALL, commodities, CacheTTL.COMMODITIES)


async def invalidate_commodities_cache() -> bool:
    """پاک کردن کش کالاها"""
    return await cache_delete(CacheKeys.COMMODITIES_ALL)
