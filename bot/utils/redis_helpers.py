# bot/utils/redis_helpers.py
"""
توابع کمکی Redis برای بات

همه توابع دارای fallback به حافظه هستند اگر Redis در دسترس نباشد.
"""

import json
import time
import logging
from typing import Optional, List, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Fallback in-memory storage (در صورت خرابی Redis)
_memory_fallback = {
    "rate_tracker": {},      # user_id -> [timestamps]
    "daily_tracker": {},     # user_id -> {"date": str, "count": int}
    "confirmations": {},     # key -> timestamp
    "cache": {},             # key -> {"data": any, "expires": timestamp}
}


async def get_redis_client():
    """دریافت کلاینت Redis"""
    try:
        import redis.asyncio as redis
        from core.redis import pool
        return redis.Redis(connection_pool=pool)
    except Exception as e:
        logger.warning(f"Redis not available: {e}")
        return None


# ============================================
# RATE TRACKING (نرخ منقضی کردن لفظ)
# ============================================

async def track_expire_rate(user_id: int, window_seconds: int = 60) -> int:
    """
    ثبت یک رویداد منقضی کردن و برگرداندن تعداد در بازه زمانی
    
    Args:
        user_id: آیدی کاربر
        window_seconds: بازه زمانی (پیش‌فرض 60 ثانیه)
    
    Returns:
        تعداد رویدادها در بازه
    """
    now = time.time()
    key = f"expire_rate:{user_id}"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            # اضافه کردن timestamp به sorted set
            await redis_client.zadd(key, {str(now): now})
            
            # حذف رویدادهای قدیمی
            cutoff = now - window_seconds
            await redis_client.zremrangebyscore(key, 0, cutoff)
            
            # تنظیم TTL برای cleanup خودکار
            await redis_client.expire(key, window_seconds * 2)
            
            # شمارش رویدادها
            count = await redis_client.zcard(key)
            await redis_client.aclose()
            return count
            
        except Exception as e:
            logger.warning(f"Redis rate tracking failed: {e}")
            await redis_client.aclose()
    
    # Fallback به حافظه
    if user_id not in _memory_fallback["rate_tracker"]:
        _memory_fallback["rate_tracker"][user_id] = []
    
    _memory_fallback["rate_tracker"][user_id].append(now)
    cutoff = now - window_seconds
    _memory_fallback["rate_tracker"][user_id] = [
        t for t in _memory_fallback["rate_tracker"][user_id] if t > cutoff
    ]
    return len(_memory_fallback["rate_tracker"][user_id])


async def track_daily_expire(user_id: int, total_offers: int) -> dict:
    """
    ثبت آمار روزانه منقضی کردن
    
    Returns:
        {"date": str, "count": int, "total_offers": int, "rate": float}
    """
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"daily_expire:{user_id}:{today}"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            # افزایش شمارنده
            count = await redis_client.incr(key)
            
            # تنظیم TTL (24 ساعت)
            await redis_client.expire(key, 86400)
            
            await redis_client.aclose()
            
            rate = (count / total_offers * 100) if total_offers > 0 else 0
            return {"date": today, "count": count, "total_offers": total_offers, "rate": rate}
            
        except Exception as e:
            logger.warning(f"Redis daily tracking failed: {e}")
            await redis_client.aclose()
    
    # Fallback
    if user_id not in _memory_fallback["daily_tracker"]:
        _memory_fallback["daily_tracker"][user_id] = {"date": today, "count": 0}
    
    if _memory_fallback["daily_tracker"][user_id]["date"] != today:
        _memory_fallback["daily_tracker"][user_id] = {"date": today, "count": 0}
    
    _memory_fallback["daily_tracker"][user_id]["count"] += 1
    count = _memory_fallback["daily_tracker"][user_id]["count"]
    rate = (count / total_offers * 100) if total_offers > 0 else 0
    
    return {"date": today, "count": count, "total_offers": total_offers, "rate": rate}


# ============================================
# DOUBLE-CLICK CONFIRMATION
# ============================================

async def check_double_click(user_id: int, offer_id: int, amount: int, timeout: float = 3.0) -> bool:
    """
    بررسی دابل‌کلیک برای تایید معامله
    
    Returns:
        True اگر کلیک دوم است (تایید شده)
        False اگر کلیک اول است
    """
    key = f"confirm:{user_id}:{offer_id}:{amount}"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            # چک کردن وجود کلید
            exists = await redis_client.exists(key)
            
            if exists:
                # کلیک دوم - حذف کلید و تایید
                await redis_client.delete(key)
                await redis_client.aclose()
                return True
            else:
                # کلیک اول - ثبت با TTL (حداقل 1 ثانیه)
                ttl = max(1, int(timeout)) if timeout >= 1 else 1
                await redis_client.setex(key, ttl, "pending")
                await redis_client.aclose()
                return False
                
        except Exception as e:
            logger.warning(f"Redis double-click failed: {e}")
            await redis_client.aclose()
    
    # Fallback
    now = time.time()
    
    # تمیزکاری کلیدهای منقضی
    expired = [k for k, v in _memory_fallback["confirmations"].items() if now - v > timeout]
    for k in expired:
        del _memory_fallback["confirmations"][k]
    
    if key in _memory_fallback["confirmations"]:
        del _memory_fallback["confirmations"][key]
        return True
    else:
        _memory_fallback["confirmations"][key] = now
        return False


# ============================================
# COMMODITY CACHE
# ============================================

async def get_cached_commodities() -> Optional[List[dict]]:
    """
    دریافت لیست کالاها از cache
    
    Returns:
        لیست کالاها یا None اگر cache خالی است
    """
    key = "cache:commodities:all"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            cached = await redis_client.get(key)
            await redis_client.aclose()
            
            if cached:
                return json.loads(cached)
            return None
            
        except Exception as e:
            logger.warning(f"Redis cache get failed: {e}")
            await redis_client.aclose()
    
    # Fallback
    cache_entry = _memory_fallback["cache"].get(key)
    if cache_entry and cache_entry["expires"] > time.time():
        return cache_entry["data"]
    return None


async def set_cached_commodities(commodities: List[dict], ttl: int = 300):
    """
    ذخیره لیست کالاها در cache
    
    Args:
        commodities: لیست کالاها
        ttl: زمان انقضا (پیش‌فرض 5 دقیقه)
    """
    key = "cache:commodities:all"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            await redis_client.setex(key, ttl, json.dumps(commodities, ensure_ascii=False))
            await redis_client.aclose()
            return
            
        except Exception as e:
            logger.warning(f"Redis cache set failed: {e}")
            await redis_client.aclose()
    
    # Fallback
    _memory_fallback["cache"][key] = {
        "data": commodities,
        "expires": time.time() + ttl
    }


async def invalidate_commodity_cache():
    """پاک کردن cache کالاها (بعد از تغییر)"""
    key = "cache:commodities:all"
    
    redis_client = await get_redis_client()
    
    if redis_client:
        try:
            await redis_client.delete(key)
            await redis_client.aclose()
        except Exception as e:
            logger.debug(f"Failed to invalidate commodity cache: {e}")
    
    # Fallback
    if key in _memory_fallback["cache"]:
        del _memory_fallback["cache"][key]
