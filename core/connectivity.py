# core/connectivity.py
"""Global Internet Connectivity Monitor.

Iran never probes Telegram directly.  The historical Iran-to-FI peer probe is
retired under the three-site architecture, so Iran records it as unavailable
without resolving or contacting a peer.  The local Redis key remains useful
to callers that need a conservative connectivity state.
"""
import asyncio
import logging
import httpx
from core.background_job_authority import JOB_CONNECTIVITY_MONITOR, check_background_job_authority
from core.config import settings
from core.legacy_direct_fi_ir_transport_fence import (
    LegacyDirectFiIrTransportRetiredError,
    assert_legacy_direct_fi_ir_transport_retired,
)
from bot.utils.redis_helpers import get_redis

logger = logging.getLogger(__name__)

REDIS_KEY_CONNECTIVITY = "connectivity:global"
CHECK_INTERVAL = 30  # seconds
TELEGRAM_API_URL = "https://api.telegram.org"


def _iran_connectivity_target_url() -> str | None:
    """Refuse the superseded Iran-to-FI peer reachability probe."""

    assert_legacy_direct_fi_ir_transport_retired(
        component="connectivity-monitor",
        operation="Iran direct FI peer HTTP probe target resolution",
    )

async def check_connectivity():
    """
    Check internet connectivity.
    - Foreign Server: may ping Telegram API directly.
    - Iran Server: never opens the retired direct FI peer probe.
    """
    try:
        # Determine target based on server mode
        if settings.server_mode == "iran":
            try:
                _iran_connectivity_target_url()
            except LegacyDirectFiIrTransportRetiredError:
                logger.info(
                    "Iran direct FI peer probe is retired; marking peer connectivity unavailable",
                    extra={
                        "event": "connectivity.iran_direct_peer_probe_retired",
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
