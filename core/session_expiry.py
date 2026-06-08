import asyncio
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select, and_
from core.db import AsyncSessionLocal
from core.job_logging import RepeatedErrorLogger, duration_ms_since, job_context
from models.session import UserSession
from core.services.session_service import deactivate_session, promote_next_primary

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 3600  # check every hour
_loop_errors = RepeatedErrorLogger(every=10)

async def expire_stale_sessions() -> int:
    """
    Find and deactivate user sessions that have been expired for more than 5 days.
    If the deactivated session was a primary session, promote the oldest active.
    """
    cutoff_time = datetime.utcnow() - timedelta(days=5)
    
    count = 0
    async with AsyncSessionLocal() as db:
        stmt = (
            select(UserSession)
            .where(
                and_(
                    UserSession.is_active == True,
                    UserSession.expires_at < cutoff_time
                )
            )
        )
        result = await db.execute(stmt)
        expired_sessions = result.scalars().all()
        
        if not expired_sessions:
            return 0
            
        for session in expired_sessions:
            was_primary = session.is_primary
            await deactivate_session(db, session)
            if was_primary:
                await promote_next_primary(db, session.user_id)
            count += 1
            
        await db.commit()
        
        if count > 0:
            logger.info(f"⏰ Auto-deactivated {count} stale user sessions.")
            
    return count

async def session_expiry_loop() -> None:
    """
    Background loop that periodically checks and deactivates stale sessions.
    """
    logger.info(f"⏰ Session expiry loop started (check every {CHECK_INTERVAL}s)")
    iteration = 0
    
    while True:
        iteration += 1
        start_time = time.perf_counter()
        with job_context("session_expiry", iteration=iteration) as run_id:
            try:
                expired_count = await expire_stale_sessions()
                if expired_count > 0:
                    logger.info(
                        "Session expiry cycle completed",
                        extra={
                            "event": "job.cycle.completed",
                            "job_name": "session_expiry",
                            "run_id": run_id,
                            "iteration": iteration,
                            "expired_count": expired_count,
                            "duration_ms": duration_ms_since(start_time),
                        },
                    )
            except Exception as e:
                _loop_errors.log(logger, "❌ Error in session expiry loop: %s", e, job_name="session_expiry", run_id=run_id)
        
        await asyncio.sleep(CHECK_INTERVAL)
