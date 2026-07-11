"""Iran-local Redis state machine for one-code Telegram/SMS login OTP delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from core.registration_contracts import (
    OTPDeliveryStateContract,
    OTPDeliveryStatus,
    OTPRequestStatus,
)
from core.utils import utc_now


OTP_FALLBACK_DUE_KEY = "otp_delivery:sms_fallback_due"


def _state_key(request_id: UUID | str) -> str:
    return f"otp_delivery:request:{request_id}"


def _mobile_request_key(mobile: str) -> str:
    return f"otp_delivery:mobile:{mobile}"


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
    telegram_id: int | None,
    ttl_seconds: int,
    now: datetime | None = None,
) -> OTPDeliveryStateContract:
    created_at = _utc(now or utc_now())
    return OTPDeliveryStateContract(
        otp_request_id=uuid4(),
        mobile_number=mobile,
        code_key=f"otp:{mobile}",
        telegram_id=telegram_id,
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
    'mobile_number', ARGV[4],
    'code_key', KEYS[1],
    'telegram_id', ARGV[5],
    'status', 'pending',
    'created_at', ARGV[6],
    'expires_at', ARGV[7],
    'telegram_delivery_status', 'not_attempted',
    'telegram_sent_at', '',
    'sms_fallback_at', '',
    'sms_delivery_status', 'not_attempted',
    'sms_sent_at', '')
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
    created = await redis.eval(
        _CREATE_SCRIPT,
        4,
        state.code_key,
        f"otp_limit:{state.mobile_number}",
        _state_key(state.otp_request_id),
        _mobile_request_key(state.mobile_number),
        otp_code,
        max(1, int(ttl_seconds)),
        str(state.otp_request_id),
        state.mobile_number,
        str(state.telegram_id or ""),
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
        request_id = _text(await redis.get(_mobile_request_key(mobile)))
        if not request_id:
            return None
    raw = await redis.hgetall(_state_key(request_id))
    if not raw:
        return None
    values = {_text(key): _text(value) for key, value in raw.items()}
    telegram_id = (values.get("telegram_id") or "").strip()
    return OTPDeliveryStateContract(
        otp_request_id=values["otp_request_id"],
        purpose=values.get("purpose") or "web_login",
        mobile_number=values["mobile_number"],
        code_key=values["code_key"],
        telegram_id=int(telegram_id) if telegram_id else None,
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
    request_id: UUID
    mobile_number: str
    otp_code: str


_CLAIM_SCRIPT = """
local status = redis.call('hget', KEYS[1], 'status')
if not status then redis.call('zrem', KEYS[2], ARGV[1]); return {0, 'missing'} end
if status ~= 'pending' then redis.call('zrem', KEYS[2], ARGV[1]); return {0, status} end
local code = redis.call('get', KEYS[3])
if not code then
    redis.call('hset', KEYS[1], 'status', 'expired')
    redis.call('zrem', KEYS[2], ARGV[1])
    return {0, 'expired'}
end
local sms_status = redis.call('hget', KEYS[1], 'sms_delivery_status')
if sms_status ~= 'not_attempted' then
    redis.call('zrem', KEYS[2], ARGV[1])
    return {0, sms_status}
end
if ARGV[3] == '1' then
    local score = redis.call('zscore', KEYS[2], ARGV[1])
    if not score or tonumber(score) > tonumber(ARGV[2]) then return {0, 'not_due'} end
end
redis.call('hset', KEYS[1], 'sms_delivery_status', 'pending')
redis.call('zrem', KEYS[2], ARGV[1])
return {1, code, redis.call('hget', KEYS[1], 'mobile_number')}
"""


async def claim_sms_delivery(
    redis,
    *,
    state: OTPDeliveryStateContract,
    require_due: bool,
    now: datetime | None = None,
) -> OTPDeliveryClaim | None:
    result = await redis.eval(
        _CLAIM_SCRIPT,
        3,
        _state_key(state.otp_request_id),
        OTP_FALLBACK_DUE_KEY,
        state.code_key,
        str(state.otp_request_id),
        _utc(now or utc_now()).timestamp(),
        "1" if require_due else "0",
    )
    if not result or int(result[0] or 0) != 1:
        return None
    return OTPDeliveryClaim(
        request_id=state.otp_request_id,
        otp_code=_text(result[1]) or "",
        mobile_number=_text(result[2]) or state.mobile_number,
    )


_RECORD_SMS_SCRIPT = """
if redis.call('hget', KEYS[1], 'sms_delivery_status') ~= 'pending' then return 0 end
redis.call('hset', KEYS[1], 'sms_delivery_status', ARGV[1], 'sms_sent_at', ARGV[2])
return 1
"""


async def record_sms_delivery_result(
    redis,
    *,
    request_id: UUID,
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
        1,
        _state_key(request_id),
        outcome.value,
        _utc(completed_at or utc_now()).isoformat(),
    )
    return int(recorded or 0) == 1


async def due_otp_request_ids(redis, *, now: datetime, limit: int) -> list[UUID]:
    raw_ids = await redis.zrangebyscore(
        OTP_FALLBACK_DUE_KEY,
        min="-inf",
        max=_utc(now).timestamp(),
        start=0,
        num=max(1, int(limit)),
    )
    request_ids: list[UUID] = []
    for raw_id in raw_ids:
        try:
            request_ids.append(UUID(_text(raw_id) or ""))
        except ValueError:
            await redis.zrem(OTP_FALLBACK_DUE_KEY, raw_id)
    return request_ids


_CONSUME_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
local request_id = redis.call('get', KEYS[4])
redis.call('del', KEYS[1], KEYS[2], KEYS[3])
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
    consumed = await redis.eval(
        _CONSUME_SCRIPT,
        5,
        f"otp:{mobile}",
        f"otp_limit:{mobile}",
        f"sms_limit:{mobile}",
        _mobile_request_key(mobile),
        OTP_FALLBACK_DUE_KEY,
        expected_code,
        "otp_delivery:request:",
    )
    return int(consumed or 0) == 1


async def cancel_otp_delivery(redis, *, mobile: str) -> None:
    state = await load_otp_delivery_state(redis, mobile=mobile)
    await redis.delete(f"otp:{mobile}", f"otp_limit:{mobile}")
    if state is not None:
        await redis.hset(
            _state_key(state.otp_request_id),
            mapping={"status": OTPRequestStatus.EXPIRED.value},
        )
        await redis.zrem(OTP_FALLBACK_DUE_KEY, str(state.otp_request_id))
