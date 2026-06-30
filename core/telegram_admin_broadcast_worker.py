"""Persistent worker for Telegram admin broadcast delivery."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from core.background_job_authority import (
    JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.telegram_admin_broadcast_delivery_service import (
    TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_NO_RECEIPT,
    claim_and_deliver_next_telegram_admin_broadcast_receipt,
    recover_expired_telegram_admin_broadcast_leases,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


@dataclass(frozen=True)
class TelegramAdminBroadcastCycleReport:
    processed_count: int
    recovered_lease_count: int
    status_counts: dict[str, int]


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "telegram_admin_broadcast_worker_batch_limit", 25)))


def _worker_lease_seconds() -> int:
    return max(1, int(getattr(settings, "telegram_admin_broadcast_worker_lease_seconds", 30)))


def _worker_recover_limit() -> int:
    return max(1, int(getattr(settings, "telegram_admin_broadcast_worker_recover_limit", 100)))


def _worker_interval_seconds() -> float:
    return max(0.1, float(getattr(settings, "telegram_admin_broadcast_worker_interval_seconds", 1.0)))


def _worker_send_interval_seconds() -> float:
    per_second = max(0.1, float(getattr(settings, "telegram_admin_broadcast_worker_max_sends_per_second", 10.0)))
    return 1.0 / per_second


def _increment_status(status_counts: dict[str, int], status: str | None) -> None:
    key = str(status or "unknown")
    status_counts[key] = status_counts.get(key, 0) + 1


async def _recover_leases() -> int:
    async with AsyncSessionLocal() as db:
        recovered = await recover_expired_telegram_admin_broadcast_leases(
            db,
            current_server=current_server(),
            max_rows=_worker_recover_limit(),
        )
        if recovered:
            await db.commit()
        return len(recovered)


async def run_telegram_admin_broadcast_delivery_cycle(
    *,
    limit: int | None = None,
) -> TelegramAdminBroadcastCycleReport:
    assert_background_job_authority(JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY)
    status_counts: dict[str, int] = {}
    processed_count = 0
    recovered_lease_count = await _recover_leases()
    send_interval = _worker_send_interval_seconds()

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await claim_and_deliver_next_telegram_admin_broadcast_receipt(
                db,
                current_server=current_server(),
                lease_seconds=_worker_lease_seconds(),
            )
            await db.commit()
        _increment_status(status_counts, result.status)
        if result.status == TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_NO_RECEIPT:
            break
        processed_count += 1
        await asyncio.sleep(send_interval)

    return TelegramAdminBroadcastCycleReport(
        processed_count=processed_count,
        recovered_lease_count=recovered_lease_count,
        status_counts=status_counts,
    )


async def telegram_admin_broadcast_delivery_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY)
    logger.info(
        "Telegram admin broadcast worker started",
        extra={
            "event": "telegram_admin_broadcast_worker.started",
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": _worker_batch_limit(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY, iteration=iteration) as run_id:
            try:
                report = await run_telegram_admin_broadcast_delivery_cycle()
                if report.processed_count or report.recovered_lease_count:
                    logger.info(
                        "Telegram admin broadcast worker cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "recovered_lease_count": report.recovered_lease_count,
                            "status_counts": report.status_counts,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram admin broadcast worker loop: %s",
                    exc,
                    job_name=JOB_TELEGRAM_ADMIN_BROADCAST_DELIVERY,
                    run_id=run_id,
                )

        await asyncio.sleep(_worker_interval_seconds())
