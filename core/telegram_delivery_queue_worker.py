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
from datetime import datetime
import logging
import math
import os
import socket
import time

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
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    defer_unstarted_telegram_delivery_lease,
    mark_telegram_delivery_dispatch_started,
    recover_expired_telegram_delivery_leases,
    release_unstarted_telegram_delivery_lease,
    resolve_telegram_delivery_result,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryOutcome,
    TelegramFreshnessDecision,
)
from core.telegram_delivery_queue_limiter import (
    TelegramDeliveryDispatchLimiter,
    TelegramDeliveryLimiterUnavailableError,
)
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import run_configured_telegram_delivery_preflight
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)

TelegramQueueGatewayCall = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]
TelegramQueueFreshnessValidator = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[TelegramFreshnessDecision],
]
class TelegramDeliveryQueueImplementationIncompleteError(RuntimeError):
    """Refuses a claim when no authoritative pre-dispatch adapter is installed."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryQueueLaneSpec:
    bot_identity: str
    freshness_validator: TelegramQueueFreshnessValidator
    gateway_call: TelegramQueueGatewayCall
    dispatch_limiter: TelegramDeliveryDispatchLimiter


@dataclass(frozen=True, slots=True)
class TelegramDeliveryQueueCycleReport:
    bot_identity: str
    processed_count: int
    recovered_count: int
    status_counts: dict[str, int]
    stale_fence_count: int


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
    gateways = gateway_calls or {}
    if dispatch_limiter is None or not all(
        callable(getattr(dispatch_limiter, method, None))
        for method in ("acquire", "observe")
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_dispatch_limiter_not_installed"
        )
    specs: list[TelegramDeliveryQueueLaneSpec] = []
    for identity in identities:
        validator = validators.get(identity)
        gateway_call = gateways.get(identity)
        if not callable(validator):
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"authoritative_freshness_validator_not_installed:{identity}"
            )
        if not callable(gateway_call):
            raise TelegramDeliveryQueueImplementationIncompleteError(
                f"telegram_lane_gateway_not_installed:{identity}"
            )
        specs.append(
            TelegramDeliveryQueueLaneSpec(
                bot_identity=identity,
                freshness_validator=validator,
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


async def run_telegram_delivery_queue_cycle(
    *,
    bot_identity: str,
    limit: int | None = None,
    freshness_validator: TelegramQueueFreshnessValidator | None = None,
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
    if dispatch_limiter is None or not all(
        callable(getattr(dispatch_limiter, method, None))
        for method in ("acquire", "observe")
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
            raise
        except Exception as exc:
            await _release_after_predispatch_error(
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                reason=f"final_freshness_validator:{type(exc).__name__}",
            )
            raise

        if not may_dispatch:
            key = freshness.outcome.value
            status_counts[key] = status_counts.get(key, 0) + 1
            processed_count += 1
            continue

        async with AsyncSessionLocal() as db:
            dispatch_marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server=current_server(),
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                now=utc_now(),
            )
            if dispatch_marked:
                await db.commit()
            else:
                await db.rollback()
        if not dispatch_marked:
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
        async with AsyncSessionLocal() as db:
            decision = await resolve_telegram_delivery_result(
                db,
                current_server=current_server(),
                job_id=job_id,
                worker_id=active_worker_id,
                lease_token=lease_token,
                result=gateway_result,
                retry_after_safety_seconds=_retry_after_safety_seconds(),
                retry_base_seconds=max(
                    0.1,
                    float(getattr(settings, "telegram_delivery_queue_retry_base_seconds", 1.0)),
                ),
                retry_max_seconds=max(
                    0.1,
                    float(getattr(settings, "telegram_delivery_queue_retry_max_seconds", 300.0)),
                ),
                now=decision_time,
            )
            await db.commit()
        if decision.outcome == TelegramDeliveryOutcome.STALE_LEASE:
            stale_fence_count += 1
        key = decision.outcome.value
        status_counts[key] = status_counts.get(key, 0) + 1
        processed_count += 1
        await dispatch_limiter.observe(job, decision, now=decision_time)

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


async def telegram_delivery_queue_loop(
    *,
    freshness_validators: Mapping[str, TelegramQueueFreshnessValidator] | None = None,
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
        gateway_calls=credential_registry.build_gateway_calls(),
        dispatch_limiter=dispatch_limiter,
        bot_identities=bot_identities,
    )
    preflight_report = await run_configured_telegram_delivery_preflight(
        settings=settings,
        credential_registry=credential_registry,
    )
    lane_identities = tuple(lane.bot_identity for lane in lanes)
    report_identity_roles = tuple(
        identity.bot_identity for identity in preflight_report.identities
    )
    if (
        preflight_report.approved_bot_identities != lane_identities
        or report_identity_roles != lane_identities
    ):
        raise TelegramDeliveryQueueImplementationIncompleteError(
            "telegram_delivery_preflight_lane_mismatch"
        )
    logger.info(
        "Telegram delivery preflight approved",
        extra={
            "event": "telegram_delivery_queue_preflight.approved",
            "bot_roles": lane_identities,
            "channel_fingerprint": preflight_report.channel_fingerprint,
            "bot_fingerprints": {
                identity.bot_identity: identity.bot_fingerprint
                for identity in preflight_report.identities
            },
            "credential_fingerprints": {
                identity.bot_identity: identity.credential_fingerprint
                for identity in preflight_report.identities
            },
            "permission_readback": {
                identity.bot_identity: identity.effective_permissions
                for identity in preflight_report.identities
            },
        },
    )
    logger.info(
        "Shared Telegram delivery queue supervisor started",
        extra={
            "event": "telegram_delivery_queue_worker.started",
            "bot_roles": lane_identities,
            "lane_count": len(lanes),
            "channel_fingerprint": preflight_report.channel_fingerprint,
        },
    )
    tasks = [
        asyncio.create_task(
            telegram_delivery_queue_lane_loop(lane),
            name=f"telegram-delivery-lane:{lane.bot_identity}",
        )
        for lane in lanes
    ]
    tasks.append(
        asyncio.create_task(
            telegram_delivery_queue_recovery_loop(),
            name="telegram-delivery-lease-recovery",
        )
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
