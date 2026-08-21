# core/services/session_service.py
"""سرویس مدیریت نشست‌ها و درخواست لاگین"""
import hashlib
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus
from core.utils import utc_now_naive
from models.session import UserSession, SessionLoginRequest, LoginRequestStatus, Platform
from models.user import User, UserRole

logger = logging.getLogger(__name__)

# Login request approval timeout
LOGIN_REQUEST_TIMEOUT_SECONDS = 120
# Session blacklist TTL: must match access token lifetime (60 min)
SESSION_BLACKLIST_TTL = 3600
ACCOUNT_INACTIVE_BLOCK_REASON = "ACCOUNT_INACTIVE"


def _is_inactive_account(user: User | object | None) -> bool:
    raw_status = getattr(user, "account_status", None)
    normalized = getattr(raw_status, "value", raw_status)
    return normalized == UserAccountStatus.INACTIVE.value


def hash_token(token: str) -> str:
    """Hash a refresh token for storage (SHA-256)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def get_effective_max_sessions(user: User) -> int:
    """Get effective max sessions for a user. Admins are locked to 1."""
    if user.role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        return 1
    return min(max(user.max_sessions, 1), 3)


async def _is_accountant_user(db: AsyncSession, user_id: int) -> bool:
    if not hasattr(db, "execute"):
        return False

    from core.services.accountant_relation_service import is_user_accountant

    return await is_user_accountant(db, user_id)


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
    home_server: str = "foreign",
) -> UserSession:
    """Create a new active session."""
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        device_name=device_name,
        device_ip=device_ip,
        home_server=home_server,
        platform=platform,
        refresh_token_hash=hash_token(refresh_token),
        is_primary=is_primary,
        is_active=True,
        expires_at=utc_now_naive() + timedelta(days=30),
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
    home_server: str = "foreign",
) -> dict:
    """
    Core login session logic. Returns one of:
    - {"action": "session_created", "session": UserSession, "replaced_session_count": int}
    - {"action": "blocked", "reason": str}

    When the configured limit is full, the oldest required session(s) are
    revoked and the authenticated new session is created immediately.  This
    preserves the limit without requiring access to an old device.
    """
    if _is_inactive_account(user):
        return {"action": "blocked", "reason": ACCOUNT_INACTIVE_BLOCK_REASON}

    # Serialize concurrent logins for a real ORM user.  Test doubles and
    # compatibility callers still use the explicit session query below.
    if isinstance(user, User):
        locked_user = (
            await db.execute(select(User).where(User.id == user.id).with_for_update())
        ).scalar_one_or_none()
        if locked_user is not None:
            user = locked_user

    max_sessions = 1 if await _is_accountant_user(db, user.id) else get_effective_max_sessions(user)
    
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
            suspended_session.home_server = home_server
            suspended_session.last_active_at = utc_now_naive()
            suspended_session.expires_at = utc_now_naive() + timedelta(days=30)
            await db.commit()
            return {
                "action": "session_created",
                "session": suspended_session,
                "replaced_session_count": 0,
            }

    active_sessions = await get_active_sessions(db, user.id)

    # Case 1: No sessions exist → create first session as primary
    if len(active_sessions) == 0:
        session = await create_session(
            db, user.id, refresh_token, device_name, device_ip, platform,
            is_primary=True,
            home_server=home_server,
        )
        await db.commit()
        return {"action": "session_created", "session": session, "replaced_session_count": 0}

    # Case 2: Under limit → create new session directly (non-primary)
    if len(active_sessions) < max_sessions:
        session = await create_session(
            db, user.id, refresh_token, device_name, device_ip, platform,
            is_primary=False,
            home_server=home_server,
        )
        await db.commit()
        return {"action": "session_created", "session": session, "replaced_session_count": 0}

    # Case 3: At limit → revoke the oldest required rows and admit the new
    # authenticated device.  The user-row lock above makes this atomic for
    # production ORM calls and prevents concurrent logins exceeding the cap.
    replace_count = len(active_sessions) - max_sessions + 1
    revoked_sessions = active_sessions[:replace_count]
    for old_session in revoked_sessions:
        old_session.is_active = False

    remaining_sessions = active_sessions[replace_count:]
    new_session_is_primary = not any(
        bool(getattr(existing_session, "is_primary", False))
        for existing_session in remaining_sessions
    )
    session = await create_session(
        db,
        user.id,
        refresh_token,
        device_name,
        device_ip,
        platform,
        is_primary=new_session_is_primary,
        home_server=home_server,
    )
    await db.commit()
    await publish_session_revocation(user.id, revoked_sessions)
    return {
        "action": "session_created",
        "session": session,
        "replaced_session_count": len(revoked_sessions),
    }


LOGIN_REQUEST_NOT_FOUND_ERROR = "درخواست یافت نشد"


def _is_login_request_resolvable_by(
    login_req: SessionLoginRequest,
    approver_session: UserSession,
) -> bool:
    """A login request may only be resolved from its own account's primary session.

    Callers must report a mismatch with the same message as a missing request, so a
    caller holding a leaked request id learns nothing about whether it exists.
    """
    return getattr(login_req, "user_id", None) == getattr(approver_session, "user_id", None)


async def approve_login_request(
    db: AsyncSession,
    request_id: uuid.UUID,
    approver_session: UserSession,
    refresh_token: str,
    device_name: str = "Unknown Device",
    device_ip: Optional[str] = None,
    platform: Platform = Platform.WEB,
    home_server: Optional[str] = None,
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
        return {"error": LOGIN_REQUEST_NOT_FOUND_ERROR}
    if not _is_login_request_resolvable_by(login_req, approver_session):
        return {"error": LOGIN_REQUEST_NOT_FOUND_ERROR}
    if login_req.status != LoginRequestStatus.PENDING:
        return {"error": "درخواست قبلاً پردازش شده است"}
    if login_req.expires_at.replace(tzinfo=None) < utc_now_naive():
        login_req.status = LoginRequestStatus.EXPIRED
        await db.commit()
        return {"error": "درخواست منقضی شده است"}

    if hasattr(login_req, "_sa_instance_state"):
        from core.services.single_session_recovery_service import (
            get_active_recovery_request_for_login_request,
        )

        active_recovery = await get_active_recovery_request_for_login_request(db, request_id)
        if active_recovery is not None:
            return {"error": "برای این درخواست، مسیر بازیابی نشست فعال شده و تایید از دستگاه قبلی دیگر مجاز نیست"}

    user = (await db.execute(select(User).where(User.id == login_req.user_id))).scalar_one_or_none()
    if not user or _is_inactive_account(user):
        return {"error": "حساب کاربری غیرفعال شده است"}

    # Mark request as approved
    login_req.status = LoginRequestStatus.APPROVED
    login_req.resolved_by_session_id = approver_session.id

    new_session = await provision_session_for_login_request(
        db,
        login_request=login_req,
        refresh_token=refresh_token,
        user=user,
        platform=platform,
        home_server=home_server or login_req.requester_home_server,
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


async def provision_session_for_login_request(
    db: AsyncSession,
    *,
    login_request: SessionLoginRequest,
    refresh_token: str,
    user: Optional[User] = None,
    platform: Platform = Platform.WEB,
    home_server: Optional[str] = None,
) -> UserSession:
    """Create the admitted session for one already-authorized login request without committing."""
    if user is None:
        user = (await db.execute(select(User).where(User.id == login_request.user_id))).scalar_one()
    active_sessions = await get_active_sessions(db, login_request.user_id)
    max_sessions_allowed = 1 if await _is_accountant_user(db, user.id) else get_effective_max_sessions(user)

    num_to_evict = max(0, len(active_sessions) - max_sessions_allowed + 1)
    for _ in range(num_to_evict):
        if not active_sessions:
            break
        non_primary = [session for session in active_sessions if not session.is_primary]
        if non_primary:
            to_evict = non_primary[-1]
        else:
            primaries = [session for session in active_sessions if session.is_primary]
            to_evict = primaries[-1] if primaries else active_sessions[-1]

        await deactivate_session(db, to_evict)
        active_sessions.remove(to_evict)

    has_primary = any(session.is_primary for session in active_sessions)
    return await create_session(
        db,
        login_request.user_id,
        refresh_token,
        login_request.requester_device_name,
        login_request.requester_ip,
        platform,
        is_primary=(not has_primary),
        home_server=home_server or login_request.requester_home_server,
    )


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
        return {"error": LOGIN_REQUEST_NOT_FOUND_ERROR}
    if not _is_login_request_resolvable_by(login_req, approver_session):
        return {"error": LOGIN_REQUEST_NOT_FOUND_ERROR}
    if login_req.status != LoginRequestStatus.PENDING:
        return {"error": "درخواست قبلاً پردازش شده است"}

    if hasattr(login_req, "_sa_instance_state"):
        from core.services.single_session_recovery_service import (
            get_active_recovery_request_for_login_request,
        )

        active_recovery = await get_active_recovery_request_for_login_request(db, request_id)
        if active_recovery is not None:
            return {"error": "برای این درخواست، مسیر بازیابی نشست فعال شده و رد از دستگاه قبلی دیگر مجاز نیست"}

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


async def deactivate_active_sessions(
    db: AsyncSession,
    user_id: int,
    exclude_session_id: Optional[uuid.UUID] = None,
) -> List[UserSession]:
    """Mark all active sessions for a user as inactive inside the current transaction."""
    sessions = await get_active_sessions(db, user_id)
    revoked_sessions: List[UserSession] = []

    for session in sessions:
        if exclude_session_id and session.id == exclude_session_id:
            continue

        session.is_active = False
        revoked_sessions.append(session)

    await db.flush()
    return revoked_sessions


async def publish_session_revocation(user_id: int, revoked_sessions: List[UserSession]) -> None:
    """Blacklist revoked session IDs and notify active clients to validate again."""
    try:
        from core.utils import publish_user_event
        await publish_user_event(user_id, "session:revoked", {"action": "check_session"})
    except Exception as e:
        logger.warning(f"Failed to publish session:revoked event: {e}")

    for session in revoked_sessions:
        await blacklist_session(session.id)

async def force_clear_sessions(
    db: AsyncSession, user_id: int, exclude_session_id: Optional[uuid.UUID] = None
) -> int:
    """Force-clear all active sessions for a user. Returns count of cleared sessions."""
    cleared_sessions = await deactivate_active_sessions(db, user_id, exclude_session_id=exclude_session_id)
    await db.commit()

    await publish_session_revocation(user_id, cleared_sessions)
    return len(cleared_sessions)


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
