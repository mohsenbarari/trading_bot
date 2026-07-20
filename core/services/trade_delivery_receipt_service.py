"""Receipt-backed trade notification delivery primitives.

This module owns the trade delivery receipt lifecycle. It intentionally does
not route trade execution or send Telegram messages; later stages can build
workers on top of these guarded helpers.
"""
from __future__ import annotations

from collections.abc import Mapping
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as redis
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import NotificationCategory, NotificationLevel
from core.redis import pool
from core.utils import publish_user_event, utc_now
from models.notification import Notification
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from core.services.trade_notification_audience_service import (
    TELEGRAM_CHANNEL,
    WEBAPP_CHANNEL,
    TradeNotificationAudience,
    build_trade_completion_notification_audience,
)


logger = logging.getLogger(__name__)

TRADE_COMPLETED_EVENT_TYPE = "trade_completed"
WEBAPP_DESTINATION_SERVER = "iran"
TELEGRAM_DESTINATION_SERVER = "foreign"
TRADE_DELIVERY_SHORT_OUTAGE_MAX_SECONDS = 120
TRADE_DELIVERY_MEDIUM_OUTAGE_MAX_SECONDS = 3600
TRADE_DELIVERY_RETENTION_DAYS = 365
TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON = "expired_delivery_after_outage"
TRADE_DELIVERY_OUTAGE_SHORT = "short"
TRADE_DELIVERY_OUTAGE_MEDIUM = "medium"
TRADE_DELIVERY_OUTAGE_LONG = "long"

NON_TERMINAL_RECEIPT_STATUSES = {
    TradeDeliveryReceiptStatus.PENDING.value,
    TradeDeliveryReceiptStatus.PROCESSING.value,
    TradeDeliveryReceiptStatus.RETRY_PENDING.value,
}

ALLOWED_RECEIPT_TRANSITIONS: dict[str, set[str]] = {
    TradeDeliveryReceiptStatus.PENDING.value: {TradeDeliveryReceiptStatus.PROCESSING.value},
    TradeDeliveryReceiptStatus.RETRY_PENDING.value: {TradeDeliveryReceiptStatus.PROCESSING.value},
    TradeDeliveryReceiptStatus.PROCESSING.value: {
        TradeDeliveryReceiptStatus.SENT.value,
        TradeDeliveryReceiptStatus.RETRY_PENDING.value,
        TradeDeliveryReceiptStatus.SKIPPED.value,
        TradeDeliveryReceiptStatus.PERMANENT_FAILED.value,
    },
}


class ReceiptLifecycleError(ValueError):
    """Raised when a receipt lifecycle invariant would be violated."""


class ReceiptOwnershipError(PermissionError):
    """Raised when a server tries to mutate another server's receipt."""


@dataclass(frozen=True)
class ReceiptUpsertResult:
    receipt: TradeDeliveryReceipt
    created: bool
    changed: bool
    terminal_preserved: bool = False


@dataclass(frozen=True)
class ReceiptTransitionResult:
    receipt: TradeDeliveryReceipt
    changed: bool
    reason: str


@dataclass(frozen=True)
class WebAppNotificationRowResult:
    notification: Notification
    created: bool


@dataclass(frozen=True)
class WebAppReceiptDeliveryResult:
    receipt: TradeDeliveryReceipt
    notification: Notification
    notification_created: bool
    sent_changed: bool
    realtime_payload: dict[str, Any]


@dataclass(frozen=True)
class ReceiptOutageClassification:
    outage_class: str
    age_seconds: float
    should_skip_remote_delivery: bool
    reason: str | None = None


@dataclass(frozen=True)
class ReceiptRetentionReport:
    dry_run: bool
    retention_days: int
    cutoff: datetime
    terminal_deleted_count: int
    terminal_candidate_count: int
    non_terminal_old_count: int
    oldest_non_terminal_at: datetime | None
    alert_required: bool


@dataclass(frozen=True)
class ReceiptSkipResult:
    receipt: TradeDeliveryReceipt
    changed: bool
    outage_class: str
    age_seconds: float
    reason: str


@dataclass(frozen=True)
class TradeDeliveryIntentBatch:
    audience: TradeNotificationAudience
    receipts: tuple[TradeDeliveryReceipt, ...]


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_terminal_status(value: Any) -> bool:
    return _enum_value(value) in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES


def _normalize_status(value: Any) -> TradeDeliveryReceiptStatus:
    if isinstance(value, TradeDeliveryReceiptStatus):
        return value
    return TradeDeliveryReceiptStatus(str(getattr(value, "value", value)))


def _normalize_channel(value: Any) -> TradeDeliveryChannel:
    if isinstance(value, TradeDeliveryChannel):
        return value
    return TradeDeliveryChannel(str(getattr(value, "value", value)))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_event_created_at(receipt: TradeDeliveryReceipt | Any, *, fallback: datetime) -> datetime:
    raw_value = getattr(receipt, "event_created_at", None)
    if isinstance(raw_value, datetime):
        return _as_aware_utc(raw_value)
    return _as_aware_utc(fallback)


def _audit_payload_mapping(receipt: TradeDeliveryReceipt | Any) -> Mapping[str, Any]:
    payload = getattr(receipt, "audit_payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _receipt_offer_home_server(receipt: TradeDeliveryReceipt | Any) -> str | None:
    audit_payload = _audit_payload_mapping(receipt)
    candidates = [
        audit_payload.get("offer_home_server"),
        audit_payload.get("home_server"),
    ]
    extra_payload = audit_payload.get("extra_payload")
    if isinstance(extra_payload, Mapping):
        candidates.extend(
            [
                extra_payload.get("offer_home_server"),
                extra_payload.get("home_server"),
            ]
        )
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if normalized in {WEBAPP_DESTINATION_SERVER, TELEGRAM_DESTINATION_SERVER}:
            return normalized
    return None


def receipt_is_opposite_server_delivery(receipt: TradeDeliveryReceipt | Any) -> bool:
    offer_home_server = _receipt_offer_home_server(receipt)
    destination_server = str(getattr(receipt, "destination_server", "") or "").strip().lower()
    if offer_home_server is None or destination_server not in {WEBAPP_DESTINATION_SERVER, TELEGRAM_DESTINATION_SERVER}:
        return False
    return offer_home_server != destination_server


def classify_trade_delivery_outage(
    *,
    event_created_at: datetime,
    visible_at: datetime | None = None,
) -> tuple[str, float]:
    current_time = _as_aware_utc(visible_at or utc_now())
    event_time = _as_aware_utc(event_created_at)
    age_seconds = max(0.0, (current_time - event_time).total_seconds())
    if age_seconds <= TRADE_DELIVERY_SHORT_OUTAGE_MAX_SECONDS:
        return TRADE_DELIVERY_OUTAGE_SHORT, age_seconds
    if age_seconds <= TRADE_DELIVERY_MEDIUM_OUTAGE_MAX_SECONDS:
        return TRADE_DELIVERY_OUTAGE_MEDIUM, age_seconds
    return TRADE_DELIVERY_OUTAGE_LONG, age_seconds


def classify_receipt_outage_for_delivery(
    receipt: TradeDeliveryReceipt | Any,
    *,
    now: datetime | None = None,
) -> ReceiptOutageClassification:
    current_time = now or utc_now()
    outage_class, age_seconds = classify_trade_delivery_outage(
        event_created_at=_safe_event_created_at(receipt, fallback=current_time),
        visible_at=current_time,
    )
    should_skip = (
        receipt_is_opposite_server_delivery(receipt)
        and outage_class in {TRADE_DELIVERY_OUTAGE_MEDIUM, TRADE_DELIVERY_OUTAGE_LONG}
    )
    return ReceiptOutageClassification(
        outage_class=outage_class,
        age_seconds=age_seconds,
        should_skip_remote_delivery=should_skip,
        reason=TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON if should_skip else None,
    )


def _ensure_local_destination(receipt: Any, current_server: str) -> None:
    if str(getattr(receipt, "destination_server", "") or "") != str(current_server or ""):
        raise ReceiptOwnershipError("receipt_destination_server_mismatch")


async def _flush_if_available(db: AsyncSession | Any) -> None:
    flush = getattr(db, "flush", None)
    if flush is not None:
        await flush()


def _result_scalar_one_or_none(result: Any) -> Any:
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        first = getattr(scalar_result, "first", None)
        if callable(first):
            return first()
        all_rows = getattr(scalar_result, "all", None)
        if callable(all_rows):
            rows = all_rows()
            return rows[0] if rows else None
    return None


def _result_scalars_all(result: Any) -> list[Any]:
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        all_rows = getattr(scalar_result, "all", None)
        if callable(all_rows):
            return list(all_rows())
    return []


def _result_scalar(result: Any) -> Any:
    scalar = getattr(result, "scalar", None)
    if callable(scalar):
        return scalar()
    scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
    if callable(scalar_one_or_none):
        return scalar_one_or_none()
    one = getattr(result, "one", None)
    if callable(one):
        row = one()
        return row[0] if isinstance(row, tuple) and row else row
    return None


def receipt_dedupe_key(
    *,
    event_type: str,
    channel: Any,
    trade_number: int,
    recipient_user_id: int,
) -> str:
    return f"{event_type}:{_normalize_channel(channel).value}:{int(trade_number)}:{int(recipient_user_id)}"


def trade_completed_receipt_dedupe_key(
    *,
    channel: Any,
    trade_number: int,
    recipient_user_id: int,
) -> str:
    return receipt_dedupe_key(
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        channel=channel,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
    )


def webapp_notification_dedupe_key(*, trade_number: int, recipient_user_id: int) -> str:
    return trade_completed_receipt_dedupe_key(
        channel=TradeDeliveryChannel.WEBAPP,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
    )


async def load_receipt_by_identity(
    db: AsyncSession,
    *,
    event_type: str,
    trade_number: int,
    recipient_user_id: int,
    channel: Any,
    for_update: bool = False,
) -> TradeDeliveryReceipt | None:
    stmt = (
        select(TradeDeliveryReceipt)
        .where(
            TradeDeliveryReceipt.event_type == event_type,
            TradeDeliveryReceipt.trade_number == int(trade_number),
            TradeDeliveryReceipt.recipient_user_id == int(recipient_user_id),
            TradeDeliveryReceipt.channel == _normalize_channel(channel),
        )
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return _result_scalar_one_or_none(result)


async def upsert_trade_delivery_receipt(
    db: AsyncSession,
    *,
    event_type: str,
    trade_number: int,
    recipient_user_id: int,
    recipient_role: str,
    channel: Any,
    destination_server: str,
    required: bool,
    reason: str | None = None,
    trade_id: int | None = None,
    offer_id: int | None = None,
    event_created_at: datetime | None = None,
    audit_payload: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> ReceiptUpsertResult:
    """Create or update a receipt by event/trade/recipient/channel identity.

    The function never commits. Missing required channels become ``pending``;
    missing non-required channels become terminal ``not_required``.
    """
    normalized_channel = _normalize_channel(channel)
    receipt = await load_receipt_by_identity(
        db,
        event_type=event_type,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
        channel=normalized_channel,
        for_update=True,
    )
    current_time = now or utc_now()
    requested_status = (
        TradeDeliveryReceiptStatus.PENDING
        if required
        else TradeDeliveryReceiptStatus.NOT_REQUIRED
    )
    dedupe_key = receipt_dedupe_key(
        event_type=event_type,
        channel=normalized_channel,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
    )

    if receipt is None:
        receipt = TradeDeliveryReceipt(
            event_type=event_type,
            dedupe_key=dedupe_key,
            trade_id=trade_id,
            trade_number=int(trade_number),
            offer_id=offer_id,
            recipient_user_id=int(recipient_user_id),
            recipient_role=recipient_role,
            channel=normalized_channel,
            destination_server=destination_server,
            status=requested_status,
            reason=reason,
            audit_payload=dict(audit_payload or {}) or None,
            event_created_at=event_created_at or current_time,
            terminal_at=current_time if requested_status == TradeDeliveryReceiptStatus.NOT_REQUIRED else None,
            created_at=current_time,
            updated_at=current_time,
        )
        db.add(receipt)
        await _flush_if_available(db)
        return ReceiptUpsertResult(receipt=receipt, created=True, changed=True)

    if str(getattr(receipt, "destination_server", "") or "") != str(destination_server or ""):
        raise ReceiptLifecycleError("receipt_destination_server_is_immutable")
    if str(getattr(receipt, "dedupe_key", "") or "") != dedupe_key:
        raise ReceiptLifecycleError("receipt_dedupe_key_mismatch")

    if _is_terminal_status(getattr(receipt, "status", None)) and requested_status.value in NON_TERMINAL_RECEIPT_STATUSES:
        return ReceiptUpsertResult(receipt=receipt, created=False, changed=False, terminal_preserved=True)

    changed = False
    for field, value in {
        "trade_id": trade_id,
        "offer_id": offer_id,
        "recipient_role": recipient_role,
        "reason": reason,
        "audit_payload": dict(audit_payload or {}) or None,
    }.items():
        if value is not None and getattr(receipt, field, None) != value:
            setattr(receipt, field, value)
            changed = True

    if not _is_terminal_status(getattr(receipt, "status", None)) and _enum_value(getattr(receipt, "status", None)) != requested_status.value:
        receipt.status = requested_status
        receipt.terminal_at = current_time if requested_status == TradeDeliveryReceiptStatus.NOT_REQUIRED else None
        changed = True

    if changed:
        receipt.updated_at = current_time
        await _flush_if_available(db)
    return ReceiptUpsertResult(receipt=receipt, created=False, changed=changed)


async def persist_trade_completion_delivery_intents(
    db: AsyncSession,
    trade: Any,
    *,
    now: datetime | None = None,
) -> TradeDeliveryIntentBatch:
    """Persist every recipient/channel obligation without performing delivery.

    The caller owns the transaction that creates the completed Trade.  This
    function never commits and never calls Redis, Telegram, Web Push, or a
    realtime provider, so a failed intent write rolls the Trade back as one
    atomic unit.
    """

    audience = await build_trade_completion_notification_audience(db, trade)
    if audience.skipped_reason:
        raise ReceiptLifecycleError(
            f"trade_delivery_intent_audience_invalid:{audience.skipped_reason}"
        )
    if audience.trade_number is None:
        raise ReceiptLifecycleError("trade_delivery_intent_trade_number_missing")

    current_time = now or utc_now()
    event_created_at = getattr(trade, "created_at", None) or current_time
    receipts: list[TradeDeliveryReceipt] = []
    for recipient in audience.recipients:
        payload = dict(recipient.extra_payload or {})
        payload.setdefault("trade_id", audience.trade_id)
        payload.setdefault("trade_number", int(audience.trade_number))
        payload.setdefault("offer_id", audience.offer_id)
        payload.setdefault("offer_home_server", audience.offer_home_server)
        payload.setdefault("recipient_user_id", int(recipient.recipient_user_id))
        payload.setdefault("recipient_role", recipient.recipient_role)
        payload.setdefault("principal_user_id", recipient.principal_user_id)
        payload.setdefault("side", recipient.side)

        for requirement in recipient.channel_requirements:
            channel = str(requirement.channel or "").strip().lower()
            if channel == WEBAPP_CHANNEL:
                normalized_channel = TradeDeliveryChannel.WEBAPP
                message = requirement.message or recipient.webapp_message
                audit_payload: dict[str, Any] = {
                    "message": message,
                    "extra_payload": payload,
                }
            elif channel == TELEGRAM_CHANNEL:
                normalized_channel = TradeDeliveryChannel.TELEGRAM
                audit_payload = {
                    "message": requirement.message,
                    "telegram_id_at_audience_build": requirement.telegram_id,
                    "extra_payload": payload,
                }
            else:
                raise ReceiptLifecycleError(
                    f"trade_delivery_intent_channel_unknown:{channel or 'missing'}"
                )

            result = await upsert_trade_delivery_receipt(
                db,
                event_type=audience.event_type,
                trade_number=int(audience.trade_number),
                recipient_user_id=int(recipient.recipient_user_id),
                recipient_role=recipient.recipient_role,
                channel=normalized_channel,
                destination_server=requirement.destination_server,
                required=bool(requirement.required),
                reason=requirement.reason,
                trade_id=audience.trade_id,
                offer_id=audience.offer_id,
                event_created_at=event_created_at,
                audit_payload=audit_payload,
                now=current_time,
            )
            receipts.append(result.receipt)

    if not receipts:
        raise ReceiptLifecycleError("trade_delivery_intent_set_empty")
    return TradeDeliveryIntentBatch(
        audience=audience,
        receipts=tuple(receipts),
    )


def transition_receipt_status(
    receipt: TradeDeliveryReceipt | Any,
    target_status: Any,
    *,
    current_server: str,
    now: datetime | None = None,
    reason: str | None = None,
    notification_id: int | None = None,
    telegram_message_id: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    next_retry_at: datetime | None = None,
    allow_lease_recovery: bool = False,
) -> ReceiptTransitionResult:
    _ensure_local_destination(receipt, current_server)
    current_status_value = _enum_value(getattr(receipt, "status", None))
    target = _normalize_status(target_status)
    target_value = target.value
    current_time = now or utc_now()

    if current_status_value == target_value:
        return ReceiptTransitionResult(receipt=receipt, changed=False, reason="already_in_target_state")

    if current_status_value in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES:
        raise ReceiptLifecycleError("terminal_receipt_cannot_reopen")

    allowed_targets = set(ALLOWED_RECEIPT_TRANSITIONS.get(current_status_value, set()))
    if allow_lease_recovery and current_status_value == TradeDeliveryReceiptStatus.PROCESSING.value:
        allowed_targets.add(TradeDeliveryReceiptStatus.PENDING.value)
        allowed_targets.add(TradeDeliveryReceiptStatus.RETRY_PENDING.value)
    if target_value not in allowed_targets:
        raise ReceiptLifecycleError(f"invalid_receipt_transition:{current_status_value}->{target_value}")

    receipt.status = target
    receipt.updated_at = current_time
    if reason is not None:
        receipt.reason = reason
    if notification_id is not None:
        receipt.notification_id = notification_id
    if telegram_message_id is not None:
        receipt.telegram_message_id = telegram_message_id
    if error_class is not None:
        receipt.last_error_class = error_class
    if error_message is not None:
        receipt.last_error = error_message
    if next_retry_at is not None:
        receipt.next_retry_at = next_retry_at

    if target == TradeDeliveryReceiptStatus.PROCESSING:
        receipt.attempt_count = int(getattr(receipt, "attempt_count", 0) or 0) + 1
    if target != TradeDeliveryReceiptStatus.PROCESSING:
        receipt.worker_id = None
        receipt.lease_until = None
    if target == TradeDeliveryReceiptStatus.SENT:
        receipt.sent_at = current_time
    if target_value in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES:
        receipt.terminal_at = current_time

    return ReceiptTransitionResult(receipt=receipt, changed=True, reason="transition_applied")


def build_claim_receipt_statement(
    *,
    destination_server: str,
    worker_id: str,
    lease_until: datetime,
    now: datetime | None = None,
    channel: Any | None = None,
):
    current_time = now or utc_now()
    filters = [
        TradeDeliveryReceipt.destination_server == destination_server,
        TradeDeliveryReceipt.status.in_(
            [TradeDeliveryReceiptStatus.PENDING, TradeDeliveryReceiptStatus.RETRY_PENDING]
        ),
        TradeDeliveryReceipt.worker_id.is_(None),
        TradeDeliveryReceipt.lease_until.is_(None),
        or_(
            TradeDeliveryReceipt.next_retry_at.is_(None),
            TradeDeliveryReceipt.next_retry_at <= current_time,
        ),
    ]
    if channel is not None:
        filters.append(TradeDeliveryReceipt.channel == _normalize_channel(channel))

    return (
        select(TradeDeliveryReceipt)
        .where(and_(*filters))
        .order_by(TradeDeliveryReceipt.next_retry_at.asc().nullsfirst(), TradeDeliveryReceipt.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def build_claim_receipt_by_identity_statement(
    *,
    event_type: str,
    trade_number: int,
    recipient_user_id: int,
    channel: Any,
    destination_server: str,
    worker_id: str,
    lease_until: datetime,
    now: datetime | None = None,
):
    current_time = now or utc_now()
    normalized_channel = _normalize_channel(channel)
    return (
        select(TradeDeliveryReceipt)
        .where(
            TradeDeliveryReceipt.event_type == event_type,
            TradeDeliveryReceipt.trade_number == int(trade_number),
            TradeDeliveryReceipt.recipient_user_id == int(recipient_user_id),
            TradeDeliveryReceipt.channel == normalized_channel,
            TradeDeliveryReceipt.destination_server == destination_server,
            TradeDeliveryReceipt.status.in_(
                [TradeDeliveryReceiptStatus.PENDING, TradeDeliveryReceiptStatus.RETRY_PENDING]
            ),
            TradeDeliveryReceipt.worker_id.is_(None),
            TradeDeliveryReceipt.lease_until.is_(None),
            or_(
                TradeDeliveryReceipt.next_retry_at.is_(None),
                TradeDeliveryReceipt.next_retry_at <= current_time,
            ),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def claim_next_receipt_for_delivery(
    db: AsyncSession,
    *,
    destination_server: str,
    worker_id: str,
    lease_seconds: int = 30,
    now: datetime | None = None,
    channel: Any | None = None,
) -> TradeDeliveryReceipt | None:
    current_time = now or utc_now()
    stmt = build_claim_receipt_statement(
        destination_server=destination_server,
        worker_id=worker_id,
        lease_until=current_time + timedelta(seconds=max(1, int(lease_seconds))),
        now=current_time,
        channel=channel,
    )
    result = await db.execute(stmt)
    receipt = _result_scalar_one_or_none(result)
    if receipt is None:
        return None
    transition_receipt_status(
        receipt,
        TradeDeliveryReceiptStatus.PROCESSING,
        current_server=destination_server,
        now=current_time,
    )
    receipt.worker_id = worker_id
    receipt.lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    await _flush_if_available(db)
    return receipt


async def claim_receipt_by_identity_for_delivery(
    db: AsyncSession,
    *,
    event_type: str,
    trade_number: int,
    recipient_user_id: int,
    channel: Any,
    destination_server: str,
    worker_id: str,
    lease_seconds: int = 30,
    now: datetime | None = None,
) -> TradeDeliveryReceipt | None:
    current_time = now or utc_now()
    stmt = build_claim_receipt_by_identity_statement(
        event_type=event_type,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
        channel=channel,
        destination_server=destination_server,
        worker_id=worker_id,
        lease_until=current_time + timedelta(seconds=max(1, int(lease_seconds))),
        now=current_time,
    )
    result = await db.execute(stmt)
    receipt = _result_scalar_one_or_none(result)
    if receipt is None:
        return None
    transition_receipt_status(
        receipt,
        TradeDeliveryReceiptStatus.PROCESSING,
        current_server=destination_server,
        now=current_time,
    )
    receipt.worker_id = worker_id
    receipt.lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    await _flush_if_available(db)
    return receipt


def build_recover_expired_leases_statement(
    *,
    destination_server: str,
    now: datetime | None = None,
    max_rows: int = 100,
):
    current_time = now or utc_now()
    return (
        select(TradeDeliveryReceipt)
        .where(
            TradeDeliveryReceipt.destination_server == destination_server,
            TradeDeliveryReceipt.status == TradeDeliveryReceiptStatus.PROCESSING,
            TradeDeliveryReceipt.lease_until.is_not(None),
            TradeDeliveryReceipt.lease_until <= current_time,
        )
        .order_by(TradeDeliveryReceipt.lease_until.asc(), TradeDeliveryReceipt.id.asc())
        .limit(max(1, int(max_rows)))
        .with_for_update(skip_locked=True)
    )


async def recover_expired_local_leases(
    db: AsyncSession,
    *,
    destination_server: str,
    now: datetime | None = None,
    max_rows: int = 100,
) -> list[TradeDeliveryReceipt]:
    current_time = now or utc_now()
    result = await db.execute(
        build_recover_expired_leases_statement(
            destination_server=destination_server,
            now=current_time,
            max_rows=max_rows,
        )
    )
    receipts = _result_scalars_all(result)
    for receipt in receipts:
        transition_receipt_status(
            receipt,
            TradeDeliveryReceiptStatus.RETRY_PENDING,
            current_server=destination_server,
            now=current_time,
            reason="lease_expired",
            next_retry_at=current_time,
            allow_lease_recovery=True,
        )
    if receipts:
        await _flush_if_available(db)
    return receipts


async def skip_receipt_after_outage_if_needed(
    db: AsyncSession,
    receipt: TradeDeliveryReceipt,
    *,
    current_server: str,
    now: datetime | None = None,
) -> ReceiptSkipResult | None:
    classification = classify_receipt_outage_for_delivery(receipt, now=now)
    if not classification.should_skip_remote_delivery:
        return None
    result = transition_receipt_status(
        receipt,
        TradeDeliveryReceiptStatus.SKIPPED,
        current_server=current_server,
        now=now,
        reason=TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON,
        error_class="TradeDeliveryOutagePolicy",
        error_message=f"{classification.outage_class}_outage_remote_delivery_expired",
    )
    await _flush_if_available(db)
    return ReceiptSkipResult(
        receipt=receipt,
        changed=result.changed,
        outage_class=classification.outage_class,
        age_seconds=classification.age_seconds,
        reason=TRADE_DELIVERY_EXPIRED_AFTER_OUTAGE_REASON,
    )


def build_terminal_receipt_retention_cleanup_statement(
    *,
    cutoff: datetime,
    limit: int = 1000,
):
    cleanup_targets = (
        select(TradeDeliveryReceipt.id)
        .where(
            TradeDeliveryReceipt.status.in_(list(TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES)),
            TradeDeliveryReceipt.terminal_at.is_not(None),
            TradeDeliveryReceipt.terminal_at < cutoff,
        )
        .order_by(TradeDeliveryReceipt.terminal_at.asc(), TradeDeliveryReceipt.id.asc())
        .limit(max(1, int(limit or 1)))
        .cte("terminal_trade_delivery_receipts_for_cleanup")
    )
    return (
        delete(TradeDeliveryReceipt)
        .where(TradeDeliveryReceipt.id.in_(select(cleanup_targets.c.id)))
        .returning(TradeDeliveryReceipt.id)
    )


async def audit_trade_delivery_receipt_retention(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    retention_days: int = TRADE_DELIVERY_RETENTION_DAYS,
) -> ReceiptRetentionReport:
    current_time = now or utc_now()
    cutoff = _as_aware_utc(current_time) - timedelta(days=max(1, int(retention_days)))
    terminal_result = await db.execute(
        select(func.count(TradeDeliveryReceipt.id)).where(
            TradeDeliveryReceipt.status.in_(list(TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES)),
            TradeDeliveryReceipt.terminal_at.is_not(None),
            TradeDeliveryReceipt.terminal_at < cutoff,
        )
    )
    non_terminal_result = await db.execute(
        select(func.count(TradeDeliveryReceipt.id), func.min(TradeDeliveryReceipt.event_created_at)).where(
            TradeDeliveryReceipt.status.in_(list(NON_TERMINAL_RECEIPT_STATUSES)),
            TradeDeliveryReceipt.event_created_at < cutoff,
        )
    )
    terminal_count = _coerce_int(_result_scalar(terminal_result)) or 0
    non_terminal_count, oldest_non_terminal_at = non_terminal_result.one()
    return ReceiptRetentionReport(
        dry_run=True,
        retention_days=max(1, int(retention_days)),
        cutoff=cutoff,
        terminal_deleted_count=0,
        terminal_candidate_count=_coerce_int(terminal_count) or 0,
        non_terminal_old_count=_coerce_int(non_terminal_count) or 0,
        oldest_non_terminal_at=oldest_non_terminal_at,
        alert_required=bool(_coerce_int(non_terminal_count) or 0),
    )


async def cleanup_terminal_trade_delivery_receipts(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    retention_days: int = TRADE_DELIVERY_RETENTION_DAYS,
    limit: int = 1000,
    dry_run: bool = True,
) -> ReceiptRetentionReport:
    audit_report = await audit_trade_delivery_receipt_retention(
        db,
        now=now,
        retention_days=retention_days,
    )
    if audit_report.alert_required:
        logger.warning(
            "Old non-terminal trade delivery receipts require operator review before retention cleanup",
            extra={
                "event": "trade_delivery_receipt.retention_non_terminal_old",
                "non_terminal_old_count": audit_report.non_terminal_old_count,
                "oldest_non_terminal_at": audit_report.oldest_non_terminal_at,
                "cutoff": audit_report.cutoff,
            },
        )
    if dry_run:
        return audit_report

    rows = list(
        (
            await db.execute(
                select(TradeDeliveryReceipt)
                .where(
                    TradeDeliveryReceipt.status.in_(
                        list(TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES)
                    ),
                    TradeDeliveryReceipt.terminal_at.is_not(None),
                    TradeDeliveryReceipt.terminal_at < audit_report.cutoff,
                )
                .order_by(
                    TradeDeliveryReceipt.terminal_at.asc(),
                    TradeDeliveryReceipt.id.asc(),
                )
                .limit(max(1, int(limit or 1)))
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
    )
    for row in rows:
        await db.delete(row)
    return ReceiptRetentionReport(
        dry_run=False,
        retention_days=audit_report.retention_days,
        cutoff=audit_report.cutoff,
        terminal_deleted_count=len(rows),
        terminal_candidate_count=audit_report.terminal_candidate_count,
        non_terminal_old_count=audit_report.non_terminal_old_count,
        oldest_non_terminal_at=audit_report.oldest_non_terminal_at,
        alert_required=audit_report.alert_required,
    )


async def load_notification_by_dedupe_key(
    db: AsyncSession,
    dedupe_key: str,
    *,
    for_update: bool = False,
) -> Notification | None:
    stmt = select(Notification).where(Notification.dedupe_key == dedupe_key).limit(1)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return _result_scalar_one_or_none(result)


async def create_or_load_webapp_notification_no_commit(
    db: AsyncSession,
    *,
    user_id: int,
    message: str,
    dedupe_key: str,
    extra_payload: Mapping[str, Any] | None = None,
    level: NotificationLevel = NotificationLevel.SUCCESS,
    category: NotificationCategory = NotificationCategory.TRADE,
) -> WebAppNotificationRowResult:
    existing = await load_notification_by_dedupe_key(db, dedupe_key, for_update=True)
    if existing is not None:
        return WebAppNotificationRowResult(notification=existing, created=False)

    notification = Notification(
        user_id=int(user_id),
        message=message,
        is_read=False,
        level=level,
        category=category,
        dedupe_key=dedupe_key,
        extra_payload=dict(extra_payload or {}) or None,
    )

    try:
        async with db.begin_nested():
            db.add(notification)
            await _flush_if_available(db)
    except IntegrityError:
        existing = await load_notification_by_dedupe_key(db, dedupe_key, for_update=True)
        if existing is None:
            raise
        return WebAppNotificationRowResult(notification=existing, created=False)

    return WebAppNotificationRowResult(notification=notification, created=True)


def notification_realtime_payload(notification: Notification | Any) -> dict[str, Any]:
    level = getattr(notification, "level", None)
    category = getattr(notification, "category", None)
    payload = {
        "id": getattr(notification, "id", None),
        "message": getattr(notification, "message", ""),
        "is_read": bool(getattr(notification, "is_read", False)),
        "created_at": getattr(notification, "created_at", None).isoformat()
        if getattr(notification, "created_at", None)
        else None,
        "level": getattr(level, "value", level),
        "category": getattr(category, "value", category),
        "dedupe_key": getattr(notification, "dedupe_key", None),
        "extra_payload": getattr(notification, "extra_payload", None),
    }
    extra_payload = getattr(notification, "extra_payload", None)
    if isinstance(extra_payload, Mapping):
        payload.update(extra_payload)
    return payload


async def mark_webapp_receipt_sent_with_notification(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt,
    notification: Notification,
    current_server: str = WEBAPP_DESTINATION_SERVER,
    now: datetime | None = None,
) -> ReceiptTransitionResult:
    if _normalize_channel(getattr(receipt, "channel", None)) != TradeDeliveryChannel.WEBAPP:
        raise ReceiptLifecycleError("receipt_channel_is_not_webapp")
    notification_id = _coerce_int(getattr(notification, "id", None))
    if notification_id is None:
        raise ReceiptLifecycleError("notification_id_required")

    current_status = _enum_value(getattr(receipt, "status", None))
    if current_status == TradeDeliveryReceiptStatus.SENT.value:
        if _coerce_int(getattr(receipt, "notification_id", None)) == notification_id:
            return ReceiptTransitionResult(receipt=receipt, changed=False, reason="already_sent")
        raise ReceiptLifecycleError("sent_receipt_notification_mismatch")

    result = transition_receipt_status(
        receipt,
        TradeDeliveryReceiptStatus.SENT,
        current_server=current_server,
        now=now,
        notification_id=notification_id,
        reason=getattr(receipt, "reason", None) or "webapp_notification_persisted",
    )
    await _flush_if_available(db)
    return result


async def complete_webapp_receipt_with_notification(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt,
    message: str,
    extra_payload: Mapping[str, Any] | None = None,
    current_server: str = WEBAPP_DESTINATION_SERVER,
    now: datetime | None = None,
) -> WebAppReceiptDeliveryResult:
    _ensure_local_destination(receipt, current_server)
    if _normalize_channel(getattr(receipt, "channel", None)) != TradeDeliveryChannel.WEBAPP:
        raise ReceiptLifecycleError("receipt_channel_is_not_webapp")

    notification_key = webapp_notification_dedupe_key(
        trade_number=int(getattr(receipt, "trade_number")),
        recipient_user_id=int(getattr(receipt, "recipient_user_id")),
    )
    notification_result = await create_or_load_webapp_notification_no_commit(
        db,
        user_id=int(getattr(receipt, "recipient_user_id")),
        message=message,
        dedupe_key=notification_key,
        extra_payload={
            **dict(extra_payload or {}),
            "delivery_receipt_id": getattr(receipt, "id", None),
        },
    )
    transition_result = await mark_webapp_receipt_sent_with_notification(
        db,
        receipt=receipt,
        notification=notification_result.notification,
        current_server=current_server,
        now=now,
    )
    return WebAppReceiptDeliveryResult(
        receipt=receipt,
        notification=notification_result.notification,
        notification_created=notification_result.created,
        sent_changed=transition_result.changed,
        realtime_payload=notification_realtime_payload(notification_result.notification),
    )


async def publish_webapp_notification_after_commit(
    *,
    user_id: int,
    notification: Notification,
    extra_payload: Mapping[str, Any] | None = None,
) -> None:
    payload = notification_realtime_payload(notification)
    if extra_payload:
        payload.update(dict(extra_payload))

    try:
        async with redis.Redis(connection_pool=pool) as redis_client:
            await redis_client.incr(f"user:{int(user_id)}:unread_count")
    except Exception:
        pass

    await publish_user_event(int(user_id), "message", payload)
    try:
        from core.web_push import schedule_notification_web_push

        schedule_notification_web_push(getattr(notification, "id", None), payload.get("extra_payload"))
    except Exception:
        pass
