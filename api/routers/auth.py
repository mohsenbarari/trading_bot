from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timedelta
import logging
import math
from pydantic import BaseModel, field_validator, model_validator
from typing import Literal, Optional
from urllib.parse import urlsplit
import secrets
import hashlib
import hmac
import time
from jose import JWTError, jwt
from core.db import get_db
from models.user import User, UserRole
from models.customer_relation import CustomerTier
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind
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
from core.session_authority import assert_login_allowed_for_server, prepare_verified_login_for_server
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
from core.log_redaction import mask_mobile
from core.metrics import record_otp_event, record_registration_completion
from core.services.registration_notification_service import (
    publish_project_user_joined_web_notifications,
)
from models.session import Platform, UserSession
import uuid
from core.utils import normalize_persian_numerals, utc_now, utc_now_naive
from core.notifications import send_telegram_message
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
    configured_telegram_delivery_producer_mode,
)
from core.telegram_legacy_otp_relay_contract import (
    LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
)
from core.services.otp_delivery_state_service import (
    OTP_CODE_TTL_SECONDS,
    OTP_DELIVERY_STATE_DECODE_ERRORS,
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
LEGACY_OTP_MANUAL_SMS_RESEND_SECONDS = 30
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


def _otp_audit_result(outcome: object) -> str:
    value = str(getattr(outcome, "value", outcome) or "").strip().lower()
    if value in {"sent", "duplicate_sent", "accepted"}:
        return "success"
    if value in {"feature_disabled", "not_linked", "blocked"}:
        return "denied"
    return "failure"


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
        record_registration_completion(
            surface="telegram",
            outcome=result.outcome.value,
        )
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
        result=_otp_audit_result(result.outcome),
        extra={"outcome": result.outcome.value},
    )
    record_otp_event(event="telegram_delivery_result", outcome=result.outcome.value)
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
    account_name: str
    mobile_number: str
    role: UserRole
    expires_at: datetime | None = None


RegistrationContextKind = Literal["invitation", "registration"]
RegistrationContextProgress = Literal["context_ready", "otp_requested", "otp_verified"]


class RegistrationContextAlreadyCompleted(Exception):
    def __init__(self, handle: str):
        super().__init__("registration_complete")
        self.handle = handle


class RegistrationContextExchangeRequest(BaseModel):
    kind: RegistrationContextKind
    token: str
    exchange_id: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        token = value.strip()
        if not token:
            raise ValueError("registration handoff is required")
        return token

    @field_validator("exchange_id")
    @classmethod
    def validate_exchange_id(cls, value: str) -> str:
        exchange_id = value.strip()
        if not 8 <= len(exchange_id) <= 128 or not all(
            character.isalnum() or character in {"-", "_"} for character in exchange_id
        ):
            raise ValueError("exchange_id is invalid")
        return exchange_id


class RegistrationContextState(BaseModel):
    version: Literal[1] = 1
    kind: RegistrationContextKind
    invitation_token: str
    progress: RegistrationContextProgress
    handoff_claim_key: str | None = None


class RegistrationHandoffClaim(BaseModel):
    version: Literal[1] = 1
    exchange_id: str
    context_handle: str
    state: RegistrationContextState
    terminal: bool = False


class RegistrationContextResponse(PendingRegistrationContext):
    kind: RegistrationContextKind
    progress: RegistrationContextProgress
    requires_otp: bool


class RegistrationContextOTPVerifyRequest(BaseModel):
    code: str


class RegistrationContextCompleteRequest(BaseModel):
    address: str

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        from core.services.invitation_lifecycle_service import validate_registration_address

        return validate_registration_address(value)

# --- Endpoints ---

REGISTRATION_CONTEXT_TTL_SECONDS = 10 * 60
REGISTRATION_CONTEXT_COOKIE_PRODUCTION = "__Host-web_registration"
REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT = "web_registration"


def _registration_context_secure_cookie() -> bool:
    return (settings.environment or "").strip().lower() in {"production", "staging"}


def _registration_context_cookie_name() -> str:
    if _registration_context_secure_cookie():
        return REGISTRATION_CONTEXT_COOKIE_PRODUCTION
    return REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT


def _registration_context_key(handle: str) -> str:
    digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    return f"registration_context:{digest}"


def _registration_context_completion_key(handle: str) -> str:
    digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    return f"registration_context_completion:{digest}"


def _registration_context_verified_key(handle: str) -> str:
    digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()
    return f"registration_context_verified:{digest}"


def _registration_handoff_claim_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"registration_handoff_claim:{digest}"


def _set_registration_context_cookie(
    response: Response,
    handle: str,
    *,
    max_age: int = REGISTRATION_CONTEXT_TTL_SECONDS,
) -> None:
    response.set_cookie(
        key=_registration_context_cookie_name(),
        value=handle,
        max_age=max(1, int(max_age)),
        path="/",
        secure=_registration_context_secure_cookie(),
        httponly=True,
        samesite="strict",
    )


def _clear_registration_context_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_registration_context_cookie_name(),
        path="/",
        secure=_registration_context_secure_cookie(),
        httponly=True,
        samesite="strict",
    )


def _registration_context_clear_cookie_header() -> str:
    response = Response()
    _clear_registration_context_cookie(response)
    return response.headers["set-cookie"]


def _registration_no_store_headers(*, clear_cookie: bool = False) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    if clear_cookie:
        headers["Set-Cookie"] = _registration_context_clear_cookie_header()
    return headers


def _assert_registration_context_same_origin(raw_request: Request) -> None:
    """Reject browser cross-site mutations in addition to SameSite=Strict.

    Non-browser/internal callers may omit Origin and Sec-Fetch-Site. Browsers that
    identify a cross-site request or supply a foreign Origin fail closed.
    """
    fetch_site = (raw_request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site == "cross-site":
        raise HTTPException(status_code=403, detail="درخواست ثبت‌نام نامعتبر است")

    origin = (raw_request.headers.get("origin") or "").strip()
    if not origin:
        return

    forwarded_proto = (raw_request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    request_scheme = getattr(getattr(raw_request, "url", None), "scheme", "") or "http"
    allowed_schemes = {request_scheme.lower()}
    if forwarded_proto.lower() in {"http", "https"}:
        allowed_schemes.add(forwarded_proto.lower())
    # Never trust a client-supplied X-Forwarded-Host. Deployment proxies
    # canonicalize Host before forwarding the request to the application.
    host = (raw_request.headers.get("host") or "").strip()
    parsed = urlsplit(origin)
    if not host or parsed.scheme.lower() not in allowed_schemes or parsed.netloc.lower() != host.lower():
        raise HTTPException(status_code=403, detail="درخواست ثبت‌نام نامعتبر است")


def _registration_context_response(
    invitation: Invitation,
    state: RegistrationContextState,
) -> RegistrationContextResponse:
    context = _serialize_pending_registration_context(invitation)
    return RegistrationContextResponse(
        **context.model_dump(),
        kind=state.kind,
        progress=state.progress,
        requires_otp=state.kind == "invitation",
    )


async def _store_registration_context(
    redis,
    state: RegistrationContextState,
    *,
    ttl: int = REGISTRATION_CONTEXT_TTL_SECONDS,
) -> tuple[str, int]:
    bounded_ttl = max(1, min(REGISTRATION_CONTEXT_TTL_SECONDS, int(ttl)))
    handle = secrets.token_urlsafe(32)
    await redis.setex(
        _registration_context_key(handle),
        bounded_ttl,
        state.model_dump_json(),
    )
    return handle, bounded_ttl


async def _load_registration_handoff_claim(
    redis,
    token: str,
) -> RegistrationHandoffClaim | None:
    raw_claim = _redis_text(await redis.get(_registration_handoff_claim_key(token)))
    if not raw_claim:
        return None
    try:
        return RegistrationHandoffClaim.model_validate_json(raw_claim)
    except (TypeError, ValueError):
        # Corrupt claim data must never make a bearer replayable.
        raise HTTPException(
            status_code=409,
            detail="این انتقال ثبت‌نام قبلاً استفاده شده است",
            headers=_registration_no_store_headers(),
        ) from None


async def _claim_registration_handoff(
    redis,
    token: str,
    claim: RegistrationHandoffClaim,
) -> bool:
    return bool(
        await redis.set(
            _registration_handoff_claim_key(token),
            claim.model_dump_json(),
            ex=REGISTRATION_CONTEXT_TTL_SECONDS,
            nx=True,
        )
    )


async def _resume_registration_handoff_claim(
    db: AsyncSession,
    redis,
    token: str,
    claim: RegistrationHandoffClaim,
) -> tuple[RegistrationContextState, Invitation, int]:
    if claim.terminal:
        raise HTTPException(
            status_code=410,
            detail="این انتقال ثبت‌نام پایان یافته است",
            headers=_registration_no_store_headers(clear_cookie=True),
        )
    context_key = _registration_context_key(claim.context_handle)
    if not await redis.get(context_key):
        remaining = int(await redis.ttl(_registration_handoff_claim_key(token)) or 0)
        if remaining <= 0:
            raise HTTPException(
                status_code=410,
                detail="جلسه ثبت‌نام در دسترس نیست یا منقضی شده است",
                headers=_registration_no_store_headers(clear_cookie=True),
            )
        try:
            invitation, _, _ = await _load_valid_invitation_by_token(
                db,
                claim.state.invitation_token,
                missing_detail="دعوت‌نامه نامعتبر است",
                completed_context_handle=(
                    claim.context_handle
                    if claim.state.progress == "otp_verified"
                    else None
                ),
            )
        except RegistrationContextAlreadyCompleted:
            await _store_registration_context_completion(
                redis,
                claim.context_handle,
            )
            await _terminalize_registration_handoff_claim(
                redis,
                claim.state.handoff_claim_key,
            )
            raise
        ttl = min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining)
        await redis.setex(context_key, ttl, claim.state.model_dump_json())
        return claim.state, invitation, ttl

    state, invitation = await _load_registration_context_by_handle(
        db,
        redis,
        claim.context_handle,
    )
    remaining = int(await redis.ttl(context_key) or 0)
    return state, invitation, max(1, min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining))


async def _delete_registration_context(
    redis,
    handle: str | None,
    *,
    terminal: bool = False,
) -> None:
    if not handle:
        return
    context_key = _registration_context_key(handle)
    raw_state = _redis_text(await redis.get(context_key))
    state = None
    if raw_state:
        try:
            state = RegistrationContextState.model_validate_json(raw_state)
        except (TypeError, ValueError):
            state = None
    if terminal and state:
        await _terminalize_registration_handoff_claim(
            redis,
            state.handoff_claim_key,
        )
    await redis.delete(context_key)
    await redis.delete(_registration_context_verified_key(handle))


async def _terminalize_registration_handoff_claim(redis, claim_key: str | None) -> None:
    if not claim_key:
        return
    raw_claim = _redis_text(await redis.get(claim_key))
    if not raw_claim:
        return
    try:
        claim = RegistrationHandoffClaim.model_validate_json(raw_claim)
        remaining = int(await redis.ttl(claim_key) or 0)
        if remaining > 0:
            await redis.setex(
                claim_key,
                min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining),
                claim.model_copy(update={"terminal": True}).model_dump_json(),
            )
    except (TypeError, ValueError):
        pass


async def _registration_context_is_completed(redis, handle: str | None) -> bool:
    return bool(handle and await redis.get(_registration_context_completion_key(handle)))


async def _store_registration_context_completion(redis, handle: str) -> None:
    await redis.setex(
        _registration_context_completion_key(handle),
        REGISTRATION_CONTEXT_TTL_SECONDS,
        "registration_complete",
    )


def _registration_context_handle(raw_request: Request) -> str | None:
    cookies = getattr(raw_request, "cookies", {})
    value = cookies.get(_registration_context_cookie_name())
    if not isinstance(value, str):
        return None
    handle = value.strip()
    return handle or None


async def _load_registration_context(
    db: AsyncSession,
    redis,
    raw_request: Request,
) -> tuple[str, RegistrationContextState, Invitation]:
    handle = _registration_context_handle(raw_request)
    if not handle:
        raise HTTPException(
            status_code=410,
            detail="جلسه ثبت‌نام در دسترس نیست یا منقضی شده است",
            headers=_registration_no_store_headers(clear_cookie=True),
        )

    state, invitation = await _load_registration_context_by_handle(db, redis, handle)
    reconciled_state = state
    if state.kind == "invitation" and state.progress == "context_ready":
        if await redis.get(f"reg_otp:{state.invitation_token}"):
            reconciled_state = state.model_copy(update={"progress": "otp_requested"})
    if state.kind == "invitation" and state.progress != "otp_verified":
        if await redis.get(_registration_context_verified_key(handle)):
            reconciled_state = state.model_copy(update={"progress": "otp_verified"})
    if reconciled_state != state:
        await _update_registration_context(redis, handle, reconciled_state)
        state = reconciled_state
    return handle, state, invitation


async def _load_registration_context_by_handle(
    db: AsyncSession,
    redis,
    handle: str,
) -> tuple[RegistrationContextState, Invitation]:
    key = _registration_context_key(handle)
    raw_state = _redis_text(await redis.get(key))
    try:
        state = RegistrationContextState.model_validate_json(raw_state)
    except (TypeError, ValueError):
        await redis.delete(key)
        await redis.delete(_registration_context_verified_key(handle))
        raise HTTPException(
            status_code=410,
            detail="جلسه ثبت‌نام در دسترس نیست یا منقضی شده است",
            headers=_registration_no_store_headers(clear_cookie=True),
        ) from None

    try:
        invitation, _, _ = await _load_valid_invitation_by_token(
            db,
            state.invitation_token,
            missing_detail="دعوت‌نامه نامعتبر است",
            completed_context_handle=(
                handle if state.progress == "otp_verified" else None
            ),
        )
    except RegistrationContextAlreadyCompleted:
        await _store_registration_context_completion(redis, handle)
        await _delete_registration_context(redis, handle, terminal=True)
        raise
    except HTTPException as exc:
        await redis.delete(key)
        await redis.delete(_registration_context_verified_key(handle))
        raise HTTPException(
            status_code=410,
            detail=exc.detail,
            headers=_registration_no_store_headers(clear_cookie=True),
        ) from None
    return state, invitation


async def _update_registration_context(
    redis,
    handle: str,
    state: RegistrationContextState,
) -> int:
    key = _registration_context_key(handle)
    remaining = int(await redis.ttl(key) or 0)
    if remaining <= 0:
        await redis.delete(key)
        await redis.delete(_registration_context_verified_key(handle))
        raise HTTPException(
            status_code=410,
            detail="جلسه ثبت‌نام در دسترس نیست یا منقضی شده است",
            headers=_registration_no_store_headers(clear_cookie=True),
        )
    bounded_ttl = min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining)
    await redis.setex(key, bounded_ttl, state.model_dump_json())
    return bounded_ttl


def _registration_context_json_response(
    payload: BaseModel | dict[str, object],
    *,
    status_code: int = 200,
) -> JSONResponse:
    content = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=_registration_no_store_headers(),
    )


def _registration_context_completed_response(handle: str) -> JSONResponse:
    response = _registration_context_json_response(
        {"status": "registration_complete"}
    )
    _set_registration_context_cookie(response, handle)
    return response


def _registration_required_response(
    invitation: Invitation,
    handle: str,
    expires_in: int,
) -> JSONResponse:
    response = _registration_context_json_response(
        {
            "status": "registration_required",
            "expires_in": expires_in,
            "invitation": _serialize_pending_registration_context(invitation).model_dump(
                mode="json"
            ),
        }
    )
    _set_registration_context_cookie(response, handle, max_age=expires_in)
    return response


async def _resume_registration_required_context(
    db: AsyncSession,
    redis,
    raw_request: Request,
    *,
    mobile: str,
) -> JSONResponse | None:
    """Recover a consumed Login OTP when its registration response was lost."""
    handle = _registration_context_handle(raw_request)
    if not handle:
        return None
    if await _registration_context_is_completed(redis, handle):
        return _registration_context_completed_response(handle)
    try:
        state, invitation = await _load_registration_context_by_handle(
            db,
            redis,
            handle,
        )
    except RegistrationContextAlreadyCompleted as completed:
        return _registration_context_completed_response(completed.handle)
    except HTTPException:
        return None
    if (
        state.kind != "registration"
        or state.progress != "otp_verified"
        or not constant_time_secret_equals(invitation.mobile_number, mobile)
    ):
        return None
    remaining = int(await redis.ttl(_registration_context_key(handle)) or 0)
    if remaining <= 0:
        await _delete_registration_context(redis, handle)
        return None
    return _registration_required_response(
        invitation,
        handle,
        min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining),
    )


async def _consume_direct_registration_handoff(
    redis,
    registration_token: str,
) -> None:
    await redis.delete(_registration_session_key(registration_token))

def _registration_session_key(registration_token: str) -> str:
    return f"registration_session:{registration_token}"


async def _load_valid_invitation_by_token(
    db: AsyncSession,
    token: str,
    *,
    missing_detail: str = "دعوت‌نامه نامعتبر است",
    completed_context_handle: str | None = None,
) -> tuple[Invitation, object | None, object | None]:
    stmt = select(Invitation).where(Invitation.token == token)
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if not invitation:
        raise HTTPException(status_code=404, detail=missing_detail)
    if invitation.is_used:
        if (
            completed_context_handle
            and getattr(invitation, "registered_user_id", None) is not None
            and getattr(invitation, "completed_at", None) is not None
            and getattr(invitation, "completed_via", None)
            == InvitationCompletionSurface.WEB
        ):
            raise RegistrationContextAlreadyCompleted(completed_context_handle)
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
        account_name=invitation.account_name,
        mobile_number=mask_mobile(invitation.mobile_number),
        role=invitation.role,
        expires_at=getattr(invitation, "expires_at", None),
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


@router.put("/me/offer-overtime", response_model=schemas.UserOfferOvertimeUpdateResponse)
async def update_my_offer_overtime(
    payload: schemas.UserOfferOvertimeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """WebApp self-service save for the overtime preference.

    Iran is the only writer. The WebApp surface lives on Iran; a call that
    somehow lands elsewhere is refused without a local write so the
    Iran-authoritative field cannot diverge.
    """
    from core.cache import invalidate_user_cache
    from core.services.offer_overtime_preference_service import (
        BOT_SAVE_UNAVAILABLE_MESSAGE,
        OfferOvertimePreferenceError,
        OfferOvertimePreferenceNotAllowedError,
        persist_overtime_preference,
    )

    if current_server() != SERVER_IRAN:
        raise HTTPException(status_code=503, detail=BOT_SAVE_UNAVAILABLE_MESSAGE)

    try:
        result = await persist_overtime_preference(
            db,
            current_user,
            payload.offer_overtime_minutes,
        )
    except OfferOvertimePreferenceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except OfferOvertimePreferenceNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc

    await db.commit()
    await db.refresh(current_user)
    telegram_id = getattr(current_user, "telegram_id", None)
    if telegram_id is not None:
        await invalidate_user_cache(telegram_id)
    return schemas.UserOfferOvertimeUpdateResponse(
        offer_overtime_minutes=result.offer_overtime_minutes,
        detail=result.detail,
        warning=result.warning,
    )


@router.post(
    "/internal/offer-overtime/update",
    response_model=schemas.UserOfferOvertimeUpdateResponse,
)
async def update_offer_overtime_internal(
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Signed foreign→Iran command that persists a bot-origin preference save."""
    from core.cache import invalidate_user_cache
    from core.services.offer_overtime_preference_service import (
        OfferOvertimePreferenceError,
        OfferOvertimePreferenceNotAllowedError,
        persist_overtime_preference,
    )

    body = await raw_request.body()
    _verify_foreign_internal_command(raw_request, body)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid internal overtime preference command") from None

    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid internal overtime preference user") from None

    user = await db.get(User, user_id)
    if user is None or getattr(user, "is_deleted", False):
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    try:
        result = await persist_overtime_preference(
            db,
            user,
            payload.get("offer_overtime_minutes"),
        )
    except OfferOvertimePreferenceError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except OfferOvertimePreferenceNotAllowedError as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc

    await db.commit()
    await db.refresh(user)
    telegram_id = getattr(user, "telegram_id", None)
    if telegram_id is not None:
        await invalidate_user_cache(telegram_id)
    return schemas.UserOfferOvertimeUpdateResponse(
        offer_overtime_minutes=result.offer_overtime_minutes,
        detail=result.detail,
        warning=result.warning,
    )


LEGACY_RAW_REGISTRATION_RETIRED_DETAIL = (
    "این مسیر ثبت‌نام بازنشسته شده است؛ صفحه را دوباره بارگذاری کنید"
)


def _raise_legacy_raw_registration_retired() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=LEGACY_RAW_REGISTRATION_RETIRED_DETAIL,
        headers=_registration_no_store_headers(),
    )


@router.post("/register-otp-request", response_model=dict, deprecated=True)
async def retired_register_otp_request(req: RegisterOTPRequest):
    """Reject raw-bearer mutation before Redis, DB, OTP, or provider access."""
    _raise_legacy_raw_registration_retired()


# Server-private implementation used only after an opaque context is loaded.
# It intentionally has no FastAPI decorator.
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

@router.post("/register-otp-verify", response_model=dict, deprecated=True)
async def retired_register_otp_verify(req: RegisterOTPVerify):
    """Reject raw-bearer mutation before proof lookup or OTP consumption."""
    _raise_legacy_raw_registration_retired()


# Server-private implementation used only after an opaque context is loaded.
async def register_otp_verify(
    req: RegisterOTPVerify,
    db: AsyncSession = Depends(get_db),
    *,
    verification_key: str,
    verification_ttl: int,
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
    
    # Persist the verified receipt before consuming the OTP. For the modern
    # flow this key is handle-bound and no longer than the remaining context;
    # a crash between these writes can therefore resume safely without giving
    # the raw invitation bearer completion authority.
    await redis.setex(
        verification_key,
        max(1, min(REGISTRATION_CONTEXT_TTL_SECONDS, int(verification_ttl))),
        "1",
    )

    # Delete OTP to prevent replay attacks.
    await redis.delete(otp_key)
    await _clear_otp_verify_subject_failures(redis, subject=verify_subject)
    
    return {"detail": "کد تایید شد"}

@router.post("/register-complete", response_model=Token, deprecated=True)
async def retired_register_complete(req: RegisterComplete):
    """Reject raw-bearer completion before proof lookup or durable mutation."""
    _raise_legacy_raw_registration_retired()


# Server-private authoritative completion helper. Modern callers must pass the
# invitation token taken from the already verified cookie context.
async def register_complete(
    req: RegisterComplete,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
    *,
    verified_invitation_token: str,
):
    invitation_token = (req.token or "").strip()
    if not invitation_token:
        raise HTTPException(status_code=400, detail="توکن دعوت الزامی است")
    if not constant_time_secret_equals(
        invitation_token,
        verified_invitation_token,
    ):
        raise HTTPException(status_code=409, detail="مرحله ثبت‌نام نامعتبر است")

    redis = await get_redis()

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

    if registration_result.first_terminal_transition:
        record_registration_completion(
            surface="webapp",
            outcome=registration_result.outcome.value,
        )

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


@router.post("/registration-context/exchange")
async def exchange_registration_context(
    req: RegistrationContextExchangeRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consume one raw handoff and replace it with an opaque HttpOnly context."""
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    old_handle = _registration_context_handle(raw_request)
    if await _registration_context_is_completed(redis, old_handle):
        return _registration_context_completed_response(old_handle)

    existing_claim = await _load_registration_handoff_claim(redis, req.token)
    if existing_claim is not None:
        if (
            existing_claim.state.kind != req.kind
            or (
                existing_claim.exchange_id != req.exchange_id
                and old_handle != existing_claim.context_handle
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="این انتقال ثبت‌نام قبلاً استفاده شده است",
                headers=_registration_no_store_headers(),
            )
        try:
            state, invitation, ttl = await _resume_registration_handoff_claim(
                db,
                redis,
                req.token,
                existing_claim,
            )
        except RegistrationContextAlreadyCompleted as completed:
            return _registration_context_completed_response(completed.handle)
        if state.kind == "registration":
            await _consume_direct_registration_handoff(
                redis,
                req.token,
            )
        if old_handle and old_handle != existing_claim.context_handle:
            await _delete_registration_context(redis, old_handle, terminal=True)
        response = _registration_context_json_response(
            _registration_context_response(invitation, state),
        )
        _set_registration_context_cookie(
            response,
            existing_claim.context_handle,
            max_age=ttl,
        )
        return response

    if req.kind == "registration":
        invitation_token = await _load_registration_session_token(
            redis,
            registration_token=req.token,
        )
        progress: RegistrationContextProgress = "otp_verified"
    else:
        invitation_token = req.token
        progress = "context_ready"

    invitation, _, _ = await _load_valid_invitation_by_token(
        db,
        invitation_token,
        missing_detail="دعوت‌نامه نامعتبر است",
    )

    state = RegistrationContextState(
        kind=req.kind,
        invitation_token=invitation_token,
        progress=progress,
        handoff_claim_key=_registration_handoff_claim_key(req.token),
    )
    handle, ttl = await _store_registration_context(redis, state)
    claim = RegistrationHandoffClaim(
        exchange_id=req.exchange_id,
        context_handle=handle,
        state=state,
    )
    if not await _claim_registration_handoff(redis, req.token, claim):
        await _delete_registration_context(redis, handle)
        winning_claim = await _load_registration_handoff_claim(redis, req.token)
        if (
            winning_claim is None
            or winning_claim.state.kind != req.kind
            or (
                winning_claim.exchange_id != req.exchange_id
                and old_handle != winning_claim.context_handle
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="این انتقال ثبت‌نام قبلاً استفاده شده است",
                headers=_registration_no_store_headers(),
            )
        try:
            state, invitation, ttl = await _resume_registration_handoff_claim(
                db,
                redis,
                req.token,
                winning_claim,
            )
        except RegistrationContextAlreadyCompleted as completed:
            return _registration_context_completed_response(completed.handle)
        handle = winning_claim.context_handle

    if req.kind == "registration":
        # Login OTP already proved ownership of this mobile. The old REG bearer
        # becomes unusable immediately after the cookie context is established.
        await _consume_direct_registration_handoff(redis, req.token)

    if old_handle and old_handle != handle:
        await _delete_registration_context(redis, old_handle, terminal=True)

    response = _registration_context_json_response(
        _registration_context_response(invitation, state),
    )
    _set_registration_context_cookie(response, handle, max_age=ttl)
    return response


@router.post("/registration-context")
async def read_registration_context(
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    handle = _registration_context_handle(raw_request)
    if await _registration_context_is_completed(redis, handle):
        return _registration_context_completed_response(handle)
    try:
        _, state, invitation = await _load_registration_context(db, redis, raw_request)
    except RegistrationContextAlreadyCompleted as completed:
        return _registration_context_completed_response(completed.handle)
    return _registration_context_json_response(
        _registration_context_response(invitation, state),
    )


@router.post("/registration-context/otp/request")
async def request_registration_context_otp(
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    pending_handle = _registration_context_handle(raw_request)
    if await _registration_context_is_completed(redis, pending_handle):
        return _registration_context_completed_response(pending_handle)
    try:
        handle, state, _ = await _load_registration_context(db, redis, raw_request)
    except RegistrationContextAlreadyCompleted as completed:
        return _registration_context_completed_response(completed.handle)
    if state.kind != "invitation" or state.progress == "otp_verified":
        raise HTTPException(
            status_code=409,
            detail="مرحله ثبت‌نام با این درخواست سازگار نیست",
            headers=_registration_no_store_headers(),
        )

    active_otp_key = f"reg_otp:{state.invitation_token}"
    active_otp = await redis.get(active_otp_key)
    active_otp_ttl = int(await redis.ttl(active_otp_key) or 0)
    if active_otp is not None and active_otp_ttl > 0:
        if state.progress != "otp_requested":
            state = state.model_copy(update={"progress": "otp_requested"})
            await _update_registration_context(redis, handle, state)
        return _registration_context_json_response(
            {
                "detail": "کد تایید ارسال شد",
                "expires_in": active_otp_ttl,
            }
        )

    try:
        receipt = await register_otp_request(
            RegisterOTPRequest(token=state.invitation_token),
            db=db,
        )
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **_registration_no_store_headers()}
        raise

    state = state.model_copy(update={"progress": "otp_requested"})
    await _update_registration_context(redis, handle, state)
    return _registration_context_json_response(receipt)


@router.post("/registration-context/otp/verify")
async def verify_registration_context_otp(
    req: RegistrationContextOTPVerifyRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    pending_handle = _registration_context_handle(raw_request)
    if await _registration_context_is_completed(redis, pending_handle):
        return _registration_context_completed_response(pending_handle)
    try:
        handle, state, _ = await _load_registration_context(db, redis, raw_request)
    except RegistrationContextAlreadyCompleted as completed:
        return _registration_context_completed_response(completed.handle)
    if state.kind != "invitation":
        raise HTTPException(
            status_code=409,
            detail="مرحله ثبت‌نام با این درخواست سازگار نیست",
            headers=_registration_no_store_headers(),
        )

    if state.progress == "otp_verified":
        return _registration_context_json_response({"detail": "کد تایید شد"})
    if state.progress != "otp_requested":
        raise HTTPException(
            status_code=409,
            detail="مرحله ثبت‌نام با این درخواست سازگار نیست",
            headers=_registration_no_store_headers(),
        )

    # If the OTP was consumed but the previous response was lost, only a proof
    # bound to this opaque cookie handle may recover the verified phase. A raw
    # invitation bearer must never inherit completion authority.
    verified_key = _registration_context_verified_key(handle)
    if await redis.get(verified_key):
        state = state.model_copy(update={"progress": "otp_verified"})
        await _update_registration_context(redis, handle, state)
        return _registration_context_json_response({"detail": "کد تایید شد"})

    remaining = int(await redis.ttl(_registration_context_key(handle)) or 0)
    if remaining <= 0:
        await _delete_registration_context(redis, handle, terminal=True)
        raise HTTPException(
            status_code=410,
            detail="جلسه ثبت‌نام در دسترس نیست یا منقضی شده است",
            headers=_registration_no_store_headers(clear_cookie=True),
        )

    try:
        receipt = await register_otp_verify(
            RegisterOTPVerify(token=state.invitation_token, code=req.code),
            db=db,
            verification_key=verified_key,
            verification_ttl=min(REGISTRATION_CONTEXT_TTL_SECONDS, remaining),
        )
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **_registration_no_store_headers()}
        raise

    state = state.model_copy(update={"progress": "otp_verified"})
    await _update_registration_context(redis, handle, state)
    return _registration_context_json_response(receipt)


@router.post("/registration-context/complete")
async def complete_registration_context(
    req: RegistrationContextCompleteRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    pending_handle = _registration_context_handle(raw_request)
    if await _registration_context_is_completed(redis, pending_handle):
        return _registration_context_completed_response(pending_handle)
    try:
        handle, state, _ = await _load_registration_context(db, redis, raw_request)
    except RegistrationContextAlreadyCompleted as completed:
        return _registration_context_completed_response(completed.handle)
    if state.progress != "otp_verified":
        raise HTTPException(
            status_code=409,
            detail="لطفاً ابتدا کد تایید را وارد کنید",
            headers=_registration_no_store_headers(),
        )

    try:
        receipt = await register_complete(
            RegisterComplete(token=state.invitation_token, address=req.address),
            raw_request=raw_request,
            db=db,
            verified_invitation_token=state.invitation_token,
        )
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), **_registration_no_store_headers()}
        raise

    await _store_registration_context_completion(redis, handle)
    await _delete_registration_context(redis, handle, terminal=True)
    response = _registration_context_json_response(receipt)
    # Keep the opaque handle until the client acknowledges the receipt via the
    # clear endpoint. If headers/body delivery is ambiguous, the bounded
    # completion marker remains reachable without persisting auth tokens.
    _set_registration_context_cookie(response, handle)
    return response


@router.post("/registration-context/clear", status_code=204)
async def clear_registration_context(raw_request: Request):
    _assert_registration_context_same_origin(raw_request)
    redis = await get_redis()
    handle = _registration_context_handle(raw_request)
    await _delete_registration_context(
        redis,
        handle,
        terminal=True,
    )
    if handle:
        await redis.delete(_registration_context_completion_key(handle))
    response = Response(status_code=204, headers=_registration_no_store_headers())
    _clear_registration_context_cookie(response)
    return response

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
        result=_otp_audit_result(attempt.outcome),
        extra={
            "outcome": attempt.outcome.value,
            "provider_attempted": attempt.provider_attempted,
            "result_recorded": attempt.result_recorded,
        },
    )
    record_otp_event(event="sms_delivery_result", outcome=attempt.outcome.value)
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


def _legacy_otp_timing_payload(*, remaining_seconds: int) -> dict:
    now = utc_now()
    remaining = max(0, int(remaining_seconds))
    expires_at = now + timedelta(seconds=remaining)
    elapsed = max(0, OTP_TTL_SECONDS - remaining)
    resend_in = max(0, LEGACY_OTP_MANUAL_SMS_RESEND_SECONDS - elapsed)
    return {
        "delivery_contract": "legacy",
        "manual_sms_resend": True,
        "legacy_sms_resend_at": (now + timedelta(seconds=resend_in)).isoformat(),
        "expires_in": remaining,
        "expires_at": expires_at.isoformat(),
    }


async def _load_otp_delivery_state_for_verification(
    redis,
    *,
    request_id: uuid.UUID | None = None,
    mobile: str | None = None,
):
    try:
        return await load_otp_delivery_state(
            redis,
            request_id=request_id,
            mobile=mobile,
        )
    except OTP_DELIVERY_STATE_DECODE_ERRORS:
        target_id = str(request_id) if request_id is not None else None
        logger.warning(
            "Malformed OTP delivery state rejected during verification",
            extra={
                "event": "otp.delivery_state_invalid",
                "otp_request_id": target_id,
            },
        )
        audit_log(
            "otp.delivery_state_invalid",
            target_type="otp_request",
            target_id=target_id,
            result="denied",
            reason="invalid_delivery_state",
        )
        return None


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
        result="success",
    )
    record_otp_event(event="requested")

    telegram_id = int(user.telegram_id) if user and user.telegram_id else None
    use_telegram = bool(telegram_id and settings.telegram_login_otp_enabled)
    sms_fallback_ready = bool(settings.otp_sms_auto_fallback_enabled)
    if use_telegram:
        fallback_seconds = OTP_SMS_FALLBACK_SECONDS
        command = TelegramOTPDeliveryCommand(
            otp_request_id=state.otp_request_id,
            telegram_id=telegram_id,
            otp_code=otp_code,
            expires_at=state.expires_at,
        )
        status_code, body = 503, {"detail": "Telegram delivery failed"}
        if sms_fallback_ready:
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
            if fallback_armed:
                try:
                    status_code, body = await forward_telegram_otp_delivery(command)
                except Exception:
                    status_code, body = 503, {"detail": "Telegram delivery failed"}
            else:
                status_code, body = 503, {"detail": "OTP fallback scheduling failed"}
        else:
            try:
                status_code, body = await forward_telegram_otp_delivery(command)
            except Exception:
                status_code, body = 503, {"detail": "Telegram delivery failed"}
        try:
            delivery = TelegramOTPDeliveryResponse.model_validate(body)
        except (TypeError, ValueError):
            delivery = None
        telegram_sent = (
            status_code == 200
            and delivery is not None
            and delivery.otp_request_id == state.otp_request_id
            and delivery.outcome
            in {
                TelegramOTPDeliveryOutcome.SENT,
                TelegramOTPDeliveryOutcome.DUPLICATE_SENT,
            }
        )
        if telegram_sent and sms_fallback_ready:
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
                    result="success",
                    extra={
                        "fallback_seconds": fallback_seconds,
                        "lifecycle_state": "scheduled",
                    },
                )
                record_otp_event(event="sms_fallback_scheduled")
                return {
                    "detail": "کد تایید ارسال شد",
                    **_otp_timing_payload(state.model_copy(update={
                        "telegram_delivery_status": OTPDeliveryStatus.ACCEPTED,
                        "telegram_sent_at": sent_at,
                        "sms_fallback_at": fallback_at,
                    }), method="telegram"),
                }
        elif telegram_sent:
            sent_at = utc_now()
            return {
                "detail": "کد تایید ارسال شد",
                **_otp_timing_payload(state.model_copy(update={
                    "telegram_delivery_status": OTPDeliveryStatus.ACCEPTED,
                    "telegram_sent_at": sent_at,
                }), method="telegram"),
            }
        if not sms_fallback_ready:
            await cancel_otp_delivery(redis, mobile=mobile)
            raise HTTPException(
                status_code=503,
                detail="سرویس ارسال کد موقتاً در دسترس نیست",
            )

    if not sms_fallback_ready:
        await cancel_otp_delivery(redis, mobile=mobile)
        raise HTTPException(
            status_code=503,
            detail="سرویس ارسال کد موقتاً در دسترس نیست",
        )

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

    queue_v1_sms_only = (
        configured_telegram_delivery_producer_mode()
        == TelegramDeliveryRuntimeMode.QUEUE_V1
    )

    rate_limit_key = f"otp_limit:{mobile}"
    otp_key = f"otp:{mobile}"

    async def active_legacy_otp_response():
        active_code = await redis.get(otp_key)
        remaining = int(await redis.ttl(otp_key) or 0)
        if active_code is None or remaining <= 0:
            return None
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    "کد قبلی هنوز معتبر است. "
                    f"لطفاً {remaining} ثانیه صبر کنید."
                ),
                "code": "otp_active",
                **_legacy_otp_timing_payload(remaining_seconds=remaining),
            },
            headers={"Cache-Control": "no-store"},
        )

    limit_val = await redis.get(rate_limit_key)
    logger.info(
        "OTP request rate-limit state checked",
        extra={"event": "otp.rate_limit_checked", "active": limit_val is not None},
    )
    
    if limit_val:
        logger.info("OTP request rate limit hit", extra={"event": "otp.rate_limited"})
        active_response = await active_legacy_otp_response()
        if active_response is not None:
            return active_response
        raise HTTPException(status_code=429, detail="لطفاً ۲ دقیقه صبر کنید")

    # تولید کد ۵ رقمی
    # First, check if valid OTP already exists (Strict "One Code per 120s" rule)
    active_otp = await redis.get(otp_key)
    
    if active_otp:
        logger.info("Active OTP exists; blocking new generation", extra={"event": "otp.active_exists"})
        active_response = await active_legacy_otp_response()
        if active_response is not None:
            return active_response
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
    
    # اولویت ۱: تلگرام فقط در runtime واقعاً Legacy.
    if not queue_v1_sms_only and is_connected and has_telegram:
        try:
            msg_text = f"🔐 کد ورود شما: `{otp_code}`\n\nاین کد تا ۲ دقیقه معتبر است."
            await send_telegram_message(
                result.telegram_id,
                msg_text,
                purpose=LEGACY_TELEGRAM_OTP_RELAY_PURPOSE,
            )
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
        delivery_state = await _load_otp_delivery_state_for_verification(
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

    # If a prior verify consumed the OTP and committed the opaque registration
    # cookie but its response body was lost, resume from that server-bound
    # context before consulting the now-consumed code.
    resumed_registration = await _resume_registration_required_context(
        db,
        redis,
        raw_request,
        mobile=mobile,
    )
    if resumed_registration is not None:
        return resumed_registration

    code = normalize_persian_numerals(request.code)

    otp_key = f"otp:{mobile}"
    stored_code = await redis.get(otp_key)
    await _ensure_otp_verify_not_locked(redis, subject=mobile, raw_request=raw_request)
    
    if not constant_time_secret_equals(code, _redis_text(stored_code)):
        await _record_otp_verify_failure(redis, subject=mobile, raw_request=raw_request, otp_key=otp_key)
        raise HTTPException(status_code=400, detail="کد تایید نامعتبر یا منقضی شده است")

    if settings.telegram_login_otp_enabled:
        if delivery_state is None:
            delivery_state = await _load_otp_delivery_state_for_verification(
                redis,
                mobile=mobile,
            )
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
            record_otp_event(
                event="verified",
                outcome=(
                    "after_sms_fallback"
                    if delivery_state.sms_delivery_status
                    != OTPDeliveryStatus.NOT_ATTEMPTED
                    else "before_sms_fallback"
                ),
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
        context_state = RegistrationContextState(
            kind="registration",
            invitation_token=invitation.token,
            progress="otp_verified",
        )
        context_handle, expires_in = await _store_registration_context(redis, context_state)
        return _registration_required_response(
            invitation,
            context_handle,
            expires_in,
        )

    if get_user_account_status(user).value == "inactive":
        await redis.delete(otp_key)
        await redis.delete(f"otp_limit:{mobile}")
        await _clear_otp_verify_subject_failures(redis, subject=mobile)
        _raise_inactive_account_error()

    login_home_server = _login_home_server(raw_request)
    remote_replaced_session_count = await prepare_verified_login_for_server(
        db,
        user,
        requested_server=login_home_server,
    )

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

    replaced_session_count = (
        int(session_result.get("replaced_session_count") or 0)
        + int(remote_replaced_session_count or 0)
    )
    if replaced_session_count > 0:
        try:
            from core.services.user_flag_service import record_session_replacement_activity

            await record_session_replacement_activity(
                db,
                user=user,
                replaced_session_count=replaced_session_count,
                device_name=device_info["device_name"],
                device_ip=device_info["device_ip"],
                platform=device_info["platform"],
                home_server=login_home_server,
            )
        except Exception:
            logger.exception(
                "Session replacement risk recording failed after successful login",
                extra={"event": "session.replacement.risk_recording_failed", "user_id": user.id},
            )
    
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

@router.post("/webapp-login")
async def webapp_login(
    login_data: WebAppLogin,
    raw_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Retired Telegram Mini App login. OTP-first WebApp auth is the supported path."""
    del login_data, raw_request, db
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Telegram Mini App login is retired",
    )

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
