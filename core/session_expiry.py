import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, and_
from core.db import AsyncSessionLocal
from models.session import UserSession
from core.services.session_service import deactivate_session, promote_next_primary

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 3600  # check every hour

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
    
    while True:
        try:
            await expire_stale_sessions()
        except Exception as e:
            logger.error(f"❌ Error in session expiry loop: {e}")
        
        await asyncio.sleep(CHECK_INTERVAL)
