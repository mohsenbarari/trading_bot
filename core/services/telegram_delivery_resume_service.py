"""Fail-closed operational resume for the configured Telegram channel.

Strict distributed atomicity across PostgreSQL, Telegram HTTP, and Redis is
not possible.  This service implements a durable saga instead: every
unfinished operation is itself a PostgreSQL activation blocker, so a crash at
any phase remains safe and an idempotent retry can continue after a fresh full
preflight.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import logging
import math
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.telegram_delivery_queue_service import (
    TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY,
    TELEGRAM_PRIMARY_BOT_IDENTITY,
    acquire_telegram_delivery_scope_locks,
)
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
from models.telegram_delivery_resume_operation import (
    ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES,
    TelegramDeliveryResumeOperation,
)


logger = logging.getLogger(__name__)

TELEGRAM_RESUME_REQUESTED = "requested"
TELEGRAM_RESUME_DATABASE_APPLIED = "database_applied"
TELEGRAM_RESUME_REDIS_APPLIED = "redis_applied"
TELEGRAM_RESUME_COMPLETED = "completed"
TELEGRAM_RESUME_FAILED = "failed"

_UNRESOLVED_DESTINATION_STATES = (
    TelegramDeliveryState.AMBIGUOUS,
    TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
    TelegramDeliveryState.PENDING_RECONCILE,
)


class TelegramDeliveryResumeError(RuntimeError):
    """Base class for a refused or incomplete operational resume."""


class TelegramDeliveryResumeValidationError(TelegramDeliveryResumeError):
    """Raised before network access for malformed or unsafe input."""


class TelegramDeliveryResumeConflictError(TelegramDeliveryResumeError):
    """Raised when another request already owns this destination transition."""


class TelegramDeliveryResumeEvidenceChangedError(TelegramDeliveryResumeError):
    """Requires another full preflight after durable pause evidence changes."""


class TelegramDeliveryResumeIncompleteError(TelegramDeliveryResumeError):
    """The operation remains a durable activation blocker and may be retried."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryResumeReport:
    operation_id: int
    request_id: str
    destination_key: str
    state: str
    resumed_job_ids: tuple[int, ...]
    attempt_count: int
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class _PreparedResume:
    operation_id: int
    state: str
    destination_key: str
    bot_identities: tuple[str, ...]
    round_pause_job_ids: tuple[int, ...]
    round_pause_evidence_hash: str
    idempotent_replay: bool


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _normalize_request_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not 16 <= len(normalized) <= 128 or any(char.isspace() for char in normalized):
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_request_id_invalid"
        )
    return normalized


def _normalize_requested_by(value: str) -> str:
    normalized = str(value or "").strip()
    if not 1 <= len(normalized) <= 128 or any(ord(char) < 32 for char in normalized):
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_requested_by_invalid"
        )
    return normalized


def _configured_scope(settings: Any) -> tuple[str, tuple[str, ...]]:
    try:
        channel_id = int(getattr(settings, "channel_id", None))
        expected_channel_id = int(
            getattr(settings, "telegram_delivery_queue_expected_channel_id", None)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_channel_configuration_invalid"
        ) from exc
    if channel_id == 0 or channel_id != expected_channel_id:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_channel_configuration_mismatch"
        )
    identities = [TELEGRAM_PRIMARY_BOT_IDENTITY]
    if bool(getattr(settings, "telegram_delivery_queue_channel_editor_enabled", False)):
        identities.append(TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY)
    return f"channel:{channel_id}", tuple(identities)


def _aware_now(now_factory: Callable[[], datetime]) -> datetime:
    value = now_factory()
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_timestamp_must_be_timezone_aware"
        )
    return value


def _pause_evidence_payload(
    records: tuple[TelegramDeliveryJobRecord, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "id": int(record.id),
            "bot_identity": str(record.bot_identity),
            "destination_key": str(record.destination_key),
            "state": _enum_value(record.state),
            "outcome_reason": str(record.outcome_reason or ""),
            "provider_status_code": record.provider_status_code,
            "provider_error_code": record.provider_error_code,
            "updated_at": (
                record.updated_at.isoformat() if record.updated_at is not None else None
            ),
        }
        for record in records
    ]


def _pause_evidence_hash(records: tuple[TelegramDeliveryJobRecord, ...]) -> str:
    payload = _pause_evidence_payload(records)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chain_evidence_hash(previous: str, current: str) -> str:
    return hashlib.sha256(f"{previous}:{current}".encode("ascii")).hexdigest()


def _safe_failure_detail(exc: BaseException) -> str:
    raw = str(exc or "").strip()
    if not raw.startswith("telegram_"):
        return type(exc).__name__[:160]
    return "".join(
        char if char.isalnum() or char in "._:-" else "_" for char in raw
    )[:500]


def _append_attempt_history(
    operation: TelegramDeliveryResumeOperation,
    *,
    phase: str,
    result: str,
    at: datetime,
    extra: dict[str, Any] | None = None,
) -> None:
    history = list(operation.attempt_history or ())
    event: dict[str, Any] = {
        "attempt": int(operation.attempt_count or 0),
        "phase": str(phase),
        "result": str(result),
        "at": at.isoformat(),
    }
    if extra:
        event.update(extra)
    operation.attempt_history = [*history, event]


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


def _validate_full_preflight_report(
    report: TelegramDeliveryPreflightReport,
    *,
    bot_identities: tuple[str, ...],
    credential_registry: TelegramDeliveryCredentialRegistry,
) -> None:
    try:
        approved = tuple(report.approved_bot_identities)
        identities = tuple(report.identities)
    except Exception as exc:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_preflight_report_invalid"
        ) from exc
    if approved != bot_identities or tuple(
        identity.bot_identity for identity in identities
    ) != bot_identities:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_preflight_roles_mismatch"
        )
    expected_credentials = credential_registry.fingerprints()
    channel_fingerprint = str(report.channel_fingerprint or "")
    if not channel_fingerprint:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_preflight_channel_fingerprint_missing"
        )
    for identity in identities:
        permissions = frozenset(identity.effective_permissions)
        if (
            identity.member_status != "administrator"
            or not str(identity.bot_fingerprint or "")
            or identity.channel_fingerprint != channel_fingerprint
            or identity.credential_fingerprint
            != expected_credentials.get(identity.bot_identity)
        ):
            raise TelegramDeliveryResumeValidationError(
                "telegram_resume_requires_full_channel_preflight"
            )
        if identity.bot_identity == TELEGRAM_PRIMARY_BOT_IDENTITY:
            if not {
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
            }.issubset(permissions):
                raise TelegramDeliveryResumeValidationError(
                    "telegram_resume_primary_permissions_missing"
                )
        elif permissions != {"can_manage_chat", "can_edit_messages"}:
            raise TelegramDeliveryResumeValidationError(
                "telegram_resume_editor_permissions_invalid"
            )


async def _blocked_destination_rows(
    db: AsyncSession,
    *,
    destination_key: str,
) -> tuple[TelegramDeliveryJobRecord, ...]:
    rows = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(
                TelegramDeliveryJobRecord.destination_key == destination_key,
                TelegramDeliveryJobRecord.state
                == TelegramDeliveryState.BLOCKED_DESTINATION,
            )
            .order_by(TelegramDeliveryJobRecord.id.asc())
            .with_for_update()
        )
    ).scalars()
    return tuple(rows)


async def _disallowed_blocker_reason(
    db: AsyncSession,
    *,
    destination_key: str,
    bot_identities: tuple[str, ...],
    now: datetime,
) -> str | None:
    blocker = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(
                or_(
                    and_(
                        TelegramDeliveryJobRecord.destination_key == destination_key,
                        or_(
                            and_(
                                TelegramDeliveryJobRecord.state
                                == TelegramDeliveryState.LEASED,
                                TelegramDeliveryJobRecord.dispatch_started_at.is_not(None),
                            ),
                            and_(
                                TelegramDeliveryJobRecord.state
                                == TelegramDeliveryState.PENDING_RETRY,
                                TelegramDeliveryJobRecord.outcome_reason
                                == "telegram_rate_limited",
                                TelegramDeliveryJobRecord.next_retry_at.is_not(None),
                                TelegramDeliveryJobRecord.next_retry_at > now,
                            ),
                            and_(
                                TelegramDeliveryJobRecord.state.in_(
                                    _UNRESOLVED_DESTINATION_STATES
                                ),
                                TelegramDeliveryJobRecord.dispatch_started_at.is_not(None),
                            ),
                        ),
                    ),
                    and_(
                        TelegramDeliveryJobRecord.bot_identity.in_(bot_identities),
                        TelegramDeliveryJobRecord.state
                        == TelegramDeliveryState.BLOCKED_BOT,
                    ),
                    TelegramDeliveryJobRecord.state
                    == TelegramDeliveryState.BLOCKED_GATEWAY,
                    and_(
                        TelegramDeliveryJobRecord.bot_identity.in_(bot_identities),
                        TelegramDeliveryJobRecord.bot_cooldown_until.is_not(None),
                        TelegramDeliveryJobRecord.bot_cooldown_until > now,
                    ),
                    and_(
                        TelegramDeliveryJobRecord.bot_identity.in_(bot_identities),
                        TelegramDeliveryJobRecord.rate_limit_probe.is_(True),
                        TelegramDeliveryJobRecord.dispatch_started_at.is_not(None),
                        TelegramDeliveryJobRecord.state.in_(
                            (
                                TelegramDeliveryState.LEASED,
                                *_UNRESOLVED_DESTINATION_STATES,
                            )
                        ),
                    ),
                )
            )
            .order_by(TelegramDeliveryJobRecord.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if blocker is None:
        return None
    state = _enum_value(blocker.state)
    if blocker.bot_cooldown_until is not None and blocker.bot_cooldown_until > now:
        return "telegram_resume_blocked_by_bot_cooldown"
    if blocker.rate_limit_probe:
        return "telegram_resume_blocked_by_rate_limit_probe"
    if state == TelegramDeliveryState.PENDING_RETRY.value:
        return "telegram_resume_blocked_by_destination_cooldown"
    if state in {item.value for item in _UNRESOLVED_DESTINATION_STATES}:
        return "telegram_resume_blocked_by_unresolved_dispatch"
    return f"telegram_resume_blocked_by_{state}"


def _report(
    operation: TelegramDeliveryResumeOperation,
    *,
    idempotent_replay: bool,
) -> TelegramDeliveryResumeReport:
    return TelegramDeliveryResumeReport(
        operation_id=int(operation.id),
        request_id=str(operation.request_id),
        destination_key=str(operation.destination_key),
        state=str(operation.state),
        resumed_job_ids=tuple(int(value) for value in (operation.pause_job_ids or ())),
        attempt_count=int(operation.attempt_count or 0),
        idempotent_replay=idempotent_replay,
    )


async def _prepare_resume(
    session_factory: Callable[[], Any],
    *,
    current_server: str,
    request_id: str,
    requested_by: str,
    destination_key: str,
    bot_identities: tuple[str, ...],
    now: datetime,
) -> _PreparedResume | TelegramDeliveryResumeReport:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_is_foreign_local"
        )
    async with session_factory() as db:
        try:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=bot_identities,
                destination_keys=(destination_key,),
            )
            operation = (
                await db.execute(
                    select(TelegramDeliveryResumeOperation)
                    .where(TelegramDeliveryResumeOperation.request_id == request_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if operation is not None:
                if (
                    str(operation.destination_key) != destination_key
                    or tuple(operation.bot_identities or ()) != bot_identities
                    or str(operation.requested_by) != requested_by
                ):
                    raise TelegramDeliveryResumeConflictError(
                        "telegram_resume_idempotency_scope_mismatch"
                    )
                if str(operation.state) == TELEGRAM_RESUME_COMPLETED:
                    replay_report = _report(operation, idempotent_replay=True)
                    await db.rollback()
                    return replay_report
            else:
                conflicting_id = (
                    await db.execute(
                        select(TelegramDeliveryResumeOperation.id)
                        .where(
                            TelegramDeliveryResumeOperation.destination_key
                            == destination_key,
                            TelegramDeliveryResumeOperation.state.in_(
                                ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES
                            ),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if conflicting_id is not None:
                    raise TelegramDeliveryResumeConflictError(
                        "telegram_resume_destination_operation_active"
                    )

            blocker_reason = await _disallowed_blocker_reason(
                db,
                destination_key=destination_key,
                bot_identities=bot_identities,
                now=now,
            )
            if blocker_reason is not None:
                raise TelegramDeliveryResumeValidationError(blocker_reason)

            pause_rows = await _blocked_destination_rows(
                db,
                destination_key=destination_key,
            )
            if operation is None and not pause_rows:
                raise TelegramDeliveryResumeValidationError(
                    "telegram_resume_destination_not_paused"
                )
            round_ids = tuple(int(record.id) for record in pause_rows)
            round_hash = _pause_evidence_hash(pause_rows)
            if operation is None:
                operation = TelegramDeliveryResumeOperation(
                    request_id=request_id,
                    destination_key=destination_key,
                    bot_identities=list(bot_identities),
                    pause_job_ids=list(round_ids),
                    pause_evidence_hash=round_hash,
                    requested_by=requested_by,
                    attempt_history=[],
                    state=TELEGRAM_RESUME_REQUESTED,
                )
                db.add(operation)
            elif str(operation.state) in {
                TELEGRAM_RESUME_REQUESTED,
                TELEGRAM_RESUME_FAILED,
            }:
                if not pause_rows:
                    raise TelegramDeliveryResumeValidationError(
                        "telegram_resume_destination_not_paused"
                    )
                operation.pause_job_ids = list(round_ids)
                operation.pause_evidence_hash = round_hash
                operation.state = TELEGRAM_RESUME_REQUESTED
                operation.db_applied_at = None
                operation.redis_applied_at = None
                operation.completed_at = None
                operation.preflight_evidence = None
                operation.preflight_completed_at = None

            operation.attempt_count = int(operation.attempt_count or 0) + 1
            operation.last_attempt_at = now
            operation.failure_class = None
            operation.failure_detail = None
            operation.updated_at = now
            _append_attempt_history(
                operation,
                phase="requested",
                result="started",
                at=now,
                extra={
                    "pause_evidence_hash": round_hash,
                    "paused_job_count": len(round_ids),
                },
            )
            await db.flush()
            prepared = _PreparedResume(
                operation_id=int(operation.id),
                state=str(operation.state),
                destination_key=destination_key,
                bot_identities=bot_identities,
                round_pause_job_ids=round_ids,
                round_pause_evidence_hash=round_hash,
                idempotent_replay=int(operation.attempt_count) > 1,
            )
            await db.commit()
            return prepared
        except Exception:
            await db.rollback()
            raise


async def _record_failure(
    session_factory: Callable[[], Any],
    *,
    operation_id: int,
    bot_identities: tuple[str, ...],
    destination_key: str,
    exc: BaseException,
    phase: str,
    now: datetime,
) -> None:
    async with session_factory() as db:
        try:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=bot_identities,
                destination_keys=(destination_key,),
            )
            operation = (
                await db.execute(
                    select(TelegramDeliveryResumeOperation)
                    .where(TelegramDeliveryResumeOperation.id == operation_id)
                    .with_for_update()
                )
            ).scalar_one()
            if str(operation.state) == TELEGRAM_RESUME_REQUESTED:
                operation.state = TELEGRAM_RESUME_FAILED
            operation.failure_class = type(exc).__name__[:160]
            operation.failure_detail = _safe_failure_detail(exc)
            operation.updated_at = now
            _append_attempt_history(
                operation,
                phase=phase,
                result="failed",
                at=now,
                extra={
                    "failure_class": operation.failure_class,
                    "failure_detail": operation.failure_detail,
                },
            )
            await db.commit()
            logger.warning(
                "Telegram channel resume phase failed",
                extra={
                    "event": "telegram_delivery.resume.phase_failed",
                    "operation_id": operation_id,
                    "phase": phase,
                    "failure_class": operation.failure_class,
                },
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Telegram resume failure audit could not be persisted",
                extra={
                    "event": "telegram_delivery.resume.failure_audit_failed",
                    "operation_id": operation_id,
                },
            )
            raise


async def _apply_database_resume(
    session_factory: Callable[[], Any],
    *,
    prepared: _PreparedResume,
    preflight_report: TelegramDeliveryPreflightReport,
    now: datetime,
) -> None:
    async with session_factory() as db:
        try:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=prepared.bot_identities,
                destination_keys=(prepared.destination_key,),
            )
            operation = (
                await db.execute(
                    select(TelegramDeliveryResumeOperation)
                    .where(TelegramDeliveryResumeOperation.id == prepared.operation_id)
                    .with_for_update()
                )
            ).scalar_one()
            if str(operation.state) == TELEGRAM_RESUME_COMPLETED:
                await db.rollback()
                return
            blocker_reason = await _disallowed_blocker_reason(
                db,
                destination_key=prepared.destination_key,
                bot_identities=prepared.bot_identities,
                now=now,
            )
            if blocker_reason is not None:
                raise TelegramDeliveryResumeEvidenceChangedError(blocker_reason)
            pause_rows = await _blocked_destination_rows(
                db,
                destination_key=prepared.destination_key,
            )
            current_ids = tuple(int(record.id) for record in pause_rows)
            current_hash = _pause_evidence_hash(pause_rows)
            if (
                current_ids != prepared.round_pause_job_ids
                or current_hash != prepared.round_pause_evidence_hash
            ):
                raise TelegramDeliveryResumeEvidenceChangedError(
                    "telegram_resume_pause_evidence_changed"
                )

            known_ids = [int(value) for value in (operation.pause_job_ids or ())]
            new_ids = [value for value in current_ids if value not in set(known_ids)]
            if new_ids:
                operation.pause_job_ids = [*known_ids, *new_ids]
                operation.pause_evidence_hash = _chain_evidence_hash(
                    str(operation.pause_evidence_hash),
                    current_hash,
                )
            for record in pause_rows:
                record.state = TelegramDeliveryState.PENDING_RETRY
                record.next_retry_at = now
                record.worker_id = None
                record.lease_until = None
                record.dispatch_started_at = None
                record.rate_limit_probe = False
                record.outcome_reason = "operator_resume_destination"
                record.updated_at = now

            operation.preflight_evidence = _preflight_evidence(preflight_report)
            operation.preflight_completed_at = now
            operation.db_applied_at = operation.db_applied_at or now
            operation.redis_applied_at = None
            operation.completed_at = None
            operation.state = TELEGRAM_RESUME_DATABASE_APPLIED
            operation.failure_class = None
            operation.failure_detail = None
            operation.updated_at = now
            _append_attempt_history(
                operation,
                phase="database_apply",
                result="succeeded",
                at=now,
                extra={"resumed_job_count": len(pause_rows)},
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _complete_after_redis(
    session_factory: Callable[[], Any],
    *,
    prepared: _PreparedResume,
    now: datetime,
) -> TelegramDeliveryResumeReport:
    async with session_factory() as db:
        try:
            await acquire_telegram_delivery_scope_locks(
                db,
                bot_identities=prepared.bot_identities,
                destination_keys=(prepared.destination_key,),
            )
            operation = (
                await db.execute(
                    select(TelegramDeliveryResumeOperation)
                    .where(TelegramDeliveryResumeOperation.id == prepared.operation_id)
                    .with_for_update()
                )
            ).scalar_one()
            if str(operation.state) == TELEGRAM_RESUME_COMPLETED:
                replay_report = _report(operation, idempotent_replay=True)
                await db.rollback()
                return replay_report
            blocker_reason = await _disallowed_blocker_reason(
                db,
                destination_key=prepared.destination_key,
                bot_identities=prepared.bot_identities,
                now=now,
            )
            new_pause_rows = await _blocked_destination_rows(
                db,
                destination_key=prepared.destination_key,
            )
            if blocker_reason is not None or new_pause_rows:
                operation.state = TELEGRAM_RESUME_DATABASE_APPLIED
                operation.redis_applied_at = None
                operation.failure_class = "TelegramDeliveryResumeEvidenceChangedError"
                operation.failure_detail = (
                    blocker_reason or "telegram_resume_new_pause_after_redis"
                )
                operation.updated_at = now
                _append_attempt_history(
                    operation,
                    phase="post_redis_recheck",
                    result="failed",
                    at=now,
                    extra={
                        "failure_class": operation.failure_class,
                        "failure_detail": operation.failure_detail,
                    },
                )
                await db.commit()
                raise TelegramDeliveryResumeIncompleteError(
                    operation.failure_detail
                )

            operation.state = TELEGRAM_RESUME_COMPLETED
            operation.redis_applied_at = now
            operation.completed_at = now
            operation.failure_class = None
            operation.failure_detail = None
            operation.updated_at = now
            _append_attempt_history(
                operation,
                phase="completed",
                result="succeeded",
                at=now,
                extra={
                    "resumed_job_count": len(operation.pause_job_ids or ()),
                },
            )
            await db.flush()
            completed_report = _report(
                operation,
                idempotent_replay=prepared.idempotent_replay,
            )
            await db.commit()
            return completed_report
        except TelegramDeliveryResumeIncompleteError:
            raise
        except Exception:
            await db.rollback()
            raise


async def resume_configured_telegram_channel(
    *,
    session_factory: Callable[[], Any],
    current_server: str,
    settings: Any,
    credential_registry: TelegramDeliveryCredentialRegistry,
    dispatch_limiter: TelegramDeliveryDispatchLimiter,
    request_id: str,
    requested_by: str,
    preflight_runner: Callable[..., Any] = run_configured_telegram_delivery_preflight,
    now_factory: Callable[[], datetime] = utc_now,
) -> TelegramDeliveryResumeReport:
    """Resume the configured channel only after a fresh, full role preflight."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_is_foreign_local"
        )
    if not callable(session_factory) or not callable(preflight_runner):
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_dependency_invalid"
        )
    normalized_request_id = _normalize_request_id(request_id)
    normalized_requested_by = _normalize_requested_by(requested_by)
    destination_key, bot_identities = _configured_scope(settings)
    if credential_registry.bot_identities != bot_identities:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_credential_roles_mismatch"
        )
    clear_gate = getattr(
        dispatch_limiter,
        "clear_destination_gate_after_database_resume",
        None,
    )
    if not callable(clear_gate):
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_limiter_control_missing"
        )
    safety_seconds = float(
        getattr(settings, "telegram_delivery_queue_retry_after_safety_seconds", 0.1)
    )
    if not math.isfinite(safety_seconds) or safety_seconds < 0:
        raise TelegramDeliveryResumeValidationError(
            "telegram_resume_retry_after_safety_invalid"
        )

    prepared_or_report = await _prepare_resume(
        session_factory,
        current_server=current_server,
        request_id=normalized_request_id,
        requested_by=normalized_requested_by,
        destination_key=destination_key,
        bot_identities=bot_identities,
        now=_aware_now(now_factory),
    )
    if isinstance(prepared_or_report, TelegramDeliveryResumeReport):
        return prepared_or_report
    prepared = prepared_or_report

    try:
        preflight_report = await preflight_runner(
            settings=settings,
            credential_registry=credential_registry,
            bot_identities=bot_identities,
            identity_only_bot_identities=(),
        )
    except Exception as exc:
        if isinstance(exc, TelegramDeliveryPreflightRateLimitedError):
            identity = str(exc.bot_identity or "")
            if identity in bot_identities:
                try:
                    await dispatch_limiter.extend_bot_cooldown(
                        identity,
                        until=_aware_now(now_factory)
                        + timedelta(
                            seconds=float(exc.retry_after_seconds) + safety_seconds
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Telegram resume preflight cooldown could not be mirrored",
                        extra={
                            "event": "telegram_delivery.resume.preflight_cooldown_failed",
                            "operation_id": prepared.operation_id,
                            "bot_identity": identity,
                        },
                    )
        await _record_failure(
            session_factory,
            operation_id=prepared.operation_id,
            bot_identities=bot_identities,
            destination_key=destination_key,
            exc=exc,
            phase="telegram_preflight",
            now=_aware_now(now_factory),
        )
        raise

    try:
        _validate_full_preflight_report(
            preflight_report,
            bot_identities=bot_identities,
            credential_registry=credential_registry,
        )
    except Exception as exc:
        await _record_failure(
            session_factory,
            operation_id=prepared.operation_id,
            bot_identities=bot_identities,
            destination_key=destination_key,
            exc=exc,
            phase="preflight_validation",
            now=_aware_now(now_factory),
        )
        raise

    try:
        await _apply_database_resume(
            session_factory,
            prepared=prepared,
            preflight_report=preflight_report,
            now=_aware_now(now_factory),
        )
    except Exception as exc:
        await _record_failure(
            session_factory,
            operation_id=prepared.operation_id,
            bot_identities=bot_identities,
            destination_key=destination_key,
            exc=exc,
            phase="database_apply",
            now=_aware_now(now_factory),
        )
        raise

    try:
        await clear_gate(destination_key)
    except Exception as exc:
        await _record_failure(
            session_factory,
            operation_id=prepared.operation_id,
            bot_identities=bot_identities,
            destination_key=destination_key,
            exc=exc,
            phase="redis_gate",
            now=_aware_now(now_factory),
        )
        raise TelegramDeliveryResumeIncompleteError(
            "telegram_resume_redis_gate_not_cleared"
        ) from exc

    report = await _complete_after_redis(
        session_factory,
        prepared=prepared,
        now=_aware_now(now_factory),
    )
    logger.info(
        "Telegram channel resume completed",
        extra={
            "event": "telegram_delivery.resume.completed",
            "operation_id": report.operation_id,
            "request_id": report.request_id,
            "destination_fingerprint": hashlib.sha256(
                report.destination_key.encode("utf-8")
            ).hexdigest()[:16],
            "resumed_job_count": len(report.resumed_job_ids),
            "attempt_count": report.attempt_count,
        },
    )
    return report
