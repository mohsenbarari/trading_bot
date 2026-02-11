# trading_bot/api/routers/auth.py
"""
API Router for Authentication - JWT Access Token + Refresh Token
"""
import hashlib
import hmac
import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings, Settings
from core.db import get_db
from core.enums import UserRole
from core.redis import get_redis
from core.security import (
    create_access_token,
    create_refresh_token,
    verify_token,
    verify_refresh_token,
    create_token_pair,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from models.user import User
from models.session import UserSession
import schemas

logger = logging.getLogger(__name__)

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
    
    # ===== Redis Cache Check =====
    from core.cache import get_cached_user_by_telegram_id, set_cached_user
    
    cached_user_data = await get_cached_user_by_telegram_id(telegram_id)
    if cached_user_data:
        # بازسازی User از کش - فقط برای چک اولیه
        # برای last_seen باید از DB بخوانیم
        user_id = cached_user_data.get("id")
        stmt = select(User).where(User.id == user_id, User.is_deleted == False)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            # Update Last Seen (هر 60 ثانیه)
            now = datetime.now(timezone.utc)
            if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() > 60:
                user.last_seen_at = now
                await db.commit()
            return user
    # =============================
    
    # Fallback به DB Query
    stmt = select(User).where(User.telegram_id == telegram_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    
    # ===== Cache User =====
    await set_cached_user(telegram_id, {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "role": user.role.value if hasattr(user.role, 'value') else str(user.role),
    })
    # ======================
        
    # --- Update Last Seen ---
    now = datetime.now(timezone.utc)
    if user.last_seen_at is None or (now - user.last_seen_at).total_seconds() > 60:
        logger.info(f"DEBUG: Updating last_seen for user {user.id} to {now}")
        user.last_seen_at = now
        await db.commit()
    # ------------------------
    
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
        stmt = select(User).where(User.role == UserRole.SUPER_ADMIN, User.is_deleted == False).limit(1)
        result = await db.execute(stmt)
        admin_user = result.scalar_one_or_none()
        if admin_user:
            return admin_user
        else:
            raise HTTPException(status_code=500, detail="DEV_API_KEY is valid, but no SUPER_ADMIN user found in database. Please create one using 'python manage.py create_super_admin'.")
    
    raise credentials_exception

# --- اندپوینت‌ها ---

# --- اندپوینت‌ها ---

@router.post("/request-otp")
async def request_otp(
    request: schemas.OTPRequest, 
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis)
):
    print(f"DEBUG: request_otp called for {request.mobile_number}", flush=True)
    # ===== Rate Limiting: 1 request per 2 minutes per mobile =====
    rate_limit_key = f"OTP_RATE_LIMIT:{request.mobile_number}"
    existing_limit = await redis_client.get(rate_limit_key)
    
    if existing_limit:
        # محاسبه زمان باقیمانده
        ttl = await redis_client.ttl(rate_limit_key)
        raise HTTPException(
            status_code=429,  # Too Many Requests
            detail=f"لطفاً {ttl} ثانیه دیگر تلاش کنید."
        )
    
    # 1. Check if user exists
    stmt = select(User).where(User.mobile_number == request.mobile_number, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        # Security: Don't reveal if user exists? 
        # For this specific app, returning 404 is fine as it's an invite-only internal app.
        raise HTTPException(status_code=404, detail="شماره موبایل یافت نشد.")

    # 2. Generate OTP
    otp_code = "".join([str(secrets.randbelow(10)) for _ in range(5)])  # 5 digit code
    
    # 3. Store in Redis (120 seconds expiry)
    await redis_client.set(f"OTP:{request.mobile_number}", otp_code, ex=120)
    
    # ===== ست کردن Rate Limit (120 ثانیه = 2 دقیقه) =====
    await redis_client.set(rate_limit_key, "1", ex=120)
    
    logger.debug(f"OTP generated for {request.mobile_number}")
    
    # 4. Send via Telegram (Relay supported)
    from core.notifications import send_telegram_message
    try:
        message_text = f"🔐 کد ورود شما:\n\n`{otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
        await send_telegram_message(chat_id=user.telegram_id, text=message_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send Telegram OTP message: {e}")
        # Don't fail the request if just notification fails? Or do we?
        # Better to log error but return success to avoid blocking user if sync is slightly delayed
        # But for OTP, if they don't get it, they can't login.
        # So we should probably return success and let the sync handle it.
        # But if sync fails completely... 
        # For now, let's catch and log, return success.
        pass

    return {"message": "کد تایید به تلگرام شما ارسال شد."}

@router.post("/verify-otp", response_model=schemas.TokenPair)
async def verify_otp(
    request: schemas.OTPVerify, 
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis)
):
    """
    تایید OTP و دریافت جفت توکن (Access + Refresh)
    """
    # 1. Verify OTP from Redis
    stored_otp = await redis_client.get(f"OTP:{request.mobile_number}")
    
    if not stored_otp or str(stored_otp) != str(request.otp_code):
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است.")
    
    # 2. Get User
    stmt = select(User).where(User.mobile_number == request.mobile_number, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد.")

    # 3. Cleanup
    await redis_client.delete(f"OTP:{request.mobile_number}")

    # 4. Generate Token Pair
    access_token, refresh_token = create_token_pair(user.id, user.telegram_id)
    
    logger.info(f"User {user.id} logged in via OTP")
    
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

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

    stmt = select(User).where(User.telegram_id == telegram_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=403, detail="User not registered. Please register via bot invitation link first.")

    # Generate Token Pair
    access_token, refresh_token = create_token_pair(user.id, user.telegram_id)
    
    logger.info(f"User {user.id} logged in via WebApp")
    
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.post("/refresh", response_model=schemas.TokenPair)
async def refresh_access_token(
    refresh_data: schemas.RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    تمدید Access Token با استفاده از Refresh Token
    
    - Refresh Token اعتبار 30 روزه دارد
    - هر بار یک جفت توکن جدید صادر می‌شود
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token نامعتبر یا منقضی شده است.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Verify Refresh Token
    payload = verify_refresh_token(refresh_data.refresh_token, credentials_exception)
    
    user_id = int(payload.get("sub"))
    telegram_id = payload.get("telegram_id")
    
    # Verify user still exists and is active
    stmt = select(User).where(User.id == user_id, User.is_deleted == False)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        raise credentials_exception
    
    # Generate new Token Pair
    access_token, refresh_token = create_token_pair(user.id, user.telegram_id)
    
    logger.info(f"User {user.id} refreshed tokens")
    
    return schemas.TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


async def get_request_source(token: str = Depends(oauth2_scheme)) -> str:
    """
    منبع درخواست (مثلاً miniapp) را از توکن استخراج می‌کند.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("source", "unknown")
    except JWTError:
        return "unknown"