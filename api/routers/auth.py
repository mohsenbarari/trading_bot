from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel
import random
import hashlib
import hmac
import time
from core.db import get_db
from models.user import User, UserRole
from models.invitation import Invitation
from core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password
)
from core.config import settings
import json
from bot.utils.redis_helpers import get_redis
from core.notifications import send_telegram_message
from core.sms import send_otp_sms, send_sms
from core.connectivity import is_internet_connected
from api.deps import get_current_user, oauth2_scheme
import schemas


from core.services.session_service import handle_login_session, get_session_by_refresh_token, hash_token, get_active_sessions
from models.session import Platform


router = APIRouter()
logger = logging.getLogger(__name__)


def _extract_device_info(request) -> dict:
    """Extract device name, IP and platform from request headers."""
    ua = ""
    if hasattr(request, 'headers'):
        ua = request.headers.get("user-agent", "")
    device_name = request.headers.get("x-device-name", "") if hasattr(request, 'headers') else ""
    if not device_name:
        if "Telegram" in ua or "TelegramBot" in ua:
            device_name = "Telegram Mini App"
        elif "Mobile" in ua or "Android" in ua or "iPhone" in ua:
            device_name = "Mobile Browser"
        else:
            device_name = "Web Browser"
    
    platform_header = request.headers.get("x-platform", "web") if hasattr(request, 'headers') else "web"
    try:
        platform = Platform(platform_header)
    except ValueError:
        platform = Platform.WEB

    ip = request.client.host if hasattr(request, 'client') and request.client else None
    return {"device_name": device_name, "device_ip": ip, "platform": platform}

# --- Schemas ---
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str

class OTPRequest(BaseModel):
    mobile_number: str

class OTPVerify(BaseModel):
    mobile_number: str
    code: str

class WebAppLogin(BaseModel):
    init_data: str

class RegisterOTPRequest(BaseModel):
    token: str

class RegisterOTPVerify(BaseModel):
    token: str
    code: str

class RegisterComplete(BaseModel):
    token: str
    address: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str

# --- Endpoints ---

@router.get("/me", response_model=schemas.UserRead)
async def read_users_me(current_user: User = Depends(get_current_user)):
    """دریافت اطلاعات کاربر جاری"""
    return current_user


@router.post("/register-otp-request", response_model=dict)
async def register_otp_request(
    req: RegisterOTPRequest,
    db: AsyncSession = Depends(get_db)
):
    # Validate invitation
    stmt = select(Invitation).where(Invitation.token == req.token)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv:
        raise HTTPException(status_code=404, detail="دعوت‌نامه نامعتبر است")
    if inv.is_used:
        raise HTTPException(status_code=400, detail="دعوت‌نامه قبلاً استفاده شده است")
    if inv.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="دعوت‌نامه منقضی شده است")

    mobile = inv.mobile_number
    
    # Rate limiting
    redis = await get_redis()
    rate_limit_key = f"otp_limit:{mobile}"
    if await redis.get(rate_limit_key):
        raise HTTPException(status_code=429, detail="لطفاً ۲ دقیقه صبر کنید")

    otp_code = str(random.randint(10000, 99999))
    otp_key = f"reg_otp:{req.token}" # Use token as key identifier for security
    
    await redis.setex(otp_key, 120, otp_code)
    await redis.setex(rate_limit_key, 120, "1")
    
    # Send SMS (Always SMS because user is not registered on Telegram yet)
    if send_otp_sms(mobile, otp_code):
        return {"detail": "کد تایید ارسال شد", "expires_in": 120}
    else:
        raise HTTPException(status_code=500, detail="خطا در ارسال پیامک")

@router.post("/register-otp-verify", response_model=dict)
async def register_otp_verify(
    req: RegisterOTPVerify,
    db: AsyncSession = Depends(get_db)
):
    redis = await get_redis()
    otp_key = f"reg_otp:{req.token}"
    stored_code = await redis.get(otp_key)
    
    if not stored_code or stored_code != req.code:
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
    
    # Delete OTP to prevent replay attacks
    await redis.delete(otp_key)
    
    # Set verified flag — 10 mins to complete registration
    verify_key = f"reg_verified:{req.token}"
    await redis.setex(verify_key, 600, "1")
    
    return {"detail": "کد تایید شد"}

@router.post("/register-complete", response_model=Token)
async def register_complete(
    req: RegisterComplete,
    raw_request: Request,
    db: AsyncSession = Depends(get_db)
):
    # 1. Check verify flag
    redis = await get_redis()
    verify_key = f"reg_verified:{req.token}"
    if not await redis.get(verify_key):
        raise HTTPException(status_code=400, detail="لطفاً ابتدا کد تایید را وارد کنید")
    
    # 2. Validate invitation again
    stmt = select(Invitation).where(Invitation.token == req.token)
    inv = (await db.execute(stmt)).scalar_one_or_none()
    
    if not inv or inv.is_used or inv.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="دعوت‌نامه نامعتبر است")
        
    # 3. Create User
    new_user = User(
        account_name=inv.account_name,
        mobile_number=inv.mobile_number,
        role=inv.role,
        full_name=inv.account_name, # Temporary full name
        address=req.address,
        has_bot_access=False, # No bot access yet
        telegram_id=None # Web only user
    )
    
    db.add(new_user)
    
    # 4. Mark invitation as used
    inv.is_used = True
    
    try:
        await db.commit()
        await db.refresh(new_user)
    except Exception as e:
        await db.rollback()
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="خطا در ثبت کاربر")
        
    # 5. Cleanup Redis
    await redis.delete(f"reg_otp:{req.token}")
    await redis.delete(verify_key)
    
    # 6. Generate Tokens (consistent with verify-otp: 60 min access, 30 day refresh)
    access_token_expires = timedelta(minutes=60)
    refresh_token_expires = timedelta(days=30)
    
    access_token = create_access_token(
        subject=new_user.id,
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        subject=new_user.id,
        expires_delta=refresh_token_expires
    )
    
    # 7. Create first session (always succeeds for new user - primary)
    device_info = _extract_device_info(raw_request)
    await handle_login_session(
        db, new_user, refresh_token,
        device_name=device_info["device_name"],
        device_ip=device_info["device_ip"],
        platform=device_info["platform"],
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    req: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    تمدید توکن دسترسی با استفاده از refresh token.
    Validates session is still active.
    """
    from jose import jwt, JWTError
    
    try:
        payload = jwt.decode(
            req.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm]
        )
        
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="توکن نامعتبر است")
        
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="توکن نامعتبر است")
        
        stmt = select(User).where(User.id == int(user_id))
        user = (await db.execute(stmt)).scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        
        if user.is_deleted:
            raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")
        
        # Validate session exists and is active
        session = await get_session_by_refresh_token(db, req.refresh_token)
        if not session:
            raise HTTPException(status_code=401, detail="نشست منقضی شده یا نامعتبر است. لطفاً دوباره وارد شوید.")
        
        # Update last_active_at
        session.last_active_at = datetime.utcnow()
        
        # Issue new tokens
        access_token_expires = timedelta(minutes=60)
        refresh_token_expires = timedelta(days=30)
        
        new_access = create_access_token(
            subject=user.id,
            expires_delta=access_token_expires
        )
        new_refresh = create_refresh_token(
            subject=user.id,
            expires_delta=refresh_token_expires
        )
        
        # Update session with new refresh token hash
        session.refresh_token_hash = hash_token(new_refresh)
        await db.commit()
        
        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer"
        }
        
    except JWTError:
        raise HTTPException(status_code=401, detail="توکن منقضی یا نامعتبر است")

@router.post("/request-otp", response_model=dict)
async def request_otp(
    request: OTPRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    درخواست OTP برای ورود.
    اگر اینترنت وصل باشد -> کد به بات تلگرام ارسال می‌شود.
    اگر اینترنت قطع باشد یا کاربر تلگرام نداشته باشد -> کد SMS می‌شود.
    """
    mobile = request.mobile_number
    # اعتبارسنجی ساده شماره موبایل
    if not mobile.startswith("09") or len(mobile) != 11:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است")

    # پیدا کردن کاربر
    stmt = select(User).where(User.mobile_number == mobile)
    result = (await db.execute(stmt)).scalar_one_or_none()
    
    if not result:
        raise HTTPException(status_code=404, detail="کاربری با این شماره موبایل یافت نشد")

    if result.is_deleted:
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")

    # Rate limiting
    redis = await get_redis()
    rate_limit_key = f"otp_limit:{mobile}"
    limit_val = await redis.get(rate_limit_key)
    logger.info(f"OTP Request for {mobile}: LimitKey={rate_limit_key}, Exists={limit_val is not None}")
    
    if limit_val:
        logger.info(f"OTP Request Rate Limit Hit for {mobile}")
        raise HTTPException(status_code=429, detail="لطفاً ۲ دقیقه صبر کنید")

    # تولید کد ۵ رقمی
    # First, check if valid OTP already exists (Strict "One Code per 120s" rule)
    otp_key = f"otp:{mobile}"
    active_otp = await redis.get(otp_key)
    
    if active_otp:
        logger.info(f"OTP Request for {mobile}: Active OTP exists ({active_otp[:2]}***). Blocking new generation.")
        # Raise 429 so frontend triggers timer
        raise HTTPException(status_code=429, detail="کد قبلی هنوز معتبر است. لطفاً صبر کنید.")

    otp_code = str(random.randint(10000, 99999))
    logger.info(f"Generated NEW OTP for {mobile}: {otp_code[:2]}***")
    
    # ذخیره در Redis (۲ دقیقه اعتبار)
    await redis.setex(otp_key, 120, otp_code)
    await redis.setex(rate_limit_key, 120, "1")

    # تصمیم‌گیری برای روش ارسال
    is_connected = await is_internet_connected()
    has_telegram = result.telegram_id is not None
    
    logger.info(f"OTP Request for {mobile}: Connected={is_connected}, HasTelegram={has_telegram}, TelegramID={result.telegram_id}")

    sent_via_telegram = False
    sent_via_sms = False
    
    # اولویت ۱: تلگرام (اگر اینترنت وصله و کاربر تلگرام داره)
    if is_connected and has_telegram:
        try:
            msg_text = f"🔐 کد ورود شما: `{otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
            await send_telegram_message(result.telegram_id, msg_text)
            sent_via_telegram = True
            logger.info(f"OTP sent via Telegram to {mobile}")
        except Exception as e:
            logger.error(f"Failed to send OTP via Telegram: {e}")
            # Fallback to SMS handled below
    
    # اولویت ۲: SMS (اگر تلگرام نشد یا اینترنت قطعه)
    if not sent_via_telegram:
        logger.info(f"Attempting SMS fallback for {mobile} (Connected={is_connected}, HasTelegram={has_telegram})")
        # اگر اینترنت وصله ولی تلگرام ارسال نشد (یا کاربر تلگرام نداره) -> SMS
        # اگر اینترنت قطعه -> SMS
        if send_otp_sms(mobile, otp_code):
            sent_via_sms = True
            logger.info(f"OTP sent via SMS to {mobile}")
        else:
            logger.error(f"Failed to send OTP via SMS to {mobile}")
            raise HTTPException(status_code=500, detail="خطا در ارسال کد تایید")

    return {
        "detail": "کد تایید ارسال شد",
        "method": "telegram" if sent_via_telegram else "sms",
        "expires_in": 120
    }

@router.post("/resend-otp-sms", response_model=dict)
async def resend_otp_sms(
    request: OTPRequest,
):
    """
    ارسال مجدد کد فعال از طریق پیامک (Fallback).
    این متد زمانی صدا زده می‌شود که کد قبلاً (مثلاً به تلگرام) ارسال شده
    و هنوز منقضی نشده، اما کاربر می‌خواهد آن را via SMS دریافت کند.
    """
    mobile = request.mobile_number
    redis = await get_redis()
    
    logger.info(f"Resend SMS Request for {mobile}")

    # Check if OTP exists
    otp_key = f"otp:{mobile}"
    otp_code = await redis.get(otp_key)
    
    if not otp_code:
         # Session expired
         logger.warning(f"Resend failed for {mobile}: OTP code not found or expired")
         raise HTTPException(status_code=400, detail="کد تایید منقضی شده است. لطفاً مجدد درخواست دهید.")
         
    # Rate limit for SMS resend (prevent spamming button)
    sms_limit_key = f"sms_limit:{mobile}"
    if await redis.get(sms_limit_key):
        logger.info(f"Resend SMS Rate Limit Hit for {mobile}")
        raise HTTPException(status_code=429, detail="لطفاً ۱ دقیقه صبر کنید")
        
    logger.info(f"Resending Existing OTP via SMS to {mobile}: {otp_code[:2]}***")
    
    # Get remaining TTL for the timer
    ttl = await redis.ttl(otp_key)
    logger.info(f"TTL for {mobile} (resend-otp-sms): {ttl}")
    
    if ttl < 0: 
        # -1 (no expiry) or -2 (missing). Should not happen here due to check above.
        # But if it does, default to 0 (or strictly, handle error)
        logger.warning(f"Unexpected TTL for {mobile}: {ttl}")
        ttl = 0


    # Send SMS
    if send_otp_sms(mobile, otp_code):
        await redis.setex(sms_limit_key, 60, "1") # 1 min limit for SMS resend
        return {
            "detail": "کد از طریق پیامک ارسال شد",
            "expires_in": ttl
        }
    else:
        logger.error(f"Failed to resend OTP via SMS to {mobile}")
        raise HTTPException(status_code=500, detail="خطا در ارسال پیامک")




@router.post("/verify-otp")
async def verify_otp(
    request: OTPVerify,
    raw_request: Request,
    db: AsyncSession = Depends(get_db)
):
    mobile = request.mobile_number
    code = request.code
    
    redis = await get_redis()
    otp_key = f"otp:{mobile}"
    stored_code = await redis.get(otp_key)
    
    if not stored_code or stored_code != code:
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
        
    # کد درست است
    stmt = select(User).where(User.mobile_number == mobile)
    user = (await db.execute(stmt)).scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        
    # پاک کردن کد OTP
    await redis.delete(otp_key)
    await redis.delete(f"otp_limit:{mobile}")
    
    # Generate tokens
    access_token_expires = timedelta(minutes=60)
    refresh_token_expires = timedelta(days=30)
    
    access_token = create_access_token(
        subject=user.id,
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        subject=user.id,
        expires_delta=refresh_token_expires
    )
    
    # Session management
    device_info = _extract_device_info(raw_request)
    session_result = await handle_login_session(
        db, user, refresh_token,
        device_name=device_info["device_name"],
        device_ip=device_info["device_ip"],
        platform=device_info["platform"],
    )
    
    if session_result["action"] == "blocked":
        raise HTTPException(status_code=429, detail=session_result["reason"])
    
    if session_result["action"] == "approval_required":
        login_req = session_result["request"]
        return {
            "status": "approval_required",
            "login_request_id": str(login_req.id),
            "message": "درخواست ورود شما ارسال شد. منتظر تایید از دستگاه اصلی باشید.",
            "expires_at": login_req.expires_at.isoformat(),
        }
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/webapp-login", response_model=Token)
async def webapp_login(
    login_data: WebAppLogin,
    raw_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Login via Telegram WebApp data validation.
    """
    init_data = login_data.init_data
    
    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")

    # Parse and validate init_data
    try:
        from urllib.parse import parse_qsl
        parsed_data = dict(parse_qsl(init_data))
        hash_val = parsed_data.pop('hash')
        
        # Sort keys
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        # Calculate HMAC
        secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_val:
            raise HTTPException(status_code=403, detail="Invalid data hash")
            
        # Check auth_date to prevent replay attacks (optional but recommended)
        auth_date = int(parsed_data.get('auth_date', 0))
        if time.time() - auth_date > 86400: # 1 day expiry
             raise HTTPException(status_code=403, detail="Data expired")

        user_data = json.loads(parsed_data['user'])
        telegram_id = user_data['id']
        
        # Find user by telegram_id
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await db.execute(stmt)).scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not registered")
            
        if user.is_deleted:
            raise HTTPException(status_code=403, detail="User is blocked")

        # Generate tokens
        access_token = create_access_token(subject=user.id)
        refresh_token = create_refresh_token(subject=user.id)
        
        # Session management
        device_info = _extract_device_info(raw_request)
        # Override platform to telegram_mini_app for webapp login
        device_info["platform"] = Platform.TELEGRAM_MINI_APP
        device_info["device_name"] = "Telegram Mini App"
        
        session_result = await handle_login_session(
            db, user, refresh_token,
            device_name=device_info["device_name"],
            device_ip=device_info["device_ip"],
            platform=device_info["platform"],
        )
        
        if session_result["action"] == "blocked":
            raise HTTPException(status_code=429, detail=session_result["reason"])
        
        if session_result["action"] == "approval_required":
            login_req = session_result["request"]
            return {
                "status": "approval_required",
                "login_request_id": str(login_req.id),
                "message": "درخواست ورود شما ارسال شد. منتظر تایید از دستگاه اصلی باشید.",
                "expires_at": login_req.expires_at.isoformat(),
            }
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
        
    except Exception as e:
        logger.error(f"WebApp login error: {e}")
        raise HTTPException(status_code=400, detail="Authentication failed")

class SetupPasswordRequest(BaseModel):
    password: str

@router.post("/setup-password", summary="تغییر رمز عبور اجباری مدیران")
async def setup_admin_password(
    req: SetupPasswordRequest,
    db: AsyncSession = Depends(get_db),
    # Note: Use oauth2 token manually here instead of Depends(get_current_user)
    # because get_current_user raises 403 on must_change_password!
    token: str = Depends(oauth2_scheme)
):
    from api.deps import get_current_user
    # We must decode token manually since get_current_user blocks it
    from jose import jwt, JWTError
    from pydantic import ValidationError
    
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        token_data = payload.get("sub")
        if token_data is None: raise JWTError()
    except (JWTError, ValidationError):
        raise HTTPException(status_code=401, detail="Invalid token")
        
    user_id = int(token_data)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        user = (await db.execute(select(User).where(User.telegram_id == user_id))).scalar_one_or_none()
        
    if not user: raise HTTPException(status_code=404)
    if not user.must_change_password:
        raise HTTPException(status_code=400, detail="شما نیازی به تغییر رمز عبور ندارید")
        
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="رمز عبور باید حداقل ۶ کاراکتر باشد")
        
    user.admin_password_hash = get_password_hash(req.password)
    user.must_change_password = False
    
    await db.commit()
    return {"detail": "رمز عبور با موفقیت ثبت شد"}
