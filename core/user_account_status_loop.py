import asyncio
import logging
import time

from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from core.services.user_account_status_service import (
    GLOBAL_LOCK_LOOP_INTERVAL_SECONDS,
    mark_due_users_globally_locked,
)


logger = logging.getLogger(__name__)
_loop_errors = RepeatedErrorLogger(every=10)


async def finalize_due_user_global_locks() -> int:
    async with AsyncSessionLocal() as db:
        blocked_count = await mark_due_users_globally_locked(db)
        if blocked_count > 0:
            await db.commit()
            logger.info("⏰ Finalized global web lock for %s inactive users.", blocked_count)
        return blocked_count


async def finalize_due_user_messenger_blocks() -> int:
    return await finalize_due_user_global_locks()


async def user_account_status_loop() -> None:
    logger.info(
        "⏰ User account-status loop started (check every %ss)",
        GLOBAL_LOCK_LOOP_INTERVAL_SECONDS,
    )
    iteration = 0
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context("user_account_status", iteration=iteration) as run_id:
            try:
                blocked_count = await finalize_due_user_global_locks()
                if blocked_count > 0:
                    logger.info(
                        "User account-status cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": "user_account_status",
                            "run_id": run_id,
                            "iteration": iteration,
                            "blocked_count": blocked_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as exc:
                _loop_errors.log(logger, "❌ Error in user account-status loop: %s", exc, job_name="user_account_status", run_id=run_id)

        await asyncio.sleep(GLOBAL_LOCK_LOOP_INTERVAL_SECONDS)
