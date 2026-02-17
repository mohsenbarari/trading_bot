from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
import logging
from pydantic import BaseModel
import random
import hashlib
import hmac
import time
from core.db import get_db
from models.user import User
from models.invitation import Invitation
from core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password
)
from core.config import settings
from bot.utils.redis_helpers import get_redis
from core.notifications import send_telegram_message
from core.sms import send_otp_sms, send_sms
from core.connectivity import is_internet_connected

router = APIRouter()
logger = logging.getLogger(__name__)

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

# --- Endpoints ---

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
    
    # Mark as verified (optional, or just rely on client flow if secure enough)
    # Ideally we should issue a temporary registration token, but for simplicity
    # and since we re-validate token in complete step, we can just return OK.
    # However, to prevent skipping verify step, let's set a flag in Redis
    
    verify_key = f"reg_verified:{req.token}"
    await redis.setex(verify_key, 600, "1") # 10 mins to complete registration
    
    return {"detail": "کد تایید شد"}

@router.post("/register-complete", response_model=Token)
async def register_complete(
    req: RegisterComplete,
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
    
    # 6. Generate Tokens
    access_token = create_access_token(new_user.id)
    refresh_token = create_refresh_token(new_user.id)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

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
    otp_code = str(random.randint(10000, 99999))
    logger.info(f"Generated NEW OTP for {mobile}: {otp_code[:2]}***")
    
    # ذخیره در Redis (۲ دقیقه اعتبار)
    otp_key = f"otp:{mobile}"
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
    
    # Send SMS
    if send_otp_sms(mobile, otp_code):
        await redis.setex(sms_limit_key, 60, "1") # 1 min limit for SMS resend
        return {"detail": "کد از طریق پیامک ارسال شد"}
    else:
        logger.error(f"Failed to resend OTP via SMS to {mobile}")
        raise HTTPException(status_code=500, detail="خطا در ارسال پیامک")



@router.post("/verify-otp", response_model=Token)
async def verify_otp(
    request: OTPVerify,
    db: AsyncSession = Depends(get_db)
):
    mobile = request.mobile_number
    code = request.code
    
    redis = await get_redis()
    otp_key = f"otp:{mobile}"
    stored_code = await redis.get(otp_key)
    
    if not stored_code or stored_code != code:
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
        
    # کد درست است - توکن صادر کن
    stmt = select(User).where(User.mobile_number == mobile)
    user = (await db.execute(stmt)).scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
        
    # پاک کردن کد OTP
    await redis.delete(otp_key)
    await redis.delete(f"otp_limit:{mobile}") # Reset rate limit on success
    
    # تولید توکن با user_id (قبلاً telegram_id بود)
    # برای backward compatibility فعلا هر دو رو میذاریم توی payload ولی subject اصلی user_id میشه
    access_token_expires = timedelta(minutes=60) # 1 ساعت
    refresh_token_expires = timedelta(days=30)
    
    # Subject is now User ID (Database ID)
    access_token = create_access_token(
        subject=user.id,
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        subject=user.id,
        expires_delta=refresh_token_expires
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/webapp-login", response_model=Token)
async def webapp_login(
    login_data: WebAppLogin,
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
        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
        
    except Exception as e:
        logger.error(f"WebApp login error: {e}")
        raise HTTPException(status_code=400, detail="Authentication failed")
