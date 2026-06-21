"""Redis-backed short gate for hot-offer trade contention."""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass

import redis.asyncio as redis
from redis.asyncio import Redis

from core.config import settings
from core.redis import get_redis_client, pool


logger = logging.getLogger(__name__)

TRADE_CONTENTION_GATE_PREFIX = "trade:contention"
_ACQUIRE_SEMAPHORE_SLOT_SCRIPT = """
local key_type = redis.call("type", KEYS[1]).ok
if key_type ~= "none" and key_type ~= "hash" then
    return 0
end
if redis.call("hlen", KEYS[1]) >= tonumber(ARGV[3]) then
    return 0
end
redis.call("hset", KEYS[1], ARGV[1], "1")
redis.call("pexpire", KEYS[1], ARGV[2])
return 1
"""
_RELEASE_SEMAPHORE_SLOT_SCRIPT = """
local key_type = redis.call("type", KEYS[1]).ok
if key_type ~= "hash" then
    return 0
end
local removed = redis.call("hdel", KEYS[1], ARGV[1])
if redis.call("hlen", KEYS[1]) == 0 then
    redis.call("del", KEYS[1])
end
return removed
"""


@dataclass
class TradeContentionLease:
    key: str | None
    token: str | None
    acquired: bool
    _client: Redis | None = None
    _owns_client: bool = False

    async def release(self) -> None:
        if not self.acquired or not self.key or not self.token or self._client is None:
            await self._close_owned_client()
            return
        try:
            await self._client.eval(_RELEASE_SEMAPHORE_SLOT_SCRIPT, 1, self.key, self.token)
        except Exception as exc:
            logger.debug("Failed to release trade contention gate %s: %s", self.key, exc)
        finally:
            self.acquired = False
            await self._close_owned_client()

    async def _close_owned_client(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


def trade_contention_lease_was_pre_gated(lease: object) -> bool:
    return bool(getattr(lease, "acquired", False) and getattr(lease, "token", None))


def build_trade_contention_gate_key(*, offer_public_id: str | None = None, offer_id: int | str | None = None) -> str:
    public_id = str(offer_public_id or "").strip()
    if public_id:
        raw_key = f"public:{public_id}"
    else:
        if offer_id is None:
            raise ValueError("offer_public_id or offer_id is required for trade contention gate")
        raw_key = f"id:{int(offer_id)}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
    return f"{TRADE_CONTENTION_GATE_PREFIX}:{digest}"


def _gate_ttl_ms(ttl_seconds: float | None = None) -> int:
    raw_ttl = settings.trade_contention_gate_ttl_seconds if ttl_seconds is None else ttl_seconds
    return max(250, int(float(raw_ttl) * 1000))


def _gate_max_inflight(max_inflight: int | None = None) -> int:
    raw_limit = settings.trade_contention_gate_max_inflight if max_inflight is None else max_inflight
    return max(1, int(raw_limit))


def _get_gate_client() -> tuple[Redis, bool]:
    try:
        return get_redis_client(), False
    except Exception:
        return redis.Redis(connection_pool=pool), True


async def try_acquire_trade_contention_gate(
    *,
    offer_public_id: str | None = None,
    offer_id: int | str | None = None,
    ttl_seconds: float | None = None,
    max_inflight: int | None = None,
) -> TradeContentionLease:
    key = build_trade_contention_gate_key(offer_public_id=offer_public_id, offer_id=offer_id)
    token = uuid.uuid4().hex
    client: Redis | None = None
    owns_client = False
    try:
        client, owns_client = _get_gate_client()
        acquired = bool(
            await client.eval(
                _ACQUIRE_SEMAPHORE_SLOT_SCRIPT,
                1,
                key,
                token,
                _gate_ttl_ms(ttl_seconds),
                _gate_max_inflight(max_inflight),
            )
        )
        lease = TradeContentionLease(
            key=key,
            token=token if acquired else None,
            acquired=acquired,
            _client=client if acquired else None,
            _owns_client=owns_client if acquired else False,
        )
        if not acquired and owns_client:
            await client.aclose()
        return lease
    except Exception as exc:
        if owns_client and client is not None:
            await client.aclose()
        logger.warning("Trade contention gate unavailable; allowing request: %s", type(exc).__name__)
        return TradeContentionLease(key=key, token=None, acquired=True)
