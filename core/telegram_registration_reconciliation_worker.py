"""Foreign-only durable reconciliation job for ready Telegram registration intents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging
import time

from core.audit_logger import audit_log
from core.background_job_authority import (
    JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.registration_contracts import (
    TelegramRegistrationCommandResponse,
    TelegramRegistrationOutcome,
)
from core.services.telegram_registration_intent_service import (
    RegistrationProjectionResolution,
    SUCCESS_OUTCOME_TO_STATUS,
    TelegramRegistrationIntentAttempt,
    claim_due_registration_intents,
    finalize_registration_intent,
    registration_projection_is_ready,
    schedule_registration_intent_retry,
)
from core.telegram_registration_transport import forward_telegram_registration_command
from core.utils import utc_now


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
_WORKER_INTERVAL_SECONDS = 1.0
_WORKER_LEASE_SECONDS = 30
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 300.0
_SYNC_WAIT_SECONDS = 5.0
_SYNC_POLL_SECONDS = 0.25
_PERSISTENT_RETRY_ERRORS = frozenset(
    {
        "authentication_configuration",
        "mixed_version_or_route",
        "protocol_invalid_response",
        "protocol_command_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class TelegramRegistrationReconciliationCycleReport:
    claimed_count: int
    status_counts: dict[str, int]


def _batch_size(limit: int | None = None) -> int:
    configured = limit if limit is not None else settings.telegram_registration_job_batch_size
    return min(100, max(1, int(configured)))


def _concurrency() -> int:
    return min(10, max(1, int(settings.telegram_registration_job_concurrency)))


def _retry_delay_seconds(intent_id: object, attempt: int) -> float:
    exponent = min(8, max(0, int(attempt) - 1))
    base = min(_RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * (2**exponent))
    digest = hashlib.sha256(f"{intent_id}:{attempt}".encode("utf-8")).digest()
    jitter_ratio = int.from_bytes(digest[:2], "big") / 65535
    return min(_RETRY_MAX_SECONDS, base + (base * 0.25 * jitter_ratio))


async def _wait_for_projection(
    attempt: TelegramRegistrationIntentAttempt,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> RegistrationProjectionResolution | None:
    deadline = asyncio.get_running_loop().time() + max(0.0, float(timeout_seconds))
    while True:
        async with AsyncSessionLocal() as db:
            resolution = await registration_projection_is_ready(db, command=attempt.command)
            if resolution is not None:
                return resolution
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(max(0.01, float(poll_seconds)), remaining))


async def _schedule_retry(
    attempt: TelegramRegistrationIntentAttempt,
    *,
    error_code: str,
    authoritative_user_id: int | None = None,
) -> str:
    if error_code in _PERSISTENT_RETRY_ERRORS and attempt.attempt >= 3:
        logger.error(
            "Persistent Telegram registration reconciliation incompatibility",
            extra={
                "event": "telegram_registration.persistent_retry_error",
                "error_class": error_code,
                "attempt": attempt.attempt,
                "command_id": str(attempt.command.command_id),
            },
        )
    retry_at = utc_now() + timedelta(
        seconds=_retry_delay_seconds(attempt.intent_id, attempt.attempt)
    )
    async with AsyncSessionLocal() as db:
        updated = await schedule_registration_intent_retry(
            db,
            intent_id=attempt.intent_id,
            attempt=attempt.attempt,
            error_code=error_code,
            next_retry_at=retry_at,
            authoritative_user_id=authoritative_user_id,
        )
        await db.commit()
    return "retry_wait" if updated else "stale_attempt"


async def _process_attempt(
    attempt: TelegramRegistrationIntentAttempt,
    *,
    sync_wait_seconds: float,
    sync_poll_seconds: float,
) -> str:
    status_code, body = await forward_telegram_registration_command(attempt.command)
    try:
        response = TelegramRegistrationCommandResponse.model_validate(body)
    except (TypeError, ValueError):
        if status_code in {401, 403}:
            error_code = "authentication_configuration"
        elif status_code == 404:
            error_code = "mixed_version_or_route"
        elif status_code >= 500:
            error_code = "transport_or_server_outage"
        else:
            error_code = "protocol_invalid_response"
        return await _schedule_retry(attempt, error_code=error_code)
    if response.command_id != attempt.command.command_id:
        return await _schedule_retry(attempt, error_code="protocol_command_mismatch")
    if status_code >= 500:
        return await _schedule_retry(attempt, error_code="transport_or_server_outage")
    if response.outcome == TelegramRegistrationOutcome.FEATURE_DISABLED:
        return await _schedule_retry(attempt, error_code="mixed_version_or_feature_disabled")
    if not response.terminal:
        return await _schedule_retry(attempt, error_code="remote_nonterminal")
    if status_code in {401, 403}:
        return await _schedule_retry(attempt, error_code="authentication_configuration")
    if status_code == 404:
        return await _schedule_retry(attempt, error_code="mixed_version_or_route")

    if response.outcome in SUCCESS_OUTCOME_TO_STATUS:
        if response.authoritative_user_id is None:
            return await _schedule_retry(attempt, error_code="success_user_missing")
        projection = await _wait_for_projection(
            attempt,
            timeout_seconds=sync_wait_seconds,
            poll_seconds=sync_poll_seconds,
        )
        if projection is None:
            return await _schedule_retry(
                attempt,
                error_code="projection_pending",
                authoritative_user_id=response.authoritative_user_id,
            )

    async with AsyncSessionLocal() as db:
        updated = await finalize_registration_intent(
            db,
            intent_id=attempt.intent_id,
            attempt=attempt.attempt,
            outcome=response.outcome,
            authoritative_user_id=response.authoritative_user_id,
            projected_user_id=(projection.local_user_id if response.outcome in SUCCESS_OUTCOME_TO_STATUS else None),
        )
        await db.commit()
    if not updated:
        return "stale_attempt"
    audit_log(
        (
            f"telegram_registration.reconciled_{response.outcome.value}"
            if response.outcome in SUCCESS_OUTCOME_TO_STATUS
            else "telegram_registration.rejected"
        ),
        target_type="telegram_registration_intent",
        target_id=str(attempt.intent_id),
        result=("success" if response.authoritative_user_id is not None else "denied"),
        extra={"outcome": response.outcome.value},
    )
    return response.outcome.value


async def run_telegram_registration_reconciliation_cycle(
    *,
    limit: int | None = None,
    sync_wait_seconds: float = _SYNC_WAIT_SECONDS,
    sync_poll_seconds: float = _SYNC_POLL_SECONDS,
) -> TelegramRegistrationReconciliationCycleReport:
    assert_background_job_authority(JOB_TELEGRAM_REGISTRATION_RECONCILIATION)
    if not settings.telegram_registration_reconciliation_enabled:
        raise RuntimeError("telegram_registration_reconciliation_disabled")
    async with AsyncSessionLocal() as db:
        attempts = await claim_due_registration_intents(
            db,
            limit=_batch_size(limit),
            lease_seconds=_WORKER_LEASE_SECONDS,
        )
        await db.commit()

    semaphore = asyncio.Semaphore(_concurrency())

    async def process(attempt: TelegramRegistrationIntentAttempt) -> str:
        async with semaphore:
            try:
                return await _process_attempt(
                    attempt,
                    sync_wait_seconds=sync_wait_seconds,
                    sync_poll_seconds=sync_poll_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Telegram registration intent processing failed",
                    extra={
                        "event": "telegram_registration.intent_processing_failed",
                        "command_id": str(attempt.command.command_id),
                        "attempt": attempt.attempt,
                        "error_type": type(exc).__name__,
                    },
                )
                return await _schedule_retry(attempt, error_code="processing_error")

    results = await asyncio.gather(*(process(attempt) for attempt in attempts))
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result] = status_counts.get(result, 0) + 1
    return TelegramRegistrationReconciliationCycleReport(
        claimed_count=len(attempts),
        status_counts=status_counts,
    )


async def telegram_registration_reconciliation_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_REGISTRATION_RECONCILIATION)
    if not settings.telegram_registration_reconciliation_enabled:
        raise RuntimeError("telegram_registration_reconciliation_disabled")
    logger.info(
        "Telegram registration reconciliation worker started",
        extra={
            "event": "telegram_registration.worker_started",
            "batch_size": _batch_size(),
            "concurrency": _concurrency(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        started = time.perf_counter()
        with job_context(JOB_TELEGRAM_REGISTRATION_RECONCILIATION, iteration=iteration) as run_id:
            try:
                report = await run_telegram_registration_reconciliation_cycle()
                if report.claimed_count:
                    logger.info(
                        "Telegram registration reconciliation cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                            "run_id": run_id,
                            "iteration": iteration,
                            "claimed_count": report.claimed_count,
                            "status_counts": report.status_counts,
                            "duration_ms": duration_ms_since(started),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram registration reconciliation loop: %s",
                    exc,
                    job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                    run_id=run_id,
                )
        await asyncio.sleep(_WORKER_INTERVAL_SECONDS)
