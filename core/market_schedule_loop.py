import asyncio
import logging

from core.db import AsyncSessionLocal
from core.services.market_schedule_service import evaluate_market_schedule, get_market_timezone_name
from core.services.market_transition_service import (
    apply_market_schedule_transition,
    load_market_schedule_overrides_window,
)
from core.trading_settings import get_trading_settings_async


logger = logging.getLogger(__name__)

MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS = 15


async def reconcile_market_schedule_runtime(*, current_time=None):
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
    while True:
        try:
            await reconcile_market_schedule_runtime()
        except Exception as exc:
            logger.error("❌ Error in market schedule loop: %s", exc)

        await asyncio.sleep(MARKET_SCHEDULE_LOOP_INTERVAL_SECONDS)