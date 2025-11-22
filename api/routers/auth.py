# trading_bot/api/routers/auth.py (کامل و اصلاح شده - رفع باگ تایپ تلگرام آیدی)

from fastapi import APIRouter, Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import hmac
import hashlib
import urllib.parse
import json
from datetime import datetime, timedelta

from core.config import settings, Settings
from core.db import get_db
from core.security import create_access_token
from core.enums import UserRole
from models.user import User
from models.session import UserSession
import schemas

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)
api_key_scheme = APIKeyHeader(name="x-api-key", auto_error=False)

async def get_settings():
    return settings

# --- توابع کمکی ---

async def validate_otp(db: AsyncSession, mobile_number: str, otp_code: str):
    # در نسخه فعلی لاجیک OTP شبیه‌سازی شده است.
    return True

async def get_current_user_from_token(token: str, db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        telegram_id_str = payload.get("sub")
        if telegram_id_str is None:
            raise credentials_exception
        
        # --- تغییر مهم: تبدیل رشته به عدد ---
        telegram_id = int(telegram_id_str) 
        # ----------------------------------

    except (JWTError, ValueError): # ValueError برای حالتی که تبدیل به عدد شکست بخورد
        raise credentials_exception
    
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user

async def get_current_user_optional(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> Optional[User]:
    if token is None:
        return None
    try:
        return await get_current_user_from_token(token, db)
    except HTTPException:
        return None

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await get_current_user_from_token(token, db)

async def verify_super_admin_or_dev_key(
    api_key_header: Optional[str] = Security(api_key_scheme),
    current_user: Optional[User] = Depends(get_current_user_optional),
    settings: Settings = Depends(get_settings)
):
    if api_key_header == settings.dev_api_key and settings.dev_api_key:
        return True
    if current_user and current_user.role == UserRole.SUPER_ADMIN:
        return True
    raise HTTPException(status_code=403, detail="Not a super admin or invalid API key")

async def get_admin_user_dependency(
    api_key_header: Optional[str] = Security(api_key_scheme),
    current_user: Optional[User] = Depends(get_current_user_optional),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
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
            raise HTTPException(status_code=500, detail="DEV_API_KEY is valid, but no SUPER_ADMIN user found in database. Please create one using 'python manage.py create_super_admin'.")
    
    raise credentials_exception

# --- اندپوینت‌ها ---

@router.post("/request-otp")
async def request_otp(request: schemas.OTPRequest, db: AsyncSession = Depends(get_db)):
    return {"message": "OTP sent (simulated)"}

@router.post("/verify-otp", response_model=schemas.Token)
async def verify_otp(request: schemas.OTPVerify, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.mobile_number == request.mobile_number)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    access_token = create_access_token(data={"sub": str(user.telegram_id), "role": user.role.value})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=schemas.UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/webapp-login", response_model=schemas.Token)
async def webapp_login(init_data_obj: schemas.WebAppInitData, db: AsyncSession = Depends(get_db)):
    """
    احراز هویت با داده‌های Telegram WebApp
    """
    init_data = init_data_obj.init_data
    
    try:
        parsed_data = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        data_dict = dict(parsed_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid init_data format")

    hash_ = data_dict.pop('hash', None)
    if not hash_:
        raise HTTPException(status_code=400, detail="Hash missing")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data_dict.items()))
    
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash != hash_:
        raise HTTPException(status_code=403, detail="Invalid Telegram hash")

    user_data_str = data_dict.get('user')
    if not user_data_str:
        raise HTTPException(status_code=400, detail="User data missing")
    
    user_data = json.loads(user_data_str)
    telegram_id = user_data.get('id') # این عدد صحیح است

    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="User not registered. Please register via bot invitation link first.")

    if not user.has_bot_access:
        raise HTTPException(status_code=403, detail="Access denied")

    # تبدیل telegram_id به رشته برای ذخیره در توکن
    access_token = create_access_token(
        data={
            "sub": str(user.telegram_id), 
            "role": user.role.value,
            "source": "miniapp"
        }
    )
    return {"access_token": access_token, "token_type": "bearer"}

async def get_request_source(token: str = Depends(oauth2_scheme)) -> str:
    """
    منبع درخواست (مثلاً miniapp) را از توکن استخراج می‌کند.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("source", "unknown")
    except JWTError:
        return "unknown"