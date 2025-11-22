# trading_bot/core/redis.py
import redis.asyncio as redis
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool
from typing import AsyncGenerator

from core.config import settings

# ایجاد Pool اتصال
pool = ConnectionPool.from_url(
    settings.redis_url, 
    decode_responses=True 
)

# ❌ دکوریتور @asynccontextmanager حذف شد
async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    Dependency injector برای گرفتن یک اتصال Redis از Pool.
    FastAPI خودش مدیریت yield را انجام می‌دهد، پس نیازی به دکوریتور نیست.
    """
    client = redis.Redis(connection_pool=pool)
    try:
        yield client
    finally:
        await client.close()

async def check_redis_connection():
    client = redis.Redis(connection_pool=pool)
    try:
        await client.ping()
        print("✅ Successfully connected to Redis pool.")
    except Exception as e:
        print(f"❌ Failed to connect to Redis: {e}")
    finally:
        await client.close()