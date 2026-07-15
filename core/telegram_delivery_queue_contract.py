"""Deterministic contracts for the shared Telegram delivery queue.

This module deliberately has no database, Redis, or Telegram side effects.  It
defines the ordering and state transitions that the persistent worker must use
in Stage 3, and provides a lock-protected reference queue for deterministic
failure-injection tests.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import Any, Protocol


class TelegramGatewayResultLike(Protocol):
    ok: bool
    method: str
    status_code: int | None
    response_text: str
    response_json: dict[str, Any] | None
    error: str | None

    @property
    def message_id(self) -> int | None:
        ...


class TelegramDeliveryPriority(IntEnum):
    INTERACTIVE = 0
    OFFER_CHANNEL = 1
    PRIVATE_TRANSACTIONAL = 2
    BULK = 3


class TelegramOfferChannelOrder(IntEnum):
    TERMINAL = 0
    PARTIAL = 1
    MARKET_CRITICAL = 2
    NEW_OFFER = 3


class TelegramDeliveryState(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    SENT = "sent"
    TERMINAL_FAILED = "terminal_failed"
    AMBIGUOUS = "ambiguous"


class TelegramDeliveryOutcome(str, Enum):
    SENT = "sent"
    RETRY_PENDING = "retry_pending"
    DESTINATION_PAUSED = "destination_paused"
    TERMINAL_FAILED = "terminal_failed"
    AMBIGUOUS = "ambiguous"
    ALREADY_RESOLVED = "already_resolved"
    STALE_LEASE = "stale_lease"


class TelegramDeliveryDedupeConflictError(ValueError):
    """Raised when one dedupe key is reused for a different logical job."""


TERMINAL_DELIVERY_STATES = {
    TelegramDeliveryState.SENT,
    TelegramDeliveryState.TERMINAL_FAILED,
    TelegramDeliveryState.AMBIGUOUS,
}

DEFAULT_MAX_RETRY_SECONDS = 300.0
DEFAULT_DESTINATION_PAUSE_SECONDS = 300.0
MAX_TELEGRAM_RETRY_AFTER_SECONDS = 24 * 60 * 60

_AMBIGUOUS_SEND_ERRORS = {
    "networkerror",
    "readerror",
    "readtimeout",
    "remoteprotocolerror",
    "timeouterror",
    "writeerror",
    "writetimeout",
}
_SAFE_RETRY_TRANSPORT_ERRORS = {
    "connecterror",
    "connecttimeout",
    "pooltimeout",
}
_MALFORMED_PAYLOAD_PATTERNS = (
    "can't parse entities",
    "message is too long",
    "message text is empty",
    "parse mode",
    "text must be non-empty",
)


@dataclass(slots=True)
class TelegramDeliveryJob:
    id: int
    dedupe_key: str
    destination_key: str
    method: str
    payload: dict[str, Any]
    priority: TelegramDeliveryPriority
    created_sequence: int
    channel_order: TelegramOfferChannelOrder | None = None
    state: TelegramDeliveryState = TelegramDeliveryState.PENDING
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    lease_until: datetime | None = None
    worker_id: str | None = None
    last_error_class: str | None = None
    last_error_message: str | None = None
    telegram_message_id: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_DELIVERY_STATES


@dataclass(frozen=True, slots=True)
class TelegramDeliveryDecision:
    outcome: TelegramDeliveryOutcome
    next_retry_at: datetime | None = None
    destination_cooldown_until: datetime | None = None
    reason: str | None = None
    offer_mutation: None = None


@dataclass(frozen=True, slots=True)
class TelegramMethodCall:
    method: str
    payload: dict[str, Any]


def delivery_order_key(job: TelegramDeliveryJob) -> tuple[int, int, int]:
    """Return priority, P1 sub-priority, and FIFO sequence."""
    channel_rank = int(job.channel_order or TelegramOfferChannelOrder.TERMINAL)
    if job.priority != TelegramDeliveryPriority.OFFER_CHANNEL:
        channel_rank = 0
    return int(job.priority), channel_rank, int(job.created_sequence)


def build_terminal_offer_edit_call(
    *,
    chat_id: int,
    message_id: int,
    text: str,
) -> TelegramMethodCall:
    """Build the single Bot API call for terminal text plus button removal."""
    return TelegramMethodCall(
        method="editMessageText",
        payload={
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": str(text),
            "reply_markup": {"inline_keyboard": []},
        },
    )


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _status_code(result: TelegramGatewayResultLike) -> int | None:
    if result.status_code is not None:
        return int(result.status_code)
    if isinstance(result.response_json, Mapping):
        try:
            return int(result.response_json.get("error_code"))
        except (TypeError, ValueError):
            return None
    return None


def _retry_after_seconds(result: TelegramGatewayResultLike) -> float | None:
    if not isinstance(result.response_json, Mapping):
        return None
    parameters = result.response_json.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    retry_after = _positive_number(parameters.get("retry_after"))
    if retry_after is None:
        return None
    return min(float(MAX_TELEGRAM_RETRY_AFTER_SECONDS), retry_after)


def _error_text(result: TelegramGatewayResultLike) -> str:
    parts = [result.response_text or "", result.error or ""]
    if isinstance(result.response_json, Mapping):
        parts.extend(
            str(result.response_json.get(key) or "")
            for key in ("description", "message", "error")
        )
    return " ".join(parts).strip().lower()


def _bounded_backoff_seconds(
    attempt_count: int,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    exponent = min(max(0, int(attempt_count) - 1), 16)
    return min(max(float(base_seconds), float(max_seconds)), float(base_seconds) * (2**exponent))


def apply_gateway_result(
    job: TelegramDeliveryJob,
    result: TelegramGatewayResultLike,
    *,
    now: datetime,
    retry_after_safety_seconds: float,
    retry_base_seconds: float = 1.0,
    retry_max_seconds: float = DEFAULT_MAX_RETRY_SECONDS,
    destination_pause_seconds: float = DEFAULT_DESTINATION_PAUSE_SECONDS,
) -> TelegramDeliveryDecision:
    """Apply one Telegram result without mutating the related domain object.

    ``sendMessage`` read/write timeouts are quarantined as ambiguous because
    Telegram may have accepted the message.  Retrying those blindly can create
    duplicate channel posts.  Stage 4 must validate the eventual reconciliation
    policy before this state is enabled in production.
    """
    if job.is_terminal:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="already_resolved",
        )

    job.worker_id = None
    job.lease_until = None
    if result.ok:
        job.state = TelegramDeliveryState.SENT
        job.next_retry_at = None
        job.last_error_class = None
        job.last_error_message = None
        job.telegram_message_id = result.message_id
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.SENT,
            reason="sent",
        )

    status_code = _status_code(result)
    error_text = _error_text(result)
    error_class = str(
        result.error
        or (f"HTTP{status_code}" if status_code is not None else "TelegramUnknownError")
    )
    job.last_error_class = error_class
    job.last_error_message = error_text[:500] or None

    if status_code == 429:
        retry_after = _retry_after_seconds(result)
        if retry_after is None:
            retry_after = _bounded_backoff_seconds(
                job.attempt_count,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
        delay = retry_after + max(0.0, float(retry_after_safety_seconds))
        retry_at = now + timedelta(seconds=delay)
        job.state = TelegramDeliveryState.PENDING
        job.next_retry_at = retry_at
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=retry_at,
            destination_cooldown_until=retry_at,
            reason="telegram_rate_limited",
        )

    if status_code == 403:
        pause_until = now + timedelta(seconds=max(1.0, float(destination_pause_seconds)))
        job.state = TelegramDeliveryState.PENDING
        job.next_retry_at = pause_until
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.DESTINATION_PAUSED,
            next_retry_at=pause_until,
            destination_cooldown_until=pause_until,
            reason="telegram_destination_forbidden",
        )

    if status_code == 400 and any(
        pattern in error_text for pattern in _MALFORMED_PAYLOAD_PATTERNS
    ):
        job.state = TelegramDeliveryState.TERMINAL_FAILED
        job.next_retry_at = None
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.TERMINAL_FAILED,
            reason="telegram_malformed_payload",
        )

    normalized_error = str(result.error or "").strip().lower()
    if job.method == "sendMessage" and normalized_error in _AMBIGUOUS_SEND_ERRORS:
        job.state = TelegramDeliveryState.AMBIGUOUS
        job.next_retry_at = None
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.AMBIGUOUS,
            reason="telegram_send_result_ambiguous",
        )

    if (
        (status_code is not None and 500 <= status_code <= 599)
        or normalized_error in _SAFE_RETRY_TRANSPORT_ERRORS
        or normalized_error in _AMBIGUOUS_SEND_ERRORS
    ):
        delay = _bounded_backoff_seconds(
            job.attempt_count,
            base_seconds=retry_base_seconds,
            max_seconds=retry_max_seconds,
        )
        retry_at = now + timedelta(seconds=delay)
        job.state = TelegramDeliveryState.PENDING
        job.next_retry_at = retry_at
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=retry_at,
            reason="telegram_temporary_failure",
        )

    job.state = TelegramDeliveryState.TERMINAL_FAILED
    job.next_retry_at = None
    return TelegramDeliveryDecision(
        outcome=TelegramDeliveryOutcome.TERMINAL_FAILED,
        reason="telegram_unhandled_permanent_failure",
    )


def reconcile_ambiguous_send(
    job: TelegramDeliveryJob,
    *,
    delivered: bool | None,
    now: datetime,
    telegram_message_id: int | None = None,
    confirmed_absent_retry_delay_seconds: float = 0.0,
) -> TelegramDeliveryDecision:
    """Resolve an ambiguous send only from explicit external evidence.

    ``delivered=None`` records that reconciliation is still inconclusive.
    ``delivered=False`` must only be used when the observer can prove absence;
    it makes retry safe.  The persistent adapter must audit that evidence.
    """
    if job.state != TelegramDeliveryState.AMBIGUOUS:
        raise ValueError("job_is_not_ambiguous")
    if delivered is None:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.AMBIGUOUS,
            reason="telegram_send_reconciliation_inconclusive",
        )
    if delivered:
        job.state = TelegramDeliveryState.SENT
        job.next_retry_at = None
        job.telegram_message_id = telegram_message_id
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.SENT,
            reason="telegram_send_reconciled_delivered",
        )
    retry_at = now + timedelta(
        seconds=max(0.0, float(confirmed_absent_retry_delay_seconds))
    )
    job.state = TelegramDeliveryState.PENDING
    job.next_retry_at = retry_at
    return TelegramDeliveryDecision(
        outcome=TelegramDeliveryOutcome.RETRY_PENDING,
        next_retry_at=retry_at,
        reason="telegram_send_reconciled_absent",
    )


@dataclass(slots=True)
class InMemoryTelegramDeliveryQueue:
    """Lock-protected reference implementation for contract tests only."""

    jobs: dict[int, TelegramDeliveryJob] = field(default_factory=dict)
    destination_cooldowns: dict[str, datetime] = field(default_factory=dict)
    _dedupe_index: dict[str, int] = field(default_factory=dict)
    _next_id: int = 1
    _next_sequence: int = 1
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enqueue(
        self,
        *,
        dedupe_key: str,
        destination_key: str,
        method: str,
        payload: Mapping[str, Any],
        priority: TelegramDeliveryPriority,
        channel_order: TelegramOfferChannelOrder | None = None,
    ) -> tuple[TelegramDeliveryJob, bool]:
        async with self._lock:
            existing_id = self._dedupe_index.get(dedupe_key)
            if existing_id is not None:
                existing = self.jobs[existing_id]
                if (
                    existing.destination_key != str(destination_key)
                    or existing.method != str(method)
                    or existing.payload != dict(payload)
                    or existing.priority != priority
                    or existing.channel_order != channel_order
                ):
                    raise TelegramDeliveryDedupeConflictError(str(dedupe_key))
                return existing, False
            job = TelegramDeliveryJob(
                id=self._next_id,
                dedupe_key=str(dedupe_key),
                destination_key=str(destination_key),
                method=str(method),
                payload=dict(payload),
                priority=priority,
                channel_order=channel_order,
                created_sequence=self._next_sequence,
            )
            self.jobs[job.id] = job
            self._dedupe_index[job.dedupe_key] = job.id
            self._next_id += 1
            self._next_sequence += 1
            return job, True

    async def claim_next(
        self,
        *,
        now: datetime,
        worker_id: str,
        lease_seconds: float,
    ) -> TelegramDeliveryJob | None:
        async with self._lock:
            ready = [
                job
                for job in self.jobs.values()
                if job.state == TelegramDeliveryState.PENDING
                and (job.next_retry_at is None or job.next_retry_at <= now)
                and self.destination_cooldowns.get(job.destination_key, now) <= now
            ]
            if not ready:
                return None
            job = min(ready, key=delivery_order_key)
            job.state = TelegramDeliveryState.LEASED
            job.worker_id = str(worker_id)
            job.lease_until = now + timedelta(seconds=max(1.0, float(lease_seconds)))
            job.attempt_count += 1
            return job

    async def resolve(
        self,
        job_id: int,
        result: TelegramGatewayResultLike,
        *,
        worker_id: str,
        now: datetime,
        retry_after_safety_seconds: float,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = DEFAULT_MAX_RETRY_SECONDS,
        destination_pause_seconds: float = DEFAULT_DESTINATION_PAUSE_SECONDS,
    ) -> TelegramDeliveryDecision:
        async with self._lock:
            job = self.jobs[int(job_id)]
            if job.is_terminal:
                return apply_gateway_result(
                    job,
                    result,
                    now=now,
                    retry_after_safety_seconds=retry_after_safety_seconds,
                    retry_base_seconds=retry_base_seconds,
                    retry_max_seconds=retry_max_seconds,
                    destination_pause_seconds=destination_pause_seconds,
                )
            if job.state != TelegramDeliveryState.LEASED or job.worker_id != str(worker_id):
                return TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.STALE_LEASE,
                    reason="stale_or_missing_lease",
                )
            decision = apply_gateway_result(
                job,
                result,
                now=now,
                retry_after_safety_seconds=retry_after_safety_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
                destination_pause_seconds=destination_pause_seconds,
            )
            if decision.destination_cooldown_until is not None:
                previous = self.destination_cooldowns.get(job.destination_key)
                if previous is None or decision.destination_cooldown_until > previous:
                    self.destination_cooldowns[job.destination_key] = decision.destination_cooldown_until
            return decision

    async def recover_expired_leases(self, *, now: datetime) -> list[int]:
        async with self._lock:
            recovered: list[int] = []
            for job in sorted(self.jobs.values(), key=delivery_order_key):
                if (
                    job.state == TelegramDeliveryState.LEASED
                    and job.lease_until is not None
                    and job.lease_until <= now
                ):
                    job.state = TelegramDeliveryState.PENDING
                    job.worker_id = None
                    job.lease_until = None
                    recovered.append(job.id)
            return recovered
