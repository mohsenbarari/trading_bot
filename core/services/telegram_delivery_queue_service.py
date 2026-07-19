"""Repository boundary for the foreign-local shared Telegram execution queue.

Every function is transaction-neutral: callers own commit/rollback. Claim and
fenced result persistence are intentionally separate so no database transaction
is held open while calling Telegram.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from core.server_routing import SERVER_FOREIGN
from core.telegram_delivery_queue_contract import (
    CLAIMABLE_DELIVERY_STATES,
    EDIT_CATCH_UP_FRESH_COUNT,
    EDIT_STALE_AFTER_SECONDS,
    FINAL_DELIVERY_STATES,
    MINIMUM_LEASE_MARGIN_SECONDS,
    NON_DURABLE_TELEGRAM_QUEUE_ACTIONS,
    TELEGRAM_NON_IDEMPOTENT_SEND_METHODS,
    TelegramDeliveryAction,
    TelegramDeliveryDedupeConflictError,
    TelegramDeliveryDecision,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramGatewayResultLike,
    apply_gateway_result,
    apply_freshness_decision,
    build_delivery_dedupe_key,
    feeder_internal_rank,
    priority_and_rank_for_action,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTION_VALUES,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_feeder_state import TelegramDeliveryFeederState
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_APPLIED,
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TELEGRAM_PROVIDER_OUTCOME_QUARANTINED,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_resume_operation import (
    ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES,
    TelegramDeliveryResumeOperation,
)
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate


TELEGRAM_DELIVERY_QUEUE_WORKER_ID = "telegram-delivery-queue-v1"
TELEGRAM_PRIMARY_BOT_IDENTITY = "primary"
TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY = "channel_editor"
SUPPORTED_TELEGRAM_BOT_IDENTITIES = frozenset(
    {TELEGRAM_PRIMARY_BOT_IDENTITY, TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY}
)
SUPPORTED_TELEGRAM_QUEUE_METHODS = frozenset(
    {
        "answerCallbackQuery",
        "banChatMember",
        "deleteMessage",
        "editMessageReplyMarkup",
        "editMessageText",
        "sendDocument",
        "sendMessage",
        "unbanChatMember",
    }
)
CHANNEL_EDITOR_METHODS = frozenset({"editMessageReplyMarkup", "editMessageText"})
CHANNEL_EDITOR_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
        TelegramDeliveryAction.TRADED_OFFER_EDIT,
        TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
        TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
        TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
        TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
        TelegramDeliveryAction.RECONCILIATION_EDIT,
    }
)


class TelegramDeliveryQueueSurfaceError(PermissionError):
    """Raised when foreign-local execution state is touched on Iran."""


class TelegramDeliveryQueueValidationError(ValueError):
    """Raised before inserting malformed or currently unsupported work."""


class TelegramDeliveryDispatchDeferredError(RuntimeError):
    """Defers a dispatch behind durable destination/bot/gateway evidence."""

    def __init__(
        self,
        *,
        retry_after_seconds: float,
        reason: str,
        cooldown_until: datetime | None = None,
        scope: str = "destination",
    ) -> None:
        self.retry_after_seconds = max(0.1, float(retry_after_seconds))
        self.reason = str(reason or "durable_dispatch_gate")[:96]
        self.cooldown_until = cooldown_until
        self.scope = str(scope or "destination")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class TelegramDeliveryEnqueueResult:
    job: TelegramDeliveryJobRecord
    created: bool


@dataclass(frozen=True, slots=True)
class TelegramDeliveryLeaseRecoveryReport:
    released_unstarted: int
    ambiguous_sends: int
    pending_reconcile: int
    job_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TelegramProviderOutcomePersistResult:
    outcome: TelegramDeliveryProviderOutcomeRecord
    created: bool


@dataclass(frozen=True, slots=True)
class TelegramProviderOutcomeBacklogReport:
    pending_count: int
    oldest_created_at: datetime | None
    due_outcome_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TelegramLimiterEvidence:
    job_id: int
    bot_identity: str
    destination_key: str
    state: TelegramDeliveryState
    outcome_reason: str | None
    next_retry_at: datetime | None
    bot_cooldown_until: datetime | None
    rate_limit_probe: bool
    lease_until: datetime | None
    observed_at: datetime


TelegramFreshnessFeedback = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, TelegramFreshnessDecision, datetime],
    Awaitable[None],
]
TelegramDeliveryResultFeedback = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, TelegramDeliveryDecision, datetime],
    Awaitable[None],
]
TelegramDispatchGuard = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[None],
]


_DURABLE_UNRESOLVED_DESTINATION_STATES = (
    TelegramDeliveryState.AMBIGUOUS,
    TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
    TelegramDeliveryState.PENDING_RECONCILE,
)


def _dispatch_scope_advisory_key(scope: str) -> int:
    return int.from_bytes(
        hashlib.sha256(
            f"telegram-delivery-dispatch:{scope}".encode("utf-8")
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )


async def acquire_telegram_delivery_scope_locks(
    db: AsyncSession,
    *,
    bot_identities: tuple[str, ...] = (),
    destination_keys: tuple[str, ...] = (),
    include_gateway: bool = True,
) -> None:
    """Serialize dispatch/control mutations with one deterministic lock order."""
    scopes: list[str] = []
    if include_gateway:
        scopes.append("gateway")
    scopes.extend(f"bot:{identity}" for identity in sorted(set(bot_identities)))
    scopes.extend(
        f"destination:{destination}"
        for destination in sorted(set(destination_keys))
    )
    for scope in scopes:
        await db.execute(
            select(func.pg_advisory_xact_lock(_dispatch_scope_advisory_key(scope)))
        )


async def _acquire_dispatch_scope_locks(
    db: AsyncSession,
    *,
    record: TelegramDeliveryJobRecord,
) -> None:
    """Serialize result persistence and final markers in a fixed order."""
    await acquire_telegram_delivery_scope_locks(
        db,
        bot_identities=(str(record.bot_identity),),
        destination_keys=(str(record.destination_key),),
    )


def _require_lifecycle_callback(
    record: TelegramDeliveryJobRecord,
    callback: Any,
    *,
    reason: str,
    admin_broadcast_reason: str,
    new_user_membership_reason: str,
    market_notice_reason: str,
    repeat_offer_response_reason: str,
    notification_action_reason: str,
    offer_success_reason: str,
    callback_reason: str,
) -> None:
    if callable(callback):
        return
    action = _enum_value(record.action_kind)
    if action == TelegramDeliveryAction.TRADE_RESULT.value:
        raise TelegramDeliveryQueueValidationError(reason)
    if action == TelegramDeliveryAction.ADMIN_BROADCAST.value:
        raise TelegramDeliveryQueueValidationError(admin_broadcast_reason)
    if action == TelegramDeliveryAction.NEW_USER_MEMBERSHIP.value:
        raise TelegramDeliveryQueueValidationError(new_user_membership_reason)
    if action in {
        TelegramDeliveryAction.MARKET_TRANSITION.value,
        TelegramDeliveryAction.MARKET_STATUS_CORRECTION.value,
    }:
        raise TelegramDeliveryQueueValidationError(market_notice_reason)
    if action == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value:
        raise TelegramDeliveryQueueValidationError(repeat_offer_response_reason)
    if action in TELEGRAM_NOTIFICATION_ACTION_VALUES:
        raise TelegramDeliveryQueueValidationError(notification_action_reason)
    if action == TelegramDeliveryAction.OFFER_SUCCESS.value:
        raise TelegramDeliveryQueueValidationError(offer_success_reason)
    if action in {
        TelegramDeliveryAction.CALLBACK_DEADLINE.value,
        TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK.value,
    }:
        raise TelegramDeliveryQueueValidationError(callback_reason)


def _require_foreign(current_server: str) -> None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramDeliveryQueueSurfaceError("telegram_delivery_jobs_are_foreign_local")


async def _assert_durable_dispatch_gate(
    db: AsyncSession,
    *,
    record: TelegramDeliveryJobRecord,
    now: datetime,
) -> None:
    """Close provider-result/Redis observation crash windows in PostgreSQL.

    Redis remains the high-throughput cadence/cooldown mechanism. This guard is
    the durable final boundary for destination in-flight/429/ambiguous work,
    bot-wide cooldowns, and hard destination/bot/gateway pauses.
    """
    # Result persistence acquires the same locks. Therefore a marker either
    # linearizes before a newly known pause, or waits and observes its commit.
    await _acquire_dispatch_scope_locks(db, record=record)

    runtime_gate = (
        await db.execute(
            select(TelegramDeliveryRuntimeGate)
            .where(
                or_(
                    TelegramDeliveryRuntimeGate.gate_key
                    == f"bot:{record.bot_identity}",
                    TelegramDeliveryRuntimeGate.gate_key == "gateway:telegram",
                ),
                or_(
                    TelegramDeliveryRuntimeGate.state.in_(
                        {"blocked", "resume_requested", "database_applied"}
                    ),
                    and_(
                        TelegramDeliveryRuntimeGate.state == "cooldown",
                        TelegramDeliveryRuntimeGate.cooldown_until.is_not(None),
                        TelegramDeliveryRuntimeGate.cooldown_until > now,
                    ),
                ),
            )
            .order_by(TelegramDeliveryRuntimeGate.gate_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if runtime_gate is not None:
        scope = str(runtime_gate.scope)
        cooldown_until = runtime_gate.cooldown_until
        retry_until = (
            cooldown_until
            if cooldown_until is not None and cooldown_until > now
            else now + timedelta(seconds=30)
        )
        raise TelegramDeliveryDispatchDeferredError(
            retry_after_seconds=max(0.1, (retry_until - now).total_seconds()),
            reason=f"durable_dispatch_gate:runtime_{scope}",
            cooldown_until=retry_until,
            scope=scope,
        )

    incomplete_resume_id = (
        await db.execute(
            select(TelegramDeliveryResumeOperation.id)
            .where(
                TelegramDeliveryResumeOperation.destination_key
                == str(record.destination_key),
                TelegramDeliveryResumeOperation.state.in_(
                    ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if incomplete_resume_id is not None:
        raise TelegramDeliveryDispatchDeferredError(
            retry_after_seconds=30.0,
            reason="durable_dispatch_gate:resume_incomplete",
            cooldown_until=now + timedelta(seconds=30.0),
            scope="destination",
        )

    blockers = list(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.id != int(record.id),
                    or_(
                        and_(
                            TelegramDeliveryJobRecord.destination_key
                            == str(record.destination_key),
                            or_(
                                and_(
                                    TelegramDeliveryJobRecord.state
                                    == TelegramDeliveryState.LEASED,
                                    TelegramDeliveryJobRecord.dispatch_started_at.is_not(
                                        None
                                    ),
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
                                        _DURABLE_UNRESOLVED_DESTINATION_STATES
                                    ),
                                    TelegramDeliveryJobRecord.dispatch_started_at.is_not(
                                        None
                                    ),
                                ),
                                TelegramDeliveryJobRecord.state
                                == TelegramDeliveryState.BLOCKED_DESTINATION,
                            ),
                        ),
                        and_(
                            TelegramDeliveryJobRecord.bot_identity
                            == str(record.bot_identity),
                            TelegramDeliveryJobRecord.state
                            == TelegramDeliveryState.BLOCKED_BOT,
                        ),
                        TelegramDeliveryJobRecord.state
                        == TelegramDeliveryState.BLOCKED_GATEWAY,
                        and_(
                            TelegramDeliveryJobRecord.bot_identity
                            == str(record.bot_identity),
                            TelegramDeliveryJobRecord.bot_cooldown_until.is_not(None),
                            TelegramDeliveryJobRecord.bot_cooldown_until > now,
                        ),
                        and_(
                            TelegramDeliveryJobRecord.bot_identity
                            == str(record.bot_identity),
                            TelegramDeliveryJobRecord.rate_limit_probe.is_(True),
                            TelegramDeliveryJobRecord.dispatch_started_at.is_not(None),
                            TelegramDeliveryJobRecord.state.in_(
                                (
                                    TelegramDeliveryState.LEASED,
                                    *_DURABLE_UNRESOLVED_DESTINATION_STATES,
                                )
                            ),
                        ),
                    ),
                )
                .order_by(TelegramDeliveryJobRecord.id.asc())
            )
        ).scalars()
    )
    if not blockers:
        return

    retry_until = now + timedelta(seconds=0.1)
    reason_state = TelegramDeliveryState.LEASED
    reason_scope = "destination"
    for blocker in blockers:
        blocker_state = TelegramDeliveryState(_enum_value(blocker.state))
        candidate_until = now + timedelta(seconds=30.0)
        candidate_reason = blocker_state.value
        candidate_scope = "destination"
        if blocker_state == TelegramDeliveryState.BLOCKED_BOT:
            candidate_scope = "bot"
        elif blocker_state == TelegramDeliveryState.BLOCKED_GATEWAY:
            candidate_scope = "gateway"
        if blocker.rate_limit_probe:
            candidate_reason = "bot_probe_inflight"
            candidate_scope = "bot"
        if (
            blocker_state == TelegramDeliveryState.PENDING_RETRY
            and blocker.next_retry_at is not None
        ):
            candidate_until = blocker.next_retry_at
        elif (
            blocker_state == TelegramDeliveryState.LEASED
            and blocker.lease_until is not None
            and blocker.lease_until > now
        ):
            candidate_until = blocker.lease_until
        elif blocker_state == TelegramDeliveryState.LEASED:
            candidate_until = now + timedelta(seconds=1.0)
        if (
            blocker.bot_identity == record.bot_identity
            and blocker.bot_cooldown_until is not None
            and blocker.bot_cooldown_until > candidate_until
        ):
            candidate_until = blocker.bot_cooldown_until
            candidate_reason = "bot_cooldown"
            candidate_scope = "bot"
        if candidate_until > retry_until:
            retry_until = candidate_until
            reason_state = candidate_reason
            reason_scope = candidate_scope

    raise TelegramDeliveryDispatchDeferredError(
        retry_after_seconds=max(0.1, (retry_until - now).total_seconds()),
        reason=f"durable_dispatch_gate:{getattr(reason_state, 'value', reason_state)}",
        cooldown_until=retry_until,
        scope=reason_scope,
    )


async def load_active_telegram_limiter_evidence(
    db: AsyncSession,
    *,
    current_server: str,
    now: datetime | None = None,
) -> tuple[TelegramLimiterEvidence, ...]:
    """Return committed cooldown/pause evidence rebuilt before claims."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    records = list(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(
                    or_(
                        and_(
                            TelegramDeliveryJobRecord.state
                            == TelegramDeliveryState.PENDING_RETRY,
                            TelegramDeliveryJobRecord.outcome_reason
                            == "telegram_rate_limited",
                            TelegramDeliveryJobRecord.next_retry_at.is_not(None),
                            TelegramDeliveryJobRecord.next_retry_at
                            > current_time,
                        ),
                        TelegramDeliveryJobRecord.state.in_(
                            (
                                TelegramDeliveryState.BLOCKED_DESTINATION,
                                TelegramDeliveryState.BLOCKED_BOT,
                                TelegramDeliveryState.BLOCKED_GATEWAY,
                            )
                        ),
                        TelegramDeliveryJobRecord.bot_cooldown_until
                        > current_time,
                        and_(
                            TelegramDeliveryJobRecord.rate_limit_probe.is_(True),
                            TelegramDeliveryJobRecord.dispatch_started_at.is_not(None),
                            TelegramDeliveryJobRecord.state.in_(
                                (
                                    TelegramDeliveryState.LEASED,
                                    *_DURABLE_UNRESOLVED_DESTINATION_STATES,
                                )
                            ),
                        ),
                    )
                )
                .order_by(
                    TelegramDeliveryJobRecord.updated_at.asc(),
                    TelegramDeliveryJobRecord.id.asc(),
                )
            )
        ).scalars()
    )
    return tuple(
        TelegramLimiterEvidence(
            job_id=int(record.id),
            bot_identity=str(record.bot_identity),
            destination_key=str(record.destination_key),
            state=TelegramDeliveryState(_enum_value(record.state)),
            outcome_reason=record.outcome_reason,
            next_retry_at=record.next_retry_at,
            bot_cooldown_until=record.bot_cooldown_until,
            rate_limit_probe=bool(record.rate_limit_probe),
            lease_until=record.lease_until,
            observed_at=record.updated_at or current_time,
        )
        for record in records
    )


async def load_incomplete_telegram_resume_destination_keys(
    db: AsyncSession,
    *,
    current_server: str,
) -> tuple[str, ...]:
    """Return fail-closed destination gates for unfinished resume sagas."""
    _require_foreign(current_server)
    rows = (
        await db.execute(
            select(TelegramDeliveryResumeOperation.destination_key)
            .where(
                TelegramDeliveryResumeOperation.state.in_(
                    ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES
                )
            )
            .order_by(TelegramDeliveryResumeOperation.destination_key.asc())
        )
    ).scalars()
    return tuple(dict.fromkeys(str(value) for value in rows))


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def canonical_telegram_delivery_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Return strict JSON payload plus the immutable execution hash."""
    normalized = dict(payload)
    try:
        canonical = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TelegramDeliveryQueueValidationError("payload_must_be_strict_json") from exc
    return normalized, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if 0 < parsed <= 9_223_372_036_854_775_807 else None


def _raw_retry_after(result: TelegramGatewayResultLike) -> int | None:
    body = result.response_json
    if not isinstance(body, Mapping):
        return None
    parameters = body.get("parameters")
    if not isinstance(parameters, Mapping):
        return None
    return _positive_int(parameters.get("retry_after"))


def _provider_ok(result: TelegramGatewayResultLike) -> bool:
    return bool(
        result.ok
        and isinstance(result.response_json, Mapping)
        and result.response_json.get("ok") is True
    )


def _provider_error_code(result: TelegramGatewayResultLike) -> int | None:
    if not isinstance(result.response_json, Mapping):
        return None
    try:
        return int(result.response_json.get("error_code"))
    except (TypeError, ValueError, OverflowError):
        return None


def _sanitized_provider_response(result: TelegramGatewayResultLike) -> dict[str, Any] | None:
    body = result.response_json
    if not isinstance(body, Mapping):
        return None
    sanitized: dict[str, Any] = {}
    for key in ("ok", "error_code"):
        if key in body and isinstance(body[key], (bool, int)):
            sanitized[key] = body[key]
    description = body.get("description")
    if description:
        sanitized["description"] = " ".join(str(description).split())[:500]
    parameters = body.get("parameters")
    if isinstance(parameters, Mapping):
        safe_parameters: dict[str, Any] = {}
        retry_after = _positive_int(parameters.get("retry_after"))
        if retry_after is not None:
            safe_parameters["retry_after"] = retry_after
        if parameters.get("migrate_to_chat_id") is not None:
            # Presence affects classification; the raw destination identifier
            # is not needed in the execution audit row.
            safe_parameters["migrate_to_chat_id"] = "redacted"
        if safe_parameters:
            sanitized["parameters"] = safe_parameters
    message_id = result.message_id
    if message_id is not None:
        sanitized["result"] = {"message_id": int(message_id)}
    return sanitized or None


def _provider_outcome_facts(
    result: TelegramGatewayResultLike,
) -> tuple[dict[str, Any], str]:
    transport_phase = str(
        getattr(result, "transport_phase", "") or ""
    ).strip().lower() or None
    if transport_phase not in {
        None,
        "pre_write",
        "write_unknown",
        "response_received",
    }:
        raise TelegramDeliveryQueueValidationError(
            "provider_outcome_transport_phase_invalid"
        )
    facts = {
        "gateway_ok": bool(result.ok),
        "method": str(result.method or ""),
        "transport_phase": transport_phase,
        "provider_status_code": (
            int(result.status_code) if result.status_code is not None else None
        ),
        "provider_response": _sanitized_provider_response(result),
        "provider_error_class": str(result.error or "")[:120] or None,
        "telegram_message_id": result.message_id,
        "retry_after_seconds": _raw_retry_after(result),
    }
    canonical = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return facts, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _provider_outcome_to_gateway_result(
    outcome: TelegramDeliveryProviderOutcomeRecord,
) -> TelegramGatewayResult:
    response = dict(outcome.provider_response or {}) or None
    return TelegramGatewayResult(
        ok=bool(outcome.gateway_ok),
        method=str(outcome.method),
        status_code=outcome.provider_status_code,
        response_json=response,
        response_text=(
            str(response.get("description") or "") if isinstance(response, Mapping) else ""
        ),
        error=outcome.provider_error_class,
        transport_phase=outcome.transport_phase,
    )


async def record_telegram_delivery_provider_outcome(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    result: TelegramGatewayResultLike,
    now: datetime | None = None,
) -> TelegramProviderOutcomePersistResult:
    """Durably record provider facts before applying domain feedback.

    The unique job/fence identity makes process retries idempotent. A conflicting
    fact for the same dispatch fence is quarantined by refusing the write.
    """
    _require_foreign(current_server)
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None:
        raise TelegramDeliveryQueueValidationError("provider_outcome_job_missing")
    if (
        int(record.lease_token or 0) != int(lease_token)
        or record.dispatch_started_at is None
        or (record.worker_id not in {None, str(worker_id)})
    ):
        raise TelegramDeliveryQueueValidationError("provider_outcome_stale_dispatch_fence")
    if str(result.method or "") != str(record.method):
        raise TelegramDeliveryQueueValidationError("provider_outcome_method_mismatch")

    facts, outcome_hash = _provider_outcome_facts(result)
    existing = (
        await db.execute(
            select(TelegramDeliveryProviderOutcomeRecord)
            .where(
                TelegramDeliveryProviderOutcomeRecord.job_id == int(job_id),
                TelegramDeliveryProviderOutcomeRecord.lease_token == int(lease_token),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if str(existing.outcome_hash) != outcome_hash:
            raise TelegramDeliveryQueueValidationError("provider_outcome_fence_conflict")
        return TelegramProviderOutcomePersistResult(outcome=existing, created=False)

    outcome = TelegramDeliveryProviderOutcomeRecord(
        job_id=int(job_id),
        lease_token=int(lease_token),
        worker_id=str(worker_id),
        bot_identity=str(record.bot_identity),
        method=str(record.method),
        transport_phase=facts["transport_phase"],
        gateway_ok=facts["gateway_ok"],
        provider_status_code=facts["provider_status_code"],
        provider_response=facts["provider_response"],
        provider_error_class=facts["provider_error_class"],
        telegram_message_id=facts["telegram_message_id"],
        retry_after_seconds=facts["retry_after_seconds"],
        outcome_hash=outcome_hash,
        apply_state=TELEGRAM_PROVIDER_OUTCOME_PENDING,
        next_apply_at=current_time,
        created_at=current_time,
        updated_at=current_time,
    )
    db.add(outcome)
    await db.flush()
    return TelegramProviderOutcomePersistResult(outcome=outcome, created=True)


def _record_to_contract(record: TelegramDeliveryJobRecord) -> TelegramDeliveryJob:
    return TelegramDeliveryJob(
        id=int(record.id),
        dedupe_key=str(record.dedupe_key),
        feeder=TelegramFeederKind(_enum_value(record.feeder_kind)),
        feeder_rank=int(record.feeder_rank),
        source_natural_id=str(record.source_natural_id),
        source_version=int(record.source_version),
        destination_key=str(record.destination_key),
        destination_class=TelegramDestinationClass(_enum_value(record.destination_class)),
        method=str(record.method),
        payload=dict(record.payload or {}),
        action=TelegramDeliveryAction(_enum_value(record.action_kind)),
        created_sequence=int(record.enqueued_seq),
        bot_identity=str(record.bot_identity),
        source_order_at=record.source_order_at,
        delivery_deadline_at=record.delivery_deadline_at,
        eligible_at=record.eligible_at,
        freshness_deadline_at=record.freshness_deadline_at,
        campaign_id=record.campaign_id,
        state=TelegramDeliveryState(_enum_value(record.state)),
        attempt_count=int(record.attempt_count or 0),
        provider_attempt_count=int(record.provider_attempt_count or 0),
        next_retry_at=record.next_retry_at,
        lease_until=record.lease_until,
        lease_token=int(record.lease_token or 0),
        worker_id=record.worker_id,
        last_error_class=record.last_error_class,
        last_error_message=record.last_error_message,
        telegram_message_id=record.telegram_message_id,
    )


def _immutable_enqueue_fields(
    record: TelegramDeliveryJobRecord,
    *,
    feeder: TelegramFeederKind,
    feeder_rank: int,
    source_natural_id: str,
    source_version: int,
    action: TelegramDeliveryAction,
    bot_identity: str,
    destination_key: str,
    destination_class: TelegramDestinationClass,
    method: str,
    payload_hash: str,
    template_version: str,
    delivery_deadline_at: datetime | None,
    eligible_at: datetime | None,
    freshness_deadline_at: datetime | None,
    source_order_at: datetime | None,
    campaign_id: str | None,
    run_id: str | None,
) -> bool:
    return (
        _enum_value(record.feeder_kind) == feeder.value
        and int(record.feeder_rank) == feeder_rank
        and record.source_natural_id == source_natural_id
        and int(record.source_version) == source_version
        and _enum_value(record.action_kind) == action.value
        and record.bot_identity == bot_identity
        and record.destination_key == destination_key
        and _enum_value(record.destination_class) == destination_class.value
        and record.method == method
        and record.payload_hash == payload_hash
        and record.template_version == template_version
        and record.delivery_deadline_at == delivery_deadline_at
        and record.eligible_at == eligible_at
        and record.freshness_deadline_at == freshness_deadline_at
        and record.source_order_at == source_order_at
        and record.campaign_id == campaign_id
        and record.run_id == run_id
    )


async def enqueue_telegram_delivery_job(
    db: AsyncSession,
    *,
    current_server: str,
    feeder: TelegramFeederKind,
    source_natural_id: str,
    source_version: int,
    action: TelegramDeliveryAction,
    bot_identity: str,
    destination_key: str,
    destination_class: TelegramDestinationClass,
    method: str,
    payload: Mapping[str, Any],
    template_version: str,
    delivery_deadline_at: datetime | None = None,
    eligible_at: datetime | None = None,
    freshness_deadline_at: datetime | None = None,
    source_order_at: datetime | None = None,
    campaign_id: str | None = None,
    run_id: str | None = None,
) -> TelegramDeliveryEnqueueResult:
    _require_foreign(current_server)
    source_identity = str(source_natural_id or "").strip()
    bot = str(bot_identity or "").strip()
    destination = str(destination_key or "").strip()
    template = str(template_version or "").strip()
    normalized_campaign_id = str(campaign_id or "").strip() or None
    normalized_run_id = str(run_id or "").strip() or None
    normalized_method = str(method or "").strip()
    if not source_identity or not bot or not destination or not template:
        raise TelegramDeliveryQueueValidationError("queue_identity_fields_required")
    length_limits = (
        (source_identity, 256, "source_natural_id_too_long"),
        (bot, 128, "bot_identity_too_long"),
        (destination, 256, "destination_key_too_long"),
        (template, 64, "template_version_too_long"),
        (normalized_campaign_id or "", 192, "campaign_id_too_long"),
        (normalized_run_id or "", 192, "run_id_too_long"),
    )
    for value, maximum, reason in length_limits:
        if len(value) > maximum:
            raise TelegramDeliveryQueueValidationError(reason)
    try:
        normalized_source_version = int(source_version)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryQueueValidationError("source_version_must_be_integer") from exc
    if normalized_source_version < 0:
        raise TelegramDeliveryQueueValidationError("source_version_must_be_non_negative")
    if action in NON_DURABLE_TELEGRAM_QUEUE_ACTIONS:
        raise TelegramDeliveryQueueValidationError(
            "telegram_action_forbidden_in_durable_queue"
        )
    if normalized_method not in SUPPORTED_TELEGRAM_QUEUE_METHODS:
        raise TelegramDeliveryQueueValidationError("telegram_method_not_allowlisted")
    if bot not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise TelegramDeliveryQueueValidationError("telegram_bot_identity_not_allowlisted")
    if bot == TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY and (
        destination_class != TelegramDestinationClass.CHANNEL
        or normalized_method not in CHANNEL_EDITOR_METHODS
        or action not in CHANNEL_EDITOR_ACTIONS
    ):
        raise TelegramDeliveryQueueValidationError("channel_editor_route_not_allowlisted")
    if feeder == TelegramFeederKind.OFFER_EDIT:
        if (
            not isinstance(source_order_at, datetime)
            or source_order_at.tzinfo is None
            or source_order_at.utcoffset() is None
        ):
            raise TelegramDeliveryQueueValidationError(
                "offer_edit_source_order_at_required"
            )
    elif source_order_at is not None:
        raise TelegramDeliveryQueueValidationError(
            "source_order_at_forbidden_for_non_offer_edit"
        )

    normalized_payload, payload_hash = canonical_telegram_delivery_payload(payload)
    feeder_rank = feeder_internal_rank(feeder, action)
    priority, priority_rank = priority_and_rank_for_action(
        action,
        delivery_deadline_at=delivery_deadline_at,
    )
    dedupe_key = build_delivery_dedupe_key(
        feeder=feeder,
        source_natural_id=source_identity,
        source_version=normalized_source_version,
        action=action,
        destination_identity=destination,
    )
    values = {
        "dedupe_key": dedupe_key,
        "feeder_kind": feeder,
        "feeder_rank": feeder_rank,
        "source_natural_id": source_identity,
        "source_version": normalized_source_version,
        "action_kind": action,
        "bot_identity": bot,
        "destination_key": destination,
        "destination_class": destination_class,
        "method": normalized_method,
        "payload": normalized_payload,
        "template_version": template,
        "payload_hash": payload_hash,
        "priority": int(priority),
        "priority_rank": int(priority_rank),
        "delivery_deadline_at": delivery_deadline_at,
        "eligible_at": eligible_at,
        "freshness_deadline_at": freshness_deadline_at,
        "source_order_at": source_order_at,
        "campaign_id": normalized_campaign_id,
        "run_id": normalized_run_id,
        "state": TelegramDeliveryState.PENDING,
        "attempt_count": 0,
        "provider_attempt_count": 0,
        "lease_token": 0,
    }
    insert_stmt = (
        postgresql_insert(TelegramDeliveryJobRecord)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(TelegramDeliveryJobRecord.id)
    )
    inserted_id = (await db.execute(insert_stmt)).scalar_one_or_none()
    created = inserted_id is not None
    lookup = select(TelegramDeliveryJobRecord).where(
        TelegramDeliveryJobRecord.id == inserted_id
        if created
        else TelegramDeliveryJobRecord.dedupe_key == dedupe_key
    )
    record = (await db.execute(lookup)).scalar_one()
    if not _immutable_enqueue_fields(
        record,
        feeder=feeder,
        feeder_rank=feeder_rank,
        source_natural_id=source_identity,
        source_version=normalized_source_version,
        action=action,
        bot_identity=bot,
        destination_key=destination,
        destination_class=destination_class,
        method=normalized_method,
        payload_hash=payload_hash,
        template_version=template,
        delivery_deadline_at=delivery_deadline_at,
        eligible_at=eligible_at,
        freshness_deadline_at=freshness_deadline_at,
        source_order_at=source_order_at,
        campaign_id=normalized_campaign_id,
        run_id=normalized_run_id,
    ):
        raise TelegramDeliveryDedupeConflictError(dedupe_key)
    return TelegramDeliveryEnqueueResult(job=record, created=created)


async def claim_next_telegram_delivery_job(
    db: AsyncSession,
    *,
    current_server: str,
    bot_identity: str,
    worker_id: str,
    request_timeout_seconds: float,
    lease_seconds: float,
    allowed_destination_classes: set[TelegramDestinationClass] | None = None,
    now: datetime | None = None,
) -> TelegramDeliveryJobRecord | None:
    _require_foreign(current_server)
    lane_identity = str(bot_identity or "").strip()
    if lane_identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise TelegramDeliveryQueueValidationError("telegram_bot_identity_not_allowlisted")
    if float(lease_seconds) < float(request_timeout_seconds) + MINIMUM_LEASE_MARGIN_SECONDS:
        raise TelegramDeliveryQueueValidationError("lease_must_cover_request_timeout_plus_margin")
    current_time = now or utc_now()
    normalized_destination_classes: tuple[TelegramDestinationClass, ...] | None = None
    if allowed_destination_classes is not None:
        normalized_destination_classes = tuple(
            sorted(
                {TelegramDestinationClass(value) for value in allowed_destination_classes},
                key=lambda value: value.value,
            )
        )
        if not normalized_destination_classes:
            raise TelegramDeliveryQueueValidationError(
                "allowed_destination_classes_must_not_be_empty"
            )
    trade_overdue = (
        (TelegramDeliveryJobRecord.action_kind == TelegramDeliveryAction.TRADE_RESULT)
        & TelegramDeliveryJobRecord.delivery_deadline_at.is_not(None)
        & (TelegramDeliveryJobRecord.delivery_deadline_at <= current_time)
    )
    effective_priority = case((trade_overdue, 0), else_=TelegramDeliveryJobRecord.priority)
    effective_rank = case((trade_overdue, 1), else_=TelegramDeliveryJobRecord.priority_rank)
    offer_edit = TelegramDeliveryJobRecord.feeder_kind == TelegramFeederKind.OFFER_EDIT
    offer_edit_stale = and_(
        offer_edit,
        TelegramDeliveryJobRecord.eligible_at.is_not(None),
        TelegramDeliveryJobRecord.eligible_at
        <= current_time - timedelta(seconds=EDIT_STALE_AFTER_SECONDS),
    )
    feeder_state = (
        await db.execute(
            select(TelegramDeliveryFeederState.fresh_success_counts).where(
                TelegramDeliveryFeederState.feeder_kind
                == TelegramFeederKind.OFFER_EDIT.value
            )
        )
    ).scalar_one_or_none()
    if not isinstance(feeder_state, dict):
        raise TelegramDeliveryQueueValidationError(
            "telegram_offer_edit_fairness_state_missing_or_invalid"
        )
    catch_up_due_ranks: list[int] = []
    try:
        for raw_rank, raw_count in feeder_state.items():
            rank = int(raw_rank)
            count = int(raw_count)
            if rank < 0 or count < 0 or count > EDIT_CATCH_UP_FRESH_COUNT:
                raise ValueError
            if count >= EDIT_CATCH_UP_FRESH_COUNT:
                catch_up_due_ranks.append(rank)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryQueueValidationError(
            "telegram_offer_edit_fairness_state_missing_or_invalid"
        ) from exc
    catch_up_due = (
        and_(
            offer_edit_stale,
            TelegramDeliveryJobRecord.feeder_rank.in_(tuple(catch_up_due_ranks)),
        )
        if catch_up_due_ranks
        else False
    )
    offer_edit_freshness_bucket = case(
        (catch_up_due, 0),
        (offer_edit_stale, 2),
        else_=1,
    )
    offer_edit_source_order = case(
        (offer_edit, TelegramDeliveryJobRecord.source_order_at),
        else_=None,
    )
    blocker = aliased(TelegramDeliveryJobRecord)
    destination_or_runtime_blocked = exists(
        select(blocker.id).where(
            blocker.id != TelegramDeliveryJobRecord.id,
            or_(
                and_(
                    blocker.destination_key == TelegramDeliveryJobRecord.destination_key,
                    or_(
                        and_(
                            blocker.state == TelegramDeliveryState.LEASED,
                            blocker.dispatch_started_at.is_not(None),
                        ),
                        and_(
                            blocker.state == TelegramDeliveryState.PENDING_RETRY,
                            blocker.outcome_reason == "telegram_rate_limited",
                            blocker.next_retry_at.is_not(None),
                            blocker.next_retry_at > current_time,
                        ),
                        and_(
                            blocker.state.in_(_DURABLE_UNRESOLVED_DESTINATION_STATES),
                            blocker.dispatch_started_at.is_not(None),
                        ),
                        blocker.state == TelegramDeliveryState.BLOCKED_DESTINATION,
                    ),
                ),
                and_(
                    blocker.bot_identity == TelegramDeliveryJobRecord.bot_identity,
                    blocker.state == TelegramDeliveryState.BLOCKED_BOT,
                ),
                blocker.state == TelegramDeliveryState.BLOCKED_GATEWAY,
                and_(
                    blocker.bot_identity == TelegramDeliveryJobRecord.bot_identity,
                    blocker.bot_cooldown_until.is_not(None),
                    blocker.bot_cooldown_until > current_time,
                ),
                and_(
                    blocker.bot_identity == TelegramDeliveryJobRecord.bot_identity,
                    blocker.rate_limit_probe.is_(True),
                    blocker.dispatch_started_at.is_not(None),
                    blocker.state.in_(
                        (
                            TelegramDeliveryState.LEASED,
                            *_DURABLE_UNRESOLVED_DESTINATION_STATES,
                        )
                    ),
                ),
            ),
        )
    )
    resume_incomplete = exists(
        select(TelegramDeliveryResumeOperation.id).where(
            TelegramDeliveryResumeOperation.destination_key
            == TelegramDeliveryJobRecord.destination_key,
            TelegramDeliveryResumeOperation.state.in_(
                ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES
            ),
        )
    )
    runtime_gate_blocked = exists(
        select(TelegramDeliveryRuntimeGate.gate_key).where(
            or_(
                TelegramDeliveryRuntimeGate.gate_key
                == func.concat("bot:", TelegramDeliveryJobRecord.bot_identity),
                TelegramDeliveryRuntimeGate.gate_key == "gateway:telegram",
            ),
            or_(
                TelegramDeliveryRuntimeGate.state.in_(
                    {"blocked", "resume_requested", "database_applied"}
                ),
                and_(
                    TelegramDeliveryRuntimeGate.state == "cooldown",
                    TelegramDeliveryRuntimeGate.cooldown_until.is_not(None),
                    TelegramDeliveryRuntimeGate.cooldown_until > current_time,
                ),
            ),
        )
    )
    claim_filters: list[Any] = [
        TelegramDeliveryJobRecord.bot_identity == lane_identity,
        TelegramDeliveryJobRecord.state.in_(tuple(CLAIMABLE_DELIVERY_STATES)),
        or_(
            TelegramDeliveryJobRecord.eligible_at.is_(None),
            TelegramDeliveryJobRecord.eligible_at <= current_time,
        ),
        or_(
            TelegramDeliveryJobRecord.next_retry_at.is_(None),
            TelegramDeliveryJobRecord.next_retry_at <= current_time,
        ),
        or_(
            TelegramDeliveryJobRecord.bot_cooldown_until.is_(None),
            TelegramDeliveryJobRecord.bot_cooldown_until <= current_time,
        ),
        ~destination_or_runtime_blocked,
        ~resume_incomplete,
        ~runtime_gate_blocked,
    ]
    if normalized_destination_classes is not None:
        claim_filters.append(
            TelegramDeliveryJobRecord.destination_class.in_(
                normalized_destination_classes
            )
        )
    stmt = (
        select(TelegramDeliveryJobRecord)
        .where(*claim_filters)
        .order_by(
            effective_priority.asc(),
            effective_rank.asc(),
            offer_edit_freshness_bucket.asc(),
            offer_edit_source_order.desc().nullslast(),
            TelegramDeliveryJobRecord.delivery_deadline_at.asc().nullslast(),
            TelegramDeliveryJobRecord.enqueued_seq.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    record.state = TelegramDeliveryState.LEASED
    record.worker_id = str(worker_id)
    record.lease_token = int(record.lease_token or 0) + 1
    record.lease_until = current_time + timedelta(seconds=float(lease_seconds))
    record.dispatch_started_at = None
    record.attempt_count = int(record.attempt_count or 0) + 1
    record.updated_at = current_time
    await db.flush()
    return record


async def mark_telegram_delivery_dispatch_started(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    dispatch_guard: TelegramDispatchGuard | None = None,
    rate_limit_probe: bool = False,
    now: datetime | None = None,
) -> bool:
    _require_foreign(current_server)
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None or (
        _enum_value(record.state) != TelegramDeliveryState.LEASED.value
        or record.worker_id != str(worker_id)
        or int(record.lease_token or 0) != int(lease_token)
        or record.lease_until is None
        or record.lease_until <= current_time
        or record.dispatch_started_at is not None
    ):
        return False
    await _assert_durable_dispatch_gate(
        db,
        record=record,
        now=current_time,
    )
    # Advisory-scope contention and the authoritative dispatch guard may take
    # long enough for an otherwise valid lease to expire. Sample the clock
    # again at the actual local linearization boundary; an expired fence must
    # never be followed by a Telegram side effect.
    dispatch_linearized_at = max(current_time, utc_now())
    if record.lease_until is None or record.lease_until <= dispatch_linearized_at:
        return False
    _require_lifecycle_callback(
        record,
        dispatch_guard,
        reason="trade_result_dispatch_guard_required",
        admin_broadcast_reason="admin_broadcast_dispatch_guard_required",
        new_user_membership_reason="new_user_membership_dispatch_guard_required",
        market_notice_reason="market_notice_dispatch_guard_required",
        repeat_offer_response_reason="repeat_offer_response_dispatch_guard_required",
        notification_action_reason="notification_action_dispatch_guard_required",
        offer_success_reason="offer_success_dispatch_guard_required",
        callback_reason="callback_dispatch_guard_required",
    )
    if dispatch_guard is not None:
        await dispatch_guard(db, record, dispatch_linearized_at)
        dispatch_linearized_at = max(dispatch_linearized_at, utc_now())
        if record.lease_until is None or record.lease_until <= dispatch_linearized_at:
            return False
    record.dispatch_started_at = dispatch_linearized_at
    record.provider_attempt_count = int(record.provider_attempt_count or 0) + 1
    record.rate_limit_probe = bool(rate_limit_probe)
    record.updated_at = dispatch_linearized_at
    await db.flush()
    return True


async def apply_telegram_delivery_freshness_result(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    decision: TelegramFreshnessDecision,
    feedback: TelegramFreshnessFeedback | None = None,
    dependency_retry_seconds: float = 1.0,
    now: datetime | None = None,
) -> bool:
    """Persist a non-SEND pre-dispatch decision under the active fence.

    Returns ``True`` only when the job remains leased and may be dispatched.
    """
    _require_foreign(current_server)
    if decision.outcome == TelegramFreshnessOutcome.SEND:
        return True
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None or (
        _enum_value(record.state) != TelegramDeliveryState.LEASED.value
        or record.worker_id != str(worker_id)
        or int(record.lease_token or 0) != int(lease_token)
    ):
        return False
    _require_lifecycle_callback(
        record,
        feedback,
        reason="trade_result_freshness_feedback_required",
        admin_broadcast_reason="admin_broadcast_freshness_feedback_required",
        new_user_membership_reason="new_user_membership_freshness_feedback_required",
        market_notice_reason="market_notice_freshness_feedback_required",
        repeat_offer_response_reason="repeat_offer_response_freshness_feedback_required",
        notification_action_reason="notification_action_freshness_feedback_required",
        offer_success_reason="offer_success_freshness_feedback_required",
        callback_reason="callback_freshness_feedback_required",
    )
    contract_job = _record_to_contract(record)
    apply_freshness_decision(contract_job, decision)
    if (
        decision.outcome == TelegramFreshnessOutcome.RECLASSIFY
        and decision.replacement_action is not None
    ):
        # action_kind is part of the immutable dedupe identity. A later
        # reconciliation adapter must rebuild the replacement payload and
        # enqueue a distinct logical job; mutating this row would corrupt both
        # dedupe and audit history.
        record.state = TelegramDeliveryState.PENDING_RECONCILE
        record.next_retry_at = None
    elif decision.outcome == TelegramFreshnessOutcome.WAIT_DEPENDENCY:
        record.state = TelegramDeliveryState.PENDING_RETRY
        record.next_retry_at = current_time + timedelta(
            seconds=max(0.1, float(dependency_retry_seconds))
        )
    else:
        record.state = contract_job.state
        record.next_retry_at = None
    record.worker_id = None
    record.lease_until = None
    record.outcome_reason = decision.reason or f"freshness_{decision.outcome.value}"
    record.updated_at = current_time
    if TelegramDeliveryState(_enum_value(record.state)) in FINAL_DELIVERY_STATES:
        record.terminal_at = current_time
    if feedback is not None:
        await feedback(db, record, decision, current_time)
    await db.flush()
    return False


async def release_unstarted_telegram_delivery_lease(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    reason: str,
    retry_seconds: float = 1.0,
    now: datetime | None = None,
) -> bool:
    _require_foreign(current_server)
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None or (
        _enum_value(record.state) != TelegramDeliveryState.LEASED.value
        or record.worker_id != str(worker_id)
        or int(record.lease_token or 0) != int(lease_token)
        or record.dispatch_started_at is not None
    ):
        return False
    record.state = TelegramDeliveryState.PENDING_RETRY
    record.next_retry_at = current_time + timedelta(seconds=max(0.1, float(retry_seconds)))
    record.worker_id = None
    record.lease_until = None
    record.last_error_class = "PreDispatchError"
    record.outcome_reason = str(reason or "pre_dispatch_error")[:160]
    record.updated_at = current_time
    await db.flush()
    return True


async def defer_unstarted_telegram_delivery_lease(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    retry_seconds: float,
    reason: str,
    now: datetime | None = None,
) -> bool:
    """Return an unstarted lease to the queue without recording an error.

    Durable pacing is expected control flow, not a provider or worker failure.
    The same lease fence used by dispatch protects this transition.
    """
    _require_foreign(current_server)
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if record is None or (
        _enum_value(record.state) != TelegramDeliveryState.LEASED.value
        or record.worker_id != str(worker_id)
        or int(record.lease_token or 0) != int(lease_token)
        or record.dispatch_started_at is not None
    ):
        return False
    record.state = TelegramDeliveryState.PENDING_RETRY
    record.next_retry_at = current_time + timedelta(
        seconds=max(0.001, float(retry_seconds))
    )
    record.worker_id = None
    record.lease_until = None
    record.last_error_class = None
    record.last_error_message = None
    record.outcome_reason = str(reason or "telegram_limiter_wait")[:160]
    record.updated_at = current_time
    await db.flush()
    return True


async def _persist_durable_rate_limit_evidence(
    db: AsyncSession,
    *,
    record: TelegramDeliveryJobRecord,
    decision: TelegramDeliveryDecision,
    provider_result_at: datetime,
    linearized_at: datetime,
    global_rate_limit_window_seconds: float,
) -> TelegramDeliveryDecision:
    """Persist both recent 429 evidence and any derived bot-wide deadline."""
    if (
        decision.reason != "telegram_rate_limited"
        or decision.destination_cooldown_until is None
    ):
        return decision

    destination_deadline = decision.destination_cooldown_until
    record.last_rate_limited_at = linearized_at
    record.last_rate_limit_until = destination_deadline
    window_seconds = max(0.001, float(global_rate_limit_window_seconds))
    window_start = linearized_at - timedelta(seconds=window_seconds)
    prior_records = list(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.id != int(record.id),
                    TelegramDeliveryJobRecord.bot_identity
                    == str(record.bot_identity),
                    TelegramDeliveryJobRecord.destination_key
                    != str(record.destination_key),
                    TelegramDeliveryJobRecord.last_rate_limited_at.is_not(None),
                    TelegramDeliveryJobRecord.last_rate_limited_at >= window_start,
                    TelegramDeliveryJobRecord.last_rate_limited_at <= linearized_at,
                    TelegramDeliveryJobRecord.last_rate_limit_until.is_not(None),
                )
                .order_by(
                    TelegramDeliveryJobRecord.last_rate_limited_at.desc(),
                    TelegramDeliveryJobRecord.id.desc(),
                )
            )
        ).scalars()
    )

    bot_deadlines = [
        deadline
        for prior in prior_records
        for deadline in (prior.last_rate_limit_until, prior.bot_cooldown_until)
        if deadline is not None
    ]
    if (
        record.bot_cooldown_until is not None
        and record.bot_cooldown_until > provider_result_at
    ):
        bot_deadlines.append(record.bot_cooldown_until)
    if not prior_records and not bot_deadlines:
        return decision

    bot_cooldown_until = max([destination_deadline, *bot_deadlines])
    record.bot_cooldown_until = bot_cooldown_until
    if record.next_retry_at is None or record.next_retry_at < bot_cooldown_until:
        record.next_retry_at = bot_cooldown_until
    return replace(
        decision,
        next_retry_at=record.next_retry_at,
        bot_cooldown_until=bot_cooldown_until,
    )


async def _persist_durable_runtime_gate_evidence(
    db: AsyncSession,
    *,
    record: TelegramDeliveryJobRecord,
    decision: TelegramDeliveryDecision,
    now: datetime,
) -> None:
    if decision.outcome == TelegramDeliveryOutcome.BOT_PAUSED:
        scope = "bot"
        bot_identity: str | None = str(record.bot_identity)
        gate_key = f"bot:{bot_identity}"
    elif decision.outcome == TelegramDeliveryOutcome.GATEWAY_PAUSED:
        scope = "gateway"
        bot_identity = None
        gate_key = "gateway:telegram"
    else:
        return
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
            state="blocked",
            version=0,
            attempt_count=0,
            attempt_history=[],
            resumed_job_ids=[],
            created_at=now,
            updated_at=now,
        )
        db.add(gate)
    gate.state = "blocked"
    gate.cooldown_until = None
    gate.reason_code = decision.reason
    gate.provider_status_code = record.provider_status_code
    gate.evidence_hash = hashlib.sha256(
        f"{gate_key}:{int(record.id)}:{int(record.lease_token or 0)}:"
        f"{record.provider_status_code}:{decision.reason}".encode("utf-8")
    ).hexdigest()
    gate.version = int(gate.version or 0) + 1
    gate.request_id = None
    gate.requested_by_hash = None
    gate.resumed_job_ids = []
    gate.preflight_evidence = None
    gate.resume_requested_at = None
    gate.database_applied_at = None
    gate.redis_applied_at = None
    gate.completed_at = None
    gate.last_error_class = None
    gate.last_error_detail = None
    gate.updated_at = now
    await db.flush()


async def resolve_telegram_delivery_result(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    worker_id: str,
    lease_token: int,
    result: TelegramGatewayResultLike,
    retry_after_safety_seconds: float,
    retry_base_seconds: float,
    retry_max_seconds: float,
    retry_jitter_ratio: float = 0.0,
    global_rate_limit_window_seconds: float = 2.0,
    feedback: TelegramDeliveryResultFeedback | None = None,
    recorded_outcome: TelegramDeliveryProviderOutcomeRecord | None = None,
    now: datetime | None = None,
) -> TelegramDeliveryDecision:
    _require_foreign(current_server)
    current_time = now or utc_now()
    record = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    normal_fence_valid = bool(
        record is not None
        and _enum_value(record.state) == TelegramDeliveryState.LEASED.value
        and record.worker_id == str(worker_id)
        and int(record.lease_token or 0) == int(lease_token)
        and record.dispatch_started_at is not None
    )
    recorded_fence_valid = bool(
        record is not None
        and recorded_outcome is not None
        and int(recorded_outcome.job_id) == int(job_id)
        and int(recorded_outcome.lease_token) == int(lease_token)
        and str(recorded_outcome.worker_id) == str(worker_id)
        and int(record.lease_token or 0) == int(lease_token)
        and record.dispatch_started_at is not None
        and record.worker_id in {None, str(worker_id)}
        and _enum_value(record.state)
        in {
            TelegramDeliveryState.LEASED.value,
            TelegramDeliveryState.AMBIGUOUS.value,
            TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value,
            TelegramDeliveryState.PENDING_RECONCILE.value,
        }
    )
    if not (normal_fence_valid or recorded_fence_valid):
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.STALE_LEASE,
            reason="stale_or_missing_lease",
        )

    _require_lifecycle_callback(
        record,
        feedback,
        reason="trade_result_delivery_feedback_required",
        admin_broadcast_reason="admin_broadcast_delivery_feedback_required",
        new_user_membership_reason="new_user_membership_delivery_feedback_required",
        market_notice_reason="market_notice_delivery_feedback_required",
        repeat_offer_response_reason="repeat_offer_response_delivery_feedback_required",
        notification_action_reason="notification_action_delivery_feedback_required",
        offer_success_reason="offer_success_delivery_feedback_required",
        callback_reason="callback_delivery_feedback_required",
    )
    await _acquire_dispatch_scope_locks(db, record=record)
    result_linearized_at = utc_now()

    contract_job = _record_to_contract(record)
    decision = apply_gateway_result(
        contract_job,
        result,
        now=current_time,
        retry_after_safety_seconds=retry_after_safety_seconds,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
        retry_jitter_ratio=retry_jitter_ratio,
    )
    record.state = contract_job.state
    record.next_retry_at = contract_job.next_retry_at
    record.worker_id = contract_job.worker_id
    record.lease_until = contract_job.lease_until
    record.last_error_class = contract_job.last_error_class
    record.last_error_message = contract_job.last_error_message
    record.telegram_message_id = contract_job.telegram_message_id
    record.provider_ok = _provider_ok(result)
    record.provider_status_code = result.status_code
    record.provider_error_code = _provider_error_code(result)
    record.provider_response = _sanitized_provider_response(result)
    record.last_retry_after_seconds = _raw_retry_after(result)
    record.rate_limit_probe = False
    decision = await _persist_durable_rate_limit_evidence(
        db,
        record=record,
        decision=decision,
        provider_result_at=current_time,
        linearized_at=result_linearized_at,
        global_rate_limit_window_seconds=global_rate_limit_window_seconds,
    )
    await _persist_durable_runtime_gate_evidence(
        db,
        record=record,
        decision=decision,
        now=current_time,
    )
    record.outcome_reason = decision.reason
    record.updated_at = max(current_time, result_linearized_at)
    if contract_job.state in {TelegramDeliveryState.SENT, TelegramDeliveryState.SENT_NOOP}:
        record.sent_at = current_time
    if contract_job.state in FINAL_DELIVERY_STATES:
        record.terminal_at = current_time
    if feedback is not None:
        await feedback(db, record, decision, current_time)
    await db.flush()
    return decision


async def apply_telegram_delivery_provider_outcome(
    db: AsyncSession,
    *,
    current_server: str,
    outcome_id: int,
    retry_after_safety_seconds: float,
    retry_base_seconds: float,
    retry_max_seconds: float,
    global_rate_limit_window_seconds: float,
    feedback: TelegramDeliveryResultFeedback,
    retry_jitter_ratio: float = 0.0,
    now: datetime | None = None,
) -> TelegramDeliveryDecision:
    """Apply one recorded provider fact and domain feedback atomically."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    outcome = (
        await db.execute(
            select(TelegramDeliveryProviderOutcomeRecord)
            .where(TelegramDeliveryProviderOutcomeRecord.id == int(outcome_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outcome is None:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.STALE_LEASE,
            reason="provider_outcome_missing",
        )
    if outcome.apply_state == TELEGRAM_PROVIDER_OUTCOME_APPLIED:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="provider_outcome_already_applied",
        )
    if outcome.apply_state != TELEGRAM_PROVIDER_OUTCOME_PENDING:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.QUARANTINED,
            reason="provider_outcome_quarantined",
        )

    result = _provider_outcome_to_gateway_result(outcome)
    decision = await resolve_telegram_delivery_result(
        db,
        current_server=current_server,
        job_id=int(outcome.job_id),
        worker_id=str(outcome.worker_id),
        lease_token=int(outcome.lease_token),
        result=result,
        retry_after_safety_seconds=retry_after_safety_seconds,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
        retry_jitter_ratio=retry_jitter_ratio,
        global_rate_limit_window_seconds=global_rate_limit_window_seconds,
        feedback=feedback,
        recorded_outcome=outcome,
        now=current_time,
    )
    if decision.outcome == TelegramDeliveryOutcome.STALE_LEASE:
        outcome.apply_state = TELEGRAM_PROVIDER_OUTCOME_QUARANTINED
        outcome.last_apply_error_class = "StaleProviderOutcomeFence"
        outcome.last_apply_error_message = decision.reason
    else:
        outcome.apply_state = TELEGRAM_PROVIDER_OUTCOME_APPLIED
        outcome.applied_at = current_time
        outcome.next_apply_at = None
        outcome.last_apply_error_class = None
        outcome.last_apply_error_message = None
    outcome.updated_at = current_time
    await db.flush()
    return decision


async def record_telegram_provider_outcome_apply_failure(
    db: AsyncSession,
    *,
    current_server: str,
    outcome_id: int,
    error: BaseException,
    retry_base_seconds: float = 1.0,
    retry_max_seconds: float = 30.0,
    now: datetime | None = None,
) -> bool:
    """Persist replay scheduling after an apply transaction rolled back."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    outcome = (
        await db.execute(
            select(TelegramDeliveryProviderOutcomeRecord)
            .where(TelegramDeliveryProviderOutcomeRecord.id == int(outcome_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outcome is None or outcome.apply_state != TELEGRAM_PROVIDER_OUTCOME_PENDING:
        return False
    outcome.apply_attempt_count = int(outcome.apply_attempt_count or 0) + 1
    exponent = min(max(0, outcome.apply_attempt_count - 1), 10)
    delay = min(
        max(0.1, float(retry_max_seconds)),
        max(0.1, float(retry_base_seconds)) * (2**exponent),
    )
    outcome.next_apply_at = current_time + timedelta(seconds=delay)
    outcome.last_apply_error_class = type(error).__name__[:120]
    outcome.last_apply_error_message = " ".join(str(error).split())[:500] or None
    outcome.updated_at = current_time
    await db.flush()
    return True


async def load_telegram_provider_outcome_backlog(
    db: AsyncSession,
    *,
    current_server: str,
    max_rows: int,
    now: datetime | None = None,
) -> TelegramProviderOutcomeBacklogReport:
    """Return bounded replay work plus complete pending age/count evidence."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    pending_count, oldest_created_at = (
        await db.execute(
            select(
                func.count(TelegramDeliveryProviderOutcomeRecord.id),
                func.min(TelegramDeliveryProviderOutcomeRecord.created_at),
            ).where(
                TelegramDeliveryProviderOutcomeRecord.apply_state
                == TELEGRAM_PROVIDER_OUTCOME_PENDING
            )
        )
    ).one()
    due_ids = tuple(
        int(value)
        for value in (
            await db.execute(
                select(TelegramDeliveryProviderOutcomeRecord.id)
                .where(
                    TelegramDeliveryProviderOutcomeRecord.apply_state
                    == TELEGRAM_PROVIDER_OUTCOME_PENDING,
                    or_(
                        TelegramDeliveryProviderOutcomeRecord.next_apply_at.is_(None),
                        TelegramDeliveryProviderOutcomeRecord.next_apply_at <= current_time,
                    ),
                )
                .order_by(
                    TelegramDeliveryProviderOutcomeRecord.next_apply_at.asc().nullsfirst(),
                    TelegramDeliveryProviderOutcomeRecord.created_at.asc(),
                    TelegramDeliveryProviderOutcomeRecord.id.asc(),
                )
                .limit(max(1, int(max_rows)))
            )
        ).scalars()
    )
    return TelegramProviderOutcomeBacklogReport(
        pending_count=int(pending_count or 0),
        oldest_created_at=oldest_created_at,
        due_outcome_ids=due_ids,
    )


async def recover_expired_telegram_delivery_leases(
    db: AsyncSession,
    *,
    current_server: str,
    max_rows: int,
    now: datetime | None = None,
) -> TelegramDeliveryLeaseRecoveryReport:
    _require_foreign(current_server)
    current_time = now or utc_now()
    stmt = (
        select(TelegramDeliveryJobRecord)
        .where(
            TelegramDeliveryJobRecord.state == TelegramDeliveryState.LEASED,
            TelegramDeliveryJobRecord.lease_until.is_not(None),
            TelegramDeliveryJobRecord.lease_until <= current_time,
            ~exists(
                select(TelegramDeliveryProviderOutcomeRecord.id).where(
                    TelegramDeliveryProviderOutcomeRecord.job_id
                    == TelegramDeliveryJobRecord.id,
                    TelegramDeliveryProviderOutcomeRecord.apply_state
                    == TELEGRAM_PROVIDER_OUTCOME_PENDING,
                )
            ),
        )
        .order_by(TelegramDeliveryJobRecord.lease_until.asc(), TelegramDeliveryJobRecord.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(max_rows)))
    )
    records = list((await db.execute(stmt)).scalars().all())
    released_unstarted = 0
    ambiguous_sends = 0
    pending_reconcile = 0
    for record in records:
        if record.dispatch_started_at is None:
            record.state = TelegramDeliveryState.PENDING_RETRY
            record.next_retry_at = current_time
            record.outcome_reason = "lease_expired_before_dispatch"
            released_unstarted += 1
        elif record.method in TELEGRAM_NON_IDEMPOTENT_SEND_METHODS:
            record.state = TelegramDeliveryState.AMBIGUOUS
            record.next_retry_at = None
            record.outcome_reason = "lease_expired_after_send_started"
            ambiguous_sends += 1
        else:
            record.state = TelegramDeliveryState.PENDING_RECONCILE
            record.next_retry_at = None
            record.outcome_reason = "lease_expired_after_idempotent_dispatch_started"
            pending_reconcile += 1
        record.worker_id = None
        record.lease_until = None
        record.last_error_class = "LeaseExpired"
        record.updated_at = current_time
    if records:
        await db.flush()
    return TelegramDeliveryLeaseRecoveryReport(
        released_unstarted=released_unstarted,
        ambiguous_sends=ambiguous_sends,
        pending_reconcile=pending_reconcile,
        job_ids=tuple(int(record.id) for record in records),
    )
