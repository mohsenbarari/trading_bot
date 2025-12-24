# trading_bot/core/redis.py
"""
Redis Client Manager - مدیریت بهینه اتصال Redis

این ماژول از یک کلاینت global استفاده می‌کند که در startup ایجاد
و در shutdown بسته می‌شود. این رویکرد بهینه‌تر از ساخت کلاینت در هر درخواست است.
"""
import logging
import redis.asyncio as redis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
from typing import AsyncGenerator, Optional

from core.config import settings

__all__ = [
    "pool",
    "init_redis",
    "close_redis",
    "get_redis_client",
    "get_redis",
    "check_redis_connection",
]

logger = logging.getLogger(__name__)

# ایجاد Pool اتصال
pool = ConnectionPool.from_url(
    settings.redis_url, 
    decode_responses=True 
)

# Singleton Redis Client
_redis_client: Optional[Redis] = None


async def init_redis() -> Redis:
    """
    ایجاد و راه‌اندازی کلاینت Redis.
    این تابع باید در startup اپلیکیشن فراخوانی شود.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(connection_pool=pool)
        await _redis_client.ping()  # تست اتصال
        logger.info("Redis client initialized successfully.")
    return _redis_client


async def close_redis() -> None:
    """
    بستن کلاینت Redis.
    این تابع باید در shutdown اپلیکیشن فراخوانی شود.
    """
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis client closed.")


def get_redis_client() -> Redis:
    """
    دسترسی به کلاینت Redis singleton.
    از این تابع برای دسترسی مستقیم (بدون dependency injection) استفاده کنید.
    
    Raises:
        RuntimeError: اگر کلاینت هنوز ایجاد نشده باشد
    """
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized. Call init_redis() first.")
    return _redis_client


async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    Dependency injector برای FastAPI.
    از کلاینت singleton استفاده می‌کند (بدون ساخت آبجکت جدید در هر درخواست).
    """
    if _redis_client is None:
        # Fallback: اگر init_redis فراخوانی نشده، یک کلاینت موقت بساز
        client = redis.Redis(connection_pool=pool)
        try:
            yield client
        finally:
            await client.close()
    else:
        # از کلاینت singleton استفاده کن (بدون بستن)
        yield _redis_client


async def check_redis_connection() -> bool:
    """
    بررسی اتصال Redis.
    
    Returns:
        True اگر اتصال برقرار باشد
    """
    try:
        client = get_redis_client()
        await client.ping()
        logger.info("Redis connection OK.")
        return True
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return False