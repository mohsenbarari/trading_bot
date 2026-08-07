"""In-process fallback pacing for legacy Telegram channel publication.

The staging calibration baseline is one operation every 0.8 seconds. A tiny
idle-earned burst is allowed, while HTTP 429 books the full ``retry_after`` as
debt. Exact channel limits are dynamic; the durable QUEUE_V1 Redis limiter is
the production cadence authority.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class TelegramChannelPace:
    rate_per_minute: float = 20.0
    capacity: float | None = None
    _tokens: float = 0.0
    _updated_at: float = 0.0
    _lock: threading.Lock = threading.Lock()

    def __post_init__(self) -> None:
        cap = float(self.capacity) if self.capacity is not None else float(self.rate_per_minute)
        self.capacity = max(1.0, cap)
        self.rate_per_minute = max(1.0, float(self.rate_per_minute))
        self._tokens = float(self.capacity)
        self._updated_at = time.monotonic()

    @property
    def refill_per_second(self) -> float:
        return float(self.rate_per_minute) / 60.0

    def _refill_unlocked(self, *, now: float) -> None:
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(
            float(self.capacity or self.rate_per_minute),
            self._tokens + elapsed * self.refill_per_second,
        )
        self._updated_at = now

    def wait_seconds_before_send(self) -> float:
        """Reserve one send and return how long to sleep before performing it.

        The token is always consumed (balance may go negative). Earlier code
        only decremented when a token was available, so waiting callers never
        paid for their send and the real rate crept to ~2x the configured one,
        which kept triggering Telegram 429 retry_after pauses.
        """
        with self._lock:
            now = time.monotonic()
            self._refill_unlocked(now=now)
            self._tokens -= 1.0
            if self._tokens >= 0.0:
                return 0.0
            return -self._tokens / self.refill_per_second

    def note_rate_limited(self, retry_after_seconds: float | None = None) -> None:
        """After a 429, drain the bucket (and book retry_after as token debt)."""
        with self._lock:
            now = time.monotonic()
            self._refill_unlocked(now=now)
            debt = 0.0
            if retry_after_seconds is not None and retry_after_seconds > 0:
                debt = float(retry_after_seconds) * self.refill_per_second
            self._tokens = min(self._tokens, 0.0) - debt

    def note_success(self) -> None:
        """No-op hook for callers that already consumed a token via wait_seconds."""
        return None


OFFER_CHANNEL_BASE_INTERVAL_SECONDS = 0.8
OFFER_CHANNEL_IDLE_BURST_CAPACITY = 2.0

# Legacy-only fallback. Two immediate operations after idle are deliberately a
# micro-burst; sustained work returns to the calibrated 0.8-second cadence.
OFFER_CHANNEL_PACE = TelegramChannelPace(
    rate_per_minute=60.0 / OFFER_CHANNEL_BASE_INTERVAL_SECONDS,
    capacity=OFFER_CHANNEL_IDLE_BURST_CAPACITY,
)
