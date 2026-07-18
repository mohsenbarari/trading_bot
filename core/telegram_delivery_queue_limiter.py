"""Redis-backed admission control for the shared Telegram delivery queue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
from typing import Any, Protocol

from core.services.telegram_delivery_queue_service import SUPPORTED_TELEGRAM_BOT_IDENTITIES
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
)


TELEGRAM_DELIVERY_LIMITER_PREFIX = "telegram:delivery:v1"

_ADMIT_SCRIPT = """
if redis.call('exists', KEYS[5]) == 1 then
    return {-1, 0, 3}
end
if redis.call('exists', KEYS[3]) == 1 then
    return {-1, 0, 1}
end
if redis.call('exists', KEYS[4]) == 1 then
    return {-1, 0, 2}
end
local now_ms = tonumber(ARGV[1])
if redis.call('exists', KEYS[7]) == 1 then
    local probe_ttl = tonumber(redis.call('pttl', KEYS[7]) or '0')
    if probe_ttl > 0 then
        return {0, now_ms + probe_ttl, 4}
    end
    redis.call('del', KEYS[7])
end
local bot_next = tonumber(redis.call('get', KEYS[1]) or '0')
local destination_next = tonumber(redis.call('get', KEYS[2]) or '0')
local not_before = math.max(bot_next, destination_next)
if not_before > now_ms then
    local reason = 1
    if destination_next >= bot_next then
        reason = 2
    end
    return {0, not_before, reason}
end
local ttl_ms = tonumber(ARGV[4])
redis.call('set', KEYS[1], now_ms + tonumber(ARGV[2]), 'PX', ttl_ms)
redis.call('set', KEYS[2], now_ms + tonumber(ARGV[3]), 'PX', ttl_ms)
if redis.call('exists', KEYS[6]) == 1 then
    redis.call('set', KEYS[7], ARGV[5], 'PX', tonumber(ARGV[6]))
    return {2, now_ms, 0}
end
return {1, now_ms, 0}
"""

_RECORD_429_SCRIPT = """
local function set_max(key, candidate, ttl_ms)
    local current = tonumber(redis.call('get', key) or '0')
    if candidate > current then
        redis.call('set', key, candidate, 'PX', ttl_ms)
        return candidate
    end
    return current
end

local destination_hash = ARGV[1]
local retry_until_ms = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local window_ms = tonumber(ARGV[4])
local probe_delay_ms = tonumber(ARGV[5])
local durable_bot_until_ms = tonumber(ARGV[6])
local job_hash = ARGV[7]
local ttl_ms = tonumber(ARGV[8])
if redis.call('get', KEYS[5]) == job_hash then
    redis.call('del', KEYS[5])
end
set_max(KEYS[1], retry_until_ms, ttl_ms)
if durable_bot_until_ms > 0 then
    set_max(KEYS[2], durable_bot_until_ms, ttl_ms)
end

local entries = redis.call('hgetall', KEYS[3])
local distinct = 0
local maximum_retry = retry_until_ms
for index = 1, #entries, 2 do
    local destination = entries[index]
    local separator = string.find(entries[index + 1], ':')
    local seen_at = tonumber(string.sub(entries[index + 1], 1, separator - 1))
    local stored_retry = tonumber(string.sub(entries[index + 1], separator + 1))
    if seen_at <= now_ms and now_ms - seen_at <= window_ms then
        distinct = distinct + 1
        if stored_retry > maximum_retry then
            maximum_retry = stored_retry
        end
    else
        redis.call('hdel', KEYS[3], destination)
    end
end

local existed = redis.call('hexists', KEYS[3], destination_hash)
redis.call('hset', KEYS[3], destination_hash, tostring(now_ms) .. ':' .. tostring(retry_until_ms))
redis.call('pexpire', KEYS[3], ttl_ms)
if existed == 0 then
    distinct = distinct + 1
end
if distinct >= 2 or durable_bot_until_ms > now_ms then
    redis.call('del', KEYS[4])
    local blocked_until = set_max(KEYS[2], maximum_retry, ttl_ms)
    return {2, blocked_until}
end
local probe_at = set_max(KEYS[2], now_ms + probe_delay_ms, ttl_ms)
redis.call('set', KEYS[4], 'required', 'PX', ttl_ms)
return {1, probe_at}
"""

_CLEAR_PROBE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('del', KEYS[1])
    redis.call('del', KEYS[2])
    return 1
end
return 0
"""

_RELEASE_PROBE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('del', KEYS[1])
    redis.call('set', KEYS[2], 'required', 'PX', tonumber(ARGV[2]))
    return 1
end
return 0
"""

_PREPARE_PREFLIGHT_SCRIPT = """
if redis.call('exists', KEYS[1]) == 1 then
    return 0
end
local bot_next = tonumber(redis.call('get', KEYS[3]) or '0')
if bot_next > tonumber(ARGV[1]) then
    return 0
end
redis.call('del', KEYS[2])
return 1
"""

_PREFLIGHT_GATE_SCRIPT = """
if redis.call('exists', KEYS[1]) == 1 or redis.call('exists', KEYS[2]) == 1 then
    return 0
end
return 1
"""

_SET_MAX_SCRIPT = """
local current = tonumber(redis.call('get', KEYS[1]) or '0')
local candidate = tonumber(ARGV[1])
if candidate > current then
    redis.call('set', KEYS[1], candidate, 'PX', tonumber(ARGV[2]))
    return candidate
end
return current
"""


class TelegramDeliveryLimiterUnavailableError(RuntimeError):
    """Fails queue dispatch closed when durable admission state is unavailable."""


class TelegramDeliveryLimiterConfigurationError(ValueError):
    """Raised before worker creation when limiter settings are unsafe."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryDispatchAdmission:
    allowed: bool
    retry_after_seconds: float | None = None
    wait_reason: str | None = None
    not_before: datetime | None = None
    is_rate_limit_probe: bool = False


class TelegramDeliveryDispatchLimiter(Protocol):
    async def acquire(
        self,
        job: Any,
        *,
        now: datetime,
    ) -> TelegramDeliveryDispatchAdmission: ...

    async def observe(
        self,
        job: Any,
        decision: TelegramDeliveryDecision,
        *,
        now: datetime,
    ) -> None: ...

    async def extend_destination_cooldown(
        self,
        job: Any,
        *,
        until: datetime,
    ) -> None: ...

    async def extend_bot_cooldown(
        self,
        bot_identity: str,
        *,
        until: datetime,
    ) -> None: ...

    async def prepare_preflight(self, bot_identity: str) -> bool: ...

    async def preflight_gate_open(self, bot_identity: str) -> bool: ...

    async def clear_destination_gate_after_database_resume(
        self,
        destination_key: str,
    ) -> None: ...


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelegramDeliveryLimiterUnavailableError(
            "telegram_limiter_timestamp_must_be_timezone_aware"
        )
    return int(value.timestamp() * 1000)


def _destination_digest(destination_key: str) -> str:
    return hashlib.sha256(str(destination_key).encode("utf-8")).hexdigest()[:32]


def _job_digest(job: Any) -> str:
    identity = (
        getattr(job, "id", None)
        or getattr(job, "job_id", None)
        or getattr(job, "dedupe_key", None)
    )
    if identity is None or not str(identity).strip():
        raise TelegramDeliveryLimiterUnavailableError(
            "telegram_limiter_job_identity_missing"
        )
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class RedisTelegramDeliveryLimiter:
    redis: Any = field(repr=False)
    bot_min_interval_seconds: float
    destination_min_interval_seconds: float
    rate_limit_probe_delay_seconds: float
    global_rate_limit_window_seconds: float
    rate_limit_probe_lease_seconds: float
    key_ttl_seconds: int = 86400
    namespace: str = TELEGRAM_DELIVERY_LIMITER_PREFIX
    _local_block_reason: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.redis is None:
            raise TelegramDeliveryLimiterConfigurationError(
                "telegram_limiter_redis_client_missing"
            )
        if not str(self.namespace or "").strip().rstrip(":"):
            raise TelegramDeliveryLimiterConfigurationError(
                "telegram_limiter_namespace_missing"
            )
        for name, value in (
            ("bot_min_interval_seconds", self.bot_min_interval_seconds),
            ("destination_min_interval_seconds", self.destination_min_interval_seconds),
            ("rate_limit_probe_delay_seconds", self.rate_limit_probe_delay_seconds),
            ("global_rate_limit_window_seconds", self.global_rate_limit_window_seconds),
            ("rate_limit_probe_lease_seconds", self.rate_limit_probe_lease_seconds),
        ):
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= 0:
                raise TelegramDeliveryLimiterConfigurationError(
                    f"telegram_limiter_invalid_setting:{name}"
                )
        if int(self.key_ttl_seconds) < 1:
            raise TelegramDeliveryLimiterConfigurationError(
                "telegram_limiter_invalid_setting:key_ttl_seconds"
            )

    def _validate_job_identity(self, job: Any) -> tuple[str, str, str]:
        bot_identity = str(getattr(job, "bot_identity", "") or "").strip()
        destination_key = str(getattr(job, "destination_key", "") or "").strip()
        if bot_identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_bot_identity_not_allowlisted"
            )
        if not destination_key:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_destination_missing"
            )
        return bot_identity, destination_key, _destination_digest(destination_key)

    def _keys(self, bot_identity: str, destination_digest: str) -> dict[str, str]:
        prefix = str(self.namespace).rstrip(":")
        return {
            "bot_next": f"{prefix}:bot:{bot_identity}:next",
            "destination_next": f"{prefix}:destination:{destination_digest}:next",
            "bot_block": f"{prefix}:bot:{bot_identity}:blocked",
            "destination_block": f"{prefix}:destination:{destination_digest}:blocked",
            "gateway_block": f"{prefix}:gateway:blocked",
            "recent_429": f"{prefix}:bot:{bot_identity}:recent-429",
            "probe_required": f"{prefix}:bot:{bot_identity}:probe-required",
            "probe_inflight": f"{prefix}:bot:{bot_identity}:probe-inflight",
        }

    def _ttl_ms(self) -> int:
        longest_control_seconds = max(
            float(self.bot_min_interval_seconds),
            float(self.destination_min_interval_seconds),
            float(self.rate_limit_probe_delay_seconds),
            float(self.global_rate_limit_window_seconds),
            float(self.rate_limit_probe_lease_seconds),
        )
        return max(
            1000,
            int(self.key_ttl_seconds) * 1000,
            int(longest_control_seconds * 1000) + 60_000,
        )

    def _ttl_ms_for_until(self, *, now: datetime, until: datetime) -> int:
        # Redis expiry is storage cleanup only; it must never shorten the
        # provider-declared retry_after. Keep one minute beyond the deadline
        # to absorb clock/worker skew and delayed observation.
        required_ms = max(0, _epoch_ms(until) - _epoch_ms(now)) + 60_000
        return max(self._ttl_ms(), required_ms)

    def _fail_closed(self, reason: str, exc: Exception | None = None) -> None:
        self._local_block_reason = reason
        error = TelegramDeliveryLimiterUnavailableError(reason)
        if exc is None:
            raise error
        raise error from exc

    async def acquire(
        self,
        job: Any,
        *,
        now: datetime,
    ) -> TelegramDeliveryDispatchAdmission:
        if self._local_block_reason is not None:
            raise TelegramDeliveryLimiterUnavailableError(self._local_block_reason)
        bot_identity, _destination_key, destination_digest = self._validate_job_identity(job)
        job_digest = _job_digest(job)
        keys = self._keys(bot_identity, destination_digest)
        now_ms = _epoch_ms(now)
        try:
            raw = await self.redis.eval(
                _ADMIT_SCRIPT,
                7,
                keys["bot_next"],
                keys["destination_next"],
                keys["bot_block"],
                keys["destination_block"],
                keys["gateway_block"],
                keys["probe_required"],
                keys["probe_inflight"],
                now_ms,
                max(1, int(float(self.bot_min_interval_seconds) * 1000)),
                max(1, int(float(self.destination_min_interval_seconds) * 1000)),
                self._ttl_ms(),
                job_digest,
                max(1, int(float(self.rate_limit_probe_lease_seconds) * 1000)),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_redis_unavailable:{type(exc).__name__}",
                exc,
            )
        try:
            values = list(raw or ())
            if len(values) != 3:
                raise ValueError("unexpected response length")
            state, not_before_ms, reason_code = (int(value) for value in values)
            if state not in {-1, 0, 1, 2}:
                raise ValueError("unexpected admission state")
            if state in {1, 2} and reason_code != 0:
                raise ValueError("unexpected allowed reason")
            if state == 0 and (
                reason_code not in {1, 2, 4} or not_before_ms <= now_ms
            ):
                raise ValueError("unexpected wait response")
            if state == -1 and reason_code not in {1, 2, 3}:
                raise ValueError("unexpected blocked reason")
        except (TypeError, ValueError, OverflowError) as exc:
            self._fail_closed("telegram_limiter_invalid_redis_response", exc)
        if state in {1, 2}:
            return TelegramDeliveryDispatchAdmission(
                allowed=True,
                is_rate_limit_probe=state == 2,
            )
        reason = {
            1: "bot_lane",
            2: "destination_gate",
            3: "circuit",
            4: "bot_probe_inflight",
        }[reason_code]
        if state == -1:
            return TelegramDeliveryDispatchAdmission(
                allowed=False,
                retry_after_seconds=30.0,
                wait_reason=reason,
            )
        retry_seconds = max(0.001, (not_before_ms - now_ms) / 1000.0)
        return TelegramDeliveryDispatchAdmission(
            allowed=False,
            retry_after_seconds=retry_seconds,
            wait_reason=reason,
            not_before=datetime.fromtimestamp(not_before_ms / 1000.0, tz=now.tzinfo),
        )

    async def observe(
        self,
        job: Any,
        decision: TelegramDeliveryDecision,
        *,
        now: datetime,
    ) -> None:
        bot_identity, _destination_key, destination_digest = self._validate_job_identity(job)
        job_digest = _job_digest(job)
        keys = self._keys(bot_identity, destination_digest)
        try:
            if decision.reason == "rate_limit_probe_cancelled_before_dispatch":
                await self.redis.eval(
                    _RELEASE_PROBE_SCRIPT,
                    2,
                    keys["probe_inflight"],
                    keys["probe_required"],
                    job_digest,
                    self._ttl_ms(),
                )
            elif (
                decision.reason == "telegram_rate_limited"
                and decision.destination_cooldown_until is not None
            ):
                ttl_until = decision.destination_cooldown_until
                if (
                    decision.bot_cooldown_until is not None
                    and decision.bot_cooldown_until > ttl_until
                ):
                    ttl_until = decision.bot_cooldown_until
                await self.redis.eval(
                    _RECORD_429_SCRIPT,
                    5,
                    keys["destination_next"],
                    keys["bot_next"],
                    keys["recent_429"],
                    keys["probe_required"],
                    keys["probe_inflight"],
                    destination_digest,
                    _epoch_ms(decision.destination_cooldown_until),
                    _epoch_ms(now),
                    max(1, int(float(self.global_rate_limit_window_seconds) * 1000)),
                    max(1, int(float(self.rate_limit_probe_delay_seconds) * 1000)),
                    (
                        _epoch_ms(decision.bot_cooldown_until)
                        if decision.bot_cooldown_until is not None
                        else 0
                    ),
                    job_digest,
                    self._ttl_ms_for_until(
                        now=now,
                        until=ttl_until,
                    ),
                )
            elif decision.outcome == TelegramDeliveryOutcome.BOT_PAUSED:
                await self.redis.set(keys["bot_block"], decision.reason or "blocked")
            elif decision.outcome == TelegramDeliveryOutcome.DESTINATION_PAUSED:
                await self.redis.set(
                    keys["destination_block"],
                    decision.reason or "blocked",
                )
            elif decision.outcome == TelegramDeliveryOutcome.GATEWAY_PAUSED:
                await self.redis.set(keys["gateway_block"], decision.reason or "blocked")
            if decision.reason not in {
                "telegram_rate_limited",
                "rate_limit_probe_cancelled_before_dispatch",
            }:
                await self.redis.eval(
                    _CLEAR_PROBE_SCRIPT,
                    2,
                    keys["probe_inflight"],
                    keys["probe_required"],
                    job_digest,
                )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_observe_failed:{type(exc).__name__}",
                exc,
            )

    async def extend_destination_cooldown(
        self,
        job: Any,
        *,
        until: datetime,
    ) -> None:
        bot_identity, _destination_key, destination_digest = self._validate_job_identity(job)
        keys = self._keys(bot_identity, destination_digest)
        try:
            await self.redis.eval(
                _SET_MAX_SCRIPT,
                1,
                keys["destination_next"],
                _epoch_ms(until),
                self._ttl_ms_for_until(now=datetime.now(tz=until.tzinfo), until=until),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_redis_unavailable:{type(exc).__name__}",
                exc,
            )

    async def extend_bot_cooldown(
        self,
        bot_identity: str,
        *,
        until: datetime,
    ) -> None:
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_bot_identity_not_allowlisted"
            )
        keys = self._keys(identity, "unused")
        try:
            await self.redis.eval(
                _SET_MAX_SCRIPT,
                1,
                keys["bot_next"],
                _epoch_ms(until),
                self._ttl_ms_for_until(
                    now=datetime.now(tz=until.tzinfo),
                    until=until,
                ),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_redis_unavailable:{type(exc).__name__}",
                exc,
            )

    async def prepare_preflight(self, bot_identity: str) -> bool:
        """Retire an elapsed probe requirement only when no probe owns it.

        The caller must first prove from durable PostgreSQL evidence that no
        cooldown, hard pause, or dispatched probe remains active. This atomic
        Redis check closes the crash window where an admitted probe has not yet
        persisted its dispatch marker.
        """
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_bot_identity_not_allowlisted"
            )
        keys = self._keys(identity, "unused")
        try:
            raw = await self.redis.eval(
                _PREPARE_PREFLIGHT_SCRIPT,
                3,
                keys["probe_inflight"],
                keys["probe_required"],
                keys["bot_next"],
                _epoch_ms(datetime.now(timezone.utc)),
            )
            ready = int(raw)
            if ready not in {0, 1}:
                raise ValueError("unexpected preflight response")
            return bool(ready)
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_preflight_prepare_failed:{type(exc).__name__}",
                exc,
            )

    async def preflight_gate_open(self, bot_identity: str) -> bool:
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_bot_identity_not_allowlisted"
            )
        keys = self._keys(identity, "unused")
        try:
            raw = await self.redis.eval(
                _PREFLIGHT_GATE_SCRIPT,
                2,
                keys["probe_inflight"],
                keys["probe_required"],
            )
            ready = int(raw)
            if ready not in {0, 1}:
                raise ValueError("unexpected preflight gate response")
            return bool(ready)
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_preflight_gate_failed:{type(exc).__name__}",
                exc,
            )

    async def _resume_delete(self, *keys: str) -> None:
        try:
            await self.redis.delete(*keys)
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_resume_failed:{type(exc).__name__}",
                exc,
            )
        self._local_block_reason = None

    async def resume_bot(self, bot_identity: str) -> None:
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_bot_identity_not_allowlisted"
            )
        keys = self._keys(identity, "unused")
        await self._resume_delete(
            keys["bot_block"],
            keys["bot_next"],
            keys["recent_429"],
            keys["probe_required"],
            keys["probe_inflight"],
        )

    async def clear_destination_gate_after_database_resume(
        self,
        destination_key: str,
    ) -> None:
        destination = str(destination_key or "").strip()
        if not destination:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_destination_missing"
            )
        digest = _destination_digest(destination)
        keys = self._keys("primary", digest)
        await self._resume_delete(
            keys["destination_block"],
            keys["destination_next"],
        )

    async def resume_destination(self, destination_key: str) -> None:
        """Compatibility primitive; this alone never authorizes activation."""
        await self.clear_destination_gate_after_database_resume(destination_key)

    async def resume_gateway(self) -> None:
        keys = self._keys("primary", "unused")
        await self._resume_delete(keys["gateway_block"])


def configured_redis_telegram_delivery_limiter(
    redis_client: Any,
    *,
    settings: Any,
) -> RedisTelegramDeliveryLimiter:
    """Build the one shared limiter instance used by all enabled bot lanes."""
    return RedisTelegramDeliveryLimiter(
        redis=redis_client,
        bot_min_interval_seconds=float(
            getattr(settings, "telegram_delivery_queue_bot_min_interval_seconds", 0.035)
        ),
        destination_min_interval_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_destination_min_interval_seconds",
                1.05,
            )
        ),
        rate_limit_probe_delay_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_rate_limit_probe_delay_seconds",
                0.1,
            )
        ),
        global_rate_limit_window_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_global_rate_limit_window_seconds",
                2.0,
            )
        ),
        rate_limit_probe_lease_seconds=max(
            1.0,
            float(
                getattr(
                    settings,
                    "telegram_delivery_queue_worker_lease_seconds",
                    30.0,
                )
            ),
            float(
                getattr(
                    settings,
                    "telegram_delivery_queue_worker_request_timeout_seconds",
                    10.0,
                )
            )
            + 5.0,
        ),
        key_ttl_seconds=int(
            getattr(settings, "telegram_delivery_queue_limiter_key_ttl_seconds", 86400)
        ),
    )
