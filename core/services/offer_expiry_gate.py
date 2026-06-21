"""Redis-backed single-flight gate for manual offer expiry."""
from __future__ import annotations

import hashlib
import inspect
import logging
import uuid
from dataclasses import dataclass

import redis.asyncio as redis
from redis.asyncio import Redis

from core.redis import get_redis_client, pool


logger = logging.getLogger(__name__)

OFFER_EXPIRY_GATE_PREFIX = "offer:expiry"
DEFAULT_OFFER_EXPIRY_GATE_TTL_SECONDS = 10.0
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


@dataclass
class OfferExpiryGateLease:
    key: str
    token: str | None
    acquired: bool
    _client: Redis | None = None
    _owns_client: bool = False

    async def release(self) -> None:
        if not self.acquired or not self.token or self._client is None:
            await self._close_owned_client()
            return
        try:
            await self._client.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.token)
        except Exception as exc:
            logger.debug("Failed to release offer expiry gate %s: %s", self.key, exc)
        finally:
            self.acquired = False
            await self._close_owned_client()

    async def _close_owned_client(self) -> None:
        if self._owns_client and self._client is not None:
            await _close_redis_client(self._client)
            self._client = None


def build_offer_expiry_gate_key(offer_id: int | str) -> str:
    digest = hashlib.sha256(f"id:{int(offer_id)}".encode("utf-8")).hexdigest()[:32]
    return f"{OFFER_EXPIRY_GATE_PREFIX}:{digest}"


def _get_gate_client() -> tuple[Redis, bool]:
    try:
        return get_redis_client(), False
    except Exception:
        return redis.Redis(connection_pool=pool), True


async def _close_redis_client(client: Redis) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def try_acquire_offer_expiry_gate(
    *,
    offer_id: int | str,
    ttl_seconds: float = DEFAULT_OFFER_EXPIRY_GATE_TTL_SECONDS,
) -> OfferExpiryGateLease:
    key = build_offer_expiry_gate_key(offer_id)
    token = uuid.uuid4().hex
    client: Redis | None = None
    owns_client = False
    try:
        client, owns_client = _get_gate_client()
        acquired = bool(await client.set(key, token, nx=True, px=max(250, int(ttl_seconds * 1000))))
        if not acquired:
            if owns_client:
                await _close_redis_client(client)
            return OfferExpiryGateLease(key=key, token=None, acquired=False)
        return OfferExpiryGateLease(key=key, token=token, acquired=True, _client=client, _owns_client=owns_client)
    except Exception as exc:
        if owns_client and client is not None:
            await _close_redis_client(client)
        logger.warning("Offer expiry gate unavailable; allowing request: %s", type(exc).__name__)
        return OfferExpiryGateLease(key=key, token=None, acquired=True)
