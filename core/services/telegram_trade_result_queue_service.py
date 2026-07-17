"""Atomic feeder handoff for Telegram trade-completion receipts.

The receipt remains in its existing domain status while the main queue owns
Telegram retry and ambiguity. ``worker_id`` is already a foreign-local,
NO_SYNC field, so it can bind the receipt to exactly one queue job without a
cross-server schema change or a second retry clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
from core.services.trade_delivery_receipt_service import (
    TELEGRAM_DESTINATION_SERVER,
    TRADE_COMPLETED_EVENT_TYPE,
    classify_receipt_outage_for_delivery,
    transition_receipt_status,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_delivery_trade_freshness import (
    TRADE_RESULT_TEMPLATE_VERSION,
    build_trade_result_delivery_payload,
    telegram_private_user_destination_key,
    trade_result_delivery_deadline,
    trade_result_delivery_source_version,
    trade_result_source_natural_id,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_queue_reconciliation_worker_id,
    trade_result_queue_receipt_worker_id,
)
from core.utils import utc_now
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User


TRADE_RESULT_QUEUE_HANDOFF = "handed_off"
TRADE_RESULT_QUEUE_SKIPPED = "skipped"
TRADE_RESULT_QUEUE_PERMANENT_FAILED = "permanent_failed"
TRADE_RESULT_QUEUE_REQUIRES_RECONCILIATION = "requires_reconciliation"

_ACTIVE_RECEIPT_STATUSES = (
    TradeDeliveryReceiptStatus.PENDING,
    TradeDeliveryReceiptStatus.RETRY_PENDING,
)
class TradeResultQueueHandoffError(RuntimeError):
    """Raised when an existing job cannot be adopted without ambiguity."""


@dataclass(frozen=True, slots=True)
class TradeResultQueueHandoffResult:
    receipt_id: int
    disposition: str
    job_id: int | None = None
    job_created: bool = False
    reason: str | None = None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


async def _flush(db: AsyncSession | Any) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()


async def _load_current_user(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt,
) -> User | None:
    recipient_user_id = _positive_int(receipt.recipient_user_id)
    if recipient_user_id is None:
        return None
    return (
        await db.execute(
            select(User).where(User.id == recipient_user_id).limit(1)
        )
    ).scalar_one_or_none()


async def _terminalize_unhandoffable_receipt(
    db: AsyncSession,
    *,
    receipt: TradeDeliveryReceipt,
    target: TradeDeliveryReceiptStatus,
    reason: str,
    error_class: str,
    now: datetime,
) -> TradeResultQueueHandoffResult:
    transition_receipt_status(
        receipt,
        TradeDeliveryReceiptStatus.PROCESSING,
        current_server=TELEGRAM_DESTINATION_SERVER,
        now=now,
    )
    transition_receipt_status(
        receipt,
        target,
        current_server=TELEGRAM_DESTINATION_SERVER,
        now=now,
        reason=reason,
        error_class=error_class,
        error_message=reason,
    )
    receipt.next_retry_at = None
    await _flush(db)
    return TradeResultQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=(
            TRADE_RESULT_QUEUE_SKIPPED
            if target == TradeDeliveryReceiptStatus.SKIPPED
            else TRADE_RESULT_QUEUE_PERMANENT_FAILED
        ),
        reason=reason,
    )


async def _select_next_due_receipt(
    db: AsyncSession,
    *,
    now: datetime,
) -> TradeDeliveryReceipt | None:
    return (
        await db.execute(
            select(TradeDeliveryReceipt)
            .where(
                TradeDeliveryReceipt.event_type == TRADE_COMPLETED_EVENT_TYPE,
                TradeDeliveryReceipt.channel == TradeDeliveryChannel.TELEGRAM,
                TradeDeliveryReceipt.destination_server == SERVER_FOREIGN,
                TradeDeliveryReceipt.status.in_(_ACTIVE_RECEIPT_STATUSES),
                TradeDeliveryReceipt.worker_id.is_(None),
                TradeDeliveryReceipt.lease_until.is_(None),
                or_(
                    TradeDeliveryReceipt.next_retry_at.is_(None),
                    TradeDeliveryReceipt.next_retry_at <= now,
                ),
            )
            .order_by(
                TradeDeliveryReceipt.next_retry_at.asc().nullsfirst(),
                TradeDeliveryReceipt.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()


async def handoff_next_due_trade_result_receipt(
    db: AsyncSession,
    *,
    current_server: str,
    now: datetime | None = None,
) -> TradeResultQueueHandoffResult | None:
    """Bind one due receipt and one logical main-queue job in one transaction."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TradeResultQueueHandoffError(
            "telegram_trade_result_handoff_is_foreign_only"
        )
    current_time = now or utc_now()
    receipt = await _select_next_due_receipt(db, now=current_time)
    if receipt is None:
        return None

    outage = classify_receipt_outage_for_delivery(receipt, now=current_time)
    if outage.should_skip_remote_delivery:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            target=TradeDeliveryReceiptStatus.SKIPPED,
            reason=str(outage.reason or "expired_delivery_after_outage"),
            error_class="TradeDeliveryOutagePolicy",
            now=current_time,
        )

    user = await _load_current_user(db, receipt=receipt)
    if user is None:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            target=TradeDeliveryReceiptStatus.SKIPPED,
            reason="telegram_user_missing_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    if _positive_int(getattr(user, "telegram_id", None)) is None:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            target=TradeDeliveryReceiptStatus.SKIPPED,
            reason="telegram_unlinked_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            target=TradeDeliveryReceiptStatus.SKIPPED,
            reason=str(access.reason or "bot_access_denied_current")[:96],
            error_class="BotAccessDenied",
            now=current_time,
        )

    try:
        source_natural_id = trade_result_source_natural_id(receipt)
        source_version = trade_result_delivery_source_version(user)
        destination_key = telegram_private_user_destination_key(
            int(receipt.recipient_user_id)
        )
        payload = build_trade_result_delivery_payload(receipt, user)
        delivery_deadline_at = trade_result_delivery_deadline(
            receipt.event_created_at
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            target=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            reason=str(exc)[:96] or "telegram_trade_result_payload_invalid",
            error_class="TelegramPayloadError",
            now=current_time,
        )

    enqueue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=TelegramFeederKind.TRADE,
        source_natural_id=source_natural_id,
        source_version=source_version,
        action=TelegramDeliveryAction.TRADE_RESULT,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.PRIVATE,
        method="sendMessage",
        payload=payload,
        template_version=TRADE_RESULT_TEMPLATE_VERSION,
        delivery_deadline_at=delivery_deadline_at,
    )
    job_id = int(enqueue_result.job.id)
    if not enqueue_result.created:
        # A correctly committed handoff always persists the job and binding in
        # the same transaction. An existing job with an unbound receipt is
        # therefore recovery work, never a safe runtime adoption candidate.
        # Isolate it durably so this poison row cannot starve later receipts;
        # the non-binding marker also makes the orphan job quarantine before
        # any Telegram side effect.
        receipt.worker_id = trade_result_queue_reconciliation_worker_id(job_id)
        receipt.lease_until = None
        receipt.next_retry_at = None
        receipt.reason = "telegram_trade_result_orphan_requires_reconciliation"
        receipt.updated_at = current_time
        await _flush(db)
        return TradeResultQueueHandoffResult(
            receipt_id=int(receipt.id),
            disposition=TRADE_RESULT_QUEUE_REQUIRES_RECONCILIATION,
            job_id=job_id,
            job_created=False,
            reason="telegram_trade_result_orphan_requires_reconciliation",
        )

    receipt.worker_id = trade_result_queue_receipt_worker_id(job_id)
    receipt.lease_until = None
    receipt.reason = "telegram_trade_result_handed_to_main_queue"
    receipt.updated_at = current_time
    await _flush(db)
    return TradeResultQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=TRADE_RESULT_QUEUE_HANDOFF,
        job_id=job_id,
        job_created=enqueue_result.created,
        reason="telegram_trade_result_handed_to_main_queue",
    )
