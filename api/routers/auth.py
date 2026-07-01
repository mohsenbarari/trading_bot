from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel
from typing import Optional
from urllib.parse import quote
import random
import secrets
import hashlib
import hmac
import time
from jose import JWTError, jwt
from core.db import get_db
from models.user import User, UserRole, set_legacy_has_bot_access_compatibility
from models.accountant_relation import AccountantRelation, AccountantRelationStatus
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.invitation import Invitation
from core.enums import NotificationCategory, NotificationLevel
from core.security import (
    constant_time_secret_equals,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password
)
from core.config import settings
import json
from bot.utils.redis_helpers import get_redis
from core.sms import send_otp_sms, send_sms
from core.connectivity import is_internet_connected
from api.deps import get_current_user, oauth2_scheme
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
from core.session_authority import assert_login_allowed_for_server
from core.services.user_account_status_service import get_user_account_status, is_user_global_web_locked
from core.services.avatar_service import resolve_owned_avatar_file_id
from core.services.bot_access_policy import bot_access_denial_message, evaluate_bot_access
from core.services.telegram_link_token_service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenError,
    build_telegram_deep_link,
    build_telegram_start_parameter,
    create_telegram_link_token,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
    TelegramNotificationRecipient,
    enqueue_telegram_notifications,
)
from models.session import Platform, UserSession
import uuid
from core.utils import create_user_notification, normalize_persian_numerals, utc_now, utc_now_naive
from core.notifications import send_telegram_message
from core.server_routing import SERVER_FOREIGN, server_from_request
from core.request_logging import client_ip_from_request
from core.services.chat_room_service import ensure_mandatory_channel_membership
from core.services.accountant_relation_service import (
    get_active_accountant_relation_for_accountant,
    get_pending_accountant_relation_by_invitation_token,
    is_user_accountant,
    is_accountant_invitation_token,
)
from core.services.customer_relation_service import (
    get_active_customer_relation_for_customer,
    get_pending_customer_relation_by_invitation_token,
    is_customer_invitation_token,
    is_user_customer,
)


router = APIRouter()
logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 120
OTP_VERIFY_FAILURE_TTL_SECONDS = OTP_TTL_SECONDS
OTP_VERIFY_LOCK_SECONDS = 300
OTP_VERIFY_SUBJECT_MAX_FAILURES = 5
OTP_VERIFY_IP_MAX_FAILURES = 30
OTP_VERIFY_LOCKED_DETAIL = "تعداد تلاش‌های ناموفق زیاد است. چند دقیقه دیگر دوباره تلاش کنید."


def _deliver_otp_via_staging_log(*, mobile: str, otp_code: str, purpose: str) -> bool:
    if settings.environment != "staging" or not settings.staging_log_otp_codes:
        return False

    logger.warning(
        "STAGING_AUTH_VALUE_FOR_TEST_ONLY purpose=%s mobile=%s value=%s",
        purpose,
        mobile,
        otp_code,
        extra={
            "staging_auth_purpose": purpose,
            "staging_auth_value": otp_code,
        },
    )
    return True


def _redis_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _stable_key_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _request_client_host(raw_request: Request | None) -> str | None:
    if raw_request is None:
        return None
    host = client_ip_from_request(raw_request)
    if not host:
        return None
    return str(host).strip() or None


def _otp_verify_subject_key(subject: str) -> str:
    return f"otp_verify_fail:subject:{_stable_key_digest(subject)}"


def _otp_verify_subject_lock_key(subject: str) -> str:
    return f"otp_verify_lock:subject:{_stable_key_digest(subject)}"


def _otp_verify_ip_key(raw_request: Request | None) -> str | None:
    host = _request_client_host(raw_request)
    if not host:
        return None
    return f"otp_verify_fail:ip:{_stable_key_digest(host)}"


def _otp_verify_ip_lock_key(raw_request: Request | None) -> str | None:
    host = _request_client_host(raw_request)
    if not host:
        return None
    return f"otp_verify_lock:ip:{_stable_key_digest(host)}"


async def _otp_counter_ttl(redis, otp_key: str) -> int:
    try:
        ttl = int(await redis.ttl(otp_key))
    except Exception:
        ttl = OTP_VERIFY_FAILURE_TTL_SECONDS
    if ttl <= 0:
        return OTP_VERIFY_FAILURE_TTL_SECONDS
    return min(ttl, OTP_VERIFY_FAILURE_TTL_SECONDS)


async def _ensure_otp_verify_not_locked(redis, *, subject: str, raw_request: Request | None) -> None:
    if await redis.get(_otp_verify_subject_lock_key(subject)):
        raise HTTPException(status_code=429, detail=OTP_VERIFY_LOCKED_DETAIL)

    ip_lock_key = _otp_verify_ip_lock_key(raw_request)
    if ip_lock_key and await redis.get(ip_lock_key):
        raise HTTPException(status_code=429, detail=OTP_VERIFY_LOCKED_DETAIL)


async def _record_otp_verify_failure(redis, *, subject: str, raw_request: Request | None, otp_key: str) -> None:
    ttl = await _otp_counter_ttl(redis, otp_key)
    subject_key = _otp_verify_subject_key(subject)
    subject_count = int(await redis.incr(subject_key))
    await redis.expire(subject_key, ttl)

    locked = False
    if subject_count >= OTP_VERIFY_SUBJECT_MAX_FAILURES:
        await redis.delete(otp_key)
        await redis.setex(_otp_verify_subject_lock_key(subject), OTP_VERIFY_LOCK_SECONDS, "1")
        locked = True

    ip_key = _otp_verify_ip_key(raw_request)
    if ip_key:
        ip_count = int(await redis.incr(ip_key))
        await redis.expire(ip_key, ttl)
        if ip_count >= OTP_VERIFY_IP_MAX_FAILURES:
            await redis.setex(_otp_verify_ip_lock_key(raw_request), OTP_VERIFY_LOCK_SECONDS, "1")
            locked = True

    if locked:
        raise HTTPException(status_code=429, detail=OTP_VERIFY_LOCKED_DETAIL)


async def _clear_otp_verify_subject_failures(redis, *, subject: str) -> None:
    subject_key = _otp_verify_subject_key(subject)
    if await redis.get(subject_key):
        await redis.delete(subject_key)


def _should_announce_project_user_registration(accountant_relation, customer_relation) -> bool:
    return accountant_relation is None and customer_relation is None


def _project_user_joined_message(user: User) -> str:
    display_name = (getattr(user, "full_name", None) or getattr(user, "account_name", "") or "").strip()
    return f"{display_name} به لیست همکاران اضافه شدند."


def _project_user_profile_route(user: User) -> str:
    account_name = (getattr(user, "account_name", "") or "").strip()
    suffix = f"?account_name={quote(account_name)}" if account_name else ""
    return f"/users/{user.id}{suffix}"


async def _publish_project_user_joined_notifications(db: AsyncSession, new_user: User) -> None:
    if not getattr(new_user, "id", None):
        return

    recipient_stmt = select(User.id).where(
        User.is_deleted == False,
        User.id != new_user.id,
    )
    try:
        recipient_ids = list((await db.execute(recipient_stmt)).scalars().all())
    except Exception as exc:
        logger.warning("Project user joined notification recipient lookup failed: %s", exc)
        return

    customer_exists = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )
    accountant_exists = (
        select(AccountantRelation.id)
        .where(
            AccountantRelation.accountant_user_id == User.id,
            AccountantRelation.status == AccountantRelationStatus.ACTIVE,
            AccountantRelation.deleted_at.is_(None),
        )
        .exists()
    )
    telegram_recipient_stmt = select(User.id, User.telegram_id).where(
        User.is_deleted == False,
        User.id != new_user.id,
        User.telegram_id.is_not(None),
        ~customer_exists,
        ~accountant_exists,
    )
    try:
        telegram_recipient_rows = list((await db.execute(telegram_recipient_stmt)).all())
    except Exception as exc:
        telegram_recipient_rows = []
        logger.warning("Project user joined Telegram recipient lookup failed: %s", exc)

    message = _project_user_joined_message(new_user)
    route = _project_user_profile_route(new_user)
    for recipient_id in recipient_ids:
        try:
            await create_user_notification(
                db,
                int(recipient_id),
                message,
                NotificationLevel.INFO,
                NotificationCategory.SYSTEM,
                extra_payload={
                    "title": "پیام مدیریت",
                    "route": route,
                },
            )
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "Project user joined notification failed",
                extra={
                    "recipient_id": recipient_id,
                    "new_user_id": new_user.id,
                    "error": str(exc),
                },
            )

    telegram_recipients = [
        TelegramNotificationRecipient(user_id=int(user_id), telegram_id=int(telegram_id))
        for user_id, telegram_id in telegram_recipient_rows
        if telegram_id is not None
    ]
    if telegram_recipients:
        try:
            await enqueue_telegram_notifications(
                db,
                recipients=telegram_recipients,
                text=message,
                source_type=TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
                source_id=new_user.id,
                parse_mode=None,
                extra_payload={
                    "title": "پیام مدیریت",
                    "route": route,
                    "exclude_customers": True,
                },
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "Project user joined Telegram notification enqueue failed",
                extra={
                    "recipient_count": len(telegram_recipients),
                    "new_user_id": new_user.id,
                    "error": str(exc),
                },
            )


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

    ip = client_ip_from_request(request)
    return {"device_name": device_name, "device_ip": ip, "platform": platform}


async def _clear_dev_bypass_sessions(
    db: AsyncSession,
    *,
    user_id: int,
    clear_all_active: bool,
) -> None:
    revoked_sessions = []
    active_sessions = await get_active_sessions(db, user_id)
    for session in active_sessions:
        if clear_all_active or session.device_name == "Dev Bypass Terminal":
            await deactivate_session(db, session)
            revoked_sessions.append(session)

    if revoked_sessions:
        await publish_session_revocation(user_id, revoked_sessions)


def _login_home_server(raw_request: Request, *, is_telegram: bool = False) -> str:
    return server_from_request(raw_request, force_telegram_foreign=is_telegram)


def _extract_request_real_ip(raw_request: Request) -> str:
    return client_ip_from_request(raw_request) or ""


def _is_local_dev_request(raw_request: Request) -> bool:
    real_ip = _extract_request_real_ip(raw_request)
    if real_ip in ("127.0.0.1", "::1", "localhost"):
        return True
    return real_ip.startswith("172.") or real_ip.startswith("192.168.") or real_ip.startswith("10.")


def _is_dev_login_environment() -> bool:
    return (settings.environment or "").strip().lower() == "staging"


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
    token: str | None = None
    registration_token: str | None = None
    address: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TelegramLinkTokenResponse(BaseModel):
    telegram_linked: bool
    can_connect_telegram: bool
    bot_username: str | None = None
    telegram_url: str | None = None
    start_parameter: str | None = None
    expires_at: datetime | None = None
    expires_in: int | None = None
    detail: str | None = None


class PendingRegistrationContext(BaseModel):
    token: str
    account_name: str
    mobile_number: str
    role: UserRole

# --- Endpoints ---

def _registration_session_key(registration_token: str) -> str:
    return f"registration_session:{registration_token}"


async def _load_valid_invitation_by_token(
    db: AsyncSession,
    token: str,
    *,
    missing_detail: str = "دعوت‌نامه نامعتبر است",
) -> tuple[Invitation, object | None, object | None]:
    stmt = select(Invitation).where(Invitation.token == token)
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if not invitation:
        raise HTTPException(status_code=404, detail=missing_detail)
    if invitation.is_used:
        raise HTTPException(status_code=400, detail="دعوت‌نامه قبلاً استفاده شده است")
    if invitation.expires_at < utc_now_naive():
        raise HTTPException(status_code=400, detail="دعوت‌نامه منقضی شده است")

    accountant_relation = None
    customer_relation = None
    if is_accountant_invitation_token(token):
        accountant_relation = await get_pending_accountant_relation_by_invitation_token(db, token)
        if not accountant_relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه حسابدار نامعتبر یا منقضی شده است")
    elif is_customer_invitation_token(token):
        customer_relation = await get_pending_customer_relation_by_invitation_token(db, token)
        if not customer_relation:
            raise HTTPException(status_code=400, detail="دعوت‌نامه مشتری نامعتبر یا منقضی شده است")

    return invitation, accountant_relation, customer_relation


async def _find_pending_invitation_for_mobile(
    db: AsyncSession,
    mobile: str,
) -> tuple[Invitation, object | None, object | None] | None:
    stmt = (
        select(Invitation)
        .where(
            Invitation.mobile_number == mobile,
            Invitation.is_used == False,
        )
        .order_by(Invitation.created_at.desc(), Invitation.id.desc())
    )
    invitations = list((await db.execute(stmt)).scalars().all())
    for invitation in invitations:
        if invitation.expires_at < utc_now_naive():
            continue
        try:
            return await _load_valid_invitation_by_token(
                db,
                invitation.token,
                missing_detail="دعوت‌نامه نامعتبر است",
            )
        except HTTPException:
            continue
    return None


def _serialize_pending_registration_context(invitation: Invitation) -> PendingRegistrationContext:
    return PendingRegistrationContext(
        token=invitation.token,
        account_name=invitation.account_name,
        mobile_number=invitation.mobile_number,
        role=invitation.role,
    )


async def _store_registration_session(
    redis,
    *,
    invitation_token: str,
) -> tuple[str, int]:
    registration_token = f"REG-{secrets.token_hex(16)}"
    ttl = max(60, int(settings.invitation_registration_session_ttl_seconds))
    await redis.setex(_registration_session_key(registration_token), ttl, invitation_token)
    return registration_token, ttl


async def _load_registration_session_invitation(
    db: AsyncSession,
    redis,
    *,
    registration_token: str,
) -> tuple[Invitation, object | None, object | None]:
    invitation_token = await redis.get(_registration_session_key(registration_token))
    if not invitation_token:
        raise HTTPException(status_code=400, detail="جلسه تکمیل ثبت‌نام منقضی شده است")
    return await _load_valid_invitation_by_token(db, invitation_token, missing_detail="دعوت‌نامه نامعتبر است")

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
    customer_management_name: str | None = None,
    telegram_linked: bool = False,
    can_connect_telegram: bool = False,
    telegram_link_denial_reason: str | None = None,
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
            "customer_management_name": customer_management_name,
            "telegram_linked": telegram_linked,
            "can_connect_telegram": can_connect_telegram,
            "telegram_link_denial_reason": telegram_link_denial_reason,
        }
    )


async def _load_current_user_relation_context(
    db: AsyncSession,
    current_user: User,
) -> dict[str, object]:
    if not isinstance(db, AsyncSession):
        is_accountant = await is_user_accountant(db, current_user.id)
        customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
        bot_access = await evaluate_bot_access(db, current_user)
        telegram_linked = getattr(current_user, "telegram_id", None) is not None
        return {
            "is_accountant": is_accountant,
            "accountant_owner_user_id": None,
            "accountant_owner_account_name": None,
            "is_customer": customer_relation is not None,
            "customer_tier": customer_relation.customer_tier if customer_relation else None,
            "customer_owner_user_id": getattr(customer_relation, "owner_user_id", None) if customer_relation else None,
            "customer_owner_account_name": None,
            "customer_management_name": getattr(customer_relation, "management_name", None) if customer_relation else None,
            "telegram_linked": telegram_linked,
            "can_connect_telegram": bool(bot_access.allowed and not telegram_linked),
            "telegram_link_denial_reason": None if bot_access.allowed else bot_access.reason,
        }

    accountant_relation = await get_active_accountant_relation_for_accountant(db, current_user.id)
    customer_relation = await get_active_customer_relation_for_customer(db, current_user.id)
    bot_access = await evaluate_bot_access(db, current_user)
    telegram_linked = getattr(current_user, "telegram_id", None) is not None
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
        "customer_management_name": getattr(customer_relation, "management_name", None) if customer_relation else None,
        "telegram_linked": telegram_linked,
        "can_connect_telegram": bool(bot_access.allowed and not telegram_linked),
        "telegram_link_denial_reason": None if bot_access.allowed else bot_access.reason,
    }


def _registration_full_name(invitation: Invitation, accountant_relation, customer_relation) -> str:
    candidate = None
    if customer_relation is not None:
        candidate = getattr(customer_relation, "management_name", None)
    elif accountant_relation is not None:
        candidate = getattr(accountant_relation, "relation_display_name", None)

    normalized = str(candidate or "").strip()
    if normalized:
        return normalized
    return invitation.account_name

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


@router.post("/telegram-link-token", response_model=TelegramLinkTokenResponse)
async def issue_telegram_link_token(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a short-lived token that must be consumed by the foreign Telegram bot."""
    bot_username = (settings.bot_username or "").strip().lstrip("@")
    if not bot_username:
        raise HTTPException(status_code=503, detail="نام کاربری ربات تنظیم نشده است")

    decision = await evaluate_bot_access(db, current_user)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=bot_access_denial_message(decision.reason))

    if getattr(current_user, "telegram_id", None) is not None:
        return TelegramLinkTokenResponse(
            telegram_linked=True,
            can_connect_telegram=False,
            bot_username=bot_username,
            detail="این حساب قبلاً به تلگرام متصل شده است",
        )

    try:
        issue_result = await create_telegram_link_token(
            db,
            current_user,
            ttl_seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS,
        )
        await db.commit()
    except TelegramLinkTokenError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=bot_access_denial_message(exc.reason)) from exc
    except Exception as exc:
        await db.rollback()
        logger.error("Telegram link token issue failed: %s", exc)
        raise HTTPException(status_code=500, detail="خطا در ساخت لینک اتصال تلگرام") from exc

    start_parameter = build_telegram_start_parameter(issue_result.token)
    return TelegramLinkTokenResponse(
        telegram_linked=False,
        can_connect_telegram=True,
        bot_username=bot_username,
        telegram_url=build_telegram_deep_link(bot_username, issue_result.token),
        start_parameter=start_parameter,
        expires_at=issue_result.record.expires_at,
        expires_in=TELEGRAM_LINK_TOKEN_TTL_SECONDS,
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
    inv, _, _ = await _load_valid_invitation_by_token(db, req.token)
    mobile = inv.mobile_number
    
    # Rate limiting
    redis = await get_redis()
    rate_limit_key = f"otp_limit:{mobile}"
    if await redis.get(rate_limit_key):
        raise HTTPException(status_code=429, detail="لطفاً ۲ دقیقه صبر کنید")

    otp_code = str(random.randint(10000, 99999))
    otp_key = f"reg_otp:{req.token}" # Use token as key identifier for security
    
    await redis.setex(otp_key, OTP_TTL_SECONDS, otp_code)
    await redis.setex(rate_limit_key, OTP_TTL_SECONDS, "1")

    if _deliver_otp_via_staging_log(mobile=mobile, otp_code=otp_code, purpose="registration"):
        return {"detail": "کد تایید در لاگ staging ثبت شد", "expires_in": OTP_TTL_SECONDS}
    
    # Send SMS (Always SMS because user is not registered on Telegram yet)
    if send_otp_sms(mobile, otp_code):
        return {"detail": "کد تایید ارسال شد", "expires_in": OTP_TTL_SECONDS}
    else:
        await redis.delete(otp_key)
        await redis.delete(rate_limit_key)
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
    verify_subject = f"registration:{req.token}"
    await _ensure_otp_verify_not_locked(redis, subject=verify_subject, raw_request=None)
    
    if not constant_time_secret_equals(submitted_code, _redis_text(stored_code)):
        await _record_otp_verify_failure(redis, subject=verify_subject, raw_request=None, otp_key=otp_key)
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
    
    # Delete OTP to prevent replay attacks
    await redis.delete(otp_key)
    await _clear_otp_verify_subject_failures(redis, subject=verify_subject)
    
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
    redis = await get_redis()

    invitation_token = (req.token or "").strip()
    registration_token = (req.registration_token or "").strip()

    if registration_token:
        inv, accountant_relation, customer_relation = await _load_registration_session_invitation(
            db,
            redis,
            registration_token=registration_token,
        )
        verify_key = None
    else:
        if not invitation_token:
            raise HTTPException(status_code=400, detail="توکن دعوت یا جلسه ثبت‌نام الزامی است")
        verify_key = f"reg_verified:{invitation_token}"
        if not await redis.get(verify_key):
            raise HTTPException(status_code=400, detail="لطفاً ابتدا کد تایید را وارد کنید")
        inv, accountant_relation, customer_relation = await _load_valid_invitation_by_token(
            db,
            invitation_token,
            missing_detail="دعوت‌نامه نامعتبر است",
        )

    new_user = User(
        account_name=inv.account_name,
        mobile_number=inv.mobile_number,
        role=inv.role,
        full_name=_registration_full_name(inv, accountant_relation, customer_relation),
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

    if _should_announce_project_user_registration(accountant_relation, customer_relation):
        await _publish_project_user_joined_notifications(db, new_user)
        
    if invitation_token:
        await redis.delete(f"reg_otp:{invitation_token}")
    if verify_key:
        await redis.delete(verify_key)
    if registration_token:
        await redis.delete(_registration_session_key(registration_token))

    refresh_token_expires = timedelta(days=30)
    access_token_expires = timedelta(minutes=60)
    
    refresh_token = create_refresh_token(
        subject=new_user.id,
        expires_delta=refresh_token_expires
    )
    
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


@router.get("/pending-registration/{registration_token}", response_model=PendingRegistrationContext)
async def get_pending_registration(
    registration_token: str,
    db: AsyncSession = Depends(get_db),
):
    redis = await get_redis()
    invitation, _, _ = await _load_registration_session_invitation(
        db,
        redis,
        registration_token=registration_token,
    )
    return _serialize_pending_registration_context(invitation)

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
    if not _is_dev_login_environment():
        raise HTTPException(status_code=404, detail="Not found")

    real_ip = _extract_request_real_ip(raw_request)
    is_local = _is_local_dev_request(raw_request)

    dev_key = raw_request.headers.get("X-DEV-API-KEY")
    if not is_local and not constant_time_secret_equals(dev_key, settings.dev_api_key):
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

@router.post("/request-otp", response_model=dict)

async def request_otp(
    request: OTPRequest,
    raw_request: Request,
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

    pending_invitation = None
    stmt = select(User).where(User.mobile_number == mobile)
    result = (await db.execute(stmt)).scalar_one_or_none()
    if result:
        if result.is_deleted:
            raise HTTPException(status_code=403, detail="حساب کاربری غیرفعال شده است")

        if get_user_account_status(result).value == "inactive":
            _raise_inactive_account_error()

        login_home_server = _login_home_server(raw_request)
        await assert_login_allowed_for_server(db, result, requested_server=login_home_server)
    else:
        pending_invitation = await _find_pending_invitation_for_mobile(db, mobile)
        if not pending_invitation:
            raise HTTPException(status_code=404, detail="کاربری با این شماره موبایل یافت نشد")

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
        logger.info("OTP Request for %s: Active OTP exists. Blocking new generation.", mobile)
        # Raise 429 so frontend triggers timer
        raise HTTPException(status_code=429, detail="کد قبلی هنوز معتبر است. لطفاً صبر کنید.")

    otp_code = str(random.randint(10000, 99999))
    logger.info("Generated NEW OTP for %s", mobile)
    
    # ذخیره در Redis (۲ دقیقه اعتبار)
    await redis.setex(otp_key, OTP_TTL_SECONDS, otp_code)
    await redis.setex(rate_limit_key, OTP_TTL_SECONDS, "1")

    if _deliver_otp_via_staging_log(mobile=mobile, otp_code=otp_code, purpose="login"):
        return {
            "detail": "کد تایید در لاگ staging ثبت شد",
            "method": "log",
            "expires_in": OTP_TTL_SECONDS,
        }

    # تصمیم‌گیری برای روش ارسال
    is_connected = await is_internet_connected()
    has_telegram = result.telegram_id is not None if result else False
    
    logger.info(
        "OTP Request for %s: Connected=%s, HasTelegram=%s, TelegramID=%s",
        mobile,
        is_connected,
        has_telegram,
        result.telegram_id if result else None,
    )

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
            await redis.delete(otp_key)
            await redis.delete(rate_limit_key)
            raise HTTPException(status_code=500, detail="خطا در ارسال کد تایید")

    return {
        "detail": "کد تایید ارسال شد",
        "method": "telegram" if sent_via_telegram else "sms",
        "expires_in": OTP_TTL_SECONDS
    }

@router.post("/resend-otp-sms", response_model=dict)
async def resend_otp_sms(
    request: OTPRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
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

    stmt = select(User).where(User.mobile_number == mobile)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user:
        if user.is_deleted or get_user_account_status(user).value == "inactive":
            _raise_inactive_account_error()

        login_home_server = _login_home_server(raw_request)
        await assert_login_allowed_for_server(db, user, requested_server=login_home_server)
    else:
        pending_invitation = await _find_pending_invitation_for_mobile(db, mobile)
        if not pending_invitation:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")
         
    # Rate limit for SMS resend (prevent spamming button)
    sms_limit_key = f"sms_limit:{mobile}"
    if await redis.get(sms_limit_key):
        logger.info(f"Resend SMS Rate Limit Hit for {mobile}")
        raise HTTPException(status_code=429, detail="لطفاً ۱ دقیقه صبر کنید")
        
    logger.info("Resending existing OTP via SMS to %s", mobile)
    
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
    await _ensure_otp_verify_not_locked(redis, subject=mobile, raw_request=raw_request)
    
    if not constant_time_secret_equals(code, _redis_text(stored_code)):
        await _record_otp_verify_failure(redis, subject=mobile, raw_request=raw_request, otp_key=otp_key)
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
        
    # کد درست است
    stmt = select(User).where(User.mobile_number == mobile)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        pending_invitation = await _find_pending_invitation_for_mobile(db, mobile)
        if not pending_invitation:
            raise HTTPException(status_code=404, detail="کاربر یافت نشد")

        invitation, _, _ = pending_invitation
        await redis.delete(otp_key)
        await redis.delete(f"otp_limit:{mobile}")
        await _clear_otp_verify_subject_failures(redis, subject=mobile)
        registration_token, expires_in = await _store_registration_session(
            redis,
            invitation_token=invitation.token,
        )
        return {
            "status": "registration_required",
            "registration_token": registration_token,
            "expires_in": expires_in,
            "invitation": _serialize_pending_registration_context(invitation).model_dump(),
        }

    if get_user_account_status(user).value == "inactive":
        await redis.delete(otp_key)
        await redis.delete(f"otp_limit:{mobile}")
        await _clear_otp_verify_subject_failures(redis, subject=mobile)
        _raise_inactive_account_error()

    login_home_server = _login_home_server(raw_request)
    try:
        await assert_login_allowed_for_server(db, user, requested_server=login_home_server)
    except HTTPException as exc:
        if exc.status_code == 409:
            await redis.delete(otp_key)
            await redis.delete(f"otp_limit:{mobile}")
            await _clear_otp_verify_subject_failures(redis, subject=mobile)
        raise

    # پاک کردن کد OTP
    await redis.delete(otp_key)
    await redis.delete(f"otp_limit:{mobile}")
    await _clear_otp_verify_subject_failures(redis, subject=mobile)

    # Generate tokens
    access_token_expires = timedelta(minutes=60)
    refresh_token_expires = timedelta(days=30)
    
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
        await assert_login_allowed_for_server(db, user, requested_server=login_home_server)
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
