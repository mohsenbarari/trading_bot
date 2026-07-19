"""Disabled-by-default execution worker for the shared Telegram queue.

The lifecycle guard currently refuses production cutover at the code level.
Tests may exercise this worker only with an explicit authoritative freshness
validator, credential-bound gateway, and durable dispatch limiter. Remaining
Stage 3 freshness and feeder work keeps runtime capability false.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import os
import socket
import time
from types import SimpleNamespace
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.background_job_authority import (
    JOB_TELEGRAM_DELIVERY_QUEUE,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_BOT_IDENTITIES,
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_DELIVERY_QUEUE_WORKER_ID,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    TelegramDeliveryDispatchDeferredError,
    TelegramDeliveryQueueValidationError,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    defer_unstarted_telegram_delivery_lease,
    load_active_telegram_limiter_evidence,
    load_incomplete_telegram_resume_destination_keys,
    load_telegram_provider_outcome_backlog,
    mark_telegram_delivery_dispatch_started,
    record_telegram_delivery_provider_outcome,
    record_telegram_provider_outcome_apply_failure,
    recover_expired_telegram_delivery_leases,
    release_unstarted_telegram_delivery_lease,
    apply_telegram_delivery_provider_outcome,
    telegram_delivery_database_now,
)
from core.services.telegram_delivery_reconciliation_service import (
    reconcile_telegram_delivery_jobs,
)
from core.services.telegram_delivery_retention_service import (
    run_telegram_delivery_retention_cycle,
)
from core.services.telegram_delivery_runtime_gate_service import (
    TELEGRAM_RUNTIME_GATE_COOLDOWN,
    load_active_telegram_runtime_gates,
    mark_telegram_preflight_gate_active,
    record_telegram_preflight_rate_limit,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFreshnessDecision,
)
from core.telegram_delivery_queue_limiter import (
    TelegramDeliveryDispatchAdmission,
    TelegramDeliveryDispatchLimiter,
    TelegramDeliveryLimiterUnavailableError,
)
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightRateLimitedError,
    run_configured_telegram_delivery_preflight,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.telegram_trade_result_queue_feeder import (
    telegram_trade_result_queue_handoff_loop,
)
from core.telegram_admin_broadcast_queue_feeder import (
    telegram_admin_broadcast_queue_handoff_loop,
)
from core.telegram_notification_outbox_queue_feeder import (
    telegram_notification_outbox_queue_handoff_loop,
)
from core.telegram_market_notice_queue_feeder import (
    telegram_market_notice_queue_handoff_loop,
)
from core.telegram_scheduled_operation_queue_feeder import (
    telegram_scheduled_operation_queue_handoff_loop,
)
from core.telegram_offer_queue_feeder import telegram_offer_queue_handoff_loop
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_RESULT_APPLICATION_MAX_ATTEMPTS = 3
_PROVIDER_OUTCOME_PERSISTENCE_RETRY_BASE_SECONDS = 0.1
_PROVIDER_OUTCOME_PERSISTENCE_RETRY_MAX_SECONDS = 5.0
_RETENTION_INTERVAL_SECONDS = 3600.0
# Provider responses that have been received but not yet committed are an
# in-process fail-stop barrier.  Slots from the same role must not claim past a
# known provider fact, and lease recovery must not race that fact into an
# ambiguous state when PostgreSQL returns.  The set is mutated only by this
# asyncio event loop; no thread synchronization is needed.
_provider_outcome_persistence_barriers: set[tuple[str, int, int]] = set()
_provider_dispatch_entries: set[tuple[str, int, int]] = set()


def _role_provider_fact_blocked(bot_identity: str) -> bool:
    lane_identity = _normalize_lane_identity(bot_identity)
    return any(
        pending_identity == lane_identity
        for pending_identity, _job_id, _lease_token
        in _provider_outcome_persistence_barriers
    )


def _try_enter_provider_dispatch(
    bot_identity: str,
    *,
    job_id: int,
    lease_token: int,
) -> bool:
    """Linearize a provider dispatch against same-role retained facts.

    These sets are accessed only on the owning asyncio event loop and this
    function has no await boundary.  A response can therefore close the role
    gate before another slot registers its provider call.  Calls registered
    before closure are already in flight and retain their own fenced fact.
    """
    entry = (_normalize_lane_identity(bot_identity), int(job_id), int(lease_token))
    if _role_provider_fact_blocked(entry[0]):
        return False
    _provider_dispatch_entries.add(entry)
    return True


def _leave_provider_dispatch(
    bot_identity: str,
    *,
    job_id: int,
    lease_token: int,
) -> None:
    _provider_dispatch_entries.discard(
        (_normalize_lane_identity(bot_identity), int(job_id), int(lease_token))
    )

TelegramQueueGatewayCall = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]
TelegramQueueFreshnessValidator = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[TelegramFreshnessDecision],
]


class TelegramQueueLifecycleFeedback(Protocol):
    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        ...

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        ...

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: Any,
        now: datetime,
    ) -> None:
        ...


class TelegramDeliveryQueueImplementationIncompleteError(RuntimeError):
    """Refuses a claim when no authoritative pre-dispatch adapter is installed."""


async def telegram_delivery_retention_loop() -> None:
    """Run bounded retention only under the foreign queue runtime owner."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await run_telegram_delivery_retention_cycle(
                    db,
                    current_server=current_server(),
                )
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _loop_errors.should_log("telegram_delivery_retention"):
                logger.exception(
                    "Telegram delivery retention cycle failed",
                    extra={
                        "event": "telegram_delivery_retention.failed",
                        "error_class": type(exc).__name__,
                    },
                )
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)


@dataclass(frozen=True, slots=True)
class TelegramDeliveryQueueLaneSpec:
    bot_identity: str
    freshness_validator: TelegramQueueFreshnessValidator
    lifecycle_feedback: TelegramQueueLifecycleFeedback
    gateway_call: TelegramQueueGatewayCall
    dispatch_limiter: TelegramDeliveryDispatchLimiter


@dataclass(frozen=True, slots=True)
class TelegramDeliveryQueueCycleReport:
    bot_identity: str
    processed_count: int
    recovered_count: int
    status_counts: dict[str, int]
    stale_fence_count: int


@dataclass(frozen=True, slots=True)
class TelegramDeliveryLimiterRehydrationReport:
    restored_count: int
    blocked_bot_identities: tuple[str, ...]
    cooldown_destination_keys: tuple[str, ...]
    hard_blocked_destination_keys: tuple[str, ...]
    hard_blocked_bot_destinations: tuple[tuple[str, str], ...]
    gateway_blocked: bool


def _assert_queue_runtime_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1 or not runtime.queue_worker_enabled:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "queue_worker_is_not_runtime_owner"
        )


def _normalize_lane_identity(bot_identity: str) -> str:
    lane_identity = str(bot_identity or "").strip()
    if lane_identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_lane_not_allowlisted"
        )
    return lane_identity


def configured_telegram_delivery_lane_identities() -> tuple[str, ...]:
    identities = [TELEGRAM_PRIMARY_BOT_IDENTITY]
    if bool(
        getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)
    ):
        identities.append(TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY)
    return tuple(identities)


def build_telegram_delivery_queue_lane_specs(
    *,
    freshness_validators: Mapping[str, TelegramQueueFreshnessValidator] | None,
    lifecycle_feedbacks: Mapping[str, TelegramQueueLifecycleFeedback] | None,
    gateway_calls: Mapping[str, TelegramQueueGatewayCall] | None,
    dispatch_limiter: TelegramDeliveryDispatchLimiter | None,
    bot_identities: Sequence[str] | None = None,
) -> tuple[TelegramDeliveryQueueLaneSpec, ...]:
    configured_identities = (
        configured_telegram_delivery_lane_identities()
        if bot_identities is None
        else tuple(bot_identities)
    )
    identities = tuple(
        _normalize_lane_identity(identity)
        for identity in configured_identities
    )
    if not identities or len(set(identities)) != len(identities):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_lane_set_invalid"
        )
    validators = freshness_validators or {}
    feedbacks = lifecycle_feedbacks or {}
    gateways = gateway_calls or {}
    if dispatch_limiter is None or not all(
        callable(getattr(dispatch_limiter, method, None))
        for method in (
            "acquire",
            "observe",
            "extend_destination_cooldown",
            "extend_bot_cooldown",
            "prepare_preflight",
            "preflight_gate_open",
        )
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_dispatch_limiter_not_installed"
        )
    specs: list[TelegramDeliveryQueueLaneSpec] = []
    for identity in identities:
        validator = validators.get(identity)
        lifecycle_feedback = feedbacks.get(identity)
        gateway_call = gateways.get(identity)
        if not callable(validator):
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"authoritative_freshness_validator_not_installed:{identity}"
            )
        if not callable(gateway_call):
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"telegram_lane_gateway_not_installed:{identity}"
            )
        if lifecycle_feedback is None or not all(
            callable(getattr(lifecycle_feedback, method, None))
            for method in (
                "assert_dispatchable",
                "apply_freshness",
                "apply_delivery_result",
            )
        ):
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"telegram_lifecycle_feedback_not_installed:{identity}"
            )
        specs.append(
            TelegramDeliveryQueueLaneSpec(
                bot_identity=identity,
                freshness_validator=validator,
                lifecycle_feedback=lifecycle_feedback,
                gateway_call=gateway_call,
                dispatch_limiter=dispatch_limiter,
            )
        )
    return tuple(specs)


def _worker_id(bot_identity: str) -> str:
    hostname = socket.gethostname().split(".", 1)[0][:48]
    lane_identity = _normalize_lane_identity(bot_identity)
    return (
        f"{TELEGRAM_DELIVERY_QUEUE_WORKER_ID}:{lane_identity}:{hostname}:{os.getpid()}"
    )[:128]


def _worker_slot_id(bot_identity: str, slot_kind: str, slot_index: int) -> str:
    suffix = f":{str(slot_kind)}{int(slot_index)}"
    base = _worker_id(bot_identity)
    return f"{base[: 128 - len(suffix)]}{suffix}"


def _lane_slot_plan(bot_identity: str) -> tuple[tuple[str, int | None], ...]:
    """Return bounded lane slots and any reserved effective-priority ceiling."""
    identity = _normalize_lane_identity(bot_identity)
    if identity == TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY:
        concurrency = max(
            1,
            int(
                getattr(
                    settings,
                    "telegram_delivery_queue_channel_editor_concurrency",
                    1,
                )
            ),
        )
        return tuple((f"general-{index}", None) for index in range(concurrency))

    concurrency = max(
        2,
        int(getattr(settings, "telegram_delivery_queue_primary_concurrency", 4)),
    )
    reserved = max(
        1,
        int(
            getattr(
                settings,
                "telegram_delivery_queue_primary_m0_reserved_concurrency",
                1,
            )
        ),
    )
    if reserved >= concurrency:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_primary_m0_reservation_invalid"
        )
    general = concurrency - reserved
    return (
        *tuple((f"general-{index}", None) for index in range(general)),
        *tuple((f"m0-reserved-{index}", 0) for index in range(reserved)),
    )


def _worker_batch_limit(limit: int | None = None) -> int:
    configured = limit if limit is not None else getattr(
        settings, "telegram_delivery_queue_worker_batch_limit", 25
    )
    return max(1, int(configured))


def _worker_interval_seconds() -> float:
    return max(
        0.1,
        float(getattr(settings, "telegram_delivery_queue_worker_interval_seconds", 1.0)),
    )


def _request_timeout_seconds() -> float:
    return max(
        0.1,
        float(
            getattr(settings, "telegram_delivery_queue_worker_request_timeout_seconds", 10.0)
        ),
    )


def _lease_seconds() -> float:
    return max(
        1.0,
        float(getattr(settings, "telegram_delivery_queue_worker_lease_seconds", 30.0)),
    )


def _recover_limit() -> int:
    return max(
        1,
        int(getattr(settings, "telegram_delivery_queue_worker_recover_limit", 100)),
    )


def _retry_after_safety_seconds() -> float:
    return max(
        0.0,
        float(getattr(settings, "telegram_delivery_queue_retry_after_safety_seconds", 0.1)),
    )


async def _persist_preflight_rate_limit_gate(
    *,
    bot_identity: str,
    retry_after_seconds: float,
    retry_after_source: str,
) -> datetime:
    async with AsyncSessionLocal() as db:
        gate = await record_telegram_preflight_rate_limit(
            db,
            current_server=current_server(),
            bot_identity=bot_identity,
            retry_after_seconds=retry_after_seconds,
            safety_seconds=_retry_after_safety_seconds(),
            retry_after_source=retry_after_source,
        )
        await db.commit()
        if gate.cooldown_until is None:
            raise TelegramDeliveryQueueImplementationIncompleteError(
                "telegram_preflight_cooldown_deadline_missing"
            )
        return gate.cooldown_until


async def _persist_preflight_success_gate(
    *,
    bot_identity: str,
    report: Any,
) -> None:
    async with AsyncSessionLocal() as db:
        await mark_telegram_preflight_gate_active(
            db,
            current_server=current_server(),
            bot_identity=bot_identity,
            report=report,
        )
        await db.commit()


async def rehydrate_telegram_delivery_limiter_state(
    dispatch_limiter: TelegramDeliveryDispatchLimiter,
) -> TelegramDeliveryLimiterRehydrationReport:
    """Rebuild Redis cooldown and hard-pause evidence before any claim."""
    async with AsyncSessionLocal() as db:
        sampled_at = await telegram_delivery_database_now(db)
        evidence_rows = await load_active_telegram_limiter_evidence(
            db,
            current_server=current_server(),
            now=sampled_at,
        )
        incomplete_resume_destinations = (
            await load_incomplete_telegram_resume_destination_keys(
                db,
                current_server=current_server(),
            )
        )
        runtime_gates = await load_active_telegram_runtime_gates(
            db,
            current_server=current_server(),
            now=sampled_at,
        )
        await db.rollback()

    restored = len(incomplete_resume_destinations)
    blocked_bot_identities: set[str] = set()
    cooldown_destination_keys: set[str] = set()
    hard_blocked_destination_keys: set[str] = set(incomplete_resume_destinations)
    hard_blocked_bot_destinations: set[tuple[str, str]] = set()
    gateway_blocked = False
    pause_decisions = {
        TelegramDeliveryState.BLOCKED_BOT: TelegramDeliveryOutcome.BOT_PAUSED,
        TelegramDeliveryState.BLOCKED_DESTINATION: (
            TelegramDeliveryOutcome.DESTINATION_PAUSED
        ),
        TelegramDeliveryState.BLOCKED_GATEWAY: (
            TelegramDeliveryOutcome.GATEWAY_PAUSED
        ),
    }
    runtime_bot_gate_identities = {
        str(gate.bot_identity)
        for gate in runtime_gates
        if gate.scope == "bot" and gate.bot_identity
    }
    runtime_gateway_gate = any(gate.scope == "gateway" for gate in runtime_gates)
    for evidence in evidence_rows:
        current_time = sampled_at
        observed_at = evidence.observed_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise TelegramDeliveryQueueImplementationIncompleteError(
                "telegram_limiter_evidence_timestamp_invalid"
            )
        if observed_at > current_time:
            observed_at = current_time
        evidence_restored = False
        if (
            evidence.state == TelegramDeliveryState.BLOCKED_BOT
            and evidence.bot_identity in runtime_bot_gate_identities
        ) or (
            evidence.state == TelegramDeliveryState.BLOCKED_GATEWAY
            and runtime_gateway_gate
        ):
            continue
        if evidence.rate_limit_probe:
            blocked_bot_identities.add(evidence.bot_identity)
            probe_gate_until = evidence.lease_until
            if probe_gate_until is None or probe_gate_until <= current_time:
                probe_gate_until = current_time + timedelta(seconds=30.0)
            await dispatch_limiter.extend_bot_cooldown(
                evidence.bot_identity,
                until=probe_gate_until,
            )
            evidence_restored = True
        if (
            evidence.bot_cooldown_until is not None
            and evidence.bot_cooldown_until > current_time
        ):
            blocked_bot_identities.add(evidence.bot_identity)
            await dispatch_limiter.extend_bot_cooldown(
                evidence.bot_identity,
                until=evidence.bot_cooldown_until,
            )
            evidence_restored = True
        if (
            evidence.state == TelegramDeliveryState.PENDING_RETRY
            and evidence.outcome_reason == "telegram_rate_limited"
            and evidence.next_retry_at is not None
            and evidence.next_retry_at > current_time
        ):
            cooldown_destination_keys.add(evidence.destination_key)
            await dispatch_limiter.observe(
                evidence,
                TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.RETRY_PENDING,
                    next_retry_at=evidence.next_retry_at,
                    destination_cooldown_until=evidence.next_retry_at,
                    bot_cooldown_until=evidence.bot_cooldown_until,
                    reason="telegram_rate_limited",
                ),
                now=observed_at,
            )
            evidence_restored = True
        elif evidence.state in pause_decisions:
            if evidence.state == TelegramDeliveryState.BLOCKED_BOT:
                blocked_bot_identities.add(evidence.bot_identity)
            elif evidence.state == TelegramDeliveryState.BLOCKED_DESTINATION:
                hard_blocked_bot_destinations.add(
                    (evidence.bot_identity, evidence.destination_key)
                )
            elif evidence.state == TelegramDeliveryState.BLOCKED_GATEWAY:
                gateway_blocked = True
            await dispatch_limiter.observe(
                evidence,
                TelegramDeliveryDecision(
                    outcome=pause_decisions[evidence.state],
                    reason=evidence.outcome_reason or evidence.state.value,
                ),
                now=observed_at,
            )
            evidence_restored = True
        if evidence_restored:
            restored += 1
    for gate in runtime_gates:
        current_time = sampled_at
        if gate.scope == "bot" and gate.bot_identity:
            blocked_bot_identities.add(gate.bot_identity)
            if (
                gate.state == TELEGRAM_RUNTIME_GATE_COOLDOWN
                and gate.cooldown_until is not None
                and gate.cooldown_until > current_time
            ):
                await dispatch_limiter.extend_bot_cooldown(
                    gate.bot_identity,
                    until=gate.cooldown_until,
                )
            else:
                await dispatch_limiter.observe(
                    SimpleNamespace(
                        bot_identity=gate.bot_identity,
                        destination_key=f"runtime:{gate.gate_key}",
                    ),
                    TelegramDeliveryDecision(
                        outcome=TelegramDeliveryOutcome.BOT_PAUSED,
                        reason=gate.reason_code or gate.state,
                    ),
                    now=min(gate.updated_at, current_time),
                )
        elif gate.scope == "gateway":
            gateway_blocked = True
            await dispatch_limiter.observe(
                SimpleNamespace(
                    bot_identity=TELEGRAM_PRIMARY_BOT_IDENTITY,
                    destination_key="runtime:gateway",
                ),
                TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.GATEWAY_PAUSED,
                    reason=gate.reason_code or gate.state,
                ),
                now=min(gate.updated_at, current_time),
            )
        restored += 1
    return TelegramDeliveryLimiterRehydrationReport(
        restored_count=restored,
        blocked_bot_identities=tuple(sorted(blocked_bot_identities)),
        cooldown_destination_keys=tuple(sorted(cooldown_destination_keys)),
        hard_blocked_destination_keys=tuple(sorted(hard_blocked_destination_keys)),
        hard_blocked_bot_destinations=tuple(
            sorted(hard_blocked_bot_destinations)
        ),
        gateway_blocked=gateway_blocked,
    )


async def _recover_expired_leases() -> int:
    if _provider_outcome_persistence_barriers:
        return 0
    async with AsyncSessionLocal() as db:
        report = await recover_expired_telegram_delivery_leases(
            db,
            current_server=current_server(),
            max_rows=_recover_limit(),
        )
        if report.job_ids:
            await db.commit()
        return len(report.job_ids)


async def _release_after_predispatch_error(
    *,
    job_id: int,
    worker_id: str,
    lease_token: int,
    reason: str,
) -> None:
    async with AsyncSessionLocal() as db:
        released = await release_unstarted_telegram_delivery_lease(
            db,
            current_server=current_server(),
            job_id=job_id,
            worker_id=worker_id,
            lease_token=lease_token,
            reason=reason,
        )
        if released:
            await db.commit()


async def _defer_for_dispatch_limit(
    *,
    job_id: int,
    worker_id: str,
    lease_token: int,
    retry_seconds: float,
    reason: str,
) -> bool:
    async with AsyncSessionLocal() as db:
        deferred = await defer_unstarted_telegram_delivery_lease(
            db,
            current_server=current_server(),
            job_id=job_id,
            worker_id=worker_id,
            lease_token=lease_token,
            retry_seconds=retry_seconds,
            reason=reason,
        )
        if deferred:
            await db.commit()
        else:
            await db.rollback()
        return deferred


async def _release_unused_rate_limit_probe(
    *,
    dispatch_limiter: TelegramDeliveryDispatchLimiter,
    job: TelegramDeliveryJobRecord,
    admission: Any,
    force: bool = False,
) -> None:
    if not force and not bool(getattr(admission, "is_rate_limit_probe", False)):
        return
    await dispatch_limiter.observe(
        job,
        TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="rate_limit_probe_cancelled_before_dispatch",
        ),
        now=utc_now(),
    )


async def _persist_delivery_result_after_dispatch(
    *,
    bot_identity: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    gateway_result: telegram_gateway.TelegramGatewayResult,
    feedback: Callable[
        [
            AsyncSession,
            TelegramDeliveryJobRecord,
            TelegramDeliveryDecision,
            datetime,
        ],
        Awaitable[None],
    ],
) -> tuple[TelegramDeliveryDecision, TelegramDeliveryDecision]:
    """Record one provider fact, then atomically apply queue/domain feedback.

    Once Telegram has answered, a transient PostgreSQL outage must not discard
    that known fact while this process is alive.  Recording therefore retries
    connection/storage failures without a small attempt cap.  Cancellation is
    remembered but deferred until the idempotent provider-outcome row commits;
    the replay worker can then finish domain feedback without another Telegram
    call.  An abrupt process/host loss can still leave a genuinely ambiguous
    dispatch because the Bot API provides no idempotency token.
    """
    lane_identity = _normalize_lane_identity(bot_identity)
    barrier = (lane_identity, int(job_id), int(lease_token))
    _provider_outcome_persistence_barriers.add(barrier)
    outcome_id: int | None = None
    attempt = 0
    cancellation_requested = False
    while outcome_id is None:
        attempt += 1
        try:
            async with AsyncSessionLocal() as db:
                persisted = await record_telegram_delivery_provider_outcome(
                    db,
                    current_server=current_server(),
                    job_id=job_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    result=gateway_result,
                )
                persisted_outcome_id = int(persisted.outcome.id)
                await db.commit()
                # A returned ORM identity is not a durable fact until commit
                # itself succeeds.  On an uncertain/failed commit the unique
                # job+lease fence makes the next insert attempt idempotent.
                outcome_id = persisted_outcome_id
        except asyncio.CancelledError:
            # A cancellation delivered after the provider response is not
            # allowed to erase the response from volatile memory.  Retrying is
            # safe because job_id+lease_token uniquely fences the outcome row.
            cancellation_requested = True
            continue
        except TelegramDeliveryQueueValidationError as exc:
            # Fence conflicts and malformed facts are invariants, not storage
            # outages.  Retrying them forever would conceal a programming or
            # reconciliation defect.
            # Keep the barrier installed and fail the lane supervisor.  A
            # validation/fence conflict after Telegram answered is not safe to
            # downgrade into an ordinary logged cycle error.
            raise TelegramDeliveryQueueImplementationIncompleteError(
                "telegram_provider_outcome_fact_rejected"
            ) from exc
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            retry_delay = min(
                _PROVIDER_OUTCOME_PERSISTENCE_RETRY_MAX_SECONDS,
                _PROVIDER_OUTCOME_PERSISTENCE_RETRY_BASE_SECONDS
                * (2 ** min(attempt - 1, 10)),
            )
            logger.warning(
                "Telegram provider outcome is retained in memory until PostgreSQL recovers",
                extra={
                    "event": "telegram_delivery.provider_outcome_record_retry",
                    "job_id": job_id,
                    "attempt": attempt,
                    "retry_delay_seconds": retry_delay,
                    "error_class": type(exc).__name__,
                },
            )
            try:
                await asyncio.sleep(retry_delay)
            except asyncio.CancelledError:
                cancellation_requested = True

    if outcome_id is None:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_provider_outcome_record_exhausted"
        )
    _provider_outcome_persistence_barriers.discard(barrier)
    if cancellation_requested:
        # The provider fact is durable.  Preserve normal task cancellation and
        # let the independent replay loop apply it after restart/resume.
        raise asyncio.CancelledError

    for attempt in range(1, _RESULT_APPLICATION_MAX_ATTEMPTS + 1):
        try:
            async with AsyncSessionLocal() as db:
                decision = await apply_telegram_delivery_provider_outcome(
                    db,
                    current_server=current_server(),
                    outcome_id=outcome_id,
                    retry_after_safety_seconds=_retry_after_safety_seconds(),
                    retry_base_seconds=max(
                        0.1,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_retry_base_seconds",
                                1.0,
                            )
                        ),
                    ),
                    retry_max_seconds=max(
                        0.1,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_retry_max_seconds",
                                300.0,
                            )
                        ),
                    ),
                    retry_jitter_ratio=float(
                        getattr(
                            settings,
                            "telegram_delivery_queue_retry_jitter_ratio",
                            0.2,
                        )
                    ),
                    global_rate_limit_window_seconds=max(
                        0.001,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_global_rate_limit_window_seconds",
                                2.0,
                            )
                        ),
                    ),
                    feedback=feedback,
                )
                await db.commit()
            return decision, decision
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with AsyncSessionLocal() as failure_db:
                await record_telegram_provider_outcome_apply_failure(
                    failure_db,
                    current_server=current_server(),
                    outcome_id=outcome_id,
                    error=exc,
                )
                await failure_db.commit()
            if attempt >= _RESULT_APPLICATION_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Retrying Telegram provider-outcome application",
                extra={
                    "event": "telegram_delivery.provider_outcome_apply_retry",
                    "job_id": job_id,
                    "outcome_id": outcome_id,
                    "attempt": attempt,
                    "max_attempts": _RESULT_APPLICATION_MAX_ATTEMPTS,
                    "error_class": type(exc).__name__,
                },
            )
            await asyncio.sleep(0.05 * attempt)

    raise RuntimeError("telegram_delivery_provider_outcome_apply_exhausted")


async def run_telegram_delivery_queue_cycle(
    *,
    bot_identity: str,
    limit: int | None = None,
    freshness_validator: TelegramQueueFreshnessValidator | None = None,
    lifecycle_feedback: TelegramQueueLifecycleFeedback | None = None,
    gateway_call: TelegramQueueGatewayCall | None = None,
    dispatch_limiter: TelegramDeliveryDispatchLimiter | None = None,
    worker_id: str | None = None,
    recover_leases: bool = True,
    allowed_destination_classes: set[TelegramDestinationClass] | None = None,
    maximum_effective_priority: int | None = None,
) -> TelegramDeliveryQueueCycleReport:
    """Run a bounded testable cycle without holding a DB transaction over HTTP."""
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    lane_identity = _normalize_lane_identity(bot_identity)
    if freshness_validator is None:
        # Refuse before lease recovery/claim so an incomplete adapter cannot
        # perturb queue state even when this function is called directly.
        raise TelegramDeliveryQueueImplementationIncompleteError(
            f"authoritative_freshness_validator_not_installed:{lane_identity}"
        )
    if gateway_call is None:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            f"telegram_lane_gateway_not_installed:{lane_identity}"
        )
    if lifecycle_feedback is None or not all(
        callable(getattr(lifecycle_feedback, method, None))
        for method in (
            "assert_dispatchable",
            "apply_freshness",
            "apply_delivery_result",
        )
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            f"telegram_lifecycle_feedback_not_installed:{lane_identity}"
        )
    if dispatch_limiter is None or not all(
        callable(getattr(dispatch_limiter, method, None))
        for method in (
            "acquire",
            "observe",
            "extend_destination_cooldown",
            "extend_bot_cooldown",
            "prepare_preflight",
            "preflight_gate_open",
        )
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_dispatch_limiter_not_installed"
        )
    validator = freshness_validator

    active_worker_id = str(worker_id or _worker_id(lane_identity))
    status_counts: dict[str, int] = {}
    stale_fence_count = 0
    processed_count = 0
    recovered_count = await _recover_expired_leases() if recover_leases else 0

    if _role_provider_fact_blocked(lane_identity):
        return TelegramDeliveryQueueCycleReport(
            bot_identity=lane_identity,
            processed_count=0,
            recovered_count=recovered_count,
            status_counts={"provider_fact_persistence_wait": 1},
            stale_fence_count=0,
        )

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server=current_server(),
                bot_identity=lane_identity,
                worker_id=active_worker_id,
                request_timeout_seconds=_request_timeout_seconds(),
                lease_seconds=_lease_seconds(),
                allowed_destination_classes=allowed_destination_classes,
                maximum_effective_priority=maximum_effective_priority,
            )
            if job is None:
                await db.rollback()
                break
            await db.commit()

        job_id = int(job.id)
        lease_token = int(job.lease_token)
        if _role_provider_fact_blocked(lane_identity):
            await _defer_for_dispatch_limit(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                retry_seconds=0.1,
                reason="provider_fact_persistence_wait_after_claim",
            )
            status_counts["provider_fact_persistence_wait"] = (
                status_counts.get("provider_fact_persistence_wait", 0) + 1
            )
            processed_count += 1
            continue
        try:
            async with AsyncSessionLocal() as db:
                current_time = await telegram_delivery_database_now(db)
                freshness = await validator(db, job, current_time)
                may_dispatch = await apply_telegram_delivery_freshness_result(
                    db,
                    current_server=current_server(),
                    job_id=job_id,
                    worker_id=active_worker_id,
                    lease_token=lease_token,
                    decision=freshness,
                    feedback=lifecycle_feedback.apply_freshness,
                    now=current_time,
                )
                await db.commit()
        except asyncio.CancelledError:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason="worker_cancelled_before_dispatch",
            )
            raise
        except Exception as exc:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason=f"freshness_validator:{type(exc).__name__}",
            )
            raise

        if not may_dispatch:
            key = freshness.outcome.value
            status_counts[key] = status_counts.get(key, 0) + 1
            processed_count += 1
            continue

        admission: TelegramDeliveryDispatchAdmission | None = None
        try:
            admission = await dispatch_limiter.acquire(job, now=utc_now())
            if not admission.allowed:
                retry_seconds = float(admission.retry_after_seconds or 0.0)
                if not math.isfinite(retry_seconds) or retry_seconds <= 0:
                    raise TelegramDeliveryLimiterUnavailableError(
                        "telegram_limiter_invalid_admission"
                    )
                wait_reason = str(admission.wait_reason or "unspecified")[:80]
                deferred = await _defer_for_dispatch_limit(
                    job_id=job_id,
                    worker_id=active_worker_id,
                    lease_token=lease_token,
                    retry_seconds=retry_seconds,
                    reason=f"telegram_limiter_wait:{wait_reason}",
                )
                if not deferred:
                    stale_fence_count += 1
                key = "limiter_wait"
                status_counts[key] = status_counts.get(key, 0) + 1
                processed_count += 1
                continue
        except asyncio.CancelledError:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason="worker_cancelled_before_dispatch",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
                # Cancellation may arrive after Redis atomically reserved the
                # probe but before ``acquire`` returned its admission object.
                # Clearing by this job's digest is idempotent and cannot clear
                # a probe owned by another job.
                force=True,
            )
            raise
        except Exception as exc:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason=f"dispatch_limiter:{type(exc).__name__}",
            )
            raise

        # Limiter admission is not a side effect at Telegram, but authoritative
        # business state may have changed while the job waited for admission.
        # Revalidate once more at the final local boundary before marking the
        # dispatch as started. This closes the claim/limiter race without
        # holding a database transaction over the network call.
        try:
            async with AsyncSessionLocal() as db:
                final_freshness_time = await telegram_delivery_database_now(db)
                freshness = await validator(db, job, final_freshness_time)
                may_dispatch = await apply_telegram_delivery_freshness_result(
                    db,
                    current_server=current_server(),
                    job_id=job_id,
                    worker_id=active_worker_id,
                    lease_token=lease_token,
                    decision=freshness,
                    feedback=lifecycle_feedback.apply_freshness,
                    now=final_freshness_time,
                )
                await db.commit()
        except asyncio.CancelledError:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason="worker_cancelled_before_dispatch",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            raise
        except Exception as exc:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason=f"final_freshness_validator:{type(exc).__name__}",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            raise

        if not may_dispatch:
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            key = freshness.outcome.value
            status_counts[key] = status_counts.get(key, 0) + 1
            processed_count += 1
            continue

        dispatch_entry_acquired = _try_enter_provider_dispatch(
            lane_identity,
            job_id=job_id,
            lease_token=lease_token,
        )
        if not dispatch_entry_acquired:
            await _defer_for_dispatch_limit(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                retry_seconds=0.1,
                reason="provider_fact_persistence_wait_before_dispatch",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            status_counts["provider_fact_persistence_wait"] = (
                status_counts.get("provider_fact_persistence_wait", 0) + 1
            )
            processed_count += 1
            continue

        try:
            async with AsyncSessionLocal() as db:
                # The dispatch marker is the local linearization point for the
                # external side effect. SERIALIZABLE makes predicate-backed
                # access reads (including customer/accountant relations) conflict
                # with a concurrent access change instead of committing a stale
                # authorization followed by a send.
                await db.connection(
                    execution_options={"isolation_level": "SERIALIZABLE"}
                )
                dispatch_marked = await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server=current_server(),
                    job_id=job_id,
                    worker_id=active_worker_id,
                    lease_token=lease_token,
                    dispatch_guard=lifecycle_feedback.assert_dispatchable,
                    rate_limit_probe=admission.is_rate_limit_probe,
                )
                if dispatch_marked:
                    await db.commit()
                else:
                    await db.rollback()
        except asyncio.CancelledError:
            _leave_provider_dispatch(
                lane_identity,
                job_id=job_id,
                lease_token=lease_token,
            )
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason="worker_cancelled_before_dispatch",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            raise
        except TelegramDeliveryDispatchDeferredError as exc:
            _leave_provider_dispatch(
                lane_identity,
                job_id=job_id,
                lease_token=lease_token,
            )
            deferred = await _defer_for_dispatch_limit(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                retry_seconds=exc.retry_after_seconds,
                reason=exc.reason,
            )
            if not deferred:
                stale_fence_count += 1
            elif exc.cooldown_until is not None:
                # PostgreSQL already owns the safety gate. Restore the Redis
                # fast-path before allowing this worker process to continue;
                # failure raises and stops the supervisor fail-closed.
                if exc.scope == "destination":
                    await dispatch_limiter.extend_destination_cooldown(
                        job,
                        until=exc.cooldown_until,
                    )
                await rehydrate_telegram_delivery_limiter_state(dispatch_limiter)
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            key = "durable_dispatch_wait"
            status_counts[key] = status_counts.get(key, 0) + 1
            processed_count += 1
            continue
        except Exception as exc:
            _leave_provider_dispatch(
                lane_identity,
                job_id=job_id,
                lease_token=lease_token,
            )
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason=f"dispatch_guard:{type(exc).__name__}",
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            raise
        if not dispatch_marked:
            _leave_provider_dispatch(
                lane_identity,
                job_id=job_id,
                lease_token=lease_token,
            )
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            stale_fence_count += 1
            continue

        try:
            try:
                gateway_result = await gateway_call(
                    str(job.method),
                    dict(job.payload or {}),
                    timeout=_request_timeout_seconds(),
                    idempotency_key=str(job.dedupe_key),
                )
            except asyncio.CancelledError:
                # Dispatch was already marked. Lease recovery must classify this as
                # ambiguous/reconcile; releasing it as retryable could duplicate.
                raise
            except Exception as exc:
                gateway_result = telegram_gateway.TelegramGatewayResult(
                    ok=False,
                    method=str(job.method),
                    idempotency_key=str(job.dedupe_key),
                    error=type(exc).__name__,
                    transport_phase="write_unknown",
                )

            decision, limiter_decision = await _persist_delivery_result_after_dispatch(
                bot_identity=lane_identity,
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                gateway_result=gateway_result,
                feedback=lifecycle_feedback.apply_delivery_result,
            )
        finally:
            _leave_provider_dispatch(
                lane_identity,
                job_id=job_id,
                lease_token=lease_token,
            )
        if decision.outcome == TelegramDeliveryOutcome.STALE_LEASE:
            stale_fence_count += 1
        key = decision.outcome.value
        status_counts[key] = status_counts.get(key, 0) + 1
        processed_count += 1
        # Redis window ordering follows completed durable persistence, not the
        # earlier provider-response timestamp. This avoids inverse commits
        # treating a future observation as a member of the current window.
        await dispatch_limiter.observe(job, limiter_decision, now=utc_now())

    return TelegramDeliveryQueueCycleReport(
        bot_identity=lane_identity,
        processed_count=processed_count,
        recovered_count=recovered_count,
        status_counts=status_counts,
        stale_fence_count=stale_fence_count,
    )


async def _telegram_delivery_queue_lane_slot_loop(
    lane: TelegramDeliveryQueueLaneSpec,
    *,
    slot_name: str,
    slot_index: int,
    maximum_effective_priority: int | None,
    allowed_destination_classes: set[TelegramDestinationClass] | None = None,
) -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    logger.info(
        "Telegram delivery execution slot started",
        extra={
            "event": "telegram_delivery_queue_lane.slot_started",
            "bot_role": lane.bot_identity,
            "slot_name": slot_name,
            "slot_index": slot_index,
            "maximum_effective_priority": maximum_effective_priority,
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": 1,
        },
    )
    iteration = 0
    while True:
        iteration += 1
        report: TelegramDeliveryQueueCycleReport | None = None
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_delivery_queue_cycle(
                    bot_identity=lane.bot_identity,
                    limit=1,
                    freshness_validator=lane.freshness_validator,
                    lifecycle_feedback=lane.lifecycle_feedback,
                    gateway_call=lane.gateway_call,
                    dispatch_limiter=lane.dispatch_limiter,
                    worker_id=_worker_slot_id(
                        lane.bot_identity,
                        "m0" if maximum_effective_priority == 0 else "g",
                        slot_index,
                    ),
                    recover_leases=False,
                    allowed_destination_classes=allowed_destination_classes,
                    maximum_effective_priority=maximum_effective_priority,
                )
                if report.processed_count:
                    logger.info(
                        "Telegram delivery lane cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "bot_role": lane.bot_identity,
                            "slot_name": slot_name,
                            "slot_index": slot_index,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "status_counts": report.status_counts,
                            "stale_fence_count": report.stale_fence_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except TelegramDeliveryQueueImplementationIncompleteError:
                raise
            except TelegramDeliveryLimiterUnavailableError:
                # A durable limiter failure is an execution safety failure,
                # not a normal cycle error. Fail the supervisor so no lane
                # keeps claiming work while dispatch admission is unknown.
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram delivery lane %s: %s",
                    lane.bot_identity,
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    bot_role=lane.bot_identity,
                    slot_name=slot_name,
                    slot_index=slot_index,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(start_time),
                )
        # The configured interval is an idle-poll delay, not a per-message
        # throughput cap.  Redis remains the sole cadence authority.  A short
        # cancellable yield also prevents a hot empty/limited database loop and
        # gives shutdown a clean boundary outside connection establishment.
        if report is not None and report.processed_count:
            await asyncio.sleep(min(0.01, _worker_interval_seconds()))
        else:
            await asyncio.sleep(_worker_interval_seconds())


async def telegram_delivery_queue_lane_loop(
    lane: TelegramDeliveryQueueLaneSpec,
) -> None:
    """Supervise bounded intra-role slots, including reserved M0 capacity."""
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    slot_plan = _lane_slot_plan(lane.bot_identity)
    logger.info(
        "Telegram delivery execution lane started",
        extra={
            "event": "telegram_delivery_queue_lane.started",
            "bot_role": lane.bot_identity,
            "slot_count": len(slot_plan),
            "m0_reserved_slot_count": sum(
                ceiling == 0 for _, ceiling in slot_plan
            ),
        },
    )
    tasks = [
        asyncio.create_task(
            _telegram_delivery_queue_lane_slot_loop(
                lane,
                slot_name=slot_name,
                slot_index=index,
                maximum_effective_priority=maximum_priority,
                allowed_destination_classes=None,
            ),
            name=(
                f"telegram-delivery-lane-slot:{lane.bot_identity}:"
                f"{slot_name}"
            ),
        )
        for index, (slot_name, maximum_priority) in enumerate(slot_plan)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def telegram_delivery_private_only_lane_loop(
    lane: TelegramDeliveryQueueLaneSpec,
    *,
    channel_destination_key: str,
) -> None:
    """Keep primary private traffic moving while channel capability is gated.

    The loop exits as soon as the durable channel gate clears so the activation
    controller must run a full channel permission preflight before channel work
    can be claimed again.
    """
    if lane.bot_identity != TELEGRAM_PRIMARY_BOT_IDENTITY:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "identity_only_lane_must_be_primary"
        )
    logger.info(
        "Telegram primary lane started in private-only mode",
        extra={
            "event": "telegram_delivery_queue_lane.private_only_started",
            "bot_role": lane.bot_identity,
        },
    )
    initial_rehydration = await rehydrate_telegram_delivery_limiter_state(
        lane.dispatch_limiter
    )
    may_start, identity_only = _telegram_delivery_lane_start_mode(
        lane,
        rehydration=initial_rehydration,
        channel_destination_key=channel_destination_key,
    )
    if not may_start or not identity_only:
        return

    slot_plan = _lane_slot_plan(lane.bot_identity)
    tasks = [
        asyncio.create_task(
            _telegram_delivery_queue_lane_slot_loop(
                lane,
                slot_name=f"private-{slot_name}",
                slot_index=index,
                maximum_effective_priority=maximum_priority,
                allowed_destination_classes={TelegramDestinationClass.PRIVATE},
            ),
            name=f"telegram-delivery-private-slot:{slot_name}",
        )
        for index, (slot_name, maximum_priority) in enumerate(slot_plan)
    ]
    try:
        while True:
            failed = next((task for task in tasks if task.done()), None)
            if failed is not None:
                await failed
                raise TelegramDeliveryQueueImplementationIncompleteError(
                    "telegram_private_lane_slot_stopped_unexpectedly"
                )
            await asyncio.sleep(_worker_interval_seconds())
            rehydration = await rehydrate_telegram_delivery_limiter_state(
                lane.dispatch_limiter
            )
            may_start, identity_only = _telegram_delivery_lane_start_mode(
                lane,
                rehydration=rehydration,
                channel_destination_key=channel_destination_key,
            )
            if not may_start or not identity_only:
                return
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def telegram_delivery_queue_recovery_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                recovered_count = await _recover_expired_leases()
                if recovered_count:
                    logger.info(
                        "Telegram delivery lease recovery completed",
                        extra={
                            "event": "telegram_delivery_queue_recovery.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "run_id": run_id,
                            "iteration": iteration,
                            "recovered_count": recovered_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except TelegramDeliveryQueueImplementationIncompleteError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram delivery lease recovery: %s",
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(start_time),
                )
        await asyncio.sleep(_worker_interval_seconds())


async def run_telegram_provider_outcome_replay_cycle(
    *,
    lifecycle_feedbacks: Mapping[str, TelegramQueueLifecycleFeedback],
    limit: int | None = None,
) -> int:
    """Apply due durable provider facts without repeating Telegram calls."""
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    batch_size = _worker_batch_limit(limit)
    async with AsyncSessionLocal() as db:
        backlog = await load_telegram_provider_outcome_backlog(
            db,
            current_server=current_server(),
            max_rows=batch_size,
        )
        await db.rollback()

    async def routed_feedback(
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        adapter = lifecycle_feedbacks.get(str(job.bot_identity))
        if adapter is None:
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"telegram_provider_outcome_feedback_not_installed:{job.bot_identity}"
            )
        await adapter.apply_delivery_result(db, job, decision, now)

    applied_count = 0
    for outcome_id in backlog.due_outcome_ids:
        try:
            async with AsyncSessionLocal() as db:
                decision = await apply_telegram_delivery_provider_outcome(
                    db,
                    current_server=current_server(),
                    outcome_id=outcome_id,
                    retry_after_safety_seconds=_retry_after_safety_seconds(),
                    retry_base_seconds=max(
                        0.1,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_retry_base_seconds",
                                1.0,
                            )
                        ),
                    ),
                    retry_max_seconds=max(
                        0.1,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_retry_max_seconds",
                                300.0,
                            )
                        ),
                    ),
                    retry_jitter_ratio=float(
                        getattr(
                            settings,
                            "telegram_delivery_queue_retry_jitter_ratio",
                            0.2,
                        )
                    ),
                    global_rate_limit_window_seconds=max(
                        0.001,
                        float(
                            getattr(
                                settings,
                                "telegram_delivery_queue_global_rate_limit_window_seconds",
                                2.0,
                            )
                        ),
                    ),
                    feedback=routed_feedback,
                )
                await db.commit()
            if decision.outcome not in {
                TelegramDeliveryOutcome.ALREADY_RESOLVED,
                TelegramDeliveryOutcome.STALE_LEASE,
            }:
                applied_count += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with AsyncSessionLocal() as failure_db:
                await record_telegram_provider_outcome_apply_failure(
                    failure_db,
                    current_server=current_server(),
                    outcome_id=outcome_id,
                    error=exc,
                )
                await failure_db.commit()
            logger.warning(
                "Telegram provider outcome replay deferred",
                extra={
                    "event": "telegram_delivery.provider_outcome_replay_deferred",
                    "outcome_id": outcome_id,
                    "error_class": type(exc).__name__,
                },
            )
    return applied_count


async def telegram_provider_outcome_replay_loop(
    *,
    lifecycle_feedbacks: Mapping[str, TelegramQueueLifecycleFeedback],
) -> None:
    while True:
        await run_telegram_provider_outcome_replay_cycle(
            lifecycle_feedbacks=lifecycle_feedbacks,
        )
        await asyncio.sleep(_worker_interval_seconds())


async def telegram_delivery_reconciliation_loop(
    *,
    lanes: Sequence[TelegramDeliveryQueueLaneSpec],
) -> None:
    validators = {lane.bot_identity: lane.freshness_validator for lane in lanes}
    freshness_feedbacks = {
        lane.bot_identity: lane.lifecycle_feedback.apply_freshness for lane in lanes
    }
    result_feedbacks = {
        lane.bot_identity: lane.lifecycle_feedback.apply_delivery_result for lane in lanes
    }
    while True:
        async with AsyncSessionLocal() as db:
            report = await reconcile_telegram_delivery_jobs(
                db,
                current_server=current_server(),
                freshness_validators=validators,
                freshness_feedbacks=freshness_feedbacks,
                result_feedbacks=result_feedbacks,
                ambiguity_grace_seconds=max(
                    1.0,
                    float(
                        getattr(
                            settings,
                            "telegram_delivery_queue_ambiguity_grace_seconds",
                            30.0,
                        )
                    ),
                ),
                max_rows=_recover_limit(),
            )
            await db.commit()
        if report.unresolved_count or report.configuration_blocked_count:
            logger.warning(
                "Telegram delivery reconciliation requires attention",
                extra={
                    "event": "telegram_delivery.reconciliation_attention",
                    "inspected_count": report.inspected_count,
                    "unresolved_count": report.unresolved_count,
                    "configuration_blocked_count": report.configuration_blocked_count,
                    "pending_provider_outcome_count": report.pending_provider_outcome_count,
                },
            )
        await asyncio.sleep(_worker_interval_seconds())


def _telegram_delivery_lane_start_mode(
    lane: TelegramDeliveryQueueLaneSpec,
    *,
    rehydration: TelegramDeliveryLimiterRehydrationReport,
    channel_destination_key: str,
) -> tuple[bool, bool]:
    """Return ``(may_start, identity_only_preflight)`` for one bot lane."""
    if rehydration.gateway_blocked:
        return False, False
    if lane.bot_identity in set(rehydration.blocked_bot_identities):
        return False, False
    channel_globally_gated = channel_destination_key in {
        *rehydration.cooldown_destination_keys,
        *rehydration.hard_blocked_destination_keys,
    }
    channel_lane_gated = (
        lane.bot_identity,
        channel_destination_key,
    ) in set(rehydration.hard_blocked_bot_destinations)
    if channel_globally_gated or channel_lane_gated:
        if lane.bot_identity == TELEGRAM_PRIMARY_BOT_IDENTITY:
            return True, True
        return False, False
    return True, False


def _assert_preflight_lane_match(
    preflight_report: Any,
    *,
    expected_bot_identities: tuple[str, ...],
) -> None:
    report_identity_roles = tuple(
        identity.bot_identity for identity in preflight_report.identities
    )
    if (
        preflight_report.approved_bot_identities != expected_bot_identities
        or report_identity_roles != expected_bot_identities
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_preflight_lane_mismatch"
        )


async def _telegram_delivery_deferred_lane_activation_loop(
    lane: TelegramDeliveryQueueLaneSpec,
    *,
    credential_registry: TelegramDeliveryCredentialRegistry,
    channel_destination_key: str,
    initial_rehydration: TelegramDeliveryLimiterRehydrationReport | None = None,
) -> None:
    """Keep one lane independently supervised until safe activation succeeds."""
    rehydration = initial_rehydration
    retry_delay = _worker_interval_seconds()
    while True:
        try:
            if rehydration is None:
                rehydration = await rehydrate_telegram_delivery_limiter_state(
                    lane.dispatch_limiter
                )
            may_start, identity_only = _telegram_delivery_lane_start_mode(
                lane,
                rehydration=rehydration,
                channel_destination_key=channel_destination_key,
            )
            if not may_start:
                rehydration = None
                await asyncio.sleep(_worker_interval_seconds())
                continue
            if not await lane.dispatch_limiter.prepare_preflight(lane.bot_identity):
                # A Redis probe may have linearized immediately before its
                # durable dispatch marker. Wait for that owner or its lease;
                # preflight must never bypass the single-probe gate.
                rehydration = None
                await asyncio.sleep(_worker_interval_seconds())
                continue
            selected = (lane.bot_identity,)
            preflight_report = await run_configured_telegram_delivery_preflight(
                settings=settings,
                credential_registry=credential_registry,
                bot_identities=selected,
                identity_only_bot_identities=selected if identity_only else (),
            )
            _assert_preflight_lane_match(
                preflight_report,
                expected_bot_identities=selected,
            )

            # Re-read durable gates after all network readbacks. A 429, revoke,
            # or operator pause may have committed while preflight was running.
            post_preflight = await rehydrate_telegram_delivery_limiter_state(
                lane.dispatch_limiter
            )
            still_may_start, still_identity_only = _telegram_delivery_lane_start_mode(
                lane,
                rehydration=post_preflight,
                channel_destination_key=channel_destination_key,
            )
            if (
                not still_may_start
                or still_identity_only != identity_only
                or not await lane.dispatch_limiter.preflight_gate_open(
                    lane.bot_identity
                )
            ):
                rehydration = post_preflight
                await asyncio.sleep(_worker_interval_seconds())
                continue
            await _persist_preflight_success_gate(
                bot_identity=lane.bot_identity,
                report=preflight_report,
            )
            logger.info(
                "Telegram delivery lane preflight approved",
                extra={
                    "event": "telegram_delivery_queue_lane.preflight_approved",
                    "bot_role": lane.bot_identity,
                    "identity_only": identity_only,
                    "channel_fingerprint": preflight_report.channel_fingerprint,
                    "bot_fingerprint": preflight_report.identities[0].bot_fingerprint,
                    "credential_fingerprint": (
                        preflight_report.identities[0].credential_fingerprint
                    ),
                    "permission_readback": (
                        preflight_report.identities[0].effective_permissions
                    ),
                    "restored_limiter_evidence_count": rehydration.restored_count,
                },
            )
            retry_delay = _worker_interval_seconds()
            if identity_only:
                await telegram_delivery_private_only_lane_loop(
                    lane,
                    channel_destination_key=channel_destination_key,
                )
            else:
                await telegram_delivery_queue_lane_loop(lane)
            rehydration = None
        except asyncio.CancelledError:
            raise
        except TelegramDeliveryPreflightRateLimitedError as exc:
            retry_delay = max(
                _worker_interval_seconds(),
                exc.retry_after_seconds + _retry_after_safety_seconds(),
            )
            rate_limited_identity = str(exc.bot_identity or lane.bot_identity)
            durable_cooldown_until = await _persist_preflight_rate_limit_gate(
                bot_identity=rate_limited_identity,
                retry_after_seconds=exc.retry_after_seconds,
                retry_after_source=exc.retry_after_source,
            )
            try:
                await lane.dispatch_limiter.extend_bot_cooldown(
                    rate_limited_identity,
                    until=durable_cooldown_until,
                )
            except asyncio.CancelledError:
                raise
            except Exception as limiter_exc:
                # The controller still waits for Telegram's full deadline. The
                # limiter marks itself fail-closed, so a later iteration cannot
                # activate the lane until Redis is healthy and state is rebuilt.
                logger.warning(
                    "Telegram preflight cooldown persistence failed; lane remains deferred",
                    extra={
                        "event": (
                            "telegram_delivery_queue_lane."
                            "preflight_cooldown_persistence_failed"
                        ),
                        "bot_role": lane.bot_identity,
                        "error_class": type(limiter_exc).__name__,
                        "retry_delay_seconds": retry_delay,
                    },
                )
            logger.warning(
                "Telegram delivery lane preflight rate limited; honoring retry_after",
                extra={
                    "event": "telegram_delivery_queue_lane.preflight_rate_limited",
                    "bot_role": lane.bot_identity,
                    "error_class": type(exc).__name__,
                    "retry_delay_seconds": retry_delay,
                },
            )
            rehydration = None
            await asyncio.sleep(retry_delay)
            retry_delay = _worker_interval_seconds()
        except Exception as exc:
            logger.warning(
                "Telegram delivery lane activation failed; keeping lane deferred",
                extra={
                    "event": "telegram_delivery_queue_lane.activation_retry",
                    "bot_role": lane.bot_identity,
                    "error_class": type(exc).__name__,
                    "retry_delay_seconds": retry_delay,
                },
            )
            rehydration = None
            await asyncio.sleep(retry_delay)
            retry_delay = min(30.0, max(_worker_interval_seconds(), retry_delay * 2.0))


async def telegram_delivery_queue_loop(
    *,
    freshness_validators: Mapping[str, TelegramQueueFreshnessValidator] | None = None,
    lifecycle_feedbacks: Mapping[str, TelegramQueueLifecycleFeedback] | None = None,
    credential_registry: TelegramDeliveryCredentialRegistry | None = None,
    dispatch_limiter: TelegramDeliveryDispatchLimiter | None = None,
    bot_identities: Sequence[str] | None = None,
) -> None:
    """Supervise independent bot lanes under the single queue-v1 owner."""
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    if credential_registry is None:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_credential_registry_not_installed"
        )
    lanes = build_telegram_delivery_queue_lane_specs(
        freshness_validators=freshness_validators,
        lifecycle_feedbacks=lifecycle_feedbacks,
        gateway_calls=credential_registry.build_gateway_calls(),
        dispatch_limiter=dispatch_limiter,
        bot_identities=bot_identities,
    )
    limiter_rehydration = await rehydrate_telegram_delivery_limiter_state(
        dispatch_limiter
    )
    configured_channel_id = getattr(settings, "channel_id", None)
    try:
        normalized_channel_id = int(configured_channel_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_preflight_channel_destination_invalid"
        ) from exc
    if normalized_channel_id == 0:
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_preflight_channel_destination_invalid"
        )
    channel_destination_key = f"channel:{normalized_channel_id}"
    logger.info(
        "Shared Telegram delivery queue supervisor started",
        extra={
            "event": "telegram_delivery_queue_worker.started",
            "bot_roles": tuple(lane.bot_identity for lane in lanes),
            "lane_count": len(lanes),
            "restored_limiter_evidence_count": limiter_rehydration.restored_count,
        },
    )
    tasks = [
        asyncio.create_task(
            _telegram_delivery_deferred_lane_activation_loop(
                lane,
                credential_registry=credential_registry,
                channel_destination_key=channel_destination_key,
                initial_rehydration=limiter_rehydration,
            ),
            name=f"telegram-delivery-supervised-lane:{lane.bot_identity}",
        )
        for lane in lanes
    ]
    tasks.append(
        asyncio.create_task(
            telegram_delivery_queue_recovery_loop(),
            name="telegram-delivery-lease-recovery",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_delivery_reconciliation_loop(lanes=lanes),
            name="telegram-delivery-reconciliation",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_provider_outcome_replay_loop(
                lifecycle_feedbacks={
                    lane.bot_identity: lane.lifecycle_feedback for lane in lanes
                }
            ),
            name="telegram-delivery-provider-outcome-replay",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_delivery_retention_loop(),
            name="telegram-delivery-retention",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_offer_queue_handoff_loop(),
            name="telegram-offer-queue-feeder",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_trade_result_queue_handoff_loop(),
            name="telegram-trade-result-queue-feeder",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_admin_broadcast_queue_handoff_loop(),
            name="telegram-admin-broadcast-queue-feeder",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_notification_outbox_queue_handoff_loop(),
            name="telegram-notification-outbox-queue-feeder",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_market_notice_queue_handoff_loop(),
            name="telegram-market-notice-queue-feeder",
        )
    )
    tasks.append(
        asyncio.create_task(
            telegram_scheduled_operation_queue_handoff_loop(),
            name="telegram-scheduled-operation-queue-feeder",
        )
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
