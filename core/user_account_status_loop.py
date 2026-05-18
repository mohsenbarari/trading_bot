import asyncio
import logging

from core.db import AsyncSessionLocal
from core.services.user_account_status_service import (
    MESSENGER_BLOCK_LOOP_INTERVAL_SECONDS,
    mark_due_users_messenger_blocked,
)


logger = logging.getLogger(__name__)


async def finalize_due_user_messenger_blocks() -> int:
    async with AsyncSessionLocal() as db:
        blocked_count = await mark_due_users_messenger_blocked(db)
        if blocked_count > 0:
            await db.commit()
            logger.info("⏰ Finalized messenger block for %s inactive users.", blocked_count)
        return blocked_count


async def user_account_status_loop() -> None:
    logger.info(
        "⏰ User account-status loop started (check every %ss)",
        MESSENGER_BLOCK_LOOP_INTERVAL_SECONDS,
    )
    while True:
        try:
            await finalize_due_user_messenger_blocks()
        except Exception as exc:
            logger.error("❌ Error in user account-status loop: %s", exc)

        await asyncio.sleep(MESSENGER_BLOCK_LOOP_INTERVAL_SECONDS)