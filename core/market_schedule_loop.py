import asyncio
import logging
import time

from core.db import AsyncSessionLocal
from core.background_job_authority import JOB_MARKET_SCHEDULE, assert_background_job_authority
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.market_schedule_service import evaluate_market_schedule, get_market_timezone_name
from core.services.market_transition_service import (
    apply_market_schedule_transition,
    load_market_schedule_overrides_window,
)
from core.trading_settings import get_trading_settings_async


logger = logging.getLogger(__name__)

MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS = 15
_loop_errors = RepeatedErrorLogger(every=10)


async def reconcile_market_schedule_runtime(*, current_time=None):
    assert_background_job_authority(JOB_MARKET_SCHEDULE)
    async with AsyncSessionLocal() as db:
        trading_settings = await get_trading_settings_async()
        timezone_name = get_market_timezone_name(trading_settings)
        overrides = await load_market_schedule_overrides_window(
            db,
            timezone_name=timezone_name,
            current_time=current_time,
        )
        evaluation = evaluate_market_schedule(
            trading_settings,
            current_time=current_time,
            overrides=overrides,
        )
        result = await apply_market_schedule_transition(
            db,
            evaluation,
            current_time=current_time,
        )
        if result.changed:
            logger.info(
                "⏰ Market schedule transitioned to %s (expired_offers=%s)",
                result.transition,
                len(result.expired_offer_ids),
            )
        return result


async def market_schedule_loop() -> None:
    logger.info(
        "⏰ Market schedule loop started (check every %ss)",
        MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS,
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context("market_schedule", iteration=iteration) as run_id:
            try:
                result = await reconcile_market_schedule_runtime()
                if getattr(result, "changed", False):
                    logger.info(
                        "Market schedule cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": "market_schedule",
                            "run_id": run_id,
                            "iteration": iteration,
                            "changed": True,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as exc:
                _loop_errors.log(logger, "❌ Error in market schedule loop: %s", exc, job_name="market_schedule", run_id=run_id)

        await asyncio.sleep(MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS)
