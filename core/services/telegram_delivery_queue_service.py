"""Repository boundary for the foreign-local shared Telegram execution queue.

Every function is transaction-neutral: callers own commit/rollback. Claim and
fenced result persistence are intentionally separate so no database transaction
is held open while calling Telegram.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.telegram_delivery_queue_contract import (
    CLAIMABLE_DELIVERY_STATES,
    FINAL_DELIVERY_STATES,
    MINIMUM_LEASE_MARGIN_SECONDS,
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
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord


TELEGRAM_DELIVERY_QUEUE_WORKER_ID = "telegram-delivery-queue-v1"
TELEGRAM_PRIMARY_BOT_IDENTITY = "primary"
TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY = "channel_editor"
SUPPORTED_TELEGRAM_BOT_IDENTITIES = frozenset(
    {TELEGRAM_PRIMARY_BOT_IDENTITY, TELEGRAM_CHANNEL_EDITOR_BOT_IDENTITY}
)
SUPPORTED_TELEGRAM_QUEUE_METHODS = frozenset(
    {
        "answerCallbackQuery",
        "editMessageReplyMarkup",
        "editMessageText",
        "sendMessage",
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


def _require_foreign(current_server: str) -> None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramDeliveryQueueSurfaceError("telegram_delivery_jobs_are_foreign_local")


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
    return parsed if parsed > 0 else None


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
        if "retry_after" in parameters and isinstance(
            parameters["retry_after"], (int, float, str)
        ):
            safe_parameters["retry_after"] = parameters["retry_after"]
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
        delivery_deadline_at=record.delivery_deadline_at,
        eligible_at=record.eligible_at,
        freshness_deadline_at=record.freshness_deadline_at,
        campaign_id=record.campaign_id,
        state=TelegramDeliveryState(_enum_value(record.state)),
        attempt_count=int(record.attempt_count or 0),
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
        "campaign_id": normalized_campaign_id,
        "run_id": normalized_run_id,
        "state": TelegramDeliveryState.PENDING,
        "attempt_count": 0,
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
    now: datetime | None = None,
) -> TelegramDeliveryJobRecord | None:
    _require_foreign(current_server)
    lane_identity = str(bot_identity or "").strip()
    if lane_identity not in SUPPORTED_TELEGRAM_BOT_IDENTITIES:
        raise TelegramDeliveryQueueValidationError("telegram_bot_identity_not_allowlisted")
    if float(lease_seconds) < float(request_timeout_seconds) + MINIMUM_LEASE_MARGIN_SECONDS:
        raise TelegramDeliveryQueueValidationError("lease_must_cover_request_timeout_plus_margin")
    current_time = now or utc_now()
    trade_overdue = (
        (TelegramDeliveryJobRecord.action_kind == TelegramDeliveryAction.TRADE_RESULT)
        & TelegramDeliveryJobRecord.delivery_deadline_at.is_not(None)
        & (TelegramDeliveryJobRecord.delivery_deadline_at <= current_time)
    )
    effective_priority = case((trade_overdue, 0), else_=TelegramDeliveryJobRecord.priority)
    effective_rank = case((trade_overdue, 1), else_=TelegramDeliveryJobRecord.priority_rank)
    stmt = (
        select(TelegramDeliveryJobRecord)
        .where(
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
        )
        .order_by(
            effective_priority.asc(),
            effective_rank.asc(),
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
    record.dispatch_started_at = current_time
    record.updated_at = current_time
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
    if record is None or (
        _enum_value(record.state) != TelegramDeliveryState.LEASED.value
        or record.worker_id != str(worker_id)
        or int(record.lease_token or 0) != int(lease_token)
        or record.dispatch_started_at is None
    ):
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.STALE_LEASE,
            reason="stale_or_missing_lease",
        )

    contract_job = _record_to_contract(record)
    decision = apply_gateway_result(
        contract_job,
        result,
        now=current_time,
        retry_after_safety_seconds=retry_after_safety_seconds,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
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
    record.outcome_reason = decision.reason
    record.updated_at = current_time
    if contract_job.state in {TelegramDeliveryState.SENT, TelegramDeliveryState.SENT_NOOP}:
        record.sent_at = current_time
    if contract_job.state in FINAL_DELIVERY_STATES:
        record.terminal_at = current_time
    await db.flush()
    return decision


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
        elif record.method == "sendMessage":
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
