"""Receipt-backed trade notification delivery primitives.

This module owns the trade delivery receipt lifecycle. It intentionally does
not route trade execution or send Telegram messages; later stages can build
workers on top of these guarded helpers.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import redis.asyncio as redis
from sqlalchemy import and_, or_, select, update
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


TRADE_COMPLETED_EVENT_TYPE = "trade_completed"
WEBAPP_DESTINATION_SERVER = "iran"
TELEGRAM_DESTINATION_SERVER = "foreign"

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
        or_(
            TradeDeliveryReceipt.next_retry_at.is_(None),
            TradeDeliveryReceipt.next_retry_at <= current_time,
        ),
    ]
    if channel is not None:
        filters.append(TradeDeliveryReceipt.channel == _normalize_channel(channel))

    claimable = (
        select(TradeDeliveryReceipt.id)
        .where(and_(*filters))
        .order_by(TradeDeliveryReceipt.next_retry_at.asc().nullsfirst(), TradeDeliveryReceipt.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
        .cte("claimable_trade_delivery_receipt")
    )
    return (
        update(TradeDeliveryReceipt)
        .where(TradeDeliveryReceipt.id == select(claimable.c.id).scalar_subquery())
        .values(
            status=TradeDeliveryReceiptStatus.PROCESSING,
            worker_id=worker_id,
            lease_until=lease_until,
            updated_at=current_time,
            attempt_count=TradeDeliveryReceipt.attempt_count + 1,
        )
        .returning(TradeDeliveryReceipt)
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
    return _result_scalar_one_or_none(result)


def build_recover_expired_leases_statement(
    *,
    destination_server: str,
    now: datetime | None = None,
    max_rows: int = 100,
):
    current_time = now or utc_now()
    expired = (
        select(TradeDeliveryReceipt.id)
        .where(
            TradeDeliveryReceipt.destination_server == destination_server,
            TradeDeliveryReceipt.status == TradeDeliveryReceiptStatus.PROCESSING,
            TradeDeliveryReceipt.lease_until.is_not(None),
            TradeDeliveryReceipt.lease_until <= current_time,
        )
        .order_by(TradeDeliveryReceipt.lease_until.asc(), TradeDeliveryReceipt.id.asc())
        .limit(max(1, int(max_rows)))
        .with_for_update(skip_locked=True)
        .cte("expired_trade_delivery_receipts")
    )
    return (
        update(TradeDeliveryReceipt)
        .where(TradeDeliveryReceipt.id.in_(select(expired.c.id)))
        .values(
            status=TradeDeliveryReceiptStatus.RETRY_PENDING,
            worker_id=None,
            lease_until=None,
            next_retry_at=current_time,
            reason="lease_expired",
            updated_at=current_time,
        )
        .returning(TradeDeliveryReceipt)
    )


async def recover_expired_local_leases(
    db: AsyncSession,
    *,
    destination_server: str,
    now: datetime | None = None,
    max_rows: int = 100,
) -> list[TradeDeliveryReceipt]:
    result = await db.execute(
        build_recover_expired_leases_statement(
            destination_server=destination_server,
            now=now,
            max_rows=max_rows,
        )
    )
    return _result_scalars_all(result)


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
