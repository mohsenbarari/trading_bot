from typing import Generator, Optional
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core import security
from core.config import settings
from core.db import get_db
from models.user import User, UserRole
from datetime import datetime
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


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        token_data = payload.get("sub")
        
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
        
    # Update last_seen
    if not user.last_seen_at or (datetime.utcnow() - user.last_seen_at).total_seconds() > 60:
        user.last_seen_at = datetime.utcnow()
        await db.commit()
        
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

async def verify_super_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    if current_user.role != UserRole.SUPER_ADMIN:
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
    if dev_key and dev_key == settings.dev_api_key:
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


