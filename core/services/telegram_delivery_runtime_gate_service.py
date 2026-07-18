"""Durable bot/gateway gates and crash-safe operational resume."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightRateLimitedError,
    TelegramDeliveryPreflightReport,
    run_configured_telegram_delivery_preflight,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryState
from core.telegram_delivery_queue_limiter import TelegramDeliveryDispatchLimiter
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate

from .telegram_delivery_queue_service import (
    SUPPORTED_TELEGRAM_BOT_IDENTITIES,
    acquire_telegram_delivery_scope_locks,
)
from .telegram_delivery_resume_service import _validate_full_preflight_report


TELEGRAM_RUNTIME_GATE_ACTIVE = "active"
TELEGRAM_RUNTIME_GATE_COOLDOWN = "cooldown"
TELEGRAM_RUNTIME_GATE_BLOCKED = "blocked"
TELEGRAM_RUNTIME_GATE_RESUME_REQUESTED = "resume_requested"
TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED = "database_applied"


class TelegramRuntimeGateError(RuntimeError):
    pass


class TelegramRuntimeGateValidationError(TelegramRuntimeGateError):
    pass


class TelegramRuntimeGateConflictError(TelegramRuntimeGateError):
    pass


class TelegramRuntimeGateResumeIncompleteError(TelegramRuntimeGateError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramRuntimeGateEvidence:
    gate_key: str
    scope: str
    bot_identity: str | None
    state: str
    cooldown_until: datetime | None
    reason_code: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramRuntimeGateResumeReport:
    gate_key: str
    scope: str
    request_id: str
    state: str
    resumed_job_ids: tuple[int, ...]
    idempotent_replay: bool


def telegram_runtime_gate_key(scope: str, bot_identity: str | None = None) -> str:
    normalized_scope = str(scope or "").strip().lower()
    if normalized_scope == "bot":
        identity = str(bot_identity or "").strip()
        if identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
            raise TelegramRuntimeGateValidationError("telegram_runtime_gate_bot_invalid")
        return f"bot:{identity}"
    if normalized_scope == "gateway" and bot_identity is None:
        return "gateway:telegram"
    raise TelegramRuntimeGateValidationError("telegram_runtime_gate_scope_invalid")


def _require_foreign(current_server: str) -> None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramRuntimeGateValidationError("telegram_runtime_gate_foreign_only")


def _safe_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _normalize_request_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not 16 <= len(normalized) <= 128 or any(char.isspace() for char in normalized):
        raise TelegramRuntimeGateValidationError("telegram_runtime_gate_request_id_invalid")
    return normalized


def _safe_error(exc: BaseException) -> tuple[str, str]:
    error_class = type(exc).__name__[:160]
    raw = " ".join(str(exc).split())
    detail = raw[:500] if raw.startswith("telegram_") else error_class
    return error_class, detail


def _preflight_evidence(report: TelegramDeliveryPreflightReport) -> dict[str, Any]:
    return {
        "approved_bot_identities": list(report.approved_bot_identities),
        "channel_fingerprint": report.channel_fingerprint,
        "identities": [
            {
                "bot_identity": identity.bot_identity,
                "credential_fingerprint": identity.credential_fingerprint,
                "bot_fingerprint": identity.bot_fingerprint,
                "channel_fingerprint": identity.channel_fingerprint,
                "member_status": identity.member_status,
                "effective_permissions": list(identity.effective_permissions),
            }
            for identity in report.identities
        ],
    }


def _append_history(
    gate: TelegramDeliveryRuntimeGate,
    *,
    phase: str,
    result: str,
    now: datetime,
) -> None:
    history = list(gate.attempt_history or ())
    gate.attempt_history = [
        *history,
        {
            "attempt": int(gate.attempt_count or 0),
            "phase": phase,
            "result": result,
            "at": now.isoformat(),
        },
    ][-100:]


def _stored_resumed_job_ids(gate: TelegramDeliveryRuntimeGate) -> tuple[int, ...]:
    values = gate.resumed_job_ids or ()
    if not isinstance(values, (list, tuple)):
        raise TelegramRuntimeGateValidationError(
            "telegram_runtime_gate_resumed_job_ids_invalid"
        )
    try:
        normalized = tuple(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise TelegramRuntimeGateValidationError(
            "telegram_runtime_gate_resumed_job_ids_invalid"
        ) from exc
    if any(value <= 0 for value in normalized) or len(set(normalized)) != len(normalized):
        raise TelegramRuntimeGateValidationError(
            "telegram_runtime_gate_resumed_job_ids_invalid"
        )
    return normalized


async def _load_or_create_gate(
    db: AsyncSession,
    *,
    scope: str,
    bot_identity: str | None,
    now: datetime,
) -> TelegramDeliveryRuntimeGate:
    gate_key = telegram_runtime_gate_key(scope, bot_identity)
    gate = (
        await db.execute(
            select(TelegramDeliveryRuntimeGate)
            .where(TelegramDeliveryRuntimeGate.gate_key == gate_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if gate is None:
        gate = TelegramDeliveryRuntimeGate(
            gate_key=gate_key,
            scope=scope,
            bot_identity=bot_identity,
            state=TELEGRAM_RUNTIME_GATE_ACTIVE,
            version=0,
            attempt_count=0,
            attempt_history=[],
            resumed_job_ids=[],
            created_at=now,
            updated_at=now,
        )
        db.add(gate)
        await db.flush()
    return gate


async def record_telegram_preflight_rate_limit(
    db: AsyncSession,
    *,
    current_server: str,
    bot_identity: str,
    retry_after_seconds: float,
    safety_seconds: float,
    now: datetime | None = None,
) -> TelegramDeliveryRuntimeGate:
    _require_foreign(current_server)
    retry_after = float(retry_after_seconds)
    safety = float(safety_seconds)
    if (
        not math.isfinite(retry_after)
        or retry_after <= 0
        or not math.isfinite(safety)
        or safety < 0
    ):
        raise TelegramRuntimeGateValidationError(
            "telegram_preflight_retry_after_invalid"
        )
    current_time = now or utc_now()
    await acquire_telegram_delivery_scope_locks(
        db,
        bot_identities=(bot_identity,),
    )
    gate = await _load_or_create_gate(
        db,
        scope="bot",
        bot_identity=bot_identity,
        now=current_time,
    )
    cooldown_until = current_time + timedelta(seconds=retry_after + safety)
    if gate.cooldown_until is None or gate.cooldown_until < cooldown_until:
        gate.cooldown_until = cooldown_until
    if gate.state in {TELEGRAM_RUNTIME_GATE_ACTIVE, TELEGRAM_RUNTIME_GATE_COOLDOWN}:
        gate.state = TELEGRAM_RUNTIME_GATE_COOLDOWN
    gate.reason_code = "telegram_preflight_rate_limited"
    gate.provider_status_code = 429
    gate.retry_after_seconds = max(1, int(math.ceil(retry_after)))
    gate.evidence_hash = _safe_hash(
        json.dumps(
            {
                "gate_key": gate.gate_key,
                "status": 429,
                "retry_after": gate.retry_after_seconds,
                "cooldown_until": gate.cooldown_until.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    gate.version = int(gate.version or 0) + 1
    gate.updated_at = current_time
    _append_history(gate, phase="preflight", result="rate_limited", now=current_time)
    await db.flush()
    return gate


async def mark_telegram_preflight_gate_active(
    db: AsyncSession,
    *,
    current_server: str,
    bot_identity: str,
    report: TelegramDeliveryPreflightReport,
    now: datetime | None = None,
) -> bool:
    _require_foreign(current_server)
    current_time = now or utc_now()
    await acquire_telegram_delivery_scope_locks(
        db,
        bot_identities=(bot_identity,),
    )
    gate_key = telegram_runtime_gate_key("bot", bot_identity)
    gate = (
        await db.execute(
            select(TelegramDeliveryRuntimeGate)
            .where(TelegramDeliveryRuntimeGate.gate_key == gate_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if gate is None:
        return False
    if gate.state != TELEGRAM_RUNTIME_GATE_COOLDOWN:
        return False
    gate.state = TELEGRAM_RUNTIME_GATE_ACTIVE
    gate.cooldown_until = None
    gate.reason_code = "telegram_preflight_succeeded"
    gate.preflight_evidence = _preflight_evidence(report)
    gate.provider_status_code = None
    gate.retry_after_seconds = None
    gate.version = int(gate.version or 0) + 1
    gate.completed_at = current_time
    gate.updated_at = current_time
    _append_history(gate, phase="preflight", result="succeeded", now=current_time)
    await db.flush()
    return True


async def load_active_telegram_runtime_gates(
    db: AsyncSession,
    *,
    current_server: str,
    now: datetime | None = None,
) -> tuple[TelegramRuntimeGateEvidence, ...]:
    _require_foreign(current_server)
    current_time = now or utc_now()
    rows = (
        await db.execute(
            select(TelegramDeliveryRuntimeGate)
            .where(
                or_(
                    TelegramDeliveryRuntimeGate.state.in_(
                        {
                            TELEGRAM_RUNTIME_GATE_BLOCKED,
                            TELEGRAM_RUNTIME_GATE_RESUME_REQUESTED,
                            TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED,
                        }
                    ),
                    (
                        TelegramDeliveryRuntimeGate.state
                        == TELEGRAM_RUNTIME_GATE_COOLDOWN
                    )
                    & (TelegramDeliveryRuntimeGate.cooldown_until > current_time),
                )
            )
            .order_by(TelegramDeliveryRuntimeGate.gate_key)
        )
    ).scalars()
    return tuple(
        TelegramRuntimeGateEvidence(
            gate_key=str(row.gate_key),
            scope=str(row.scope),
            bot_identity=row.bot_identity,
            state=str(row.state),
            cooldown_until=row.cooldown_until,
            reason_code=row.reason_code,
            updated_at=row.updated_at or current_time,
        )
        for row in rows
    )


async def _blocked_jobs(
    db: AsyncSession,
    *,
    scope: str,
    bot_identity: str | None,
) -> tuple[TelegramDeliveryJobRecord, ...]:
    condition = (
        (
            TelegramDeliveryJobRecord.state == TelegramDeliveryState.BLOCKED_BOT
        )
        & (TelegramDeliveryJobRecord.bot_identity == bot_identity)
        if scope == "bot"
        else TelegramDeliveryJobRecord.state == TelegramDeliveryState.BLOCKED_GATEWAY
    )
    return tuple(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(condition)
                .order_by(TelegramDeliveryJobRecord.id)
                .with_for_update()
            )
        ).scalars()
    )


async def resume_telegram_runtime_gate(
    *,
    session_factory: Callable[[], Any],
    current_server: str,
    settings: Any,
    credential_registry: TelegramDeliveryCredentialRegistry,
    dispatch_limiter: TelegramDeliveryDispatchLimiter,
    scope: str,
    bot_identity: str | None,
    request_id: str,
    requested_by: str,
    preflight_runner: Callable[..., Any] = run_configured_telegram_delivery_preflight,
    now_factory: Callable[[], datetime] = utc_now,
) -> TelegramRuntimeGateResumeReport:
    _require_foreign(current_server)
    normalized_scope = str(scope or "").strip().lower()
    gate_key = telegram_runtime_gate_key(normalized_scope, bot_identity)
    normalized_request = _normalize_request_id(request_id)
    actor = str(requested_by or "").strip()
    if not actor:
        raise TelegramRuntimeGateValidationError("telegram_runtime_gate_actor_required")
    now = now_factory()
    if now.tzinfo is None or now.utcoffset() is None:
        raise TelegramRuntimeGateValidationError("telegram_runtime_gate_time_invalid")
    selected_identities = (
        (str(bot_identity),)
        if normalized_scope == "bot"
        else credential_registry.bot_identities
    )
    if any(identity not in credential_registry.bot_identities for identity in selected_identities):
        raise TelegramRuntimeGateValidationError(
            "telegram_runtime_gate_credential_identity_missing"
        )

    idempotent_replay = False
    database_already_applied = False
    async with session_factory() as db:
        await acquire_telegram_delivery_scope_locks(
            db,
            bot_identities=selected_identities,
        )
        gate = await _load_or_create_gate(
            db,
            scope=normalized_scope,
            bot_identity=bot_identity,
            now=now,
        )
        blockers = await _blocked_jobs(
            db,
            scope=normalized_scope,
            bot_identity=bot_identity,
        )
        if gate.state == TELEGRAM_RUNTIME_GATE_ACTIVE and gate.request_id == normalized_request:
            report = TelegramRuntimeGateResumeReport(
                gate_key=gate_key,
                scope=normalized_scope,
                request_id=normalized_request,
                state=gate.state,
                resumed_job_ids=_stored_resumed_job_ids(gate),
                idempotent_replay=True,
            )
            await db.rollback()
            return report
        if gate.state == TELEGRAM_RUNTIME_GATE_ACTIVE and not blockers:
            await db.rollback()
            raise TelegramRuntimeGateValidationError("telegram_runtime_gate_not_paused")
        if gate.state in {
            TELEGRAM_RUNTIME_GATE_RESUME_REQUESTED,
            TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED,
        } and gate.request_id != normalized_request:
            await db.rollback()
            raise TelegramRuntimeGateConflictError("telegram_runtime_gate_resume_conflict")
        if (
            gate.cooldown_until is not None
            and gate.cooldown_until > now
            and gate.request_id == normalized_request
        ):
            await db.rollback()
            raise TelegramRuntimeGateResumeIncompleteError(
                "telegram_runtime_gate_preflight_cooldown_active"
            )
        idempotent_replay = gate.request_id == normalized_request
        database_already_applied = (
            gate.state == TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED
            and gate.request_id == normalized_request
        )
        if not database_already_applied:
            gate.state = TELEGRAM_RUNTIME_GATE_RESUME_REQUESTED
        gate.request_id = normalized_request
        gate.requested_by_hash = _safe_hash(actor)
        gate.attempt_count = int(gate.attempt_count or 0) + 1
        gate.resume_requested_at = now
        gate.last_error_class = None
        gate.last_error_detail = None
        gate.version = int(gate.version or 0) + 1
        gate.updated_at = now
        _append_history(
            gate,
            phase="prepare",
            result=("database_already_applied" if database_already_applied else "succeeded"),
            now=now,
        )
        await db.commit()

    try:
        preflight = await preflight_runner(
            settings=settings,
            credential_registry=credential_registry,
            bot_identities=selected_identities,
            identity_only_bot_identities=(),
        )
        _validate_full_preflight_report(
            preflight,
            bot_identities=selected_identities,
            credential_registry=credential_registry,
        )
    except Exception as exc:
        failed_at = now_factory()
        async with session_factory() as db:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=selected_identities,
            )
            gate = await _load_or_create_gate(
                db,
                scope=normalized_scope,
                bot_identity=bot_identity,
                now=failed_at,
            )
            if gate.request_id == normalized_request:
                error_class, detail = _safe_error(exc)
                gate.last_error_class = error_class
                gate.last_error_detail = detail
                if isinstance(exc, TelegramDeliveryPreflightRateLimitedError):
                    retry_after = float(exc.retry_after_seconds)
                    safety = float(
                        getattr(
                            settings,
                            "telegram_delivery_queue_retry_after_safety_seconds",
                            0.1,
                        )
                    )
                    gate.cooldown_until = failed_at + timedelta(
                        seconds=retry_after + safety
                    )
                    gate.reason_code = "telegram_preflight_rate_limited"
                    gate.provider_status_code = 429
                    gate.retry_after_seconds = max(1, int(math.ceil(retry_after)))
                    gate.evidence_hash = _safe_hash(
                        json.dumps(
                            {
                                "gate_key": gate.gate_key,
                                "request_id": normalized_request,
                                "status": 429,
                                "retry_after": gate.retry_after_seconds,
                                "cooldown_until": gate.cooldown_until.isoformat(),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                gate.updated_at = failed_at
                _append_history(gate, phase="preflight", result="failed", now=failed_at)
                await db.commit()
        raise

    applied_at = now_factory()
    async with session_factory() as db:
        await acquire_telegram_delivery_scope_locks(
            db,
            bot_identities=selected_identities,
        )
        gate = (
            await db.execute(
                select(TelegramDeliveryRuntimeGate)
                .where(TelegramDeliveryRuntimeGate.gate_key == gate_key)
                .with_for_update()
            )
        ).scalar_one()
        if gate.request_id != normalized_request:
            await db.rollback()
            raise TelegramRuntimeGateConflictError("telegram_runtime_gate_resume_fence_lost")
        if gate.state == TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED:
            resumed_ids = _stored_resumed_job_ids(gate)
        else:
            blockers = await _blocked_jobs(
                db,
                scope=normalized_scope,
                bot_identity=bot_identity,
            )
            resumed_ids = tuple(int(row.id) for row in blockers)
            for row in blockers:
                row.state = TelegramDeliveryState.PENDING_RETRY
                row.next_retry_at = applied_at
                row.worker_id = None
                row.lease_until = None
                row.dispatch_started_at = None
                row.rate_limit_probe = False
                row.bot_cooldown_until = None
                row.outcome_reason = f"operator_resume_{normalized_scope}"
                row.updated_at = applied_at
            gate.state = TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED
            gate.resumed_job_ids = list(resumed_ids)
        gate.preflight_evidence = _preflight_evidence(preflight)
        gate.database_applied_at = applied_at
        gate.cooldown_until = None
        gate.provider_status_code = None
        gate.retry_after_seconds = None
        gate.last_error_class = None
        gate.last_error_detail = None
        gate.version = int(gate.version or 0) + 1
        gate.updated_at = applied_at
        _append_history(gate, phase="database", result="succeeded", now=applied_at)
        await db.commit()

    try:
        if normalized_scope == "bot":
            await dispatch_limiter.resume_bot(str(bot_identity))
        else:
            await dispatch_limiter.resume_gateway()
    except Exception as exc:
        failed_at = now_factory()
        async with session_factory() as db:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=selected_identities,
            )
            gate = (
                await db.execute(
                    select(TelegramDeliveryRuntimeGate)
                    .where(TelegramDeliveryRuntimeGate.gate_key == gate_key)
                    .with_for_update()
                )
            ).scalar_one()
            if gate.request_id == normalized_request:
                gate.last_error_class, gate.last_error_detail = _safe_error(exc)
                gate.updated_at = failed_at
                _append_history(gate, phase="redis", result="failed", now=failed_at)
                await db.commit()
        raise TelegramRuntimeGateResumeIncompleteError(
            "telegram_runtime_gate_redis_not_cleared"
        ) from exc

    completed_at = now_factory()
    async with session_factory() as db:
        await acquire_telegram_delivery_scope_locks(
            db,
            bot_identities=selected_identities,
        )
        gate = (
            await db.execute(
                select(TelegramDeliveryRuntimeGate)
                .where(TelegramDeliveryRuntimeGate.gate_key == gate_key)
                .with_for_update()
            )
        ).scalar_one()
        blockers = await _blocked_jobs(
            db,
            scope=normalized_scope,
            bot_identity=bot_identity,
        )
        if gate.request_id != normalized_request or blockers:
            await db.rollback()
            raise TelegramRuntimeGateResumeIncompleteError(
                "telegram_runtime_gate_new_blocker_after_redis"
            )
        gate.state = TELEGRAM_RUNTIME_GATE_ACTIVE
        gate.redis_applied_at = completed_at
        gate.completed_at = completed_at
        gate.reason_code = f"operator_resume_{normalized_scope}_completed"
        gate.version = int(gate.version or 0) + 1
        gate.updated_at = completed_at
        _append_history(gate, phase="completed", result="succeeded", now=completed_at)
        await db.commit()
    return TelegramRuntimeGateResumeReport(
        gate_key=gate_key,
        scope=normalized_scope,
        request_id=normalized_request,
        state=TELEGRAM_RUNTIME_GATE_ACTIVE,
        resumed_job_ids=resumed_ids,
        idempotent_replay=idempotent_replay,
    )
