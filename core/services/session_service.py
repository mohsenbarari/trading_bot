# core/services/session_service.py
"""سرویس مدیریت نشست‌ها و درخواست لاگین"""
import hashlib
import math
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import UserSession, SessionLoginRequest, LoginRequestStatus, Platform
from models.user import User, UserRole

logger = logging.getLogger(__name__)

# Anti-abuse base thresholds
ANTI_ABUSE_BASE = {"daily": 2, "weekly": 5, "monthly": 7}
# Login request approval timeout
LOGIN_REQUEST_TIMEOUT_SECONDS = 120
# Session blacklist TTL: must match access token lifetime (60 min)
SESSION_BLACKLIST_TTL = 3600


def hash_token(token: str) -> str:
    """Hash a refresh token for storage (SHA-256)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def get_effective_max_sessions(user: User) -> int:
    """Get effective max sessions for a user. Admins are locked to 1."""
    if user.role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        return 1
    return min(max(user.max_sessions, 1), 3)


def calculate_threshold(base: int, max_sessions: int) -> int:
    """Threshold = Floor( Base * (1 + 0.5 * (Sessions - 1)) )"""
    return math.floor(base * (1 + 0.5 * (max_sessions - 1)))


async def get_active_sessions(
    db: AsyncSession, user_id: int
) -> List[UserSession]:
    """Get all active sessions for a user, ordered by created_at ASC (oldest first)."""
    stmt = (
        select(UserSession)
        .where(
            and_(
                UserSession.user_id == user_id,
                UserSession.is_active == True,
            )
        )
        .order_by(UserSession.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_session_by_refresh_token(
    db: AsyncSession, refresh_token: str
) -> Optional[UserSession]:
    """Find active session by refresh token hash."""
    token_hash = hash_token(refresh_token)
    stmt = select(UserSession).where(
        and_(
            UserSession.refresh_token_hash == token_hash,
            UserSession.is_active == True,
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_session(
    db: AsyncSession,
    user_id: int,
    refresh_token: str,
    device_name: str = "Unknown Device",
    device_ip: Optional[str] = None,
    platform: Platform = Platform.WEB,
    is_primary: bool = False,
) -> UserSession:
    """Create a new active session."""
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        device_name=device_name,
        device_ip=device_ip,
        platform=platform,
        refresh_token_hash=hash_token(refresh_token),
        is_primary=is_primary,
        is_active=True,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(session)
    await db.flush()
    return session


async def deactivate_session(db: AsyncSession, session: UserSession) -> None:
    """Mark a session as inactive and blacklist its ID."""
    session.is_active = False
    await db.flush()
    await blacklist_session(session.id)


async def promote_next_primary(db: AsyncSession, user_id: int) -> Optional[UserSession]:
    """After primary is removed, promote the oldest remaining active session."""
    sessions = await get_active_sessions(db, user_id)
    if sessions:
        oldest = sessions[0]
        oldest.is_primary = True
        await db.flush()
        return oldest
    return None


async def handle_login_session(
    db: AsyncSession,
    user: User,
    refresh_token: str,
    device_name: str = "Unknown Device",
    device_ip: Optional[str] = None,
    platform: Platform = Platform.WEB,
    suspended_refresh_token: Optional[str] = None,
) -> dict:
    """
    Core login session logic. Returns one of:
    - {"action": "session_created", "session": UserSession}
    - {"action": "approval_required", "request": SessionLoginRequest}
    - {"action": "blocked", "reason": str}
    """
    max_sessions = get_effective_max_sessions(user)
    
    # Attempt to revive suspended session
    if suspended_refresh_token:
        # A suspended session is one where it's still marked is_active=True,
        # but its expires_at has passed (and frontend caught it, leading to this OTP verification).
        # OR it might still be technically within the 30 days but the client triggered a re-login.
        token_hash = hash_token(suspended_refresh_token)
        stmt = select(UserSession).where(
            and_(
                UserSession.user_id == user.id,
                UserSession.refresh_token_hash == token_hash,
                UserSession.is_active == True,
            )
        )
        suspended_session = (await db.execute(stmt)).scalar_one_or_none()
        
        if suspended_session:
            # We revive it: update tokens and device info, extend expiry
            suspended_session.refresh_token_hash = hash_token(refresh_token)
            suspended_session.device_name = device_name
            if device_ip:
                suspended_session.device_ip = device_ip
            suspended_session.platform = platform
            suspended_session.last_active_at = datetime.utcnow()
            suspended_session.expires_at = datetime.utcnow() + timedelta(days=30)
            await db.commit()
            return {"action": "session_created", "session": suspended_session}

    active_sessions = await get_active_sessions(db, user.id)

    # Case 1: No sessions exist → create first session as primary
    if len(active_sessions) == 0:
        session = await create_session(
            db, user.id, refresh_token, device_name, device_ip, platform,
            is_primary=True,
        )
        await db.commit()
        return {"action": "session_created", "session": session}

    # Case 2: Under limit → create new session directly (non-primary)
    if len(active_sessions) < max_sessions:
        session = await create_session(
            db, user.id, refresh_token, device_name, device_ip, platform,
            is_primary=False,
        )
        await db.commit()
        return {"action": "session_created", "session": session}

    # Case 3: At limit → check anti-abuse then create login request
    from bot.utils.redis_helpers import get_redis
    redis = await get_redis()

    # Check anti-abuse thresholds
    for period, base in ANTI_ABUSE_BASE.items():
        threshold = calculate_threshold(base, max_sessions)
        key = f"session_req:{user.id}:{period}"
        count = await redis.get(key)
        count = int(count) if count else 0
        if count >= threshold:
            return {
                "action": "blocked",
                "reason": f"تعداد درخواست‌های ورود بیش از حد مجاز ({period})"
            }

    # Check for existing pending request
    stmt = select(SessionLoginRequest).where(
        and_(
            SessionLoginRequest.user_id == user.id,
            SessionLoginRequest.status == LoginRequestStatus.PENDING,
            SessionLoginRequest.expires_at > datetime.utcnow(),
        )
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        try:
            from core.utils import publish_user_event
            await publish_user_event(user.id, "session:login_request", {
                "request_id": str(existing.id),
                "device_name": existing.requester_device_name or device_name,
                "device_ip": existing.requester_ip or device_ip,
                "expires_at": existing.expires_at.isoformat(),
            })
        except Exception as e:
            logger.warning(f"Failed to publish login request event (existing): {e}")
        return {"action": "approval_required", "request": existing}

    # Create login request
    login_request = SessionLoginRequest(
        id=uuid.uuid4(),
        user_id=user.id,
        requester_device_name=device_name,
        requester_ip=device_ip,
        status=LoginRequestStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(seconds=LOGIN_REQUEST_TIMEOUT_SECONDS),
    )
    db.add(login_request)

    # Increment anti-abuse counters
    ttls = {"daily": 86400, "weekly": 604800, "monthly": 2592000}
    for period, ttl in ttls.items():
        key = f"session_req:{user.id}:{period}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl)
        await pipe.execute()

    await db.commit()

    # Notify primary device(s) via real-time pub/sub
    try:
        from core.utils import publish_user_event
        await publish_user_event(user.id, "session:login_request", {
            "request_id": str(login_request.id),
            "device_name": device_name,
            "device_ip": device_ip,
            "expires_at": login_request.expires_at.isoformat(),
        })
    except Exception as e:
        logger.warning(f"Failed to publish login request event: {e}")

    return {"action": "approval_required", "request": login_request}


async def approve_login_request(
    db: AsyncSession,
    request_id: uuid.UUID,
    approver_session: UserSession,
    refresh_token: str,
    device_name: str = "Unknown Device",
    device_ip: Optional[str] = None,
    platform: Platform = Platform.WEB,
) -> dict:
    """
    Approve a login request from the primary device.
    Deactivates the newest non-primary session and creates new session.
    """
    stmt = select(SessionLoginRequest).where(
        SessionLoginRequest.id == request_id
    )
    login_req = (await db.execute(stmt)).scalar_one_or_none()
    if not login_req:
        return {"error": "درخواست یافت نشد"}
    if login_req.status != LoginRequestStatus.PENDING:
        return {"error": "درخواست قبلاً پردازش شده است"}
    if login_req.expires_at.replace(tzinfo=None) < datetime.utcnow():
        login_req.status = LoginRequestStatus.EXPIRED
        await db.commit()
        return {"error": "درخواست منقضی شده است"}

    # Load user to get max_sessions
    user = (await db.execute(select(User).where(User.id == login_req.user_id))).scalar_one()
    active_sessions = await get_active_sessions(db, login_req.user_id)

    # Find the newest non-primary session to evict
    non_primary = [s for s in active_sessions if not s.is_primary]
    if non_primary:
        # Evict the newest non-primary session
        newest_non_primary = non_primary[-1]  # list is ordered ASC, so last is newest
        await deactivate_session(db, newest_non_primary)

    # Mark request as approved
    login_req.status = LoginRequestStatus.APPROVED
    login_req.resolved_by_session_id = approver_session.id

    # Create new session
    new_session = await create_session(
        db, login_req.user_id, refresh_token,
        login_req.requester_device_name,
        login_req.requester_ip,
        platform,
        is_primary=False,
    )

    await db.commit()
    
    # Notify the requester that their login was approved
    try:
        from core.utils import publish_user_event
        await publish_user_event(login_req.user_id, "session:login_approved", {
            "request_id": str(request_id),
        })
    except Exception as e:
        logger.warning(f"Failed to publish login approved event: {e}")
    
    return {"session": new_session}


async def reject_login_request(
    db: AsyncSession,
    request_id: uuid.UUID,
    approver_session: UserSession,
) -> dict:
    """Reject a login request."""
    stmt = select(SessionLoginRequest).where(
        SessionLoginRequest.id == request_id
    )
    login_req = (await db.execute(stmt)).scalar_one_or_none()
    if not login_req:
        return {"error": "درخواست یافت نشد"}
    if login_req.status != LoginRequestStatus.PENDING:
        return {"error": "درخواست قبلاً پردازش شده است"}

    login_req.status = LoginRequestStatus.REJECTED
    login_req.resolved_by_session_id = approver_session.id
    await db.commit()
    
    # Notify the requester that their login was rejected
    try:
        from core.utils import publish_user_event
        await publish_user_event(login_req.user_id, "session:login_rejected", {
            "request_id": str(request_id),
        })
    except Exception as e:
        logger.warning(f"Failed to publish login rejected event: {e}")
    
    return {"success": True}


async def logout_session(
    db: AsyncSession, session: UserSession
) -> Optional[UserSession]:
    """
    Logout (deactivate) a session. If it was primary, promote oldest remaining.
    Returns the new primary session if one was promoted, else None.
    """
    was_primary = session.is_primary
    await deactivate_session(db, session)

    new_primary = None
    if was_primary:
        new_primary = await promote_next_primary(db, session.user_id)

    await db.commit()
    
    try:
        from core.utils import publish_user_event
        await publish_user_event(session.user_id, "session:revoked", {"action": "check_session"})
    except Exception as e:
        logger.warning(f"Failed to publish session:revoked event: {e}")
        
    return new_primary

async def force_clear_sessions(
    db: AsyncSession, user_id: int
) -> int:
    """Force-clear all active sessions for a user. Returns count of cleared sessions."""
    sessions = await get_active_sessions(db, user_id)
    count = 0
    for s in sessions:
        s.is_active = False
        count += 1
    await db.commit()
    
    try:
        from core.utils import publish_user_event
        await publish_user_event(user_id, "session:revoked", {"action": "check_session"})
    except Exception as e:
        logger.warning(f"Failed to publish session:revoked event: {e}")
        
    # Blacklist all session IDs
    try:
        from bot.utils.redis_helpers import get_redis
        r = await get_redis()
        for s in sessions:
            await r.setex(f"session_blacklist:{s.id}", SESSION_BLACKLIST_TTL, "1")
    except Exception as e:
        logger.warning(f"Failed to blacklist sessions: {e}")

    return count


async def blacklist_session(session_id) -> None:
    """Add a session ID to the Redis blacklist so access tokens are immediately invalidated."""
    try:
        from bot.utils.redis_helpers import get_redis
        r = await get_redis()
        await r.setex(f"session_blacklist:{session_id}", SESSION_BLACKLIST_TTL, "1")
    except Exception as e:
        logger.warning(f"Failed to blacklist session {session_id}: {e}")


async def is_session_blacklisted(session_id: str) -> bool:
    """Check if a session ID is in the Redis blacklist."""
    try:
        from bot.utils.redis_helpers import get_redis
        r = await get_redis()
        return await r.exists(f"session_blacklist:{session_id}") > 0
    except Exception:
        return False
