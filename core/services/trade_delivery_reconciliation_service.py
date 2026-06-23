"""Read-only shadow reconciliation for trade-completion delivery receipts."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.services.trade_delivery_receipt_service import (
    TRADE_COMPLETED_EVENT_TYPE,
    receipt_dedupe_key,
    webapp_notification_dedupe_key,
)
from core.services.trade_notification_audience_service import (
    TELEGRAM_CHANNEL,
    WEBAPP_CHANNEL,
    TradeNotificationAudience,
    build_trade_completion_notification_audience,
)
from models.notification import Notification
from models.trade import Trade, TradeStatus
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)


LOCAL_REPAIR_ACTION_NONE = "none"
LOCAL_REPAIR_ACTION_CREATE_PENDING_RECEIPT = "create_pending_receipt"
LOCAL_REPAIR_ACTION_CREATE_NOT_REQUIRED_RECEIPT = "create_not_required_receipt"
LOCAL_REPAIR_ACTION_CREATE_WEBAPP_NOTIFICATION = "create_webapp_notification_and_mark_sent"
LOCAL_REPAIR_ACTION_DELIVER_TELEGRAM = "telegram_worker_delivery_required"
LOCAL_REPAIR_ACTION_READ_ONLY = "read_only_opposite_server"


@dataclass(frozen=True, slots=True)
class TradeDeliveryExpectationReport:
    event_type: str
    trade_id: int | None
    trade_number: int
    offer_id: int | None
    offer_home_server: str | None
    recipient_user_id: int
    recipient_role: str
    principal_user_id: int
    side: str
    counterparty_user_id: int | None
    channel: str
    destination_server: str
    required: bool
    reason: str
    dedupe_key: str
    notification_dedupe_key: str | None
    receipt_id: int | None
    receipt_status: str | None
    receipt_destination_server: str | None
    notification_id: int | None
    telegram_message_id: int | None
    local_owner: bool
    read_only: bool
    missing_receipt: bool
    missing_notification: bool
    delivery_gap: bool
    repairable: bool
    repair_action: str
    current_side_effect_state: str
    explanation: str
    extra_payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TradeDeliveryTradeReport:
    trade_id: int | None
    trade_number: int | None
    offer_id: int | None
    offer_home_server: str | None
    skipped_reason: str | None
    expectations: tuple[TradeDeliveryExpectationReport, ...]


@dataclass(frozen=True, slots=True)
class TradeDeliveryReconciliationReport:
    current_server: str
    dry_run: bool
    trade_count: int
    expectation_count: int
    local_expectation_count: int
    missing_receipt_count: int
    missing_notification_count: int
    delivery_gap_count: int
    repairable_count: int
    read_only_count: int
    trades: tuple[TradeDeliveryTradeReport, ...]

    @property
    def expectations(self) -> tuple[TradeDeliveryExpectationReport, ...]:
        return tuple(expectation for trade in self.trades for expectation in trade.expectations)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _result_scalars_all(result: Any) -> list[Any]:
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        all_rows = getattr(scalar_result, "all", None)
        if callable(all_rows):
            return list(all_rows())
    all_rows = getattr(result, "all", None)
    if callable(all_rows):
        rows = list(all_rows())
        return [row[0] if isinstance(row, tuple) and row else row for row in rows]
    return []


def _receipt_identity(receipt: TradeDeliveryReceipt | Any) -> tuple[str, int, int, str] | None:
    trade_number = _coerce_int(getattr(receipt, "trade_number", None))
    recipient_user_id = _coerce_int(getattr(receipt, "recipient_user_id", None))
    event_type = str(getattr(receipt, "event_type", "") or "")
    channel = _enum_value(getattr(receipt, "channel", None))
    if not event_type or trade_number is None or recipient_user_id is None or not channel:
        return None
    return event_type, trade_number, recipient_user_id, channel


def _notification_id(notification: Notification | Any | None) -> int | None:
    if notification is None:
        return None
    return _coerce_int(getattr(notification, "id", None))


def _receipt_status_value(receipt: TradeDeliveryReceipt | Any | None) -> str | None:
    if receipt is None:
        return None
    return _enum_value(getattr(receipt, "status", None))


def _receipt_is_sent(receipt: TradeDeliveryReceipt | Any | None) -> bool:
    return _receipt_status_value(receipt) == TradeDeliveryReceiptStatus.SENT.value


def _receipt_is_terminal(receipt: TradeDeliveryReceipt | Any | None) -> bool:
    status = _receipt_status_value(receipt)
    return status in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES


def build_completed_trade_shadow_scan_statement(
    *,
    limit: int = 100,
    trade_numbers: Sequence[int] | None = None,
    since: datetime | None = None,
):
    """Build the read-only completed-trade scan used by shadow reconciliation."""
    stmt = (
        select(Trade)
        .options(
            selectinload(Trade.offer),
            selectinload(Trade.offer_user),
            selectinload(Trade.responder_user),
            selectinload(Trade.commodity),
        )
        .where(Trade.status == TradeStatus.COMPLETED)
    )
    normalized_trade_numbers = [
        normalized
        for raw in list(trade_numbers or [])
        for normalized in [_coerce_int(raw)]
        if normalized is not None
    ]
    if normalized_trade_numbers:
        stmt = stmt.where(Trade.trade_number.in_(sorted(set(normalized_trade_numbers))))
    if since is not None:
        stmt = stmt.where(Trade.created_at >= since)
    return stmt.order_by(Trade.created_at.desc(), Trade.id.desc()).limit(max(int(limit or 1), 1))


async def load_completed_trades_for_shadow_reconciliation(
    db: AsyncSession,
    *,
    limit: int = 100,
    trade_numbers: Sequence[int] | None = None,
    since: datetime | None = None,
) -> list[Trade]:
    result = await db.execute(
        build_completed_trade_shadow_scan_statement(
            limit=limit,
            trade_numbers=trade_numbers,
            since=since,
        )
    )
    return _result_scalars_all(result)


async def load_trade_delivery_receipts_for_trade_numbers(
    db: AsyncSession,
    *,
    trade_numbers: Sequence[int],
    event_type: str = TRADE_COMPLETED_EVENT_TYPE,
) -> dict[tuple[str, int, int, str], TradeDeliveryReceipt]:
    normalized_trade_numbers = [
        normalized
        for raw in trade_numbers
        for normalized in [_coerce_int(raw)]
        if normalized is not None
    ]
    if not normalized_trade_numbers:
        return {}
    result = await db.execute(
        select(TradeDeliveryReceipt).where(
            TradeDeliveryReceipt.event_type == event_type,
            TradeDeliveryReceipt.trade_number.in_(sorted(set(normalized_trade_numbers))),
        )
    )
    receipt_map: dict[tuple[str, int, int, str], TradeDeliveryReceipt] = {}
    for receipt in _result_scalars_all(result):
        identity = _receipt_identity(receipt)
        if identity is not None:
            receipt_map[identity] = receipt
    return receipt_map


async def load_trade_notifications_for_dedupe_keys(
    db: AsyncSession,
    *,
    dedupe_keys: Sequence[str],
) -> dict[str, Notification]:
    normalized_keys = sorted({str(key) for key in dedupe_keys if str(key or "").strip()})
    if not normalized_keys:
        return {}
    result = await db.execute(select(Notification).where(Notification.dedupe_key.in_(normalized_keys)))
    return {
        str(getattr(notification, "dedupe_key", "") or ""): notification
        for notification in _result_scalars_all(result)
        if getattr(notification, "dedupe_key", None)
    }


def build_expectation_reports_for_audience(
    audience: TradeNotificationAudience,
    *,
    current_server: str,
    receipt_map: Mapping[tuple[str, int, int, str], TradeDeliveryReceipt | Any] | None = None,
    notification_map: Mapping[str, Notification | Any] | None = None,
) -> TradeDeliveryTradeReport:
    trade_number = _coerce_int(audience.trade_number)
    if trade_number is None:
        return TradeDeliveryTradeReport(
            trade_id=_coerce_int(audience.trade_id),
            trade_number=None,
            offer_id=_coerce_int(audience.offer_id),
            offer_home_server=audience.offer_home_server,
            skipped_reason=audience.skipped_reason or "missing_trade_number",
            expectations=(),
        )
    if audience.skipped_reason:
        return TradeDeliveryTradeReport(
            trade_id=_coerce_int(audience.trade_id),
            trade_number=trade_number,
            offer_id=_coerce_int(audience.offer_id),
            offer_home_server=audience.offer_home_server,
            skipped_reason=audience.skipped_reason,
            expectations=(),
        )

    reports: list[TradeDeliveryExpectationReport] = []
    receipts = receipt_map or {}
    notifications = notification_map or {}
    for recipient in audience.recipients:
        recipient_user_id = _coerce_int(recipient.recipient_user_id)
        principal_user_id = _coerce_int(recipient.principal_user_id)
        if recipient_user_id is None or principal_user_id is None:
            continue
        for requirement in recipient.channel_requirements:
            channel = _enum_value(requirement.channel)
            if channel not in {WEBAPP_CHANNEL, TELEGRAM_CHANNEL}:
                continue
            destination_server = str(requirement.destination_server or "")
            identity = (audience.event_type, trade_number, recipient_user_id, channel)
            receipt = receipts.get(identity)
            dedupe_key = receipt_dedupe_key(
                event_type=audience.event_type,
                channel=channel,
                trade_number=trade_number,
                recipient_user_id=recipient_user_id,
            )
            notification_key = (
                webapp_notification_dedupe_key(
                    trade_number=trade_number,
                    recipient_user_id=recipient_user_id,
                )
                if channel == WEBAPP_CHANNEL
                else None
            )
            notification = notifications.get(notification_key) if notification_key else None
            local_owner = destination_server == str(current_server or "")
            read_only = not local_owner
            missing_receipt = receipt is None
            receipt_status = _receipt_status_value(receipt)
            required = bool(requirement.required)
            missing_notification = bool(
                required
                and channel == WEBAPP_CHANNEL
                and notification is None
            )
            if channel == TELEGRAM_CHANNEL:
                missing_notification = False

            delivery_gap = _delivery_gap(
                channel=channel,
                required=required,
                receipt=receipt,
                missing_receipt=missing_receipt,
                missing_notification=missing_notification,
            )
            repair_action = _repair_action(
                channel=channel,
                required=required,
                receipt=receipt,
                local_owner=local_owner,
                missing_receipt=missing_receipt,
                missing_notification=missing_notification,
            )
            repairable = local_owner and repair_action not in {
                LOCAL_REPAIR_ACTION_NONE,
                LOCAL_REPAIR_ACTION_READ_ONLY,
            }
            reports.append(
                TradeDeliveryExpectationReport(
                    event_type=audience.event_type,
                    trade_id=_coerce_int(audience.trade_id),
                    trade_number=trade_number,
                    offer_id=_coerce_int(audience.offer_id),
                    offer_home_server=audience.offer_home_server,
                    recipient_user_id=recipient_user_id,
                    recipient_role=recipient.recipient_role,
                    principal_user_id=principal_user_id,
                    side=recipient.side,
                    counterparty_user_id=_coerce_int(recipient.counterparty_user_id),
                    channel=channel,
                    destination_server=destination_server,
                    required=required,
                    reason=requirement.reason,
                    dedupe_key=dedupe_key,
                    notification_dedupe_key=notification_key,
                    receipt_id=_coerce_int(getattr(receipt, "id", None)),
                    receipt_status=receipt_status,
                    receipt_destination_server=getattr(receipt, "destination_server", None),
                    notification_id=_notification_id(notification)
                    or _coerce_int(getattr(receipt, "notification_id", None)),
                    telegram_message_id=_coerce_int(getattr(receipt, "telegram_message_id", None)),
                    local_owner=local_owner,
                    read_only=read_only,
                    missing_receipt=missing_receipt,
                    missing_notification=missing_notification,
                    delivery_gap=delivery_gap,
                    repairable=repairable,
                    repair_action=repair_action,
                    current_side_effect_state=_current_side_effect_state(
                        channel=channel,
                        required=required,
                        receipt=receipt,
                        notification=notification,
                    ),
                    explanation=_explain_expectation(
                        channel=channel,
                        destination_server=destination_server,
                        required=required,
                        reason=requirement.reason,
                        local_owner=local_owner,
                        delivery_gap=delivery_gap,
                    ),
                    extra_payload=recipient.extra_payload,
                )
            )

    return TradeDeliveryTradeReport(
        trade_id=_coerce_int(audience.trade_id),
        trade_number=trade_number,
        offer_id=_coerce_int(audience.offer_id),
        offer_home_server=audience.offer_home_server,
        skipped_reason=None,
        expectations=tuple(reports),
    )


async def build_trade_delivery_shadow_report_for_trades(
    db: AsyncSession,
    trades: Sequence[Trade | Any],
    *,
    current_server: str,
) -> TradeDeliveryReconciliationReport:
    audiences = [
        await build_trade_completion_notification_audience(db, trade)
        for trade in trades
    ]
    trade_numbers = [
        normalized
        for audience in audiences
        for normalized in [_coerce_int(audience.trade_number)]
        if normalized is not None
    ]
    receipt_map = await load_trade_delivery_receipts_for_trade_numbers(db, trade_numbers=trade_numbers)

    notification_keys: list[str] = []
    for audience in audiences:
        trade_number = _coerce_int(audience.trade_number)
        if trade_number is None or audience.skipped_reason:
            continue
        for recipient in audience.recipients:
            recipient_user_id = _coerce_int(recipient.recipient_user_id)
            if recipient_user_id is None:
                continue
            for requirement in recipient.channel_requirements:
                if _enum_value(requirement.channel) == WEBAPP_CHANNEL and bool(requirement.required):
                    notification_keys.append(
                        webapp_notification_dedupe_key(
                            trade_number=trade_number,
                            recipient_user_id=recipient_user_id,
                        )
                    )

    notification_map = await load_trade_notifications_for_dedupe_keys(db, dedupe_keys=notification_keys)
    trade_reports = tuple(
        build_expectation_reports_for_audience(
            audience,
            current_server=current_server,
            receipt_map=receipt_map,
            notification_map=notification_map,
        )
        for audience in audiences
    )
    expectations = tuple(expectation for report in trade_reports for expectation in report.expectations)
    return TradeDeliveryReconciliationReport(
        current_server=str(current_server or ""),
        dry_run=True,
        trade_count=len(audiences),
        expectation_count=len(expectations),
        local_expectation_count=sum(1 for expectation in expectations if expectation.local_owner),
        missing_receipt_count=sum(1 for expectation in expectations if expectation.missing_receipt),
        missing_notification_count=sum(1 for expectation in expectations if expectation.missing_notification),
        delivery_gap_count=sum(1 for expectation in expectations if expectation.delivery_gap),
        repairable_count=sum(1 for expectation in expectations if expectation.repairable),
        read_only_count=sum(1 for expectation in expectations if expectation.read_only),
        trades=trade_reports,
    )


async def run_trade_delivery_shadow_reconciliation(
    db: AsyncSession,
    *,
    current_server: str,
    limit: int = 100,
    trade_numbers: Sequence[int] | None = None,
    since: datetime | None = None,
) -> TradeDeliveryReconciliationReport:
    trades = await load_completed_trades_for_shadow_reconciliation(
        db,
        limit=limit,
        trade_numbers=trade_numbers,
        since=since,
    )
    return await build_trade_delivery_shadow_report_for_trades(
        db,
        trades,
        current_server=current_server,
    )


def _delivery_gap(
    *,
    channel: str,
    required: bool,
    receipt: TradeDeliveryReceipt | Any | None,
    missing_receipt: bool,
    missing_notification: bool,
) -> bool:
    if missing_receipt:
        return True
    if not required:
        return not _receipt_is_terminal(receipt)
    if channel == WEBAPP_CHANNEL:
        return missing_notification or not _receipt_is_sent(receipt)
    if channel == TELEGRAM_CHANNEL:
        return not _receipt_is_sent(receipt)
    return False


def _repair_action(
    *,
    channel: str,
    required: bool,
    receipt: TradeDeliveryReceipt | Any | None,
    local_owner: bool,
    missing_receipt: bool,
    missing_notification: bool,
) -> str:
    if not local_owner:
        return LOCAL_REPAIR_ACTION_READ_ONLY
    if missing_receipt:
        return (
            LOCAL_REPAIR_ACTION_CREATE_PENDING_RECEIPT
            if required
            else LOCAL_REPAIR_ACTION_CREATE_NOT_REQUIRED_RECEIPT
        )
    if not required:
        return (
            LOCAL_REPAIR_ACTION_NONE
            if _receipt_is_terminal(receipt)
            else LOCAL_REPAIR_ACTION_CREATE_NOT_REQUIRED_RECEIPT
        )
    if channel == WEBAPP_CHANNEL and (missing_notification or not _receipt_is_sent(receipt)):
        return LOCAL_REPAIR_ACTION_CREATE_WEBAPP_NOTIFICATION
    if channel == TELEGRAM_CHANNEL and not _receipt_is_sent(receipt):
        return LOCAL_REPAIR_ACTION_DELIVER_TELEGRAM
    return LOCAL_REPAIR_ACTION_NONE


def _current_side_effect_state(
    *,
    channel: str,
    required: bool,
    receipt: TradeDeliveryReceipt | Any | None,
    notification: Notification | Any | None,
) -> str:
    if not required:
        return "channel_not_required"
    if channel == WEBAPP_CHANNEL:
        if notification is not None:
            return "webapp_notification_with_dedupe_exists"
        if _coerce_int(getattr(receipt, "notification_id", None)) is not None:
            return "webapp_receipt_links_notification_id"
        return "webapp_notification_not_found_by_dedupe"
    if channel == TELEGRAM_CHANNEL:
        if _receipt_is_sent(receipt):
            if _coerce_int(getattr(receipt, "telegram_message_id", None)) is not None:
                return "telegram_receipt_sent_with_message_id"
            return "telegram_receipt_sent_without_message_id"
        if receipt is None:
            return "telegram_direct_send_not_durable"
        return "telegram_receipt_not_sent"
    return "unknown_channel"


def _explain_expectation(
    *,
    channel: str,
    destination_server: str,
    required: bool,
    reason: str,
    local_owner: bool,
    delivery_gap: bool,
) -> str:
    ownership = "local" if local_owner else "opposite_server_read_only"
    requirement = "required" if required else "not_required"
    gap = "gap" if delivery_gap else "ok"
    return (
        f"{channel}:{requirement}:{reason}:destination={destination_server}:"
        f"ownership={ownership}:state={gap}"
    )
