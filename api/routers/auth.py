from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import logging
import math
from pydantic import BaseModel, field_validator, model_validator
from typing import Optional
import secrets
import hashlib
import hmac
import time
from jose import JWTError, jwt
from core.db import get_db
from models.user import User, UserRole
from models.customer_relation import CustomerTier
from models.invitation import Invitation, InvitationKind
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
from core.sms import SMSDeliveryOutcome, send_otp_sms, send_sms
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
from core.services.authoritative_registration_service import (
    AuthoritativeRegistrationError,
    AuthoritativeRegistrationRequest,
    complete_invitation_registration,
)
from core.services.authoritative_telegram_account_link_service import (
    complete_authoritative_telegram_account_link,
)
from core.registration_contracts import (
    OTPDeliveryStatus,
    TelegramRegistrationCommand,
    TelegramRegistrationCommandResponse,
    TelegramRegistrationOutcome,
    TelegramOTPDeliveryCommand,
    TelegramOTPDeliveryOutcome,
    TelegramOTPDeliveryResponse,
)
from core.telegram_account_link_contracts import TelegramAccountLinkCommand
from core.trade_forwarding import verify_internal_signature
from core.audit_logger import audit_log
from core.services.registration_notification_service import (
    publish_project_user_joined_web_notifications,
)
from models.session import Platform, UserSession
import uuid
from core.utils import normalize_persian_numerals, utc_now, utc_now_naive
from core.notifications import send_telegram_message
from core.services.otp_delivery_state_service import (
    OTP_CODE_TTL_SECONDS,
    OTP_SMS_FALLBACK_SECONDS,
    arm_sms_fallback,
    build_otp_delivery_state,
    cancel_otp_delivery,
    claim_sms_delivery,
    consume_otp_code,
    create_otp_delivery_state,
    load_otp_delivery_state,
    mobile_for_delivery_state,
    schedule_sms_fallback,
    validate_otp_delivery_runtime_settings,
)
from core.services.otp_sms_delivery_service import execute_claimed_otp_sms_delivery
from core.services.telegram_otp_delivery_service import deliver_telegram_otp_once
from core.telegram_otp_transport import forward_telegram_otp_delivery
from core.registration_feature_policy import direct_registration_runtime_ready
from core.server_routing import (
    SERVER_FOREIGN,
    SERVER_IRAN,
    current_server,
    normalize_server,
    server_from_request,
)
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

OTP_TTL_SECONDS = OTP_CODE_TTL_SECONDS
OTP_VERIFY_FAILURE_TTL_SECONDS = OTP_TTL_SECONDS
OTP_VERIFY_LOCK_SECONDS = 300
OTP_VERIFY_SUBJECT_MAX_FAILURES = 5
OTP_VERIFY_IP_MAX_FAILURES = 30
OTP_VERIFY_LOCKED_DETAIL = "تعداد تلاش‌های ناموفق زیاد است. چند دقیقه دیگر دوباره تلاش کنید."


def _verify_foreign_internal_command(raw_request: Request, body: bytes) -> None:
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=401, detail="Invalid internal registration signature")
    if current_server() != SERVER_IRAN:
        raise HTTPException(status_code=403, detail="Registration reconciliation is Iran-authoritative")
    if normalize_server(raw_request.headers.get("x-source-server"), default="") != SERVER_FOREIGN:
        raise HTTPException(status_code=401, detail="Invalid internal registration source")


def _verify_iran_internal_command(raw_request: Request, body: bytes) -> None:
    if not verify_internal_signature(
        body,
        raw_request.headers.get("x-timestamp"),
        raw_request.headers.get("x-signature"),
        raw_request.headers.get("x-api-key"),
    ):
        raise HTTPException(status_code=401, detail="Invalid internal OTP signature")
    if current_server() != SERVER_FOREIGN:
        raise HTTPException(status_code=403, detail="Telegram OTP delivery is foreign-only")
    if normalize_server(raw_request.headers.get("x-source-server"), default="") != SERVER_IRAN:
        raise HTTPException(status_code=401, detail="Invalid internal OTP source")


def _registration_outcome_event(outcome: TelegramRegistrationOutcome) -> str:
    if outcome == TelegramRegistrationOutcome.CREATED:
        return "telegram_registration.reconciled_created"
    if outcome == TelegramRegistrationOutcome.LINKED_EXISTING:
        return "telegram_registration.reconciled_linked_existing"
    if outcome == TelegramRegistrationOutcome.ALREADY_LINKED:
        return "telegram_registration.reconciled_already_linked"
    return "telegram_registration.rejected"


@router.post(
    "/internal/telegram-registration/reconcile",
    response_model=TelegramRegistrationCommandResponse,
)
async def reconcile_telegram_registration_internal(
    raw_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    _verify_foreign_internal_command(raw_request, body)
    response.headers["Cache-Control"] = "no-store"
    try:
        payload = json.loads(body)
        command = TelegramRegistrationCommand.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid internal registration command") from None
    if not direct_registration_runtime_ready(settings):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return TelegramRegistrationCommandResponse(
            command_id=command.command_id,
            outcome=TelegramRegistrationOutcome.FEATURE_DISABLED,
            terminal=False,
        )

    result = await complete_invitation_registration(
        db,
        AuthoritativeRegistrationRequest.for_telegram(
            command=command,
            source_server=SERVER_FOREIGN,
        ),
    )
    if result.first_terminal_transition:
        audit_log(
            _registration_outcome_event(result.outcome),
            target_type="telegram_registration_command",
            target_id=str(command.command_id),
            result=("success" if result.authoritative_user_id is not None else "denied"),
            extra={"outcome": result.outcome.value},
        )
        if result.outcome == TelegramRegistrationOutcome.LINKED_EXISTING:
            audit_log(
                "telegram_registration_intent.linked_existing_user",
                target_type="user",
                target_id=result.authoritative_user_id,
                result="success",
            )
    return TelegramRegistrationCommandResponse(
        command_id=command.command_id,
        outcome=result.outcome,
        authoritative_user_id=result.authoritative_user_id,
    )


@router.post(
    "/internal/telegram-link/complete",
    response_model=TelegramRegistrationCommandResponse,
)
async def complete_telegram_account_link_internal(
    raw_request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    body = await raw_request.body()
    _verify_foreign_internal_command(raw_request, body)
    response.headers["Cache-Control"] = "no-store"
    try:
        payload = json.loads(body)
        command = TelegramAccountLinkCommand.model_validate(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid internal account-link command") from None
    if not settings.registration_sync_v2_enabled:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return TelegramRegistrationCommandResponse(
            command_id=command.command_id,
            outcome=TelegramRegistrationOutcome.FEATURE_DISABLED,
            terminal=False,
        )
    result = await complete_authoritative_telegram_account_link(
        db,
        command=command,
        source_server=SERVER_FOREIGN,
    )
    if result.first_terminal_transition:
        audit_log(
            "telegram_account_link.completed"
            if result.authoritative_user_id is not None
            else "telegram_account_link.rejected",
            target_type="telegram_account_link_command",
            target_id=str(command.command_id),
            result=("success" if result.authoritative_user_id is not None else "denied"),
            extra={"outcome": result.outcome.value},
        )
    return TelegramRegistrationCommandResponse(
        command_id=command.command_id,
        outcome=result.outcome,
        authoritative_user_id=result.authoritative_user_id,
    )


@router.post(
    "/internal/telegram-otp/deliver",
    response_model=TelegramOTPDeliveryResponse,
)
async def deliver_telegram_otp_internal(
    raw_request: Request,
    response: Response,
):
    body = await raw_request.body()
    _verify_iran_internal_command(raw_request, body)
    response.headers["Cache-Control"] = "no-store"
    try:
        command = TelegramOTPDeliveryCommand.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid internal OTP delivery command") from None
    if not settings.telegram_login_otp_enabled:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return TelegramOTPDeliveryResponse(
            otp_request_id=command.otp_request_id,
            outcome=TelegramOTPDeliveryOutcome.FEATURE_DISABLED,
            terminal=False,
        )
    redis = await get_redis()
    result = await deliver_telegram_otp_once(redis, command=command)
    audit_log(
        "otp.telegram_delivery_result",
        target_type="otp_request",
        target_id=str(command.otp_request_id),
        result=result.outcome.value,
    )
    return result


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
    mobile_number: str | None = None
    otp_request_id: uuid.UUID | None = None
    code: str
    suspended_refresh_token: Optional[str] = None

    @model_validator(mode="after")
    def require_mobile_or_request_id(self):
        if not self.mobile_number and self.otp_request_id is None:
            raise ValueError("mobile_number or otp_request_id is required")
        return self

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

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        from core.services.invitation_lifecycle_service import validate_registration_address

        return validate_registration_address(value)

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
    if getattr(invitation, "revoked_at", None) is not None:
        raise HTTPException(status_code=400, detail="دعوت‌نامه لغو شده است")
    if getattr(invitation, "kind", None) == InvitationKind.LEGACY_UNKNOWN:
        raise HTTPException(status_code=400, detail="وضعیت دعوت‌نامه قدیمی نامشخص است؛ دعوت‌نامه جدید دریافت کنید")
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
            Invitation.revoked_at.is_(None),
            Invitation.kind != InvitationKind.LEGACY_UNKNOWN,
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
    invitation_token = await _load_registration_session_token(
        redis,
        registration_token=registration_token,
    )
    return await _load_valid_invitation_by_token(db, invitation_token, missing_detail="دعوت‌نامه نامعتبر است")


async def _load_registration_session_token(
    redis,
    *,
    registration_token: str,
) -> str:
    invitation_token = _redis_text(await redis.get(_registration_session_key(registration_token)))
    if not invitation_token:
        raise HTTPException(status_code=400, detail="جلسه تکمیل ثبت‌نام منقضی شده است")
    return invitation_token

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

    otp_code = _generate_otp_code()
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
        invitation_token = await _load_registration_session_token(
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

    try:
        registration_result = await complete_invitation_registration(
            db,
            AuthoritativeRegistrationRequest.for_web(
                invitation_token=invitation_token,
                address=req.address,
            ),
        )
    except AuthoritativeRegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from None
    except Exception as exc:
        logger.error(
            "Authoritative Web registration failed",
            extra={
                "event": "registration.web_transaction_failed",
                "error_class": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=500, detail="خطا در ثبت کاربر")

    new_user = registration_result.user
    if new_user is None or registration_result.authoritative_user_id is None:
        logger.error(
            "Authoritative Web registration returned no user",
            extra={"event": "registration.web_result_invariant_failed"},
        )
        raise HTTPException(status_code=500, detail="خطا در ثبت کاربر")

    if registration_result.announce_project_user:
        await publish_project_user_joined_web_notifications(
            new_user_id=int(new_user.id),
            account_name=str(new_user.account_name or ""),
            full_name=str(getattr(new_user, "full_name", "") or ""),
        )

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

    cleanup_keys = []
    if invitation_token:
        cleanup_keys.append(("registration_otp", f"reg_otp:{invitation_token}"))
    if verify_key:
        cleanup_keys.append(("registration_verification", verify_key))
    if registration_token:
        cleanup_keys.append(
            ("registration_session", _registration_session_key(registration_token))
        )
    for cleanup_kind, cleanup_key in cleanup_keys:
        try:
            await redis.delete(cleanup_key)
        except Exception as exc:
            logger.warning(
                "Post-registration Redis cleanup failed",
                extra={
                    "event": "registration.redis_cleanup_failed",
                    "cleanup_kind": cleanup_kind,
                    "error_class": type(exc).__name__,
                },
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

def _generate_otp_code() -> str:
    return f"{secrets.randbelow(90000) + 10000:05d}"


async def _deliver_stage6_sms(redis, *, state) -> SMSDeliveryOutcome:
    claim = await claim_sms_delivery(redis, state=state, require_due=False)
    if claim is None:
        refreshed = await load_otp_delivery_state(
            redis,
            request_id=state.otp_request_id,
        )
        if refreshed and refreshed.sms_delivery_status == OTPDeliveryStatus.ACCEPTED:
            return SMSDeliveryOutcome.ACCEPTED
        return SMSDeliveryOutcome.AMBIGUOUS
    attempt = await execute_claimed_otp_sms_delivery(redis, claim=claim)
    audit_log(
        "otp.sms_delivery_result",
        target_type="otp_request",
        target_id=str(claim.request_id),
        result=attempt.outcome.value,
        extra={
            "provider_attempted": attempt.provider_attempted,
            "result_recorded": attempt.result_recorded,
        },
    )
    return attempt.outcome


def _otp_delivery_method(state) -> str | None:
    if state.telegram_delivery_status in {
        OTPDeliveryStatus.PENDING,
        OTPDeliveryStatus.ACCEPTED,
    }:
        return "telegram"
    if state.sms_delivery_status != OTPDeliveryStatus.NOT_ATTEMPTED:
        return "sms"
    return None


def _otp_timing_payload(state, *, method: str | None) -> dict:
    now = utc_now()
    expires_in = max(0, math.ceil((state.expires_at - now).total_seconds()))
    payload = {
        "otp_request_id": str(state.otp_request_id),
        "method": method,
        "expires_in": expires_in,
        "expires_at": state.expires_at.isoformat(),
    }
    if state.sms_fallback_at is not None:
        payload["sms_fallback_at"] = state.sms_fallback_at.isoformat()
        payload["sms_fallback_in"] = max(
            0,
            math.ceil((state.sms_fallback_at - now).total_seconds()),
        )
    return payload


async def _request_stage6_login_otp(
    redis,
    *,
    mobile: str,
    user: User | None,
) -> dict:
    if current_server() != SERVER_IRAN:
        raise HTTPException(status_code=503, detail="سرویس ارسال کد موقتاً در دسترس نیست")
    if settings.environment == "staging" and settings.staging_log_otp_codes:
        raise HTTPException(
            status_code=503,
            detail="ارسال واقعی کد در تنظیمات staging غیرفعال است",
        )

    try:
        validate_otp_delivery_runtime_settings(settings)
    except RuntimeError:
        logger.error(
            "Stage 6 OTP runtime configuration is invalid",
            extra={"event": "otp.runtime_configuration_invalid"},
        )
        raise HTTPException(
            status_code=503,
            detail="سرویس ارسال کد موقتاً در دسترس نیست",
        ) from None

    ttl_seconds = OTP_CODE_TTL_SECONDS
    otp_code = _generate_otp_code()
    state = build_otp_delivery_state(
        mobile=mobile,
        ttl_seconds=ttl_seconds,
    )
    if not await create_otp_delivery_state(
        redis,
        state=state,
        otp_code=otp_code,
        ttl_seconds=ttl_seconds,
    ):
        existing = await load_otp_delivery_state(redis, mobile=mobile)
        if existing is None:
            remaining = max(0, int(await redis.ttl(f"otp:{mobile}")))
            expires_at = utc_now() + timedelta(seconds=remaining)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "کد قبلی هنوز معتبر است. لطفاً صبر کنید.",
                    "code": "otp_active",
                    "retry_after": remaining,
                    "method": None,
                    "expires_in": remaining,
                    "expires_at": expires_at.isoformat(),
                },
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            status_code=429,
            content={
                "detail": "کد قبلی هنوز معتبر است. لطفاً صبر کنید.",
                "code": "otp_active",
                "retry_after": max(
                    0,
                    int((existing.expires_at - utc_now()).total_seconds()),
                ),
                **_otp_timing_payload(
                    existing,
                    method=_otp_delivery_method(existing),
                ),
            },
            headers={"Cache-Control": "no-store"},
        )
    audit_log(
        "otp.requested",
        target_type="otp_request",
        target_id=str(state.otp_request_id),
        result="accepted",
    )

    telegram_id = int(user.telegram_id) if user and user.telegram_id else None
    use_telegram = bool(
        telegram_id
        and settings.telegram_login_otp_enabled
        and settings.otp_sms_auto_fallback_enabled
    )
    if use_telegram:
        fallback_seconds = OTP_SMS_FALLBACK_SECONDS
        # Persist recovery before the remote side effect. A valid acknowledgement
        # moves this conservative deadline to exactly fallback_seconds after ack.
        recovery_at = utc_now() + timedelta(seconds=fallback_seconds + 5)
        try:
            fallback_armed = await arm_sms_fallback(
                redis,
                request_id=state.otp_request_id,
                recovery_at=recovery_at,
            )
        except Exception:
            fallback_armed = False
        command = TelegramOTPDeliveryCommand(
            otp_request_id=state.otp_request_id,
            telegram_id=telegram_id,
            otp_code=otp_code,
            expires_at=state.expires_at,
        )
        if fallback_armed:
            try:
                status_code, body = await forward_telegram_otp_delivery(command)
            except Exception:
                status_code, body = 503, {"detail": "Telegram delivery failed"}
        else:
            status_code, body = 503, {"detail": "OTP fallback scheduling failed"}
        try:
            delivery = TelegramOTPDeliveryResponse.model_validate(body)
        except (TypeError, ValueError):
            delivery = None
        if (
            status_code == 200
            and delivery is not None
            and delivery.otp_request_id == state.otp_request_id
            and delivery.outcome
            in {
                TelegramOTPDeliveryOutcome.SENT,
                TelegramOTPDeliveryOutcome.DUPLICATE_SENT,
            }
        ):
            sent_at = utc_now()
            fallback_at = sent_at + timedelta(seconds=fallback_seconds)
            try:
                scheduled = await schedule_sms_fallback(
                    redis,
                    request_id=state.otp_request_id,
                    telegram_sent_at=sent_at,
                    fallback_at=fallback_at,
                )
            except Exception:
                scheduled = False
            if scheduled:
                audit_log(
                    "otp.sms_fallback_scheduled",
                    target_type="otp_request",
                    target_id=str(state.otp_request_id),
                    result="scheduled",
                    extra={"fallback_seconds": fallback_seconds},
                )
                return {
                    "detail": "کد تایید ارسال شد",
                    **_otp_timing_payload(state.model_copy(update={
                        "telegram_delivery_status": OTPDeliveryStatus.ACCEPTED,
                        "telegram_sent_at": sent_at,
                        "sms_fallback_at": fallback_at,
                    }), method="telegram"),
                }

    sms_outcome = await _deliver_stage6_sms(redis, state=state)
    if sms_outcome == SMSDeliveryOutcome.ACCEPTED:
        return {
            "detail": "کد تایید ارسال شد",
            **_otp_timing_payload(state, method="sms"),
        }
    if sms_outcome == SMSDeliveryOutcome.FAILED:
        await cancel_otp_delivery(redis, mobile=mobile)
    raise HTTPException(status_code=500, detail="خطا در ارسال کد تایید")


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
    if settings.telegram_login_otp_enabled:
        return await _request_stage6_login_otp(
            redis,
            mobile=mobile,
            user=result,
        )

    rate_limit_key = f"otp_limit:{mobile}"
    limit_val = await redis.get(rate_limit_key)
    logger.info(
        "OTP request rate-limit state checked",
        extra={"event": "otp.rate_limit_checked", "active": limit_val is not None},
    )
    
    if limit_val:
        logger.info("OTP request rate limit hit", extra={"event": "otp.rate_limited"})
        raise HTTPException(status_code=429, detail="لطفاً ۲ دقیقه صبر کنید")

    # تولید کد ۵ رقمی
    # First, check if valid OTP already exists (Strict "One Code per 120s" rule)
    otp_key = f"otp:{mobile}"
    active_otp = await redis.get(otp_key)
    
    if active_otp:
        logger.info("Active OTP exists; blocking new generation", extra={"event": "otp.active_exists"})
        # Raise 429 so frontend triggers timer
        raise HTTPException(status_code=429, detail="کد قبلی هنوز معتبر است. لطفاً صبر کنید.")

    otp_code = _generate_otp_code()
    logger.info("Generated new OTP", extra={"event": "otp.generated"})
    
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
        "Legacy OTP delivery method evaluated",
        extra={
            "event": "otp.legacy_delivery_evaluated",
            "connected": is_connected,
            "has_telegram": has_telegram,
        },
    )

    sent_via_telegram = False
    sent_via_sms = False
    
    # اولویت ۱: تلگرام (اگر اینترنت وصله و کاربر تلگرام داره)
    if is_connected and has_telegram:
        try:
            msg_text = f"🔐 کد ورود شما: `{otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
            await send_telegram_message(result.telegram_id, msg_text)
            sent_via_telegram = True
            logger.info("Legacy OTP sent via Telegram", extra={"event": "otp.legacy_telegram_sent"})
        except Exception as e:
            logger.error(f"Failed to send OTP via Telegram: {e}")
            # Fallback to SMS handled below
    
    # اولویت ۲: SMS (اگر تلگرام نشد یا اینترنت قطعه)
    if not sent_via_telegram:
        logger.info("Attempting legacy OTP SMS delivery", extra={"event": "otp.legacy_sms_attempt"})
        # اگر اینترنت وصله ولی تلگرام ارسال نشد (یا کاربر تلگرام نداره) -> SMS
        # اگر اینترنت قطعه -> SMS
        if send_otp_sms(mobile, otp_code):
            sent_via_sms = True
            logger.info("Legacy OTP sent via SMS", extra={"event": "otp.legacy_sms_sent"})
        else:
            logger.error("Failed to send legacy OTP via SMS", extra={"event": "otp.legacy_sms_failed"})
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
    
    logger.info("Legacy OTP SMS resend requested", extra={"event": "otp.legacy_resend_requested"})

    # Check if OTP exists
    otp_key = f"otp:{mobile}"
    otp_code = await redis.get(otp_key)
    
    if not otp_code:
         # Session expired
         logger.warning("OTP resend failed because code is missing or expired")
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
        logger.info("OTP SMS resend rate limit hit")
        raise HTTPException(status_code=429, detail="لطفاً ۱ دقیقه صبر کنید")
        
    logger.info("Resending existing OTP via SMS")
    
    # Get remaining TTL for the timer
    ttl = await redis.ttl(otp_key)
    logger.info("OTP SMS resend TTL checked", extra={"remaining_ttl_seconds": ttl})
    
    if ttl < 0: 
        # -1 (no expiry) or -2 (missing). Should not happen here due to check above.
        # But if it does, default to 0 (or strictly, handle error)
        logger.warning("Unexpected OTP TTL", extra={"remaining_ttl_seconds": ttl})
        ttl = 0


    if settings.telegram_login_otp_enabled:
        state = await load_otp_delivery_state(redis, mobile=mobile)
        if state is not None:
            outcome = await _deliver_stage6_sms(redis, state=state)
            if outcome == SMSDeliveryOutcome.ACCEPTED:
                await redis.setex(sms_limit_key, 60, "1")
                return {
                    "detail": "کد از طریق پیامک ارسال شد",
                    **_otp_timing_payload(state, method="sms"),
                }
            raise HTTPException(status_code=500, detail="خطا در ارسال پیامک")

    # Legacy compatibility when Stage 6 state is disabled or absent.
    if send_otp_sms(mobile, otp_code):
        await redis.setex(sms_limit_key, 60, "1") # 1 min limit for SMS resend
        return {
            "detail": "کد از طریق پیامک ارسال شد",
            "expires_in": ttl
        }
    else:
        logger.error("Failed to resend OTP via SMS")
        raise HTTPException(status_code=500, detail="خطا در ارسال پیامک")




@router.post("/verify-otp")
async def verify_otp(
    request: OTPVerify,
    raw_request: Request,
    db: AsyncSession = Depends(get_db)
):
    redis = await get_redis()
    delivery_state = None
    if settings.telegram_login_otp_enabled and request.otp_request_id is not None:
        delivery_state = await load_otp_delivery_state(
            redis,
            request_id=request.otp_request_id,
        )
        if delivery_state is None:
            raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
        try:
            mobile = mobile_for_delivery_state(delivery_state)
        except RuntimeError:
            logger.error(
                "OTP delivery target could not be resolved",
                extra={
                    "event": "otp.delivery_target_resolution_failed",
                    "otp_request_id": str(request.otp_request_id),
                },
            )
            raise HTTPException(
                status_code=400,
                detail="کد تایید نامعتبر یا منقضی شده است",
            ) from None
        if request.mobile_number:
            supplied_mobile = normalize_persian_numerals(request.mobile_number)
            if not constant_time_secret_equals(mobile, supplied_mobile):
                raise HTTPException(
                    status_code=400,
                    detail="کد تایید نامعتبر یا منقضی شده است",
                )
    elif request.mobile_number:
        mobile = normalize_persian_numerals(request.mobile_number)
    else:
        raise HTTPException(status_code=400, detail="شماره موبایل نامعتبر است")
    code = normalize_persian_numerals(request.code)

    otp_key = f"otp:{mobile}"
    stored_code = await redis.get(otp_key)
    await _ensure_otp_verify_not_locked(redis, subject=mobile, raw_request=raw_request)
    
    if not constant_time_secret_equals(code, _redis_text(stored_code)):
        await _record_otp_verify_failure(redis, subject=mobile, raw_request=raw_request, otp_key=otp_key)
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")

    if settings.telegram_login_otp_enabled:
        if delivery_state is None:
            delivery_state = await load_otp_delivery_state(redis, mobile=mobile)
        if not await consume_otp_code(
            redis,
            mobile=mobile,
            expected_code=code,
        ):
            raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")
        if delivery_state is not None:
            audit_log(
                "otp.verified",
                target_type="otp_request",
                target_id=str(delivery_state.otp_request_id),
                result="success",
            )
        
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
