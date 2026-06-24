# core/connectivity.py
"""Global Internet Connectivity Monitor.

Iran never probes Telegram directly. On Iran, this monitor checks the configured
foreign peer reachability and stores the result in Redis key
``connectivity:global``.
"""
import asyncio
import logging
import httpx
from core.background_job_authority import JOB_CONNECTIVITY_MONITOR, check_background_job_authority
from core.config import settings
from bot.utils.redis_helpers import get_redis

logger = logging.getLogger(__name__)

REDIS_KEY_CONNECTIVITY = "connectivity:global"
CHECK_INTERVAL = 30  # seconds
TELEGRAM_API_URL = "https://api.telegram.org"


def _iran_connectivity_target_url() -> str | None:
    for value in (
        getattr(settings, "foreign_server_url", None),
        getattr(settings, "peer_server_url", None),
        getattr(settings, "germany_server_url", None),
    ):
        target = str(value or "").strip().rstrip("/")
        if target:
            return target
    return None

async def check_connectivity():
    """
    Check internet connectivity.
    - Foreign Server: may ping Telegram API directly.
    - Iran Server: only pings the foreign peer and never falls back to Telegram.
    """
    try:
        # Determine target based on server mode
        if settings.server_mode == "iran":
            target_url = _iran_connectivity_target_url()
            if not target_url:
                logger.warning(
                    "Iran connectivity monitor has no foreign peer URL; marking disconnected",
                    extra={
                        "event": "connectivity.iran_peer_url_missing",
                        "server_mode": settings.server_mode,
                    },
                )
                return False
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
    decision = check_background_job_authority(JOB_CONNECTIVITY_MONITOR)
    if not decision.ok:
        # Foreign server is assumed to be always connected
        logger.info(
            "Connectivity monitor skipped by background job authority policy",
            extra=decision.as_log_extra(),
        )
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
