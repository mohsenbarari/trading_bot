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
from typing import Any, Protocol

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
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    defer_unstarted_telegram_delivery_lease,
    load_active_telegram_limiter_evidence,
    load_incomplete_telegram_resume_destination_keys,
    mark_telegram_delivery_dispatch_started,
    recover_expired_telegram_delivery_leases,
    release_unstarted_telegram_delivery_lease,
    resolve_telegram_delivery_result,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
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
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_RESULT_PERSISTENCE_MAX_ATTEMPTS = 3

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


async def rehydrate_telegram_delivery_limiter_state(
    dispatch_limiter: TelegramDeliveryDispatchLimiter,
) -> TelegramDeliveryLimiterRehydrationReport:
    """Rebuild Redis cooldown and hard-pause evidence before any claim."""
    sampled_at = utc_now()
    async with AsyncSessionLocal() as db:
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
        await db.rollback()

    restored = len(incomplete_resume_destinations)
    blocked_bot_identities: set[str] = set()
    cooldown_destination_keys: set[str] = set()
    hard_blocked_destination_keys: set[str] = set(incomplete_resume_destinations)
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
    for evidence in evidence_rows:
        current_time = utc_now()
        observed_at = evidence.observed_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise TelegramDeliveryQueueImplementationIncompleteError(
                "telegram_limiter_evidence_timestamp_invalid"
            )
        if observed_at > current_time:
            observed_at = current_time
        evidence_restored = False
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
            blocked_bot_identities.add(evidence.bot_identity)
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
                hard_blocked_destination_keys.add(evidence.destination_key)
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
    return TelegramDeliveryLimiterRehydrationReport(
        restored_count=restored,
        blocked_bot_identities=tuple(sorted(blocked_bot_identities)),
        cooldown_destination_keys=tuple(sorted(cooldown_destination_keys)),
        hard_blocked_destination_keys=tuple(sorted(hard_blocked_destination_keys)),
        gateway_blocked=gateway_blocked,
    )


async def _recover_expired_leases() -> int:
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
    decision_time: datetime,
) -> tuple[TelegramDeliveryDecision, TelegramDeliveryDecision]:
    """Persist one known provider result without ever repeating the API call.

    A short database/feedback interruption while the process is still alive
    should not discard a definitive 429 or success. An exhausted retry budget
    deliberately leaves the dispatch-marked lease for ambiguous recovery.
    """
    last_classified_decision: TelegramDeliveryDecision | None = None
    for attempt in range(1, _RESULT_PERSISTENCE_MAX_ATTEMPTS + 1):
        decision: TelegramDeliveryDecision | None = None
        try:
            async with AsyncSessionLocal() as db:
                decision = await resolve_telegram_delivery_result(
                    db,
                    current_server=current_server(),
                    job_id=job_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    result=gateway_result,
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
                    now=decision_time,
                )
                await db.commit()
            limiter_decision = (
                last_classified_decision
                if decision.outcome == TelegramDeliveryOutcome.STALE_LEASE
                and last_classified_decision is not None
                else decision
            )
            return decision, limiter_decision
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if (
                decision is not None
                and decision.outcome != TelegramDeliveryOutcome.STALE_LEASE
            ):
                last_classified_decision = decision
            if attempt >= _RESULT_PERSISTENCE_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Retrying Telegram provider-result persistence",
                extra={
                    "event": "telegram_delivery.result_persistence_retry",
                    "job_id": job_id,
                    "attempt": attempt,
                    "max_attempts": _RESULT_PERSISTENCE_MAX_ATTEMPTS,
                    "error_class": type(exc).__name__,
                },
            )
            await asyncio.sleep(0.05 * attempt)

    raise RuntimeError("telegram_delivery_result_persistence_retry_exhausted")


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

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server=current_server(),
                bot_identity=lane_identity,
                worker_id=active_worker_id,
                request_timeout_seconds=_request_timeout_seconds(),
                lease_seconds=_lease_seconds(),
            )
            if job is None:
                await db.rollback()
                break
            await db.commit()

        job_id = int(job.id)
        lease_token = int(job.lease_token)
        current_time = utc_now()
        try:
            async with AsyncSessionLocal() as db:
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
        final_freshness_time = utc_now()
        try:
            async with AsyncSessionLocal() as db:
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
                    now=utc_now(),
                )
                if dispatch_marked:
                    await db.commit()
                else:
                    await db.rollback()
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
        except TelegramDeliveryDispatchDeferredError as exc:
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
            await _release_unused_rate_limit_probe(
                dispatch_limiter=dispatch_limiter,
                job=job,
                admission=admission,
            )
            stale_fence_count += 1
            continue

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
            )

        decision_time = utc_now()
        decision, limiter_decision = await _persist_delivery_result_after_dispatch(
            job_id=job_id,
            worker_id=active_worker_id,
            lease_token=lease_token,
            gateway_result=gateway_result,
            feedback=lifecycle_feedback.apply_delivery_result,
            decision_time=decision_time,
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


async def telegram_delivery_queue_lane_loop(
    lane: TelegramDeliveryQueueLaneSpec,
) -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_runtime_owner()
    logger.info(
        "Telegram delivery execution lane started",
        extra={
            "event": "telegram_delivery_queue_lane.started",
            "bot_role": lane.bot_identity,
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": _worker_batch_limit(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_delivery_queue_cycle(
                    bot_identity=lane.bot_identity,
                    freshness_validator=lane.freshness_validator,
                    lifecycle_feedback=lane.lifecycle_feedback,
                    gateway_call=lane.gateway_call,
                    dispatch_limiter=lane.dispatch_limiter,
                    recover_leases=False,
                )
                if report.processed_count:
                    logger.info(
                        "Telegram delivery lane cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "bot_role": lane.bot_identity,
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
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(start_time),
                )
        await asyncio.sleep(_worker_interval_seconds())


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
    if channel_destination_key in set(rehydration.cooldown_destination_keys):
        return False, False
    if channel_destination_key in set(rehydration.hard_blocked_destination_keys):
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
            await telegram_delivery_queue_lane_loop(lane)
            rehydration = None
        except asyncio.CancelledError:
            raise
        except TelegramDeliveryPreflightRateLimitedError as exc:
            retry_delay = max(
                _worker_interval_seconds(),
                exc.retry_after_seconds + _retry_after_safety_seconds(),
            )
            try:
                await lane.dispatch_limiter.extend_bot_cooldown(
                    lane.bot_identity,
                    until=utc_now() + timedelta(seconds=retry_delay),
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
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
