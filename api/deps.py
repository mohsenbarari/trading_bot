from typing import Generator, Optional
import uuid
import hashlib
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core import security
from core.config import settings
from core.db import get_db
from core.services.accountant_relation_service import EffectiveOwnerActor, resolve_effective_owner_actor
from core.services.user_account_status_service import is_user_global_web_locked
from core.request_context import set_request_context
from models.session import UserSession
from models.user import User, UserRole
import logging

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.frontend_url}/login"
)

# نسخه اختیاری — برای endpointهایی که هم با token و هم با API key کار می‌کنند
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl=f"{settings.frontend_url}/login",
    auto_error=False
)

# DEV_API_KEY for bypass
DEV_API_KEY_HEADER = "X-DEV-API-KEY"

from fastapi.security import APIKeyHeader
api_key_header = APIKeyHeader(name=DEV_API_KEY_HEADER, auto_error=False)


def _opaque_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def _ensure_user_access_allowed(user: User) -> None:
    if user.is_deleted or is_user_global_web_locked(user):
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")

    if user.must_change_password and user.role in [UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="REQUIRES_PASSWORD_CHANGE",
        )


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        token_data = payload.get("sub")
        session_id = payload.get("sid")
        
        if token_data is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if session has been revoked (Redis blacklist)
    if session_id:
        from core.services.session_service import is_session_blacklisted
        if await is_session_blacklisted(session_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
    # Try to find user by ID (new way) or telegram_id (old way)
    # The payload 'sub' is a string. If it's a digit, it could be either.
    # However, user.id is usually small int, telegram_id is huge int.
    # Let's try both lookups to be safe and compatible during migration.
    
    user_id_or_telegram_id = int(token_data)
    
    # 1. Try by ID (primary key) - Preferred for new system
    stmt = select(User).where(User.id == user_id_or_telegram_id)
    user = (await db.execute(stmt)).scalar_one_or_none()
    
    if not user:
        # 2. Fallback: Try by telegram_id (Legacy tokens)
        stmt = select(User).where(User.telegram_id == user_id_or_telegram_id)
        user = (await db.execute(stmt)).scalar_one_or_none()
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if session_id:
        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        active_session = await db.get(UserSession, session_uuid)
        if not active_session or not active_session.is_active or active_session.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
    _ensure_user_access_allowed(user)

    set_request_context(
        actor_id=user.id,
        actor_role=getattr(user.role, "value", str(user.role)),
        session_id_hash=_opaque_session_id(session_id),
    )
        
    return user

async def get_current_user_optional(
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional)
) -> Optional[User]:
    if not token:
        return None
    try:
        return await get_current_user(db, token)
    except HTTPException:
        return None


async def get_effective_owner_actor_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EffectiveOwnerActor:
    return await resolve_effective_owner_actor(db, current_user)


async def get_effective_owner_user(
    context: EffectiveOwnerActor = Depends(get_effective_owner_actor_context),
) -> User:
    return context.owner_user

async def verify_super_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user


async def verify_admin_user(
    current_user: User = Depends(get_current_user)
) -> User:
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges"
        )
    return current_user

async def verify_super_admin_or_dev_key(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    dev_key: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db)
):
    """
    Allow access if user is SUPER_ADMIN OR if valid DEV_API_KEY is provided
    """
    # 1. Check Dev Key
    if security.constant_time_secret_equals(dev_key, settings.dev_api_key):
        return None # Special return value indicating system access

    # 2. Check User Token
    if token:
        user = await get_current_user(db, token)
        if user.role == UserRole.SUPER_ADMIN:
            return user
            
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authenticated"
    )


async def verify_admin_or_dev_key(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    dev_key: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db)
):
    """
    Allow access if user is SUPER_ADMIN/MIDDLE_MANAGER OR if valid DEV_API_KEY is provided.
    """
    if security.constant_time_secret_equals(dev_key, settings.dev_api_key):
        return None

    if token:
        user = await get_current_user(db, token)
        if user.role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
            return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authenticated"
    )
