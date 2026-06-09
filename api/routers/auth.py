from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, Security
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, update
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel
from typing import Optional
import random
import hashlib
import hmac
import time
from jose import JWTError, jwt
from core.db import get_db
from models.user import User, UserRole, set_legacy_has_bot_access_compatibility
from models.accountant_relation import AccountantRelationStatus
from models.customer_relation import CustomerRelationStatus, CustomerTier
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
from api.deps import get_current_user, get_current_user_optional, oauth2_scheme, oauth2_scheme_optional, api_key_header
import schemas


from core.services.session_service import (
    ACCOUNT_INACTIVE_BLOCK_REASON,
    deactivate_session,
    get_active_sessions,
    get_session_by_refresh_token,
    handle_login_session,
    hash_token,
    publish_session_revocation,
)
from core.services.user_account_status_service import get_user_account_status, is_user_global_web_locked
from core.services.avatar_service import resolve_owned_avatar_file_id
from models.session import Platform, UserSession
import uuid
from core.utils import normalize_persian_numerals, utc_now, utc_now_naive
from core.server_routing import SERVER_FOREIGN, server_from_request
from core.services.chat_room_service import ensure_mandatory_channel_membership
from core.services.accountant_relation_service import (
    get_active_accountant_relation_for_accountant,
    get_pending_accountant_relation_by_invitation_token,
    is_accountant_invitation_token,
    is_user_accountant,
)
from core.services.customer_relation_service import (
    get_active_customer_relation_for_customer,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
    is_user_customer,
)


router = APIRouter()
logger = logging.getLogger(__name__)

TEST_ACCOUNT_SWITCH_CLAIM = "dev_account_switch"
TEST_ACCOUNT_SWITCH_DEVICE_NAME = "Dashboard Test Switcher"


def _raise_inactive_account_error() -> None:
    raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")


def _raise_for_session_blocked_reason(reason: str) -> None:
    if reason == ACCOUNT_INACTIVE_BLOCK_REASON:
        _raise_inactive_account_error()
    raise HTTPException(status_code=429, detail=reason)


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


def _dev_bypass_requires_single_session(user: User | object) -> bool:
    role = getattr(user, "role", None)
    if role in (UserRole.SUPER_ADMIN, UserRole.MIDDLE_MANAGER):
        return True

    raw_max_sessions = getattr(user, "max_sessions", 1)
    try:
        max_sessions = int(raw_max_sessions)
    except (TypeError, ValueError):
        max_sessions = 1
    return min(max(max_sessions, 1), 3) == 1


async def _clear_dev_bypass_sessions(
    db: AsyncSession,
    *,
    user_id: int,
    clear_all_active: bool,
) -> None:
    revoked_sessions = []
    active_sessions = await get_active_sessions(db, user_id)
    for session in active_sessions:
        if clear_all_active or session.device_name in {TEST_ACCOUNT_SWITCH_DEVICE_NAME, "Dev Bypass Terminal"}:
            await deactivate_session(db, session)
            revoked_sessions.append(session)

    if revoked_sessions:
        await publish_session_revocation(user_id, revoked_sessions)


def _login_home_server(raw_request: Request, *, is_telegram: bool = False) -> str:
    return server_from_request(raw_request, force_telegram_foreign=is_telegram)


def _extract_request_real_ip(raw_request: Request) -> str:
    client_ip = raw_request.client.host if raw_request.client else ""
    forwarded = raw_request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else client_ip


def _is_local_dev_request(raw_request: Request) -> bool:
    real_ip = _extract_request_real_ip(raw_request)
    if real_ip in ("127.0.0.1", "::1", "localhost"):
        return True
    return real_ip.startswith("172.") or real_ip.startswith("192.168.") or real_ip.startswith("10.")


def _has_test_switch_claim(token: Optional[str]) -> bool:
    if not token:
        return False

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return False

    return payload.get(TEST_ACCOUNT_SWITCH_CLAIM) is True


def _test_switch_token_data() -> dict:
    return {TEST_ACCOUNT_SWITCH_CLAIM: True}


def _serialize_test_switch_user_option(
    user: User,
    *,
    is_accountant: bool,
    is_customer: bool,
    customer_tier: CustomerTier | None,
) -> "TestSwitchUserOption":
    return TestSwitchUserOption(
        id=user.id,
        full_name=user.full_name,
        account_name=user.account_name,
        mobile_number=user.mobile_number,
        role=user.role,
        is_accountant=is_accountant,
        is_customer=is_customer,
        customer_tier=customer_tier,
    )


def _assert_test_switch_access(
    raw_request: Request,
    *,
    current_user: User | None,
    token: str | None,
    dev_key: str | None,
) -> None:
    if dev_key and dev_key == settings.dev_api_key:
        return

    if _is_local_dev_request(raw_request):
        return

    if current_user and current_user.role == UserRole.SUPER_ADMIN:
        return

    if _has_test_switch_claim(token):
        return

    raise HTTPException(
        status_code=403,
        detail="سوییچ موقت حساب فقط در محیط توسعه یا نشست‌های سوییچ‌شده مجاز است",
    )

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
    suspended_refresh_token: Optional[str] = None

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


class TestSwitchUserOption(BaseModel):
    id: int
    full_name: str | None = None
    account_name: str
    mobile_number: str
    role: UserRole
    is_accountant: bool = False
    is_customer: bool = False
    customer_tier: CustomerTier | None = None

# --- Endpoints ---

def _serialize_current_user_response(
    current_user: User,
    *,
    is_accountant: bool,
    is_customer: bool,
    accountant_owner_user_id: int | None = None,
    accountant_owner_account_name: str | None = None,
    customer_tier: CustomerTier | None = None,
    customer_owner_user_id: int | None = None,
    customer_owner_account_name: str | None = None,
) -> schemas.UserRead:
    return schemas.UserRead.model_validate(current_user).model_copy(
        update={
            "is_accountant": is_accountant,
            "accountant_owner_user_id": accountant_owner_user_id,
            "accountant_owner_account_name": accountant_owner_account_name,
            "is_customer": is_customer,
            "customer_tier": customer_tier,
            "customer_owner_user_id": customer_owner_user_id,
            "customer_owner_account_name": customer_owner_account_name,
        }
    )


async def _load_current_user_relation_context(
    db: AsyncSession,
    current_user: User,
) -> dict[str, object]:
    accountant_relation = await get_active_accountant_relation_for_accountant(db, current_user.id)
    customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
    return {
        "is_accountant": accountant_relation is not None,
        "accountant_owner_user_id": accountant_relation.owner_user_id if accountant_relation else None,
        "accountant_owner_account_name": (
            accountant_relation.owner_user.account_name
            if accountant_relation and accountant_relation.owner_user and not accountant_relation.owner_user.is_deleted
            else None
        ),
        "is_customer": customer_relation is not None,
        "customer_tier": customer_relation.customer_tier if customer_relation else None,
        "customer_owner_user_id": customer_relation.owner_user_id if customer_relation else None,
        "customer_owner_account_name": (
            customer_relation.owner_user.account_name
            if customer_relation and customer_relation.owner_user and not customer_relation.owner_user.is_deleted
            else None
        ),
    }

@router.get("/me", response_model=schemas.UserRead)
async def read_users_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """دریافت اطلاعات کاربر جاری"""
    return _serialize_current_user_response(
        current_user,
        **await _load_current_user_relation_context(db, current_user),
    )


@router.put("/me/avatar", response_model=schemas.UserRead)
async def update_my_avatar(
    payload: schemas.UserAvatarUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.avatar_file_id = await resolve_owned_avatar_file_id(
        db,
        actor_id=current_user.id,
        avatar_file_id=payload.avatar_file_id,
    )
    await db.commit()
    await db.refresh(current_user)
    return _serialize_current_user_response(
        current_user,
        **await _load_current_user_relation_context(db, current_user),
    )


@router.put("/me/address", response_model=schemas.UserAddressUpdateResponse)
async def update_my_address(
    payload: schemas.UserAddressUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.address = payload.address
    await db.commit()
    await db.refresh(current_user)
    return schemas.UserAddressUpdateResponse(address=current_user.address)


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
    if inv.expires_at < utc_now_naive():
        raise HTTPException(status_code=400, detail="دعوت‌نامه منقضی شده است")

    if is_accountant_invitation_token(req.token):
        relation = await get_pending_accountant_relation_by_invitation_token(db, req.token)
        if not relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه حسابدار نامعتبر یا منقضی شده است")
    elif is_customer_invitation_token(req.token):
        relation = await get_pending_customer_relation_by_invitation_token(db, req.token)
        if not relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه مشتری نامعتبر یا منقضی شده است")
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
    submitted_code = normalize_persian_numerals(req.code)
    
    if not stored_code or stored_code != submitted_code:
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
    
    if not inv or inv.is_used or inv.expires_at < utc_now_naive():
        raise HTTPException(status_code=400, detail="دعوت‌نامه نامعتبر است")

    accountant_relation = None
    customer_relation = None
    if is_accountant_invitation_token(req.token):
        accountant_relation = await get_pending_accountant_relation_by_invitation_token(db, req.token)
        if not accountant_relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه حسابدار نامعتبر یا منقضی شده است")
    elif is_customer_invitation_token(req.token):
        customer_relation = await get_pending_customer_relation_by_invitation_token(db, req.token)
        if not customer_relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه مشتری نامعتبر یا منقضی شده است")
        
    # 3. Create User
    new_user = User(
        account_name=inv.account_name,
        mobile_number=inv.mobile_number,
        role=inv.role,
        full_name=inv.account_name, # Temporary full name
        address=req.address,
        telegram_id=None, # Web only user
        home_server=_login_home_server(raw_request),
        max_sessions=1,
    )
    set_legacy_has_bot_access_compatibility(
        new_user,
        enabled=accountant_relation is None and customer_relation is None,
    )
    
    db.add(new_user)
    
    # 4. Mark invitation as used
    inv.is_used = True
    if accountant_relation:
        accountant_relation.accountant_user_id = new_user.id
        accountant_relation.status = AccountantRelationStatus.ACTIVE
        accountant_relation.activated_at = utc_now()
        accountant_relation.deleted_at = None
    if customer_relation:
        customer_relation.customer_user_id = new_user.id
        customer_relation.status = CustomerRelationStatus.ACTIVE
        customer_relation.activated_at = utc_now()
        customer_relation.deleted_at = None
    
    try:
        await db.flush()
        if accountant_relation:
            accountant_relation.accountant_user_id = new_user.id
        if customer_relation:
            customer_relation.customer_user_id = new_user.id
        await ensure_mandatory_channel_membership(db, user=new_user)
        await db.commit()
        await db.refresh(new_user)
    except Exception as e:
        await db.rollback()
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="خطا در ثبت کاربر")
        
    # 5. Cleanup Redis
    await redis.delete(f"reg_otp:{req.token}")
    await redis.delete(verify_key)
    
    # 6. Generate refresh token first
    refresh_token_expires = timedelta(days=30)
    access_token_expires = timedelta(minutes=60)
    
    refresh_token = create_refresh_token(
        subject=new_user.id,
        expires_delta=refresh_token_expires
    )
    
    # 7. Create first session (always succeeds for new user - primary)
    device_info = _extract_device_info(raw_request)
    session_result = await handle_login_session(
        db, new_user, refresh_token,
        device_name=device_info["device_name"],
        device_ip=device_info["device_ip"],
        platform=device_info["platform"],
        home_server=new_user.home_server,
    )
    
    # Generate access token with session_id
    session_id = str(session_result["session"].id) if session_result.get("session") else None
    access_token = create_access_token(
        subject=new_user.id,
        expires_delta=access_token_expires,
        session_id=session_id,
        server_id=new_user.home_server,
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

        if is_user_global_web_locked(user):
            _raise_inactive_account_error()
        
        # Validate session exists and is active
        session = await get_session_by_refresh_token(db, req.refresh_token)
        if not session:
            raise HTTPException(status_code=401, detail="نشست منقضی شده یا نامعتبر است. لطفاً دوباره وارد شوید.")
            
        # Revoke if the overall 30-day validity has passed
        from core.utils import utc_now
        if session.expires_at and session.expires_at < utc_now():
            raise HTTPException(status_code=401, detail="SESSION_EXPIRED_REQUIRE_OTP")
        # Update last_active_at
        session.last_active_at = utc_now()
        
        # Issue new token
        access_token_expires = timedelta(minutes=60)
        access_token_kwargs = {
            "subject": user.id,
            "expires_delta": access_token_expires,
            "session_id": str(session.id),
            "server_id": session.home_server or user.home_server or SERVER_FOREIGN,
        }
        if payload.get(TEST_ACCOUNT_SWITCH_CLAIM) is True:
            access_token_kwargs["data"] = _test_switch_token_data()

        new_access = create_access_token(**access_token_kwargs)
        
        await db.commit()
        
        return {
            "access_token": new_access,
            "refresh_token": req.refresh_token, # keep the same refresh token
            "token_type": "bearer"
        }
        
    except JWTError:
        raise HTTPException(status_code=401, detail="توکن منقضی یا نامعتبر است")


@router.post("/dev-login")
async def dev_login(raw_request: Request, db: AsyncSession = Depends(get_db)):
    """ورود ویژه‌ی توسعه‌دهنده (بدون نیاز به کد، محدودیت سشن و ... مقدور از روی رزولوشن محلی)"""
    real_ip = _extract_request_real_ip(raw_request)
    is_local = _is_local_dev_request(raw_request)

    dev_key = raw_request.headers.get("X-DEV-API-KEY")
    if not is_local and (not dev_key or dev_key != settings.dev_api_key):
        raise HTTPException(status_code=403, detail="دسترسی فقط از محیط برنامه‌نویسی یا با کلید امکان‌پذیر است")
        
    dev_mobile = "09999999999"
    stmt = select(User).where(User.mobile_number == dev_mobile)
    user = (await db.execute(stmt)).scalar_one_or_none()
    login_home_server = _login_home_server(raw_request)
    
    if not user:
        user = User(
            account_name="dev_" + str(int(time.time())),
            mobile_number=dev_mobile,
            full_name="کاربر توسعه‌دهنده (تست)",
            address="توسعه‌دهنده",
            role=UserRole.SUPER_ADMIN,
        )
        db.add(user)
        await db.flush()
        user.home_server = login_home_server
        await ensure_mandatory_channel_membership(db, user=user)
        await db.commit()
        await db.refresh(user)
    else:
        user.home_server = login_home_server
        await ensure_mandatory_channel_membership(db, user=user)

    await _clear_dev_bypass_sessions(
        db,
        user_id=user.id,
        clear_all_active=True,
    )
        
    refresh_token = create_refresh_token(subject=user.id)
    device_info = _extract_device_info(raw_request)
    
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        device_name="Dev Bypass Terminal",
        device_ip=real_ip,
        platform=device_info["platform"],
        refresh_token_hash=hash_token(refresh_token),
        home_server=login_home_server,
        is_primary=True,
        is_active=True,
        expires_at=utc_now() + timedelta(days=365)
    )
    db.add(session)
    await db.commit()
    
    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        subject=user.id,
        expires_delta=access_token_expires,
        session_id=str(session.id),
        server_id=login_home_server,
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user.id,
        "role": user.role
    }


@router.get("/dev-switch/users", response_model=list[TestSwitchUserOption])
async def list_test_switch_users(
    raw_request: Request,
    search: str | None = Query(default=None, min_length=1, max_length=100),
    limit: int = Query(default=80, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
    current_user: Optional[User] = Depends(get_current_user_optional),
    dev_key: Optional[str] = Security(api_key_header),
):
    _assert_test_switch_access(
        raw_request,
        current_user=current_user,
        token=token,
        dev_key=dev_key,
    )

    stmt = select(User).where(User.is_deleted == False).order_by(User.id.desc())
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                User.full_name.ilike(pattern),
                User.account_name.ilike(pattern),
                User.mobile_number.ilike(pattern),
            )
        )
    stmt = stmt.limit(limit)
    users = (await db.execute(stmt)).scalars().all()

    result: list[TestSwitchUserOption] = []
    for listed_user in users:
        customer_relation = await get_active_customer_relation_for_customer(db, listed_user.id)
        result.append(
            _serialize_test_switch_user_option(
                listed_user,
                is_accountant=await is_user_accountant(db, listed_user.id),
                is_customer=customer_relation is not None,
                customer_tier=customer_relation.customer_tier if customer_relation else None,
            )
        )

    return result


@router.post("/dev-switch/{target_user_id}", response_model=Token)
async def switch_test_account(
    target_user_id: int,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme_optional),
    current_user: Optional[User] = Depends(get_current_user_optional),
    dev_key: Optional[str] = Security(api_key_header),
):
    _assert_test_switch_access(
        raw_request,
        current_user=current_user,
        token=token,
        dev_key=dev_key,
    )

    target_user = await db.get(User, target_user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    if target_user.is_deleted:
        raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")

    if get_user_account_status(target_user).value == "inactive":
        _raise_inactive_account_error()

    login_home_server = _login_home_server(raw_request)
    target_user.home_server = login_home_server

    await _clear_dev_bypass_sessions(
        db,
        user_id=target_user.id,
        clear_all_active=_dev_bypass_requires_single_session(target_user),
    )

    refresh_token = create_refresh_token(subject=target_user.id, data=_test_switch_token_data())
    device_info = _extract_device_info(raw_request)
    real_ip = _extract_request_real_ip(raw_request)

    session = UserSession(
        id=uuid.uuid4(),
        user_id=target_user.id,
        device_name=TEST_ACCOUNT_SWITCH_DEVICE_NAME,
        device_ip=real_ip,
        platform=device_info["platform"],
        refresh_token_hash=hash_token(refresh_token),
        home_server=login_home_server,
        is_primary=_dev_bypass_requires_single_session(target_user),
        is_active=True,
        expires_at=utc_now() + timedelta(days=30),
    )
    db.add(session)
    await ensure_mandatory_channel_membership(db, user=target_user)
    await db.commit()

    access_token = create_access_token(
        subject=target_user.id,
        data=_test_switch_token_data(),
        expires_delta=timedelta(minutes=60),
        session_id=str(session.id),
        server_id=login_home_server,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
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
    mobile = normalize_persian_numerals(request.mobile_number)
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

    if get_user_account_status(result).value == "inactive":
        _raise_inactive_account_error()

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
    mobile = normalize_persian_numerals(request.mobile_number)
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
    mobile = normalize_persian_numerals(request.mobile_number)
    code = normalize_persian_numerals(request.code)
    
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

    if get_user_account_status(user).value == "inactive":
        await redis.delete(otp_key)
        await redis.delete(f"otp_limit:{mobile}")
        _raise_inactive_account_error()
        
    # پاک کردن کد OTP
    await redis.delete(otp_key)
    await redis.delete(f"otp_limit:{mobile}")
    
    # Generate tokens
    access_token_expires = timedelta(minutes=60)
    refresh_token_expires = timedelta(days=30)
    
    refresh_token = create_refresh_token(
        subject=user.id,
        expires_delta=refresh_token_expires
    )
    
    # Session management
    device_info = _extract_device_info(raw_request)
    login_home_server = _login_home_server(raw_request)
    user.home_server = login_home_server
    session_result = await handle_login_session(
        db, user, refresh_token,
        device_name=device_info["device_name"],
        device_ip=device_info["device_ip"],
        platform=device_info["platform"],
        suspended_refresh_token=request.suspended_refresh_token,
        home_server=login_home_server,
    )
    
    if session_result["action"] == "blocked":
        _raise_for_session_blocked_reason(session_result["reason"])
    
    if session_result["action"] == "approval_required":
        login_req = session_result["request"]
        return {
            "status": "approval_required",
            "login_request_id": str(login_req.id),
            "message": "درخواست ورود شما ارسال شد. منتظر تایید از دستگاه اصلی باشید.",
            "expires_at": login_req.expires_at.isoformat(),
        }
    
    # Generate access token with session_id
    session_id = str(session_result["session"].id) if session_result.get("session") else None
    access_token = create_access_token(
        subject=user.id,
        expires_delta=access_token_expires,
        session_id=session_id,
        server_id=login_home_server,
    )
    
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
        blocked_error = None
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
        refresh_token = create_refresh_token(subject=user.id)
        
        # Session management
        device_info = _extract_device_info(raw_request)
        # Override platform to telegram_mini_app for webapp login
        device_info["platform"] = Platform.TELEGRAM_MINI_APP
        device_info["device_name"] = "Telegram Mini App"
        login_home_server = SERVER_FOREIGN
        user.home_server = login_home_server
        
        session_result = await handle_login_session(
            db, user, refresh_token,
            device_name=device_info["device_name"],
            device_ip=device_info["device_ip"],
            platform=device_info["platform"],
            home_server=login_home_server,
        )
        
        if session_result["action"] == "blocked":
            if session_result["reason"] == ACCOUNT_INACTIVE_BLOCK_REASON:
                blocked_error = HTTPException(status_code=403, detail="INACTIVE_ACCOUNT_BLOCKED")
            else:
                blocked_error = HTTPException(status_code=429, detail=session_result["reason"])
        
        if session_result["action"] == "approval_required":
            login_req = session_result["request"]
            return {
                "status": "approval_required",
                "login_request_id": str(login_req.id),
                "message": "درخواست ورود شما ارسال شد. منتظر تایید از دستگاه اصلی باشید.",
                "expires_at": login_req.expires_at.isoformat(),
            }

        if blocked_error is not None:
            raise blocked_error
        
        session_id = str(session_result["session"].id) if session_result.get("session") else None
        access_token = create_access_token(subject=user.id, session_id=session_id, server_id=login_home_server)
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except HTTPException as exc:
        if exc.detail == "INACTIVE_ACCOUNT_BLOCKED":
            raise HTTPException(status_code=403, detail="User is blocked")
        logger.error(f"WebApp login error: {exc}")
        raise HTTPException(status_code=400, detail="Authentication failed")
        
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
