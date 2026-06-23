"""Receipt-backed WebApp delivery for trade-completion notifications."""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.trade_delivery_receipt_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    WEBAPP_DESTINATION_SERVER,
    claim_receipt_by_identity_for_delivery,
    complete_webapp_receipt_with_notification,
    publish_webapp_notification_after_commit,
    upsert_trade_delivery_receipt,
)
from core.services.trade_notification_audience_service import (
    WEBAPP_CHANNEL,
    build_trade_completion_notification_audience,
)
from core.utils import utc_now
from models.notification import Notification
from models.trade import Trade
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


logger = logging.getLogger(__name__)

WEBAPP_TRADE_DELIVERY_WORKER_ID = "webapp-trade-delivery"

WEBAPP_DELIVERY_STATUS_SENT = "sent"
WEBAPP_DELIVERY_STATUS_ALREADY_SENT = "already_sent"
WEBAPP_DELIVERY_STATUS_QUEUED_FOR_IRAN = "queued_for_iran"
WEBAPP_DELIVERY_STATUS_CLAIM_BUSY = "claim_busy"
WEBAPP_DELIVERY_STATUS_TERMINAL_PRESERVED = "terminal_preserved"


@dataclass(frozen=True, slots=True)
class WebAppTradeDeliveryResult:
    status: str
    trade_number: int
    recipient_user_id: int
    current_server: str
    destination_server: str
    receipt: TradeDeliveryReceipt | Any | None = None
    notification: Notification | Any | None = None
    notification_created: bool = False
    receipt_created: bool = False
    receipt_changed: bool = False
    sent_changed: bool = False
    realtime_published: bool = False
    publish_error_class: str | None = None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _receipt_status(receipt: TradeDeliveryReceipt | Any | None) -> str | None:
    if receipt is None:
        return None
    return _enum_value(getattr(receipt, "status", None))


def _is_sent(receipt: TradeDeliveryReceipt | Any | None) -> bool:
    return _receipt_status(receipt) == TradeDeliveryReceiptStatus.SENT.value


def _extra_payload(
    raw_payload: Mapping[str, Any] | None,
    *,
    trade_id: int | None,
    trade_number: int,
    offer_id: int | None,
    recipient_user_id: int,
    recipient_role: str,
    principal_user_id: int | None,
    side: str | None,
) -> dict[str, Any]:
    payload = dict(raw_payload or {})
    payload.setdefault("trade_id", trade_id)
    payload.setdefault("trade_number", trade_number)
    payload.setdefault("offer_id", offer_id)
    payload.setdefault("recipient_user_id", recipient_user_id)
    payload.setdefault("recipient_role", recipient_role)
    if principal_user_id is not None:
        payload.setdefault("principal_user_id", principal_user_id)
    if side:
        payload.setdefault("side", side)
    return payload


async def _commit_if_requested(db: AsyncSession | Any, commit: bool) -> None:
    if commit and callable(getattr(db, "commit", None)):
        await db.commit()


async def _safe_publish_webapp_side_effect(
    *,
    user_id: int,
    notification: Notification | Any,
    extra_payload: Mapping[str, Any] | None,
) -> tuple[bool, str | None]:
    try:
        await publish_webapp_notification_after_commit(
            user_id=user_id,
            notification=notification,
            extra_payload=extra_payload,
        )
        return True, None
    except Exception as exc:
        logger.warning(
            "Trade WebApp notification side effect failed after durable delivery",
            extra={
                "event": "trade_webapp_delivery.publish_failed",
                "user_id": user_id,
                "notification_id": getattr(notification, "id", None),
                "error_class": type(exc).__name__,
            },
        )
        return False, type(exc).__name__


async def deliver_webapp_trade_notification(
    db: AsyncSession,
    *,
    trade_number: int,
    recipient_user_id: int,
    message: str,
    current_server: str,
    trade_id: int | None = None,
    offer_id: int | None = None,
    recipient_role: str = "trade_recipient",
    principal_user_id: int | None = None,
    side: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
    event_created_at: datetime | None = None,
    reason: str = "webapp_required",
    worker_id: str = WEBAPP_TRADE_DELIVERY_WORKER_ID,
    lease_seconds: int = 30,
    commit: bool = True,
    publish_after_commit: bool = True,
    now: datetime | None = None,
) -> WebAppTradeDeliveryResult:
    """Upsert, claim, persist, and publish one WebApp trade notification.

    The notification row and receipt ``sent`` transition are committed before
    realtime/Web Push side effects run. On non-Iran servers this function only
    upserts the Iran-owned receipt and returns without creating a notification.
    """
    normalized_trade_number = int(trade_number)
    normalized_recipient_user_id = int(recipient_user_id)
    normalized_current_server = str(current_server or "")
    current_time = now or utc_now()
    payload = _extra_payload(
        extra_payload,
        trade_id=trade_id,
        trade_number=normalized_trade_number,
        offer_id=offer_id,
        recipient_user_id=normalized_recipient_user_id,
        recipient_role=recipient_role,
        principal_user_id=principal_user_id,
        side=side,
    )

    upsert_result = await upsert_trade_delivery_receipt(
        db,
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        trade_number=normalized_trade_number,
        recipient_user_id=normalized_recipient_user_id,
        recipient_role=recipient_role,
        channel=TradeDeliveryChannel.WEBAPP,
        destination_server=WEBAPP_DESTINATION_SERVER,
        required=True,
        reason=reason,
        trade_id=trade_id,
        offer_id=offer_id,
        event_created_at=event_created_at or current_time,
        audit_payload={
            "message": message,
            "extra_payload": payload,
        },
        now=current_time,
    )
    receipt = upsert_result.receipt

    if normalized_current_server != WEBAPP_DESTINATION_SERVER:
        await _commit_if_requested(db, commit)
        return WebAppTradeDeliveryResult(
            status=WEBAPP_DELIVERY_STATUS_QUEUED_FOR_IRAN,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=WEBAPP_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
        )

    if _is_sent(receipt):
        await _commit_if_requested(db, commit and upsert_result.changed)
        return WebAppTradeDeliveryResult(
            status=WEBAPP_DELIVERY_STATUS_ALREADY_SENT,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=WEBAPP_DESTINATION_SERVER,
            receipt=receipt,
            notification_created=False,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
            notification=None,
        )

    if upsert_result.terminal_preserved:
        await _commit_if_requested(db, commit and upsert_result.changed)
        return WebAppTradeDeliveryResult(
            status=WEBAPP_DELIVERY_STATUS_TERMINAL_PRESERVED,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=WEBAPP_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
        )

    claimed_receipt = await claim_receipt_by_identity_for_delivery(
        db,
        event_type=TRADE_COMPLETED_EVENT_TYPE,
        trade_number=normalized_trade_number,
        recipient_user_id=normalized_recipient_user_id,
        channel=TradeDeliveryChannel.WEBAPP,
        destination_server=WEBAPP_DESTINATION_SERVER,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        now=current_time,
    )
    if claimed_receipt is None:
        await _commit_if_requested(db, commit and upsert_result.changed)
        return WebAppTradeDeliveryResult(
            status=WEBAPP_DELIVERY_STATUS_CLAIM_BUSY,
            trade_number=normalized_trade_number,
            recipient_user_id=normalized_recipient_user_id,
            current_server=normalized_current_server,
            destination_server=WEBAPP_DESTINATION_SERVER,
            receipt=receipt,
            receipt_created=upsert_result.created,
            receipt_changed=upsert_result.changed,
        )

    delivery_result = await complete_webapp_receipt_with_notification(
        db,
        receipt=claimed_receipt,
        message=message,
        extra_payload=payload,
        current_server=WEBAPP_DESTINATION_SERVER,
        now=current_time,
    )
    await _commit_if_requested(db, commit)

    realtime_published = False
    publish_error_class = None
    if publish_after_commit and delivery_result.sent_changed:
        realtime_published, publish_error_class = await _safe_publish_webapp_side_effect(
            user_id=normalized_recipient_user_id,
            notification=delivery_result.notification,
            extra_payload=delivery_result.realtime_payload.get("extra_payload"),
        )

    return WebAppTradeDeliveryResult(
        status=WEBAPP_DELIVERY_STATUS_SENT,
        trade_number=normalized_trade_number,
        recipient_user_id=normalized_recipient_user_id,
        current_server=normalized_current_server,
        destination_server=WEBAPP_DESTINATION_SERVER,
        receipt=claimed_receipt,
        notification=delivery_result.notification,
        notification_created=delivery_result.notification_created,
        receipt_created=upsert_result.created,
        receipt_changed=True,
        sent_changed=delivery_result.sent_changed,
        realtime_published=realtime_published,
        publish_error_class=publish_error_class,
    )


async def repair_webapp_trade_delivery_for_trade(
    db: AsyncSession,
    trade: Trade | Any,
    *,
    current_server: str,
    commit: bool = True,
    publish_after_commit: bool = True,
    now: datetime | None = None,
) -> tuple[WebAppTradeDeliveryResult, ...]:
    audience = await build_trade_completion_notification_audience(db, trade)
    if audience.skipped_reason:
        return ()
    trade_number = _coerce_int(audience.trade_number)
    if trade_number is None:
        return ()

    event_created_at = getattr(trade, "created_at", None)
    results: list[WebAppTradeDeliveryResult] = []
    for recipient in audience.recipients:
        recipient_user_id = _coerce_int(recipient.recipient_user_id)
        if recipient_user_id is None:
            continue
        for requirement in recipient.channel_requirements:
            if _enum_value(requirement.channel) != WEBAPP_CHANNEL or not requirement.required:
                continue
            results.append(
                await deliver_webapp_trade_notification(
                    db,
                    trade_number=trade_number,
                    recipient_user_id=recipient_user_id,
                    message=requirement.message or recipient.webapp_message,
                    current_server=current_server,
                    trade_id=_coerce_int(audience.trade_id),
                    offer_id=_coerce_int(audience.offer_id),
                    recipient_role=recipient.recipient_role,
                    principal_user_id=_coerce_int(recipient.principal_user_id),
                    side=recipient.side,
                    extra_payload=recipient.extra_payload,
                    event_created_at=event_created_at,
                    reason=requirement.reason,
                    commit=commit,
                    publish_after_commit=publish_after_commit,
                    now=now,
                )
            )
    return tuple(results)


async def repair_webapp_trade_delivery_for_trades(
    db: AsyncSession,
    trades: Sequence[Trade | Any],
    *,
    current_server: str,
    commit: bool = True,
    publish_after_commit: bool = True,
    now: datetime | None = None,
) -> tuple[WebAppTradeDeliveryResult, ...]:
    results: list[WebAppTradeDeliveryResult] = []
    for trade in trades:
        results.extend(
            await repair_webapp_trade_delivery_for_trade(
                db,
                trade,
                current_server=current_server,
                commit=commit,
                publish_after_commit=publish_after_commit,
                now=now,
            )
        )
    return tuple(results)
