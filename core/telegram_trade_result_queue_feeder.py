"""Queue-mode coordinator for Telegram trade-result receipts."""
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
from core.services.telegram_trade_result_queue_service import (
    handoff_next_due_trade_result_receipt,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_runtime,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


class TelegramTradeResultQueueFeederOwnershipError(RuntimeError):
    """Raised before DB access when queue-v1 is not the execution owner."""


@dataclass(frozen=True, slots=True)
class TelegramTradeResultQueueFeederReport:
    processed_count: int
    disposition_counts: dict[str, int]


def _assert_queue_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if (
        runtime.mode != TelegramDeliveryRuntimeMode.QUEUE_V1
        or not runtime.queue_worker_enabled
        or runtime.legacy_workers_enabled
    ):
        raise TelegramTradeResultQueueFeederOwnershipError(
            "telegram_trade_result_feeder_is_not_runtime_owner"
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


async def run_telegram_trade_result_queue_handoff_cycle(
    *,
    limit: int | None = None,
) -> TelegramTradeResultQueueFeederReport:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    counts: dict[str, int] = {}
    processed = 0
    for _ in range(_batch_limit(limit)):
        async with AsyncSessionLocal() as db:
            result = await handoff_next_due_trade_result_receipt(
                db,
                current_server=current_server(),
            )
            if result is None:
                await db.rollback()
                break
            await db.commit()
        counts[result.disposition] = counts.get(result.disposition, 0) + 1
        processed += 1
    return TelegramTradeResultQueueFeederReport(
        processed_count=processed,
        disposition_counts=counts,
    )


async def telegram_trade_result_queue_handoff_loop() -> None:
    assert_background_job_authority(JOB_TELEGRAM_DELIVERY_QUEUE)
    _assert_queue_owner()
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context(JOB_TELEGRAM_DELIVERY_QUEUE, iteration=iteration) as run_id:
            try:
                report = await run_telegram_trade_result_queue_handoff_cycle()
                if report.processed_count:
                    logger.info(
                        "Telegram trade-result handoff cycle completed",
                        extra={
                            "event": "telegram_trade_result_queue_feeder.completed",
                            "job_name": JOB_TELEGRAM_DELIVERY_QUEUE,
                            "run_id": run_id,
                            "iteration": iteration,
                            "processed_count": report.processed_count,
                            "disposition_counts": report.disposition_counts,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except asyncio.CancelledError:
                raise
            except TelegramTradeResultQueueFeederOwnershipError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram trade-result queue feeder: %s",
                    exc,
                    job_name=JOB_TELEGRAM_DELIVERY_QUEUE,
                    run_id=run_id,
                    iteration=iteration,
                    duration_ms=duration_ms_since(start_time),
                )
        await asyncio.sleep(_interval_seconds())
