"""PII-free health snapshots for dual-platform registration background jobs."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from core.background_job_authority import (
    JOB_OTP_SMS_FALLBACK,
    JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
)
from core.metrics import record_registration_job_health
from core.registration_feature_policy import registration_reconciliation_runtime_ready
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, normalize_server
from core.services.otp_delivery_state_service import OTP_FALLBACK_DUE_KEY
from core.services.telegram_registration_intent_service import TERMINAL_INTENT_STATUSES
from core.utils import utc_now
from models.telegram_registration_intent import (
    TelegramRegistrationIntent,
    TelegramRegistrationIntentStatus,
)


JOB_HEARTBEAT_MAX_AGE_SECONDS = 60
JOB_HEARTBEAT_MAX_FUTURE_SKEW_SECONDS = 5
REGISTRATION_PENDING_MAX_AGE_SECONDS = 300
OTP_FALLBACK_MAX_LAG_SECONDS = 2
JOB_HEALTH_TTL_SECONDS = 3600

_JOB_HEALTH_KEY_PREFIX = "observability:registration_job:"
_SAFE_CODE = re.compile(r"^[a-z0-9_.:-]{1,96}$")


@dataclass(frozen=True, slots=True)
class RegistrationIntentQueueSummary:
    pending_count: int
    oldest_pending_age_seconds: float


@dataclass(frozen=True, slots=True)
class OTPFallbackQueueSummary:
    pending_count: int
    oldest_pending_age_seconds: float
    lag_seconds: float


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(value: datetime | None, *, now: datetime) -> float:
    if value is None:
        return 0.0
    return max(0.0, (_utc(now) - _utc(value)).total_seconds())


def _safe_code(value: object | None, *, fallback: str = "none") -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _SAFE_CODE.fullmatch(candidate) else fallback


def _health_key(job_name: str) -> str:
    return f"{_JOB_HEALTH_KEY_PREFIX}{_safe_code(job_name, fallback='unknown')}"


def _decode_json(raw: object | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _nonnegative_int(value: object | None) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_float(value: object | None) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, number) if math.isfinite(number) else 0.0


def _bool_value(value: object | None, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return default


async def summarize_registration_intent_queue(
    db,
    *,
    now: datetime | None = None,
) -> RegistrationIntentQueueSummary:
    sampled_at = _utc(now or utc_now())
    pending_statuses = tuple(
        status for status in TelegramRegistrationIntentStatus if status not in TERMINAL_INTENT_STATUSES
    )
    result = await db.execute(
        select(
            func.count(TelegramRegistrationIntent.id),
            func.min(TelegramRegistrationIntent.created_at),
        ).where(TelegramRegistrationIntent.status.in_(pending_statuses))
    )
    pending_count, oldest_created_at = result.one()
    return RegistrationIntentQueueSummary(
        pending_count=max(0, int(pending_count or 0)),
        oldest_pending_age_seconds=_age_seconds(oldest_created_at, now=sampled_at),
    )


async def summarize_otp_fallback_queue(
    redis,
    *,
    now: datetime | None = None,
) -> OTPFallbackQueueSummary:
    sampled_at = _utc(now or utc_now())
    pending_count = max(0, int(await redis.zcard(OTP_FALLBACK_DUE_KEY) or 0))
    oldest_score: float | None = None
    if pending_count:
        entries = await redis.zrange(OTP_FALLBACK_DUE_KEY, 0, 0, withscores=True)
        if entries:
            oldest_score = float(entries[0][1])
    oldest_age = (
        max(0.0, sampled_at.timestamp() - oldest_score)
        if oldest_score is not None
        else 0.0
    )
    return OTPFallbackQueueSummary(
        pending_count=pending_count,
        oldest_pending_age_seconds=oldest_age,
        lag_seconds=oldest_age,
    )


async def load_registration_job_snapshot(redis, *, job_name: str) -> dict[str, Any] | None:
    try:
        payload = _decode_json(await redis.get(_health_key(job_name)))
        if payload is None or payload.get("job_name") != job_name:
            return None
        payload["server_mode"] = normalize_server(payload.get("server_mode"), default="unknown")
        for field in ("heartbeat_at", "last_success_at", "last_error_at"):
            parsed = _parse_timestamp(payload.get(field))
            payload[field] = parsed.isoformat() if parsed is not None else None
        last_result = payload.get("last_result")
        payload["last_result"] = (
            last_result
            if isinstance(last_result, str) and last_result in {"success", "error"}
            else None
        )
        payload["pending_count"] = _nonnegative_int(payload.get("pending_count"))
        payload["oldest_pending_age_seconds"] = _nonnegative_float(
            payload.get("oldest_pending_age_seconds")
        )
        payload["batch_size"] = _nonnegative_int(payload.get("batch_size"))
        payload["batch_duration_ms"] = _nonnegative_float(payload.get("batch_duration_ms"))
        payload["connectivity_healthy"] = _bool_value(
            payload.get("connectivity_healthy"),
            default=True,
        )
        payload["lag_seconds"] = _nonnegative_float(payload.get("lag_seconds"))
        if payload.get("last_error_code") is not None:
            payload["last_error_code"] = _safe_code(
                payload.get("last_error_code"),
                fallback="internal_error",
            )
        return payload
    except Exception:
        return None


async def refresh_registration_job_metrics(redis) -> None:
    """Hydrate process-local metrics from Redis for multi-worker scrapes."""

    for job_name in (
        JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
        JOB_OTP_SMS_FALLBACK,
    ):
        snapshot = await load_registration_job_snapshot(redis, job_name=job_name)
        if snapshot is not None:
            record_registration_job_health(snapshot, count_cycle=False)


async def record_registration_job_snapshot(
    redis,
    *,
    job_name: str,
    server_mode: str,
    result: str,
    pending_count: int,
    oldest_pending_age_seconds: float,
    batch_size: int,
    batch_duration_ms: float,
    connectivity_healthy: bool = True,
    lag_seconds: float = 0.0,
    error_code: str | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    sampled_at = _utc(observed_at or utc_now())
    previous = await load_registration_job_snapshot(redis, job_name=job_name) or {}
    normalized_result = "success" if result == "success" else "error"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "job_name": job_name,
        "server_mode": normalize_server(server_mode, default="unknown"),
        "heartbeat_at": sampled_at.isoformat(),
        "last_success_at": previous.get("last_success_at"),
        "last_error_at": previous.get("last_error_at"),
        "last_error_code": previous.get("last_error_code"),
        "last_result": normalized_result,
        "pending_count": max(0, int(pending_count)),
        "oldest_pending_age_seconds": _nonnegative_float(oldest_pending_age_seconds),
        "batch_size": max(0, int(batch_size)),
        "batch_duration_ms": _nonnegative_float(batch_duration_ms),
        "connectivity_healthy": bool(connectivity_healthy),
        "lag_seconds": _nonnegative_float(lag_seconds),
    }
    if normalized_result == "success":
        payload["last_success_at"] = sampled_at.isoformat()
    else:
        payload["last_error_at"] = sampled_at.isoformat()
        payload["last_error_code"] = _safe_code(error_code, fallback="internal_error")
    await redis.set(
        _health_key(job_name),
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ),
        ex=JOB_HEALTH_TTL_SECONDS,
    )
    record_registration_job_health(payload)
    return payload


def _parse_timestamp(value: object | None) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(str(value))) if value else None
    except (TypeError, ValueError):
        return None


def _render_job_health(
    snapshot: dict[str, Any] | None,
    *,
    job_name: str,
    expected_server: str,
    current_server: str,
    enabled: bool,
    observability_available: bool,
    now: datetime,
) -> dict[str, Any]:
    expected_here = current_server == expected_server
    heartbeat = _parse_timestamp((snapshot or {}).get("heartbeat_at"))
    last_success = _parse_timestamp((snapshot or {}).get("last_success_at"))
    last_error = _parse_timestamp((snapshot or {}).get("last_error_at"))
    heartbeat_age = _age_seconds(heartbeat, now=now) if heartbeat else None
    heartbeat_future_skew = (
        max(0.0, (_utc(heartbeat) - _utc(now)).total_seconds())
        if heartbeat
        else 0.0
    )
    if not enabled:
        status = "disabled"
    elif not expected_here:
        status = "not_expected"
    elif not observability_available:
        status = "unavailable"
    elif heartbeat_age is None:
        status = "missing"
    elif (
        heartbeat_age > JOB_HEARTBEAT_MAX_AGE_SECONDS
        or heartbeat_future_skew > JOB_HEARTBEAT_MAX_FUTURE_SKEW_SECONDS
    ):
        status = "stale"
    else:
        status = "healthy"
    return {
        "job_name": job_name,
        "enabled": bool(enabled),
        "expected_server": expected_server,
        "expected_on_this_server": expected_here,
        "observability_available": bool(observability_available),
        "status": status,
        "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
        "last_success_age_seconds": (
            round(_age_seconds(last_success, now=now), 3) if last_success else None
        ),
        "last_error_age_seconds": (
            round(_age_seconds(last_error, now=now), 3) if last_error else None
        ),
        "last_error_code": (snapshot or {}).get("last_error_code"),
        "last_result": (snapshot or {}).get("last_result"),
        "pending_count": _nonnegative_int((snapshot or {}).get("pending_count")),
        "oldest_pending_age_seconds": _nonnegative_float(
            (snapshot or {}).get("oldest_pending_age_seconds")
        ),
        "batch_size": _nonnegative_int((snapshot or {}).get("batch_size")),
        "batch_duration_ms": _nonnegative_float((snapshot or {}).get("batch_duration_ms")),
        "connectivity_healthy": (
            _bool_value((snapshot or {}).get("connectivity_healthy"), default=True)
            if observability_available
            else False
        ),
        "lag_seconds": _nonnegative_float((snapshot or {}).get("lag_seconds")),
    }


def _build_registration_health(
    *,
    settings_obj,
    registration_snapshot: dict[str, Any] | None,
    otp_snapshot: dict[str, Any] | None,
    observability_available: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    sampled_at = _utc(now or utc_now())
    current_server = normalize_server(getattr(settings_obj, "server_mode", None), default="unknown")
    registration_enabled = registration_reconciliation_runtime_ready(settings_obj)
    otp_enabled = bool(
        getattr(settings_obj, "telegram_login_otp_enabled", False)
        and getattr(settings_obj, "otp_sms_auto_fallback_enabled", False)
    )
    return {
        "status": "ok" if observability_available else "redis_unavailable",
        "thresholds": {
            "heartbeat_max_age_seconds": JOB_HEARTBEAT_MAX_AGE_SECONDS,
            "registration_pending_max_age_seconds": REGISTRATION_PENDING_MAX_AGE_SECONDS,
            "otp_fallback_max_lag_seconds": OTP_FALLBACK_MAX_LAG_SECONDS,
        },
        "jobs": {
            JOB_TELEGRAM_REGISTRATION_RECONCILIATION: _render_job_health(
                registration_snapshot,
                job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                expected_server=SERVER_FOREIGN,
                current_server=current_server,
                enabled=registration_enabled,
                observability_available=observability_available,
                now=sampled_at,
            ),
            JOB_OTP_SMS_FALLBACK: _render_job_health(
                otp_snapshot,
                job_name=JOB_OTP_SMS_FALLBACK,
                expected_server=SERVER_IRAN,
                current_server=current_server,
                enabled=otp_enabled,
                observability_available=observability_available,
                now=sampled_at,
            ),
        },
    }


async def dual_platform_registration_health(redis, *, settings_obj, now: datetime | None = None) -> dict[str, Any]:
    registration_snapshot = await load_registration_job_snapshot(
        redis, job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION
    )
    otp_snapshot = await load_registration_job_snapshot(redis, job_name=JOB_OTP_SMS_FALLBACK)
    return _build_registration_health(
        settings_obj=settings_obj,
        registration_snapshot=registration_snapshot,
        otp_snapshot=otp_snapshot,
        observability_available=True,
        now=now,
    )


def unavailable_registration_health(*, settings_obj, now: datetime | None = None) -> dict[str, Any]:
    return _build_registration_health(
        settings_obj=settings_obj,
        registration_snapshot=None,
        otp_snapshot=None,
        observability_available=False,
        now=now,
    )


def registration_health_log_fields(health: dict[str, Any]) -> dict[str, Any]:
    jobs = health.get("jobs") or {}
    registration = jobs.get(JOB_TELEGRAM_REGISTRATION_RECONCILIATION) or {}
    otp = jobs.get(JOB_OTP_SMS_FALLBACK) or {}
    registration_expected = bool(
        registration.get("enabled") and registration.get("expected_on_this_server")
    )
    otp_expected = bool(otp.get("enabled") and otp.get("expected_on_this_server"))
    registration_heartbeat_unhealthy = int(
        registration_expected
        and registration.get("status") in {"missing", "stale", "unavailable"}
    )
    otp_heartbeat_unhealthy = int(
        otp_expected and otp.get("status") in {"missing", "stale", "unavailable"}
    )
    registration_pending_healthy_age = (
        _nonnegative_float(registration.get("oldest_pending_age_seconds"))
        if registration_expected and registration.get("connectivity_healthy") is True
        else 0.0
    )
    return {
        "registration_observability_unavailable": int(
            health.get("status") == "redis_unavailable"
        ),
        "registration_job_enabled": registration.get("enabled"),
        "registration_job_status": registration.get("status"),
        "registration_job_heartbeat_age_seconds": registration.get("heartbeat_age_seconds"),
        "registration_job_pending_count": registration.get("pending_count"),
        "registration_job_oldest_pending_age_seconds": registration.get("oldest_pending_age_seconds"),
        "registration_job_connectivity_healthy": registration.get("connectivity_healthy"),
        "registration_job_heartbeat_unhealthy": registration_heartbeat_unhealthy,
        "registration_job_pending_healthy_age_seconds": registration_pending_healthy_age,
        # Avoid the reserved "otp" key fragment: centralized log redaction treats
        # every such field as secret-bearing, even when the value is only health data.
        "login_sms_fallback_job_enabled": otp.get("enabled"),
        "login_sms_fallback_job_status": otp.get("status"),
        "login_sms_fallback_job_heartbeat_age_seconds": otp.get("heartbeat_age_seconds"),
        "login_sms_fallback_job_pending_count": otp.get("pending_count"),
        "login_sms_fallback_job_lag_seconds": otp.get("lag_seconds"),
        "login_sms_fallback_job_heartbeat_unhealthy": otp_heartbeat_unhealthy,
    }
