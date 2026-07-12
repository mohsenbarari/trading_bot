"""Iran-only background-leader job for due Web-login OTP SMS fallback."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import logging
import time

from core.audit_logger import audit_log
from core.background_job_authority import JOB_OTP_SMS_FALLBACK, assert_background_job_authority
from core.config import settings
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.metrics import observe_otp_fallback_delay, record_otp_event
from core.redis import get_redis_client
from core.registration_observability import (
    load_registration_job_snapshot,
    record_registration_job_snapshot,
    summarize_otp_fallback_queue,
)
from core.services.otp_delivery_state_service import (
    claim_sms_delivery,
    due_otp_request_ids,
    load_otp_delivery_state,
)
from core.services.otp_sms_delivery_service import execute_claimed_otp_sms_delivery
from core.utils import utc_now


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_INTERVAL_SECONDS = 1.0
_BATCH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class OTPFallbackCycleReport:
    due_count: int
    outcome_counts: dict[str, int]


async def run_otp_sms_fallback_cycle(*, limit: int = _BATCH_LIMIT) -> OTPFallbackCycleReport:
    assert_background_job_authority(JOB_OTP_SMS_FALLBACK)
    if not (
        settings.telegram_login_otp_enabled
        and settings.otp_sms_auto_fallback_enabled
    ):
        raise RuntimeError("otp_sms_fallback_disabled")

    redis = get_redis_client()
    request_ids = await due_otp_request_ids(redis, now=utc_now(), limit=limit)
    semaphore = asyncio.Semaphore(
        min(20, max(1, int(settings.otp_sms_fallback_job_concurrency)))
    )

    async def deliver(request_id):
        async with semaphore:
            state = await load_otp_delivery_state(redis, request_id=request_id)
            if state is None:
                return "missing"
            claim = await claim_sms_delivery(
                redis,
                state=state,
                require_due=True,
            )
            if claim is None:
                return "not_claimed"
            audit_log(
                "otp.sms_fallback_claimed",
                target_type="otp_request",
                target_id=str(claim.request_id),
                result="success",
            )
            record_otp_event(event="sms_fallback_claimed")
            if state.sms_fallback_at is not None:
                observe_otp_fallback_delay(
                    (utc_now() - state.sms_fallback_at).total_seconds()
                )
            attempt = await execute_claimed_otp_sms_delivery(redis, claim=claim)
            audit_log(
                "otp.sms_delivery_result",
                target_type="otp_request",
                target_id=str(claim.request_id),
                result=("success" if attempt.outcome.value == "accepted" else "failure"),
                extra={
                    "outcome": attempt.outcome.value,
                    "provider_attempted": attempt.provider_attempted,
                    "result_recorded": attempt.result_recorded,
                },
            )
            record_otp_event(event="sms_delivery_result", outcome=attempt.outcome.value)
            logger.info(
                "OTP SMS fallback delivery completed",
                extra={
                    "event": "otp.sms_delivery_result",
                    "otp_request_id": str(claim.request_id),
                    "outcome": attempt.outcome.value,
                    "provider_attempted": attempt.provider_attempted,
                    "result_recorded": attempt.result_recorded,
                },
            )
            if not attempt.result_recorded:
                return "ambiguous_unrecorded"
            return attempt.outcome.value

    outcomes = await asyncio.gather(*(deliver(request_id) for request_id in request_ids))
    return OTPFallbackCycleReport(
        due_count=len(request_ids),
        outcome_counts=dict(Counter(outcomes)),
    )


async def otp_sms_fallback_loop() -> None:
    assert_background_job_authority(JOB_OTP_SMS_FALLBACK)
    iteration = 0
    while True:
        iteration += 1
        started = time.perf_counter()
        with job_context(JOB_OTP_SMS_FALLBACK, iteration=iteration) as run_id:
            try:
                report = await run_otp_sms_fallback_cycle()
                redis = get_redis_client()
                queue = await summarize_otp_fallback_queue(redis)
                duration_ms = duration_ms_since(started)
                await record_registration_job_snapshot(
                    redis,
                    job_name=JOB_OTP_SMS_FALLBACK,
                    server_mode=settings.server_mode,
                    result="success",
                    pending_count=queue.pending_count,
                    oldest_pending_age_seconds=queue.oldest_pending_age_seconds,
                    batch_size=report.due_count,
                    batch_duration_ms=duration_ms,
                    lag_seconds=queue.lag_seconds,
                )
                if report.due_count:
                    logger.info(
                        "OTP SMS fallback cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_OTP_SMS_FALLBACK,
                            "run_id": run_id,
                            "iteration": iteration,
                            "claimed_count": report.due_count,
                            "status_counts": report.outcome_counts,
                            "duration_ms": duration_ms,
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    redis = get_redis_client()
                    previous = await load_registration_job_snapshot(
                        redis,
                        job_name=JOB_OTP_SMS_FALLBACK,
                    ) or {}
                    await record_registration_job_snapshot(
                        redis,
                        job_name=JOB_OTP_SMS_FALLBACK,
                        server_mode=settings.server_mode,
                        result="error",
                        pending_count=int(previous.get("pending_count") or 0),
                        oldest_pending_age_seconds=float(
                            previous.get("oldest_pending_age_seconds") or 0
                        ),
                        batch_size=0,
                        batch_duration_ms=duration_ms_since(started),
                        lag_seconds=float(previous.get("lag_seconds") or 0),
                        error_code=type(exc).__name__.lower(),
                    )
                except Exception as health_exc:
                    logger.warning(
                        "Could not record OTP fallback job health",
                        extra={
                            "event": "otp.job_health_failed",
                            "error_type": type(health_exc).__name__,
                        },
                    )
                _loop_errors.log(
                    logger,
                    "OTP SMS fallback cycle failed: %s",
                    exc,
                    job_name=JOB_OTP_SMS_FALLBACK,
                    run_id=run_id,
                )
        await asyncio.sleep(_INTERVAL_SECONDS)
