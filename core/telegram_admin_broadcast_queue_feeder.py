"""Queue-mode coordinator for sequential Telegram admin broadcasts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from core.background_job_authority import (
    JOB_TELEGRAM_DELIVERY_QUEUE,
    assert_background_job_authority,
)
from core.config import settings
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.server_routing import current_server
from core.services.telegram_admin_broadcast_queue_service import (
    ADMIN_BROADCAST_QUEUE_HANDOFF,
    handoff_next_due_telegram_admin_broadcast_receipt,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


class TelegramAdminBroadcastQueueFeederOwnershipError(RuntimeError):
    """Raised before DB access when queue-v1 is not the execution owner."""


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastQueueFeederReport:
    processed_count: int
    disposition_counts: dict[str, int]
    active_handoff_count: int


def _assert_queue_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if (
        runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1
        or not runtime.queue_worker_enabled
        or runtime.legacy_workers_enabled
    ):
        raise TelegramAdminBroadcastQueueFeederOwnershipError(
            "telegram_admin_broadcast_feeder_is_not_runtime_owner"
        )


def _batch_limit(limit: int | None = None) -> int:
    configured = limit if limit is not None else getattr(
        settings,
        "telegram_delivery_queue_worker_batch_limit",
        25,
    )
    return max(1, int(configured))


def _interval_seconds() -> float:
    return max(
        0.1,
        float(
            getattr(
                settings,
                "telegram_delivery_queue_worker_interval_seconds",
                1.0,
            )
        ),
    )


async def run_telegram_admin_broadcast_queue_handoff_cycle(
    *,
    limit: int | None = None,
) -> TelegramAdminBroadcastQueueFeederReport:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    counts: dict[str, int] = {}
    processed = 0
    active_handoff_count = 0
    for _ in range(_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await handoff_next_due_telegram_admin_broadcast_receipt(
                db,
                current_server=current_server(),
            )
            if result is None:
                await db.rollback()
                break
            await db.commit()
        counts[result.disposition] = counts.get(result.disposition, 0) + 1
        processed += 1
        if result.disposition == ADMIN_BROADCAST_QUEUE_HANDOFF:
            active_handoff_count += 1
            # Continue only to fill the second global campaign slot. The
            # service itself enforces one active recipient per campaign and
            # the durable round-robin order under an advisory lock.
    return TelegramAdminBroadcastQueueFeederReport(
        processed_count=processed,
        disposition_counts=counts,
        active_handoff_count=active_handoff_count,
    )


async def telegram_admin_broadcast_queue_handoff_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_admin_broadcast_queue_handoff_cycle()
                if report.processed_count:
                    logger.info(
                        "Telegram admin broadcast handoff cycle completed",
                        extra={
                            "event": "telegram_admin_broadcast_queue_feeder.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "disposition_counts": report.disposition_counts,
                            "active_handoff_count": report.active_handoff_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except TelegramAdminBroadcastQueueFeederOwnershipError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram admin broadcast queue feeder: %s",
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(start_time),
                )
        await asyncio.sleep(_interval_seconds())
