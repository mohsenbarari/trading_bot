"""Iran-local, restart-safe state machine for one-code login OTP delivery."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from collections import Counter
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings
from core.registration_contracts import (
    OTPDeliveryStateContract,
    OTPDeliveryStatus,
    OTPRequestStatus,
    normalize_registration_mobile_number,
)
from core.server_routing import SERVER_IRAN, normalize_server
from core.utils import utc_now


OTP_FALLBACK_DUE_KEY = "otp_delivery:sms_fallback_due"
OTP_CODE_TTL_SECONDS = 120
OTP_SMS_FALLBACK_SECONDS = 40
OTP_SMS_CLAIM_LEASE_SECONDS = 30
OTP_SMS_MINIMUM_SEND_TTL_SECONDS = 15
_MIN_STATE_SECRET_LENGTH = 32
_DUE_SCAN_MULTIPLIER = 5
_DUE_MIN_SCAN_BUDGET = 500
_SAFE_FALLBACK_ISOLATION_REASONS = frozenset({
    "invalid_contract",
    "invalid_delivery_target",
    "worker_exception",
})


def validate_otp_delivery_runtime_settings(settings_obj=settings) -> None:
    """Fail closed on drift from the owner-approved Stage 6 timing/privacy contract."""

    if not bool(getattr(settings_obj, "telegram_login_otp_enabled", False)):
        return
    if int(getattr(settings_obj, "otp_ttl_seconds", 0)) != OTP_CODE_TTL_SECONDS:
        raise RuntimeError("OTP_TTL_SECONDS must be exactly 120 when Telegram login OTP is enabled")
    if (
        bool(getattr(settings_obj, "otp_sms_auto_fallback_enabled", False))
        and int(getattr(settings_obj, "otp_sms_auto_fallback_seconds", 0))
        != OTP_SMS_FALLBACK_SECONDS
    ):
        raise RuntimeError(
            "OTP_SMS_AUTO_FALLBACK_SECONDS must be exactly 40 when automatic fallback is enabled"
        )
    if normalize_server(getattr(settings_obj, "server_mode", None)) == SERVER_IRAN:
        secret = str(getattr(settings_obj, "otp_delivery_state_secret", "") or "")
        if len(secret) < _MIN_STATE_SECRET_LENGTH:
            raise RuntimeError(
                "OTP_DELIVERY_STATE_SECRET must contain at least 32 characters on Iran"
            )


def _state_secret() -> bytes:
    secret = str(getattr(settings, "otp_delivery_state_secret", "") or "")
    if len(secret) < _MIN_STATE_SECRET_LENGTH:
        raise RuntimeError("OTP delivery state encryption is not configured")
    return secret.encode("utf-8")


def _fernet() -> Fernet:
    key = hashlib.sha256(b"trading-bot:otp-delivery-state:v1\x00" + _state_secret()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _identity_digest(mobile: str) -> str:
    return hmac.new(
        _state_secret(),
        f"otp-mobile:v1:{mobile}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _seal_mobile(mobile: str) -> str:
    return _fernet().encrypt(mobile.encode("ascii")).decode("ascii")


def _open_mobile(ciphertext: str) -> str:
    try:
        mobile = _fernet().decrypt(ciphertext.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise RuntimeError("OTP delivery target cannot be decrypted") from exc
    return normalize_registration_mobile_number(mobile)


def mobile_for_delivery_state(state: OTPDeliveryStateContract) -> str:
    mobile = _open_mobile(state.delivery_target_ciphertext)
    if not hmac.compare_digest(state.identity_digest, _identity_digest(mobile)):
        raise RuntimeError("OTP delivery target identity mismatch")
    return mobile


def _state_key(request_id: UUID | str) -> str:
    return f"otp_delivery:request:{request_id}"


def _mobile_request_key(mobile: str) -> str:
    return f"otp_delivery:mobile:{_identity_digest(mobile)}"


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_otp_delivery_state(
    *,
    mobile: str,
    telegram_id: int | None = None,
    ttl_seconds: int,
    now: datetime | None = None,
) -> OTPDeliveryStateContract:
    del telegram_id  # Telegram identity belongs only to the short-lived signed command.
    normalized_mobile = normalize_registration_mobile_number(mobile)
    created_at = _utc(now or utc_now())
    return OTPDeliveryStateContract(
        otp_request_id=uuid4(),
        identity_digest=_identity_digest(normalized_mobile),
        delivery_target_ciphertext=_seal_mobile(normalized_mobile),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=max(1, int(ttl_seconds))),
    )


_CREATE_SCRIPT = """
if redis.call('exists', KEYS[1]) == 1 or redis.call('exists', KEYS[2]) == 1 then
    return 0
end
redis.call('set', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('set', KEYS[2], '1', 'EX', ARGV[2])
redis.call('hset', KEYS[3],
    'otp_request_id', ARGV[3],
    'purpose', 'web_login',
    'identity_digest', ARGV[4],
    'delivery_target_ciphertext', ARGV[5],
    'status', 'pending',
    'created_at', ARGV[6],
    'expires_at', ARGV[7],
    'telegram_delivery_status', 'not_attempted',
    'telegram_sent_at', '',
    'sms_fallback_at', '',
    'sms_delivery_status', 'not_attempted',
    'sms_sent_at', '',
    'sms_claim_id', '',
    'sms_claimed_at', '',
    'sms_claim_lease_until', '',
    'sms_claim_lease_until_epoch', '',
    'sms_provider_started_at', '')
redis.call('expire', KEYS[3], ARGV[2])
redis.call('set', KEYS[4], ARGV[3], 'EX', ARGV[2])
return 1
"""


async def create_otp_delivery_state(
    redis,
    *,
    state: OTPDeliveryStateContract,
    otp_code: str,
    ttl_seconds: int,
) -> bool:
    mobile = mobile_for_delivery_state(state)
    created = await redis.eval(
        _CREATE_SCRIPT,
        4,
        f"otp:{mobile}",
        f"otp_limit:{mobile}",
        _state_key(state.otp_request_id),
        _mobile_request_key(mobile),
        otp_code,
        max(1, int(ttl_seconds)),
        str(state.otp_request_id),
        state.identity_digest,
        state.delivery_target_ciphertext,
        state.created_at.isoformat(),
        state.expires_at.isoformat(),
    )
    return int(created or 0) == 1


def _optional_datetime(value: object | None) -> datetime | None:
    raw = (_text(value) or "").strip()
    return datetime.fromisoformat(raw) if raw else None


async def load_otp_delivery_state(
    redis,
    *,
    request_id: UUID | str | None = None,
    mobile: str | None = None,
) -> OTPDeliveryStateContract | None:
    if request_id is None:
        if not mobile:
            raise ValueError("request_id or mobile is required")
        normalized_mobile = normalize_registration_mobile_number(mobile)
        request_id = _text(await redis.get(_mobile_request_key(normalized_mobile)))
        if not request_id:
            return None
    raw = await redis.hgetall(_state_key(request_id))
    if not raw:
        return None
    values = {_text(key): _text(value) for key, value in raw.items()}
    claim_id = (values.get("sms_claim_id") or "").strip()
    return OTPDeliveryStateContract(
        otp_request_id=values["otp_request_id"],
        purpose=values.get("purpose") or "web_login",
        identity_digest=values["identity_digest"],
        delivery_target_ciphertext=values["delivery_target_ciphertext"],
        status=values.get("status") or OTPRequestStatus.PENDING.value,
        created_at=datetime.fromisoformat(values["created_at"]),
        expires_at=datetime.fromisoformat(values["expires_at"]),
        telegram_delivery_status=(
            values.get("telegram_delivery_status")
            or OTPDeliveryStatus.NOT_ATTEMPTED.value
        ),
        telegram_sent_at=_optional_datetime(values.get("telegram_sent_at")),
        sms_fallback_at=_optional_datetime(values.get("sms_fallback_at")),
        sms_delivery_status=(
            values.get("sms_delivery_status")
            or OTPDeliveryStatus.NOT_ATTEMPTED.value
        ),
        sms_sent_at=_optional_datetime(values.get("sms_sent_at")),
        sms_claim_id=claim_id or None,
        sms_claimed_at=_optional_datetime(values.get("sms_claimed_at")),
        sms_claim_lease_until=_optional_datetime(values.get("sms_claim_lease_until")),
        sms_provider_started_at=_optional_datetime(values.get("sms_provider_started_at")),
    )


_ARM_FALLBACK_SCRIPT = """
if redis.call('hget', KEYS[1], 'status') ~= 'pending' then return 0 end
if redis.call('hget', KEYS[1], 'telegram_delivery_status') ~= 'not_attempted' then return 0 end
redis.call('hset', KEYS[1],
    'telegram_delivery_status', 'pending',
    'sms_fallback_at', ARGV[1])
redis.call('zadd', KEYS[2], ARGV[2], ARGV[3])
return 1
"""


async def arm_sms_fallback(
    redis,
    *,
    request_id: UUID,
    recovery_at: datetime,
) -> bool:
    """Persist a conservative deadline before the foreign side effect starts."""

    armed = await redis.eval(
        _ARM_FALLBACK_SCRIPT,
        2,
        _state_key(request_id),
        OTP_FALLBACK_DUE_KEY,
        _utc(recovery_at).isoformat(),
        _utc(recovery_at).timestamp(),
        str(request_id),
    )
    return int(armed or 0) == 1


_SCHEDULE_SCRIPT = """
if redis.call('hget', KEYS[1], 'status') ~= 'pending' then return 0 end
local telegram_status = redis.call('hget', KEYS[1], 'telegram_delivery_status')
if telegram_status ~= 'not_attempted' and telegram_status ~= 'pending' then return 0 end
redis.call('hset', KEYS[1],
    'telegram_delivery_status', 'accepted',
    'telegram_sent_at', ARGV[1],
    'sms_fallback_at', ARGV[2])
redis.call('zadd', KEYS[2], ARGV[3], ARGV[4])
return 1
"""


async def schedule_sms_fallback(
    redis,
    *,
    request_id: UUID,
    telegram_sent_at: datetime,
    fallback_at: datetime,
) -> bool:
    scheduled = await redis.eval(
        _SCHEDULE_SCRIPT,
        2,
        _state_key(request_id),
        OTP_FALLBACK_DUE_KEY,
        _utc(telegram_sent_at).isoformat(),
        _utc(fallback_at).isoformat(),
        _utc(fallback_at).timestamp(),
        str(request_id),
    )
    return int(scheduled or 0) == 1


@dataclass(frozen=True, slots=True)
class OTPDeliveryClaim:
    claim_id: UUID
    request_id: UUID
    mobile_number: str
    otp_code: str
    lease_until: datetime


_CLAIM_SCRIPT = """
local status = redis.call('hget', KEYS[1], 'status')
if not status then redis.call('zrem', KEYS[2], ARGV[1]); return {0, 'missing'} end
if status ~= 'pending' then redis.call('zrem', KEYS[2], ARGV[1]); return {0, status} end
local sms_status = redis.call('hget', KEYS[1], 'sms_delivery_status')
if sms_status == 'pending' then
    local lease_epoch = redis.call('hget', KEYS[1], 'sms_claim_lease_until_epoch')
    if lease_epoch and lease_epoch ~= '' and tonumber(ARGV[2]) < tonumber(lease_epoch) then
        return {0, 'claimed'}
    end
    local lease_score = redis.call('zscore', KEYS[2], ARGV[1])
    if lease_score and tonumber(ARGV[2]) < tonumber(lease_score) then
        return {0, 'claimed'}
    end
    local provider_started = redis.call('hget', KEYS[1], 'sms_provider_started_at')
    if provider_started and provider_started ~= '' then
        redis.call('hset', KEYS[1],
            'sms_delivery_status', 'ambiguous',
            'sms_sent_at', ARGV[5])
        redis.call('zrem', KEYS[2], ARGV[1])
        return {0, 'ambiguous'}
    end
elseif sms_status ~= 'not_attempted' then
    redis.call('zrem', KEYS[2], ARGV[1])
    return {0, sms_status}
end
local code = redis.call('get', KEYS[3])
if not code then
    redis.call('hset', KEYS[1], 'status', 'expired')
    redis.call('zrem', KEYS[2], ARGV[1])
    return {0, 'expired'}
end
local code_ttl = redis.call('ttl', KEYS[3])
if code_ttl >= 0 and code_ttl < tonumber(ARGV[8]) then
    redis.call('hset', KEYS[1], 'sms_delivery_status', 'cancelled')
    redis.call('zrem', KEYS[2], ARGV[1])
    return {0, 'insufficient_ttl'}
end
if ARGV[3] == '1' then
    local score = redis.call('zscore', KEYS[2], ARGV[1])
    if not score or tonumber(score) > tonumber(ARGV[2]) then return {0, 'not_due'} end
end
redis.call('hset', KEYS[1],
    'sms_delivery_status', 'pending',
    'sms_claim_id', ARGV[4],
    'sms_claimed_at', ARGV[5],
    'sms_claim_lease_until', ARGV[6],
    'sms_claim_lease_until_epoch', ARGV[7],
    'sms_provider_started_at', '')
redis.call('zadd', KEYS[2], ARGV[7], ARGV[1])
return {1, code}
"""


async def claim_sms_delivery(
    redis,
    *,
    state: OTPDeliveryStateContract,
    require_due: bool,
    now: datetime | None = None,
    lease_seconds: int = OTP_SMS_CLAIM_LEASE_SECONDS,
    claim_id: UUID | None = None,
) -> OTPDeliveryClaim | None:
    claimed_at = _utc(now or utc_now())
    lease_until = claimed_at + timedelta(seconds=max(1, int(lease_seconds)))
    selected_claim_id = claim_id or uuid4()
    mobile = mobile_for_delivery_state(state)
    result = await redis.eval(
        _CLAIM_SCRIPT,
        3,
        _state_key(state.otp_request_id),
        OTP_FALLBACK_DUE_KEY,
        f"otp:{mobile}",
        str(state.otp_request_id),
        claimed_at.timestamp(),
        "1" if require_due else "0",
        str(selected_claim_id),
        claimed_at.isoformat(),
        lease_until.isoformat(),
        lease_until.timestamp(),
        OTP_SMS_MINIMUM_SEND_TTL_SECONDS,
    )
    if not result or int(result[0] or 0) != 1:
        return None
    return OTPDeliveryClaim(
        claim_id=selected_claim_id,
        request_id=state.otp_request_id,
        otp_code=_text(result[1]) or "",
        mobile_number=mobile,
        lease_until=lease_until,
    )


_MARK_PROVIDER_STARTED_SCRIPT = """
if redis.call('hget', KEYS[1], 'status') ~= 'pending' then return 0 end
if redis.call('hget', KEYS[1], 'sms_delivery_status') ~= 'pending' then return 0 end
if redis.call('hget', KEYS[1], 'sms_claim_id') ~= ARGV[1] then return 0 end
redis.call('hset', KEYS[1], 'sms_provider_started_at', ARGV[2])
return 1
"""


async def mark_sms_provider_attempt_started(
    redis,
    *,
    claim: OTPDeliveryClaim,
    started_at: datetime | None = None,
) -> bool:
    marked = await redis.eval(
        _MARK_PROVIDER_STARTED_SCRIPT,
        1,
        _state_key(claim.request_id),
        str(claim.claim_id),
        _utc(started_at or utc_now()).isoformat(),
    )
    return int(marked or 0) == 1


_RECORD_SMS_SCRIPT = """
if redis.call('hget', KEYS[1], 'sms_delivery_status') ~= 'pending' then return 0 end
if redis.call('hget', KEYS[1], 'sms_claim_id') ~= ARGV[1] then return 0 end
redis.call('hset', KEYS[1], 'sms_delivery_status', ARGV[2], 'sms_sent_at', ARGV[3])
redis.call('zrem', KEYS[2], ARGV[4])
return 1
"""


async def record_sms_delivery_result(
    redis,
    *,
    claim: OTPDeliveryClaim,
    outcome: OTPDeliveryStatus,
    completed_at: datetime | None = None,
) -> bool:
    if outcome not in {
        OTPDeliveryStatus.ACCEPTED,
        OTPDeliveryStatus.FAILED,
        OTPDeliveryStatus.AMBIGUOUS,
    }:
        raise ValueError("invalid SMS terminal outcome")
    recorded = await redis.eval(
        _RECORD_SMS_SCRIPT,
        2,
        _state_key(claim.request_id),
        OTP_FALLBACK_DUE_KEY,
        str(claim.claim_id),
        outcome.value,
        _utc(completed_at or utc_now()).isoformat(),
        str(claim.request_id),
    )
    return int(recorded or 0) == 1


_ISOLATE_FALLBACK_STATE_SCRIPT = """
local status = redis.call('hget', KEYS[1], 'status')
if status == 'pending' then
    redis.call('hset', KEYS[1],
        'status', 'expired',
        'terminal_reason', ARGV[2])
end
redis.call('zrem', KEYS[2], ARGV[1])
return status or 'missing'
"""


async def isolate_invalid_otp_fallback_state(
    redis,
    *,
    request_id: UUID,
    reason: str,
) -> str:
    bounded_reason = (
        reason if reason in _SAFE_FALLBACK_ISOLATION_REASONS else "worker_exception"
    )
    result = await redis.eval(
        _ISOLATE_FALLBACK_STATE_SCRIPT,
        2,
        _state_key(request_id),
        OTP_FALLBACK_DUE_KEY,
        str(request_id),
        bounded_reason,
    )
    return _text(result) or "missing"


@dataclass(frozen=True, slots=True)
class OTPDueSelection:
    request_ids: tuple[UUID, ...]
    isolated_counts: dict[str, int]


async def select_due_otp_requests(
    redis,
    *,
    now: datetime,
    limit: int,
) -> OTPDueSelection:
    """Select valid due work while isolating corrupt state without exposing identity."""

    wanted = max(1, int(limit))
    scan_budget = max(_DUE_MIN_SCAN_BUDGET, wanted * _DUE_SCAN_MULTIPLIER)
    request_ids: list[UUID] = []
    isolated: Counter[str] = Counter()
    retained_offset = 0
    scanned = 0
    now_score = _utc(now).timestamp()

    while len(request_ids) < wanted and scanned < scan_budget:
        chunk_size = min(max(wanted, 50), scan_budget - scanned)
        raw_ids = await redis.zrangebyscore(
            OTP_FALLBACK_DUE_KEY,
            min="-inf",
            max=now_score,
            start=retained_offset,
            num=chunk_size,
        )
        if not raw_ids:
            break
        scanned += len(raw_ids)
        retained_this_round = 0
        for raw_id in raw_ids:
            text_id = _text(raw_id) or ""
            try:
                request_id = UUID(text_id)
            except ValueError:
                await redis.zrem(OTP_FALLBACK_DUE_KEY, raw_id)
                isolated["malformed_request_id"] += 1
                continue
            status = _text(await redis.hget(_state_key(request_id), "status"))
            if status != OTPRequestStatus.PENDING.value:
                await redis.zrem(OTP_FALLBACK_DUE_KEY, raw_id)
                isolated["missing_or_terminal"] += 1
                continue
            try:
                state = await load_otp_delivery_state(redis, request_id=request_id)
            except (KeyError, TypeError, ValueError, UnicodeError):
                await isolate_invalid_otp_fallback_state(
                    redis,
                    request_id=request_id,
                    reason="invalid_contract",
                )
                isolated["invalid_contract"] += 1
                continue
            if state is None:
                await redis.zrem(OTP_FALLBACK_DUE_KEY, raw_id)
                isolated["missing_or_terminal"] += 1
                continue
            try:
                mobile_for_delivery_state(state)
            except (RuntimeError, ValueError):
                await isolate_invalid_otp_fallback_state(
                    redis,
                    request_id=request_id,
                    reason="invalid_delivery_target",
                )
                isolated["invalid_delivery_target"] += 1
                continue
            request_ids.append(request_id)
            retained_this_round += 1
            if len(request_ids) >= wanted:
                break
        retained_offset += retained_this_round
        if len(raw_ids) < chunk_size:
            break
    return OTPDueSelection(
        request_ids=tuple(request_ids),
        isolated_counts=dict(isolated),
    )


async def due_otp_request_ids(redis, *, now: datetime, limit: int) -> list[UUID]:
    """Return valid due IDs while removing missing/terminal/malformed prefix poison."""
    selection = await select_due_otp_requests(redis, now=now, limit=limit)
    return list(selection.request_ids)


_CONSUME_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
local request_id = redis.call('get', KEYS[4])
redis.call('del', KEYS[1], KEYS[2], KEYS[3], KEYS[4])
if request_id then
    local state_key = ARGV[2] .. request_id
    if redis.call('hget', state_key, 'status') == 'pending' then
        redis.call('hset', state_key, 'status', 'consumed')
    end
    redis.call('zrem', KEYS[5], request_id)
end
return 1
"""


async def consume_otp_code(
    redis,
    *,
    mobile: str,
    expected_code: str,
) -> bool:
    normalized_mobile = normalize_registration_mobile_number(mobile)
    consumed = await redis.eval(
        _CONSUME_SCRIPT,
        5,
        f"otp:{normalized_mobile}",
        f"otp_limit:{normalized_mobile}",
        f"sms_limit:{normalized_mobile}",
        _mobile_request_key(normalized_mobile),
        OTP_FALLBACK_DUE_KEY,
        expected_code,
        "otp_delivery:request:",
    )
    return int(consumed or 0) == 1


async def cancel_otp_delivery(redis, *, mobile: str) -> None:
    normalized_mobile = normalize_registration_mobile_number(mobile)
    state = await load_otp_delivery_state(redis, mobile=normalized_mobile)
    await redis.delete(
        f"otp:{normalized_mobile}",
        f"otp_limit:{normalized_mobile}",
        _mobile_request_key(normalized_mobile),
    )
    if state is not None:
        await redis.hset(
            _state_key(state.otp_request_id),
            mapping={"status": OTPRequestStatus.EXPIRED.value},
        )
        await redis.zrem(OTP_FALLBACK_DUE_KEY, str(state.otp_request_id))
