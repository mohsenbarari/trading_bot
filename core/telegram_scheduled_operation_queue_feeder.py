"""Queue-mode feeder for bounded scheduled Telegram source receipts."""
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
from core.services.telegram_scheduled_operation_service import (
    handoff_next_due_telegram_scheduled_operation,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


class TelegramScheduledOperationQueueFeederOwnershipError(RuntimeError):
    """Raised before DB access when queue-v1 is not the execution owner."""


@dataclass(frozen=True, slots=True)
class TelegramScheduledOperationQueueFeederReport:
    processed_count: int
    disposition_counts: dict[str, int]


def _assert_queue_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if (
        runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1
        or not runtime.queue_worker_enabled
        or runtime.legacy_workers_enabled
    ):
        raise TelegramScheduledOperationQueueFeederOwnershipError(
            "telegram_scheduled_operation_feeder_is_not_runtime_owner"
        )


def _channel_id() -> int:
    value = getattr(settings, "channel_id", None)
    if isinstance(value, bool):
        raise TelegramScheduledOperationQueueFeederOwnershipError(
            "telegram_scheduled_operation_channel_invalid"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramScheduledOperationQueueFeederOwnershipError(
            "telegram_scheduled_operation_channel_invalid"
        ) from exc
    if parsed == 0:
        raise TelegramScheduledOperationQueueFeederOwnershipError(
            "telegram_scheduled_operation_channel_invalid"
        )
    return parsed


def _batch_limit(limit: int | None = None) -> int:
    return max(
        1,
        int(
            limit
            if limit is not None
            else getattr(settings, "telegram_delivery_queue_worker_batch_limit", 25)
        ),
    )


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


async def run_telegram_scheduled_operation_queue_handoff_cycle(
    *,
    limit: int | None = None,
) -> TelegramScheduledOperationQueueFeederReport:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    channel_id = _channel_id()
    counts: dict[str, int] = {}
    processed = 0
    for _ in range(_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await handoff_next_due_telegram_scheduled_operation(
                db,
                current_server=current_server(),
                expected_channel_id=channel_id,
            )
            if result is None:
                await db.rollback()
                break
            await db.commit()
        counts[result.disposition] = counts.get(result.disposition, 0) + 1
        processed += 1
    return TelegramScheduledOperationQueueFeederReport(
        processed_count=processed,
        disposition_counts=counts,
    )


async def telegram_scheduled_operation_queue_handoff_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    iteration = 0
    while True:
        iteration += 1
        started = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_scheduled_operation_queue_handoff_cycle()
                if report.processed_count:
                    logger.info(
                        "Telegram scheduled-operation handoff cycle completed",
                        extra={
                            "event": "telegram_scheduled_operation_queue_feeder.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "disposition_counts": report.disposition_counts,
                            "duration_ms": duration_ms_since(started),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except TelegramScheduledOperationQueueFeederOwnershipError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram scheduled-operation queue feeder: %s",
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(started),
                )
        await asyncio.sleep(_interval_seconds())
