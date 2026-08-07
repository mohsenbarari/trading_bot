"""Redis-backed admission control for the shared Telegram delivery queue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
if redis.call('exists', KEYS[5]) == 1 then
    return {-1, 0, 3, now_ms}
end
if redis.call('exists', KEYS[3]) == 1 then
    return {-1, 0, 1, now_ms}
end
if redis.call('exists', KEYS[4]) == 1 then
    return {-1, 0, 2, now_ms}
end
if redis.call('exists', KEYS[7]) == 1 then
    local probe_ttl = tonumber(redis.call('pttl', KEYS[7]) or '0')
    if probe_ttl > 0 then
        return {0, now_ms + probe_ttl, 4, now_ms}
    end
    redis.call('del', KEYS[7])
end
if redis.call('exists', KEYS[8]) == 1 then
    local inflight_ttl = tonumber(redis.call('pttl', KEYS[8]) or '0')
    if inflight_ttl > 0 then
        return {0, now_ms + inflight_ttl, 5, now_ms}
    end
    redis.call('del', KEYS[8])
end
local bot_next = tonumber(redis.call('get', KEYS[1]) or '0')
local destination_next = tonumber(redis.call('get', KEYS[2]) or '0')
local not_before = math.max(bot_next, destination_next)
local burst_capacity = tonumber(ARGV[6])
local burst_idle_ms = tonumber(ARGV[7])
local burst_disabled_until = tonumber(redis.call('get', KEYS[10]) or '0')
if burst_disabled_until <= now_ms and burst_disabled_until > 0 then
    redis.call('del', KEYS[10])
    burst_disabled_until = 0
end
local last_accept_ms = tonumber(redis.call('hget', KEYS[9], 'last_accept_ms') or '0')
local burst_remaining = tonumber(redis.call('hget', KEYS[9], 'remaining') or '0')
if burst_capacity > 1 and burst_disabled_until == 0 then
    if last_accept_ms == 0 or now_ms - last_accept_ms >= burst_idle_ms then
        burst_remaining = burst_capacity
    end
else
    burst_remaining = 0
end
local use_burst = burst_remaining > 0
if not use_burst and not_before > now_ms then
    local reason = 1
    if destination_next >= bot_next then
        reason = 2
    end
    return {0, not_before, reason, now_ms}
end
local ttl_ms = tonumber(ARGV[3])
redis.call('set', KEYS[1], now_ms + tonumber(ARGV[1]), 'PX', ttl_ms)
redis.call('set', KEYS[2], now_ms + tonumber(ARGV[2]), 'PX', ttl_ms)
if use_burst then
    burst_remaining = burst_remaining - 1
end
redis.call(
    'hset',
    KEYS[9],
    'last_accept_ms',
    now_ms,
    'remaining',
    math.max(0, burst_remaining)
)
redis.call('pexpire', KEYS[9], ttl_ms)
redis.call('set', KEYS[8], ARGV[4], 'PX', tonumber(ARGV[5]))
if redis.call('exists', KEYS[6]) == 1 then
    redis.call('set', KEYS[7], ARGV[4], 'PX', tonumber(ARGV[5]))
    return {2, now_ms, 0, now_ms}
end
return {1, now_ms, 0, now_ms}
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

local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local destination_hash = ARGV[1]
local retry_until_ms = now_ms + tonumber(ARGV[2])
local window_ms = tonumber(ARGV[3])
local probe_delay_ms = tonumber(ARGV[4])
local durable_bot_delay_ms = tonumber(ARGV[5])
local durable_bot_until_ms = 0
if durable_bot_delay_ms > 0 then
    durable_bot_until_ms = now_ms + durable_bot_delay_ms
end
local job_hash = ARGV[6]
local ttl_ms = tonumber(ARGV[7])
if redis.call('get', KEYS[5]) == job_hash then
    redis.call('del', KEYS[5])
end
if redis.call('get', KEYS[6]) == job_hash then
    redis.call('del', KEYS[6])
end
redis.call('hset', KEYS[7], 'last_accept_ms', now_ms, 'remaining', 0)
redis.call('pexpire', KEYS[7], ttl_ms)
local burst_disabled_until = math.max(
    retry_until_ms,
    now_ms + tonumber(ARGV[8])
)
set_max(KEYS[8], burst_disabled_until, ttl_ms)
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

_CLEAR_ADMISSION_SCRIPT = """
local cleared = 0
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('del', KEYS[1])
    cleared = 1
end
if redis.call('get', KEYS[2]) == ARGV[1] then
    redis.call('del', KEYS[2])
    redis.call('del', KEYS[3])
    cleared = 1
end
return cleared
"""

_RELEASE_ADMISSION_SCRIPT = """
local cleared = 0
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('del', KEYS[1])
    cleared = 1
end
if redis.call('get', KEYS[2]) == ARGV[1] then
    redis.call('del', KEYS[2])
    if tonumber(ARGV[2]) == 1 then
        redis.call('set', KEYS[3], 'required', 'PX', tonumber(ARGV[3]))
    end
    cleared = 1
end
return cleared
"""

_PREPARE_PREFLIGHT_SCRIPT = """
if redis.call('exists', KEYS[1]) == 1 then
    return 0
end
local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local bot_next = tonumber(redis.call('get', KEYS[3]) or '0')
if bot_next > now_ms then
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
local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local current = tonumber(redis.call('get', KEYS[1]) or '0')
local candidate = tonumber(ARGV[1])
if candidate > current then
    local ttl_ms = math.max(tonumber(ARGV[2]), candidate - now_ms + 60000)
    redis.call('set', KEYS[1], candidate, 'PX', ttl_ms)
    return candidate
end
return current
"""

_SET_DESTINATION_COOLDOWN_SCRIPT = """
local function set_max(key, candidate, ttl_ms, now_ms)
    local current = tonumber(redis.call('get', key) or '0')
    if candidate > current then
        local effective_ttl = math.max(ttl_ms, candidate - now_ms + 60000)
        redis.call('set', key, candidate, 'PX', effective_ttl)
        return candidate
    end
    return current
end
local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local ttl_ms = tonumber(ARGV[2])
local cooldown_until = tonumber(ARGV[1])
local burst_disabled_until = math.max(
    cooldown_until,
    now_ms + tonumber(ARGV[3])
)
set_max(KEYS[1], cooldown_until, ttl_ms, now_ms)
set_max(KEYS[2], burst_disabled_until, ttl_ms, now_ms)
redis.call('hset', KEYS[3], 'last_accept_ms', now_ms, 'remaining', 0)
redis.call('pexpire', KEYS[3], ttl_ms)
return {cooldown_until, burst_disabled_until}
"""

_RECORD_SLOW_RESPONSE_SCRIPT = """
local function set_max(key, candidate, ttl_ms, now_ms)
    local current = tonumber(redis.call('get', key) or '0')
    if candidate > current then
        local effective_ttl = math.max(ttl_ms, candidate - now_ms + 60000)
        redis.call('set', key, candidate, 'PX', effective_ttl)
        return candidate
    end
    return current
end
local server_time = redis.call('TIME')
local now_ms = (tonumber(server_time[1]) * 1000) + math.floor(tonumber(server_time[2]) / 1000)
local slowdown_until = now_ms + tonumber(ARGV[1])
local burst_disabled_until = now_ms + tonumber(ARGV[2])
local ttl_ms = tonumber(ARGV[3])
set_max(KEYS[1], slowdown_until, ttl_ms, now_ms)
set_max(KEYS[2], burst_disabled_until, ttl_ms, now_ms)
redis.call('hset', KEYS[3], 'last_accept_ms', now_ms, 'remaining', 0)
redis.call('pexpire', KEYS[3], ttl_ms)
return {slowdown_until, burst_disabled_until}
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

    async def observe_provider_latency(
        self,
        job: Any,
        *,
        provider_latency_seconds: float,
        now: datetime,
    ) -> bool: ...

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
    destination_burst_capacity: int = 1
    destination_burst_idle_seconds: float = 3.2
    destination_burst_recovery_seconds: float = 300.0
    edit_slow_response_seconds: float = 2.0
    edit_slowdown_seconds: float = 1.6
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
            ("destination_burst_idle_seconds", self.destination_burst_idle_seconds),
            (
                "destination_burst_recovery_seconds",
                self.destination_burst_recovery_seconds,
            ),
            ("edit_slow_response_seconds", self.edit_slow_response_seconds),
            ("edit_slowdown_seconds", self.edit_slowdown_seconds),
        ):
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= 0:
                raise TelegramDeliveryLimiterConfigurationError(
                    f"telegram_limiter_invalid_setting:{name}"
                )
        if (
            isinstance(self.destination_burst_capacity, bool)
            or int(self.destination_burst_capacity) < 1
        ):
            raise TelegramDeliveryLimiterConfigurationError(
                "telegram_limiter_invalid_setting:destination_burst_capacity"
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
            # Hard permission/capability pauses are credential-specific.  The
            # shared destination cadence key above remains cross-bot so adding
            # the editor never multiplies the channel's accepted send rate.
            "destination_block": (
                f"{prefix}:bot:{bot_identity}:destination:"
                f"{destination_digest}:blocked"
            ),
            "gateway_block": f"{prefix}:gateway:blocked",
            "recent_429": f"{prefix}:bot:{bot_identity}:recent-429",
            "probe_required": f"{prefix}:bot:{bot_identity}:probe-required",
            "probe_inflight": f"{prefix}:bot:{bot_identity}:probe-inflight",
            # All bot identities share these destination controls. A second bot
            # must never multiply the channel burst or create parallel sends.
            "destination_inflight": (
                f"{prefix}:destination:{destination_digest}:inflight"
            ),
            "destination_burst_state": (
                f"{prefix}:destination:{destination_digest}:burst-state"
            ),
            "destination_burst_disabled_until": (
                f"{prefix}:destination:{destination_digest}:burst-disabled-until"
            ),
        }

    def _ttl_ms(self) -> int:
        longest_control_seconds = max(
            float(self.bot_min_interval_seconds),
            float(self.destination_min_interval_seconds),
            float(self.rate_limit_probe_delay_seconds),
            float(self.global_rate_limit_window_seconds),
            float(self.rate_limit_probe_lease_seconds),
            float(self.destination_burst_idle_seconds),
            float(self.destination_burst_recovery_seconds),
        )
        return max(
            1000,
            int(self.key_ttl_seconds) * 1000,
            int(longest_control_seconds * 1000) + 60_000,
        )

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
        bot_identity, destination_key, destination_digest = self._validate_job_identity(job)
        destination_class = str(
            getattr(
                getattr(job, "destination_class", None),
                "value",
                getattr(job, "destination_class", ""),
            )
            or ""
        ).strip().lower()
        job_digest = _job_digest(job)
        keys = self._keys(bot_identity, destination_digest)
        # Validate the caller-provided timestamp for API consistency, but never
        # use it for admission. Every atomic comparison is based on Redis TIME
        # so skewed workers cannot shorten or bypass pacing/cooldown gates.
        _epoch_ms(now)
        try:
            raw = await self.redis.eval(
                _ADMIT_SCRIPT,
                10,
                keys["bot_next"],
                keys["destination_next"],
                keys["bot_block"],
                keys["destination_block"],
                keys["gateway_block"],
                keys["probe_required"],
                keys["probe_inflight"],
                keys["destination_inflight"],
                keys["destination_burst_state"],
                keys["destination_burst_disabled_until"],
                max(1, int(float(self.bot_min_interval_seconds) * 1000)),
                max(1, int(float(self.destination_min_interval_seconds) * 1000)),
                self._ttl_ms(),
                job_digest,
                max(1, int(float(self.rate_limit_probe_lease_seconds) * 1000)),
                (
                    max(1, int(self.destination_burst_capacity))
                    if destination_class == "channel"
                    else 1
                ),
                max(1, int(float(self.destination_burst_idle_seconds) * 1000)),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_redis_unavailable:{type(exc).__name__}",
                exc,
            )
        try:
            values = list(raw or ())
            if len(values) != 4:
                raise ValueError("unexpected response length")
            state, not_before_ms, reason_code, redis_now_ms = (
                int(value) for value in values
            )
            if state not in {-1, 0, 1, 2}:
                raise ValueError("unexpected admission state")
            if state in {1, 2} and reason_code != 0:
                raise ValueError("unexpected allowed reason")
            if state == 0 and (
                reason_code not in {1, 2, 4, 5} or not_before_ms <= redis_now_ms
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
            5: "destination_inflight",
        }[reason_code]
        if state == -1:
            return TelegramDeliveryDispatchAdmission(
                allowed=False,
                retry_after_seconds=30.0,
                wait_reason=reason,
            )
        retry_seconds = max(0.001, (not_before_ms - redis_now_ms) / 1000.0)
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
            if decision.reason in {
                "rate_limit_probe_cancelled_before_dispatch",
                "dispatch_admission_cancelled_before_dispatch",
            }:
                await self.redis.eval(
                    _RELEASE_ADMISSION_SCRIPT,
                    3,
                    keys["destination_inflight"],
                    keys["probe_inflight"],
                    keys["probe_required"],
                    job_digest,
                    (
                        1
                        if decision.reason
                        == "rate_limit_probe_cancelled_before_dispatch"
                        else 0
                    ),
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
                    8,
                    keys["destination_next"],
                    keys["bot_next"],
                    keys["recent_429"],
                    keys["probe_required"],
                    keys["probe_inflight"],
                    keys["destination_inflight"],
                    keys["destination_burst_state"],
                    keys["destination_burst_disabled_until"],
                    destination_digest,
                    max(
                        1,
                        _epoch_ms(decision.destination_cooldown_until)
                        - _epoch_ms(now),
                    ),
                    max(1, int(float(self.global_rate_limit_window_seconds) * 1000)),
                    max(1, int(float(self.rate_limit_probe_delay_seconds) * 1000)),
                    (
                        max(1, _epoch_ms(decision.bot_cooldown_until) - _epoch_ms(now))
                        if decision.bot_cooldown_until is not None
                        else 0
                    ),
                    job_digest,
                    max(
                        self._ttl_ms(),
                        _epoch_ms(ttl_until)
                        - _epoch_ms(now)
                        + int(
                            float(self.destination_burst_recovery_seconds)
                            * 1000
                        )
                        + 60_000,
                    ),
                    max(
                        1,
                        int(
                            float(self.destination_burst_recovery_seconds)
                            * 1000
                        ),
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
                "dispatch_admission_cancelled_before_dispatch",
            }:
                await self.redis.eval(
                    _CLEAR_ADMISSION_SCRIPT,
                    3,
                    keys["destination_inflight"],
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
                _SET_DESTINATION_COOLDOWN_SCRIPT,
                3,
                keys["destination_next"],
                keys["destination_burst_disabled_until"],
                keys["destination_burst_state"],
                _epoch_ms(until),
                self._ttl_ms(),
                max(
                    1,
                    int(
                        float(self.destination_burst_recovery_seconds) * 1000
                    ),
                ),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_redis_unavailable:{type(exc).__name__}",
                exc,
            )

    async def observe_provider_latency(
        self,
        job: Any,
        *,
        provider_latency_seconds: float,
        now: datetime,
    ) -> bool:
        """Apply a soft edit brake when Telegram holds a channel request.

        Telegram may delay channel edits for several seconds without returning
        429. This signal suppresses the next micro-burst and extends only the
        affected destination gate; ordinary 0.9-second pacing resumes after the
        short slowdown.
        """

        _epoch_ms(now)
        latency = float(provider_latency_seconds)
        if not math.isfinite(latency) or latency < 0:
            raise TelegramDeliveryLimiterUnavailableError(
                "telegram_limiter_invalid_provider_latency"
            )
        method = str(getattr(job, "method", "") or "")
        destination_class = str(
            getattr(
                getattr(job, "destination_class", None),
                "value",
                getattr(job, "destination_class", ""),
            )
            or ""
        ).strip().lower()
        if (
            method not in {"editMessageText", "editMessageReplyMarkup"}
            or destination_class != "channel"
            or latency < float(self.edit_slow_response_seconds)
        ):
            return False
        bot_identity, _destination_key, destination_digest = (
            self._validate_job_identity(job)
        )
        keys = self._keys(bot_identity, destination_digest)
        try:
            await self.redis.eval(
                _RECORD_SLOW_RESPONSE_SCRIPT,
                3,
                keys["destination_next"],
                keys["destination_burst_disabled_until"],
                keys["destination_burst_state"],
                max(1, int(float(self.edit_slowdown_seconds) * 1000)),
                max(
                    1,
                    int(
                        float(self.destination_burst_recovery_seconds) * 1000
                    ),
                ),
                self._ttl_ms(),
            )
        except Exception as exc:
            self._fail_closed(
                f"telegram_limiter_latency_observation_failed:{type(exc).__name__}",
                exc,
            )
        return True

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
                self._ttl_ms(),
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
        primary_keys = self._keys("primary", digest)
        editor_keys = self._keys("channel_editor", digest)
        await self._resume_delete(
            primary_keys["destination_block"],
            editor_keys["destination_block"],
            primary_keys["destination_next"],
            primary_keys["destination_inflight"],
            primary_keys["destination_burst_state"],
            primary_keys["destination_burst_disabled_until"],
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
                0.9,
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
        destination_burst_capacity=int(
            getattr(
                settings,
                "telegram_delivery_queue_destination_burst_capacity",
                2,
            )
        ),
        destination_burst_idle_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_destination_burst_idle_seconds",
                3.2,
            )
        ),
        destination_burst_recovery_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_destination_burst_recovery_seconds",
                300.0,
            )
        ),
        edit_slow_response_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_edit_slow_response_seconds",
                2.0,
            )
        ),
        edit_slowdown_seconds=float(
            getattr(
                settings,
                "telegram_delivery_queue_edit_slowdown_seconds",
                1.6,
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
