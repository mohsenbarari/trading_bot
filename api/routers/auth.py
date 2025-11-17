# trading_bot/api/routers/auth.py (کامل و اصلاح شده)

from fastapi import APIRouter, Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from core.config import settings, Settings
from core.db import get_db, AsyncSessionLocal
from core.security import create_access_token
from core.enums import UserRole
from models.user import User
from models.session import UserSession
import schemas

# (توابع get_settings, validate_otp, check_user_exists_by_mobile بدون تغییر)
# ...
async def get_settings():
    return settings

async def validate_otp(db: AsyncSession, mobile_number: str, otp_code: str):
    ...

async def check_user_exists_by_mobile(db: AsyncSession, mobile_number: str):
    ...
# ...

# --- تعریف شیوه‌های احراز هویت ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)
api_key_scheme = APIKeyHeader(name="x-api-key", auto_error=False)


# (توابع get_current_user_from_token, get_current_user_optional, get_current_user بدون تغییر)
# ...
async def get_current_user_from_token(token: str, db: AsyncSession = Depends(get_db)) -> User:
    ...

async def get_current_user_optional(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    ...

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_exception
    
    user = await get_current_user_from_token(token, db)
    if user is None:
        raise credentials_exception
    return user
# ...


# (تابع verify_super_admin_or_dev_key بدون تغییر)
async def verify_super_admin_or_dev_key(
    api_key_header: Optional[str] = Security(api_key_scheme),
    current_user: Optional[User] = Depends(get_current_user_optional),
    settings: Settings = Depends(get_settings)
):
    """
    بررسی می‌کند که آیا درخواست یا با API Key معتبر آمده یا با توکن JWT یک ادمین ارشد.
    """
    if api_key_header == settings.dev_api_key and settings.dev_api_key:
        return True
    
    if current_user and current_user.role == UserRole.SUPER_ADMIN:
        return True
    
    raise HTTPException(status_code=403, detail="Not a super admin or invalid API key")


# --- تابع get_admin_user_dependency (اصلاح شد) ---
async def get_admin_user_dependency(
    api_key_header: Optional[str] = Security(api_key_scheme),
    current_user: Optional[User] = Depends(get_current_user_optional),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    کاربر ادمین را برمی‌گرداند.
    اگر با توکن JWT لاگین کرده باشد، همان کاربر را برمی‌گرداند.
    اگر با API Key لاگین کرده باشد، اولین کاربر ادمین ارشد دیتابیس را برمی‌گرداند.
    """
    credentials_exception = HTTPException(
        # --- ۱. این خط اصلاح شد ---
        status_code=status.HTTP_403_FORBIDDEN, # <--- قبلاً HTTP_403 بود
        # ------------------------
        detail="Not authenticated as super admin or invalid API key"
    )

    if current_user:
        if current_user.role == UserRole.SUPER_ADMIN:
            return current_user
        else:
            raise credentials_exception

    if api_key_header == settings.dev_api_key and settings.dev_api_key:
        stmt = select(User).where(User.role == UserRole.SUPER_ADMIN).limit(1)
        result = await db.execute(stmt)
        admin_user = result.scalar_one_or_none()
        if admin_user:
            return admin_user
        else:
            raise HTTPException(status_code=500, detail="DEV_API_KEY is valid, but no SUPER_ADMIN user found in database.")
    
    raise credentials_exception
# ----------------------------------------------------

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

# (اندپوینت‌های request_otp, verify_otp, read_users_me, webapp_login بدون تغییر)
@router.post("/request-otp")
async def request_otp(request: schemas.OTPRequest, db: AsyncSession = Depends(get_db)):
    ...

@router.post("/verify-otp", response_model=schemas.Token)
async def verify_otp(request: schemas.OTPVerify, db: AsyncSession = Depends(get_db)):
    ...

@router.get("/me", response_model=schemas.UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/webapp-login", response_model=schemas.Token)
async def webapp_login(init_data_obj: schemas.WebAppInitData, db: AsyncSession = Depends(get_db)):
    ...