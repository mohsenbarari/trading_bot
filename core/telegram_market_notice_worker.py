"""Credentialed legacy worker for durable market-channel notices."""
from __future__ import annotations

import asyncio
import logging
import time

from core.background_job_authority import (
    JOB_TELEGRAM_MARKET_NOTICE_DELIVERY,
    assert_background_job_authority,
)
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.market_transition_service import (
    reconcile_due_market_channel_notice_receipts,
    reconcile_market_channel_notice_for_current_state,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    configured_telegram_delivery_runtime,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)
MARKET_NOTICE_WORKER_INTERVAL_SECONDS = 5.0


def _assert_legacy_owner() -> None:
    runtime = configured_telegram_delivery_runtime()
    if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
        raise TelegramDeliveryRuntimeConfigurationError(
            "legacy_market_notice_worker_is_not_runtime_owner"
        )


async def run_telegram_market_notice_cycle() -> None:
    _assert_legacy_owner()
    assert_background_job_authority(JOB_TELEGRAM_MARKET_NOTICE_DELIVERY)
    async with AsyncSessionLocal() as db:
        await reconcile_market_channel_notice_for_current_state(
            db,
            source="credentialed_bot_market_notice",
        )
        await reconcile_due_market_channel_notice_receipts(
            db,
            source="credentialed_bot_market_notice_retry",
        )


async def telegram_market_notice_delivery_loop() -> None:
    _assert_legacy_owner()
    assert_background_job_authority(JOB_TELEGRAM_MARKET_NOTICE_DELIVERY)
    iteration = 0
    while True:
        iteration += 1
        started = time.perf_counter()
        with job_context(
            JOB_TELEGRAM_MARKET_NOTICE_DELIVERY,
            iteration=iteration,
        ) as run_id:
            try:
                await run_telegram_market_notice_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _loop_errors.log(
                    logger,
                    "Error in Telegram market notice worker: %s",
                    exc,
                    job_name=JOB_TELEGRAM_MARKET_NOTICE_DELIVERY,
                    run_id=run_id,
                    duration_ms=duration_ms_since(started),
                )
        await asyncio.sleep(MARKET_NOTICE_WORKER_INTERVAL_SECONDS)
