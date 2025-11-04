# api/routers/auth.py (نسخه نهایی با وابستگی‌های اختیاری)
import hmac
import hashlib
import json
from urllib.parse import unquote
import random
import httpx
from typing import Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete

from core.db import get_db
from core.config import settings
from core.security import create_access_token, verify_token
from models.user import User
from models.session import UserSession, Platform
import schemas

router = APIRouter(prefix="/auth", tags=["Authentication"])

otp_storage = {}

# --- طرح‌های امنیتی ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")
# طرح اختیاری (auto_error=False) که اگر توکن نبود، خطا ندهد
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False) 
api_key_scheme = APIKeyHeader(name="X-Dev-Key", auto_error=False)


# --- وابستگی: بررسی کلید توسعه (برای /invitations) ---
async def verify_dev_key(x_dev_key: str | None = Depends(api_key_scheme)):
    if not settings.dev_api_key:
        return # کلیدی در .env ست نشده، چک را رد کن

    if not x_dev_key or x_dev_key != settings.dev_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Dev-Key header"
        )
    return True

# --- وابستگی: دریافت کاربر فعلی (اجباری) ---
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"},
    )
    payload = verify_token(token, credentials_exception)
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    user = (await db.execute(select(User).where(User.id == int(user_id)))).scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user

# === تابع جدید: دریافت کاربر فعلی (اختیاری) ===
async def get_current_user_optional(token: str | None = Depends(oauth2_scheme_optional), db: AsyncSession = Depends(get_db)) -> User | None:
    """
    Tries to get the current user from a JWT.
    Returns User if token is valid, None if token is missing or invalid.
    """
    if token is None:
        return None
    try:
        # Use a dummy exception, we don't want to raise
        credentials_exception = HTTPException(status_code=400) 
        payload = verify_token(token, credentials_exception)
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        user = (await db.execute(select(User).where(User.id == int(user_id)))).scalar_one_or_none()
        return user
    except Exception:
        return None # Token missing, invalid, or expired

# === تابع جدید: بررسی کلید توسعه (اختیاری) ===
async def verify_dev_key_optional(x_dev_key: str | None = Depends(api_key_scheme)) -> bool:
    """
    Checks if a valid dev key was provided.
    Returns True if valid, False otherwise.
    """
    if not settings.dev_api_key:
        return False # No dev key is set in .env
    
    if x_dev_key and x_dev_key == settings.dev_api_key:
        return True
    return False

# --- بقیه اندپوینت‌های auth (بدون تغییر) ---

@router.post("/request-otp", status_code=200)
async def request_otp(request: schemas.OTPRequest, db: AsyncSession = Depends(get_db)):
    # ... (کد شما بدون تغییر) ...
    user = (await db.execute(select(User).where(User.mobile_number == request.mobile_number))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User with this mobile number not found.")
    otp = str(random.randint(100000, 999999))
    otp_storage[user.telegram_id] = otp
    bot_token = settings.bot_token
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": user.telegram_id, "text": f"کد ورود شما: {otp}"}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)
    return {"message": "OTP sent to your Telegram account."}


@router.post("/verify-otp", response_model=schemas.Token)
async def verify_otp(request: schemas.OTPVerify, db: AsyncSession = Depends(get_db)):
    # ... (کد شما بدون تغییر) ...
    user = (await db.execute(select(User).where(User.mobile_number == request.mobile_number))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="User not found.")
    stored_otp = otp_storage.get(user.telegram_id)
    if not stored_otp or stored_otp != request.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP.")
    del otp_storage[user.telegram_id]
    delete_stmt = delete(UserSession).where(and_(UserSession.user_id == user.id, UserSession.platform == request.platform))
    await db.execute(delete_stmt)
    new_session = UserSession(user_id=user.id, platform=request.platform, device_fingerprint=request.device_fingerprint)
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    access_token = create_access_token(data={"sub": str(user.id), "session_id": new_session.id})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=schemas.User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/webapp-login", response_model=schemas.Token)
async def webapp_login(init_data_obj: schemas.WebAppInitData, db: AsyncSession = Depends(get_db)):
    # ... (کد شما بدون تغییر) ...
    try:
        init_data = init_data_obj.init_data
        is_valid, user_data_str = verify_init_data(settings.bot_token, init_data)
        if not is_valid or not user_data_str:
            raise HTTPException(status_code=401, detail="Invalid initData from Telegram")
        user_data = json.loads(user_data_str)
        telegram_id = user_data.get("id")
        user = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found in our database")
        access_token = create_access_token(data={"sub": str(user.id)})
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def verify_init_data(bot_token: str, init_data: str) -> Tuple[bool, str | None]:
    # ... (کد شما بدون تغییر) ...
    try:
        parsed_data = {k: unquote(v) for k, v in (item.split('=') for item in init_data.split('&'))}
        received_hash = parsed_data.pop('hash')
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new("WebAppData".encode(), bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calculated_hash == received_hash, parsed_data.get('user')
    except Exception:
        return False, None