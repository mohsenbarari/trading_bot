"""Persistent worker for generic Telegram notification outbox delivery."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from core.background_job_authority import (
    JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_DELIVERY_STATUS_NO_ROW,
    claim_and_deliver_next_telegram_notification_outbox,
    recover_expired_telegram_notification_outbox_leases,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


@dataclass(frozen=True)
class TelegramNotificationOutboxCycleReport:
    processed_count: int
    recovered_lease_count: int
    status_counts: dict[str, int]
    alert_count: int


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "telegram_notification_outbox_worker_batch_limit", 50)))


def _worker_lease_seconds() -> int:
    return max(1, int(getattr(settings, "telegram_notification_outbox_worker_lease_seconds", 30)))


def _worker_recover_limit() -> int:
    return max(1, int(getattr(settings, "telegram_notification_outbox_worker_recover_limit", 100)))


def _worker_interval_seconds() -> float:
    return max(0.1, float(getattr(settings, "telegram_notification_outbox_worker_interval_seconds", 1.0)))


def _worker_send_interval_seconds() -> float:
    per_second = max(0.1, float(getattr(settings, "telegram_notification_outbox_worker_max_sends_per_second", 10.0)))
    return 1.0 / per_second


def _increment_status(status_counts: dict[str, int], status: str | None) -> None:
    key = str(status or "unknown")
    status_counts[key] = status_counts.get(key, 0) + 1


async def _recover_leases() -> int:
    async with AsyncSessionLocal() as db:
        recovered = await recover_expired_telegram_notification_outbox_leases(
            db,
            current_server=current_server(),
            lease_seconds=_worker_lease_seconds(),
            max_rows=_worker_recover_limit(),
        )
        if recovered:
            await db.commit()
        return len(recovered)


async def run_telegram_notification_outbox_delivery_cycle(
    *,
    limit: int | None = None,
) -> TelegramNotificationOutboxCycleReport:
    assert_background_job_authority(JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY)
    status_counts: dict[str, int] = {}
    processed_count = 0
    alert_count = 0
    recovered_lease_count = await _recover_leases()
    send_interval = _worker_send_interval_seconds()

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await claim_and_deliver_next_telegram_notification_outbox(
                db,
                current_server=current_server(),
                lease_seconds=_worker_lease_seconds(),
            )
            await db.commit()
        _increment_status(status_counts, result.status)
        if result.status == TELEGRAM_NOTIFICATION_DELIVERY_STATUS_NO_ROW:
            break
        if result.alert_required:
            alert_count += 1
            logger.warning(
                "Telegram notification outbox delivery requires attention",
                extra={
                    "event": "telegram_notification_outbox.delivery_alert",
                    "status": result.status,
                    "reason": result.reason,
                    "outbox_id": getattr(result.outbox, "id", None),
                    "recipient_user_id": result.recipient_user_id,
                },
            )
        processed_count += 1
        await asyncio.sleep(send_interval)

    return TelegramNotificationOutboxCycleReport(
        processed_count=processed_count,
        recovered_lease_count=recovered_lease_count,
        status_counts=status_counts,
        alert_count=alert_count,
    )


async def telegram_notification_outbox_delivery_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY)
    logger.info(
        "Telegram notification outbox worker started",
        extra={
            "event": "telegram_notification_outbox_worker.started",
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": _worker_batch_limit(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY, iteration=iteration) as run_id:
            try:
                report = await run_telegram_notification_outbox_delivery_cycle()
                if report.processed_count or report.recovered_lease_count:
                    logger.info(
                        "Telegram notification outbox worker cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "recovered_lease_count": report.recovered_lease_count,
                            "status_counts": report.status_counts,
                            "alert_count": report.alert_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Telegram notification outbox worker cycle failed",
                    extra={
                        "event": "job.cycle.failed",
                        "job_name": JOB_TELEGRAM_NOTIFICATION_OUTBOX_DELIVERY,
                        "run_id": run_id,
                        "iteration": iteration,
                        "duration_ms": duration_ms_since(start_time),
                        "error_type": type(exc).__name__,
                    },
                    exc_info=True,
                )
        await asyncio.sleep(_worker_interval_seconds())
