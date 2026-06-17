"""Market page presence helpers backed by Redis."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from core.redis import get_redis_client

logger = logging.getLogger(__name__)

MARKET_PAGE_PRESENCE_TTL_SECONDS = 75
MARKET_PAGE_PRESENCE_KEY_PREFIX = "presence:market_page"


def is_market_route(path: str | None) -> bool:
    if not isinstance(path, str):
        return False
    normalized = path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return normalized == "/market"


def _market_page_presence_key(user_id: int, connection_id: str) -> str:
    return f"{MARKET_PAGE_PRESENCE_KEY_PREFIX}:{int(user_id)}:{connection_id}"


def _market_page_presence_pattern(user_id: int) -> str:
    return f"{MARKET_PAGE_PRESENCE_KEY_PREFIX}:{int(user_id)}:*"


async def set_market_page_presence(
    user_id: int,
    connection_id: str,
    *,
    path: str | None,
    visible: bool,
) -> None:
    """Mark or clear one websocket connection's market-page presence."""
    if not connection_id:
        return

    try:
        client = get_redis_client()
        key = _market_page_presence_key(user_id, connection_id)
        if visible and is_market_route(path):
            await client.setex(key, MARKET_PAGE_PRESENCE_TTL_SECONDS, "1")
        else:
            await client.delete(key)
    except Exception as exc:
        logger.debug("Failed to update market page presence: %s", exc)


async def refresh_market_page_presence(user_id: int, connection_id: str, *, active: bool) -> None:
    if not active or not connection_id:
        return
    try:
        client = get_redis_client()
        await client.expire(
            _market_page_presence_key(user_id, connection_id),
            MARKET_PAGE_PRESENCE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.debug("Failed to refresh market page presence: %s", exc)


async def clear_market_page_presence(user_id: int, connection_id: str) -> None:
    if not connection_id:
        return
    try:
        client = get_redis_client()
        await client.delete(_market_page_presence_key(user_id, connection_id))
    except Exception as exc:
        logger.debug("Failed to clear market page presence: %s", exc)


async def load_market_page_user_ids(user_ids: Iterable[int]) -> set[int]:
    """Return users who currently have at least one visible market websocket."""
    normalized_user_ids = {int(user_id) for user_id in user_ids if user_id is not None}
    if not normalized_user_ids:
        return set()

    try:
        client = get_redis_client()
    except Exception as exc:
        logger.debug("Failed to load market page presence: %s", exc)
        return set()

    present_user_ids: set[int] = set()
    for user_id in normalized_user_ids:
        try:
            async for _key in client.scan_iter(match=_market_page_presence_pattern(user_id), count=10):
                present_user_ids.add(user_id)
                break
        except Exception as exc:
            logger.debug("Failed to scan market page presence for user %s: %s", user_id, exc)
    return present_user_ids
