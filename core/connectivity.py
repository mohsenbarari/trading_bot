# core/connectivity.py
"""
Global Internet Connectivity Monitor (Iran Server)

Periodically checks connectivity to Telegram API to determine if
Iran's internet has access to global networks.
Stores status in Redis key 'connectivity:global'.
"""
import asyncio
import logging
import httpx
from core.config import settings
from bot.utils.redis_helpers import get_redis

logger = logging.getLogger(__name__)

REDIS_KEY_CONNECTIVITY = "connectivity:global"
CHECK_INTERVAL = 30  # seconds
TELEGRAM_API_URL = "https://api.telegram.org"

async def check_connectivity():
    """
    Check internet connectivity.
    - Foreign Server: Pings Telegram API directly.
    - Iran Server: Pings Foreign Server (to verify relay path).
    """
    try:
        # Determine target based on server mode
        if settings.server_mode == "iran" and settings.foreign_server_url:
            # Check connection to Foreign Server
            target_url = settings.foreign_server_url
        else:
            # Check connection to Telegram API
            target_url = TELEGRAM_API_URL

        # Use a simple HEAD request (or GET if HEAD not supported)
        # We use a short timeout (5s) to fail fast
        async with httpx.AsyncClient(timeout=5.0) as client:
            # We don't need a valid response, just reachability
            # Using base URL is usually blocked if filtering is active
            resp = await client.get(f"{target_url}")
            # Any response (even 404/500) means we reached the server
            return True
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout):
        return False
    except Exception as e:
        logger.debug(f"Connectivity check error: {e}")
        return False


async def connectivity_monitor_loop():
    """Background task to update connectivity status."""
    if settings.server_mode != "iran":
        # Foreign server is assumed to be always connected
        return

    logger.info("🌐 Connectivity monitor started")
    redis = await get_redis()
    
    while True:
        is_connected = await check_connectivity()
        
        # Store as string "true"/"false" for easy reading
        await redis.set(REDIS_KEY_CONNECTIVITY, "true" if is_connected else "false")
        
        # Log state changes could be noisy, so we just update silently usually
        # or log only on change if needed.
        
        await asyncio.sleep(CHECK_INTERVAL)

async def is_internet_connected() -> bool:
    """Read cached connectivity status from Redis."""
    if settings.server_mode != "iran":
        return True
        
    redis = await get_redis()
    val = await redis.get(REDIS_KEY_CONNECTIVITY)
    return val == "true"
