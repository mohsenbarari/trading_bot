"""Session management API endpoints."""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from core.db import get_db
from core.config import settings
from api.deps import get_current_user
from models.user import User, UserRole
from models.session import UserSession, SessionLoginRequest, LoginRequestStatus, Platform
from core.services.session_service import (
    get_active_sessions,
    get_session_by_refresh_token,
    handle_login_session,
    approve_login_request,
    reject_login_request,
    logout_session,
    force_clear_sessions,
    hash_token,
    get_effective_max_sessions,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Schemas ---
class SessionOut(BaseModel):
    id: str
    device_name: str
    device_ip: Optional[str] = None
    platform: str
    is_primary: bool
    is_active: bool
    created_at: datetime
    last_active_at: datetime

    class Config:
        from_attributes = True

class LoginRequestOut(BaseModel):
    id: str
    requester_device_name: str
    requester_ip: Optional[str] = None
    status: str
    created_at: datetime
    expires_at: datetime

    class Config:
        from_attributes = True

class LoginRequestAction(BaseModel):
    request_id: str

class MaxSessionsUpdate(BaseModel):
    max_sessions: int


def session_to_dict(s: UserSession) -> dict:
    return {
        "id": str(s.id),
        "device_name": s.device_name,
        "device_ip": s.device_ip,
        "platform": s.platform.value if hasattr(s.platform, 'value') else str(s.platform),
        "is_primary": s.is_primary,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None,
    }


def login_request_to_dict(r: SessionLoginRequest) -> dict:
    return {
        "id": str(r.id),
        "requester_device_name": r.requester_device_name,
        "requester_ip": r.requester_ip,
        "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
    }


# --- Endpoints ---

class VerifySessionRequest(BaseModel):
    refresh_token: str

@router.post("/verify")
async def verify_my_session(
    req: VerifySessionRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    بررسی اینکه آیا نشست برای یک refresh_token خاص هنوز فعال است یا خیر.
    (استفاده در فرانت‌اند هنگام دریافت نوتیفیکیشن لغو نشست)
    """
    from core.services.session_service import get_session_by_refresh_token
    session = await get_session_by_refresh_token(db, req.refresh_token)
    if not session:
        raise HTTPException(status_code=401, detail="نشست شما باطل شده است")
    return {"status": "active"}

@router.get("/active", response_model=List[dict])
async def list_active_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """لیست نشست‌های فعال کاربر جاری"""
    # Extract session_id from JWT to mark current session
    current_session_id = None
    try:
        from jose import jwt as jose_jwt
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = jose_jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            current_session_id = payload.get("sid")
    except Exception:
        pass
    
    sessions = await get_active_sessions(db, current_user.id)
    result = []
    for s in sessions:
        d = session_to_dict(s)
        d["is_current"] = (str(s.id) == current_session_id) if current_session_id else False
        result.append(d)
    return result


@router.delete("/{session_id}")
async def terminate_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    پایان دادن به یک نشست.
    - کاربر عادی: فقط نشست‌‌های خودش 
    - نشست primary را نمی‌توان حذف کرد مگر آخرین نشست باشد
    """
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه نشست نامعتبر است")

    stmt = select(UserSession).where(
        and_(UserSession.id == sid, UserSession.user_id == current_user.id, UserSession.is_active == True)
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="نشست یافت نشد")

    # Don't allow terminating primary if other sessions exist
    if session.is_primary:
        active_sessions = await get_active_sessions(db, current_user.id)
        if len(active_sessions) > 1:
            raise HTTPException(
                status_code=400,
                detail="نشست اصلی را نمی‌توان حذف کرد. ابتدا نشست‌های دیگر را حذف کنید."
            )

    await logout_session(db, session)
    return {"detail": "نشست با موفقیت پایان یافت"}


@router.post("/logout-all")
async def logout_all_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """پایان دادن به همه نشست‌ها"""
    count = await force_clear_sessions(db, current_user.id)
    return {"detail": f"{count} نشست پایان یافت"}


@router.get("/login-requests/pending", response_model=List[dict])
async def get_pending_login_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """دریافت درخواست‌های ورود در انتظار تایید"""
    stmt = select(SessionLoginRequest).where(
        and_(
            SessionLoginRequest.user_id == current_user.id,
            SessionLoginRequest.status == LoginRequestStatus.PENDING,
            SessionLoginRequest.expires_at > datetime.utcnow(),
        )
    )
    result = await db.execute(stmt)
    requests = result.scalars().all()
    return [login_request_to_dict(r) for r in requests]


@router.post("/login-requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    تایید درخواست ورود از دستگاه جدید.
    فقط از نشست primary مجاز است.
    """
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    # Verify caller has a primary session
    from core.security import create_refresh_token
    
    # Find caller's primary session
    stmt = select(UserSession).where(
        and_(
            UserSession.user_id == current_user.id,
            UserSession.is_primary == True,
            UserSession.is_active == True,
        )
    )
    primary_session = (await db.execute(stmt)).scalar_one_or_none()
    if not primary_session:
        raise HTTPException(status_code=403, detail="فقط از نشست اصلی مجاز به تایید هستید")

    # Generate refresh token for new session
    new_refresh = create_refresh_token(subject=current_user.id)

    result = await approve_login_request(
        db, rid, primary_session, new_refresh,
        device_ip=request.client.host if request.client else None,
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Store actual refresh token in Redis for poll endpoint (TTL 5 min)
    try:
        from bot.utils.redis_helpers import get_redis
        r = await get_redis()
        await r.setex(f"login_req_token:{request_id}", 300, new_refresh)
    except Exception as e:
        logger.warning(f"Failed to store refresh token for poll: {e}")

    return {"detail": "درخواست ورود تایید شد", "session": session_to_dict(result["session"])}


@router.post("/login-requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """رد درخواست ورود"""
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه درخواست نامعتبر است")

    # Find caller's primary session
    stmt = select(UserSession).where(
        and_(
            UserSession.user_id == current_user.id,
            UserSession.is_primary == True,
            UserSession.is_active == True,
        )
    )
    primary_session = (await db.execute(stmt)).scalar_one_or_none()
    if not primary_session:
        raise HTTPException(status_code=403, detail="فقط از نشست اصلی مجاز به رد هستید")

    result = await reject_login_request(db, rid, primary_session)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return {"detail": "درخواست ورود رد شد"}


@router.get("/login-requests/{request_id}/status")
async def poll_login_request_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Polling endpoint for new device waiting for approval.
    No auth required — uses request ID as temporary token.
    """
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="شناسه نامعتبر است")

    stmt = select(SessionLoginRequest).where(SessionLoginRequest.id == rid)
    login_req = (await db.execute(stmt)).scalar_one_or_none()

    if not login_req:
        raise HTTPException(status_code=404, detail="درخواست یافت نشد")

    status = login_req.status.value if hasattr(login_req.status, 'value') else str(login_req.status)
    
    response = {"status": status}
    
    # If approved, include the session tokens
    if login_req.status == LoginRequestStatus.APPROVED:
        from core.security import create_access_token
        # Find the new session by user + newest
        stmt2 = select(UserSession).where(
            and_(
                UserSession.user_id == login_req.user_id,
                UserSession.is_active == True,
            )
        ).order_by(UserSession.created_at.desc()).limit(1)
        new_session = (await db.execute(stmt2)).scalar_one_or_none()
        if new_session:
            response["access_token"] = create_access_token(
                subject=login_req.user_id,
                session_id=str(new_session.id),
            )
            # Retrieve actual refresh token from Redis
            try:
                from bot.utils.redis_helpers import get_redis
                r = await get_redis()
                actual_refresh = await r.get(f"login_req_token:{request_id}")
                if actual_refresh:
                    response["refresh_token"] = actual_refresh
                    await r.delete(f"login_req_token:{request_id}")
            except Exception:
                pass
            response["token_type"] = "bearer"
    elif login_req.expires_at.replace(tzinfo=None) < datetime.utcnow():
        response["status"] = "expired"

    return response
