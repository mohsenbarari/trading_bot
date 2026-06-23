"""Persistent workers for receipt-backed trade notification delivery."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from core.background_job_authority import (
    JOB_TRADE_TELEGRAM_DELIVERY,
    JOB_TRADE_WEBAPP_DELIVERY,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.trade_delivery_receipt_service import (
    TELEGRAM_DESTINATION_SERVER,
    WEBAPP_DESTINATION_SERVER,
    recover_expired_local_leases,
)
from core.services.trade_telegram_delivery_service import (
    TELEGRAM_DELIVERY_STATUS_NO_RECEIPT,
    claim_and_deliver_next_telegram_receipt,
)
from core.services.trade_webapp_delivery_service import (
    WEBAPP_DELIVERY_STATUS_NO_RECEIPT,
    claim_and_deliver_next_webapp_receipt,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


@dataclass(frozen=True)
class TradeDeliveryCycleReport:
    job_name: str
    destination_server: str
    processed_count: int
    recovered_lease_count: int
    status_counts: dict[str, int]


def _worker_batch_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, int(limit))
    return max(1, int(getattr(settings, "trade_delivery_worker_batch_limit", 50)))


def _worker_lease_seconds() -> int:
    return max(1, int(getattr(settings, "trade_delivery_worker_lease_seconds", 30)))


def _worker_recover_limit() -> int:
    return max(1, int(getattr(settings, "trade_delivery_worker_recover_limit", 100)))


def _worker_interval_seconds() -> float:
    return max(0.1, float(getattr(settings, "trade_delivery_worker_interval_seconds", 1.0)))


def _increment_status(status_counts: dict[str, int], status: str | None) -> None:
    key = str(status or "unknown")
    status_counts[key] = status_counts.get(key, 0) + 1


async def _recover_leases(destination_server: str) -> int:
    async with AsyncSessionLocal() as db:
        recovered = await recover_expired_local_leases(
            db,
            destination_server=destination_server,
            max_rows=_worker_recover_limit(),
        )
        if recovered:
            await db.commit()
        return len(recovered)


async def run_webapp_trade_delivery_cycle(*, limit: int | None = None) -> TradeDeliveryCycleReport:
    assert_background_job_authority(JOB_TRADE_WEBAPP_DELIVERY)
    status_counts: dict[str, int] = {}
    processed_count = 0
    recovered_lease_count = await _recover_leases(WEBAPP_DESTINATION_SERVER)

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await claim_and_deliver_next_webapp_receipt(
                db,
                current_server=current_server(),
                lease_seconds=_worker_lease_seconds(),
            )
        _increment_status(status_counts, result.status)
        if result.status == WEBAPP_DELIVERY_STATUS_NO_RECEIPT:
            break
        processed_count += 1

    return TradeDeliveryCycleReport(
        job_name=JOB_TRADE_WEBAPP_DELIVERY,
        destination_server=WEBAPP_DESTINATION_SERVER,
        processed_count=processed_count,
        recovered_lease_count=recovered_lease_count,
        status_counts=status_counts,
    )


async def run_telegram_trade_delivery_cycle(*, limit: int | None = None) -> TradeDeliveryCycleReport:
    assert_background_job_authority(JOB_TRADE_TELEGRAM_DELIVERY)
    status_counts: dict[str, int] = {}
    processed_count = 0
    recovered_lease_count = await _recover_leases(TELEGRAM_DESTINATION_SERVER)

    for _ in range(_worker_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await claim_and_deliver_next_telegram_receipt(
                db,
                current_server=current_server(),
                lease_seconds=_worker_lease_seconds(),
            )
        _increment_status(status_counts, result.status)
        if result.status == TELEGRAM_DELIVERY_STATUS_NO_RECEIPT:
            break
        processed_count += 1

    return TradeDeliveryCycleReport(
        job_name=JOB_TRADE_TELEGRAM_DELIVERY,
        destination_server=TELEGRAM_DESTINATION_SERVER,
        processed_count=processed_count,
        recovered_lease_count=recovered_lease_count,
        status_counts=status_counts,
    )


async def _trade_delivery_loop(job_name: str, cycle_runner) -> None:
    assert_background_job_authority(job_name)
    logger.info(
        "Trade delivery worker started",
        extra={
            "event": "trade_delivery_worker.started",
            "job_name": job_name,
            "interval_seconds": _worker_interval_seconds(),
            "batch_limit": _worker_batch_limit(),
        },
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(job_name, iteration=iteration) as run_id:
            try:
                report = await cycle_runner()
                if report.processed_count or report.recovered_lease_count:
                    logger.info(
                        "Trade delivery worker cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": job_name,
                            "run_id": run_id,
                            "iteration": iteration,
                            "destination_server": report.destination_server,
                            "processed_count": report.processed_count,
                            "recovered_lease_count": report.recovered_lease_count,
                            "status_counts": report.status_counts,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in trade delivery worker loop: %s",
                    exc,
                    job_name=job_name,
                    run_id=run_id,
                )

        await asyncio.sleep(_worker_interval_seconds())


async def webapp_trade_delivery_loop() -> None:
    await _trade_delivery_loop(JOB_TRADE_WEBAPP_DELIVERY, run_webapp_trade_delivery_cycle)


async def telegram_trade_delivery_loop() -> None:
    await _trade_delivery_loop(JOB_TRADE_TELEGRAM_DELIVERY, run_telegram_trade_delivery_cycle)
