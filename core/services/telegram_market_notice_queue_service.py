"""Atomic handoff of market-channel notice receipts to the shared queue."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.market_transition_service import (
    MARKET_CLOSED_CHANNEL_NOTICE,
    MARKET_NOTICE_STATUS_FAILED,
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_STATUS_SKIPPED,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    MARKET_NOTICE_TRANSITION_CLOSED,
    MARKET_NOTICE_TRANSITION_OPENED,
    MARKET_OPENED_CHANNEL_NOTICE,
    market_channel_notice_dedupe_key,
    market_channel_notice_freshness_deadline,
)
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_market_freshness import (
    MARKET_NOTICE_CAMPAIGN_ID,
    MARKET_NOTICE_DELIVERY_SOURCE_VERSION,
    MARKET_NOTICE_TEMPLATE_VERSION,
    build_market_notice_payload,
    market_notice_action_for_status,
    telegram_market_notice_destination_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.utils import utc_now
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt


MARKET_NOTICE_QUEUE_HANDOFF = "handed_off"
MARKET_NOTICE_QUEUE_SUPPRESSED = "suppressed_stale"
MARKET_NOTICE_QUEUE_SKIPPED = "skipped"
MARKET_NOTICE_QUEUE_REQUIRES_RECONCILIATION = "requires_reconciliation"

_HANDOFFABLE_STATUSES = (MARKET_NOTICE_STATUS_PENDING, MARKET_NOTICE_STATUS_FAILED)
_NOTICE_TEXT_BY_TRANSITION = {
    MARKET_NOTICE_TRANSITION_OPENED: MARKET_OPENED_CHANNEL_NOTICE,
    MARKET_NOTICE_TRANSITION_CLOSED: MARKET_CLOSED_CHANNEL_NOTICE,
}


class TelegramMarketNoticeQueueHandoffError(RuntimeError):
    """Raised before an unsafe or cross-server market-notice handoff."""


@dataclass(frozen=True, slots=True)
class TelegramMarketNoticeQueueHandoffResult:
    receipt_id: int
    disposition: str
    job_id: int | None = None
    job_created: bool = False
    reason: str | None = None


def _strict_nonzero_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        return None
    return value


async def _select_next_due_receipt(
    db: AsyncSession,
    *,
    now: datetime,
) -> MarketChannelNoticeReceipt | None:
    return (
        await db.execute(
            select(MarketChannelNoticeReceipt)
            .where(
                MarketChannelNoticeReceipt.status.in_(_HANDOFFABLE_STATUSES),
                MarketChannelNoticeReceipt.queue_job_id.is_(None),
                MarketChannelNoticeReceipt.queue_handed_off_at.is_(None),
                MarketChannelNoticeReceipt.queue_reconciliation_required_at.is_(None),
                or_(
                    MarketChannelNoticeReceipt.next_retry_at.is_(None),
                    MarketChannelNoticeReceipt.next_retry_at <= now,
                ),
            )
            .order_by(
                MarketChannelNoticeReceipt.next_retry_at.asc().nullsfirst(),
                MarketChannelNoticeReceipt.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()


def _validate_receipt_contract(
    receipt: MarketChannelNoticeReceipt,
) -> tuple[str, datetime, str]:
    transition = str(receipt.transition or "").strip().lower()
    if receipt.transition != transition or transition not in _NOTICE_TEXT_BY_TRANSITION:
        raise ValueError("market_notice_transition_invalid")
    if not isinstance(receipt.transition_at, datetime):
        raise ValueError("market_notice_transition_at_invalid")
    expected_text = _NOTICE_TEXT_BY_TRANSITION[transition]
    if receipt.notice_text != expected_text:
        raise ValueError("market_notice_template_invalid")
    expected_dedupe = market_channel_notice_dedupe_key(
        transition=transition,
        transition_at=receipt.transition_at,
        notice_text=receipt.notice_text,
    )
    if receipt.dedupe_key != expected_dedupe:
        raise ValueError("market_notice_dedupe_invalid")
    market_notice_action_for_status(receipt.status)
    return transition, receipt.transition_at, expected_dedupe


async def _terminalize_unhandoffable_receipt(
    db: AsyncSession,
    *,
    receipt: MarketChannelNoticeReceipt,
    reason: str,
    now: datetime,
) -> TelegramMarketNoticeQueueHandoffResult:
    receipt.status = MARKET_NOTICE_STATUS_SKIPPED
    receipt.next_retry_at = None
    receipt.last_attempt_at = now
    receipt.last_error_class = "TelegramMarketNoticePayloadError"
    receipt.last_error = str(reason or "market_notice_payload_invalid")[:500]
    receipt.queue_job_id = None
    receipt.queue_handed_off_at = None
    receipt.queue_reconciliation_required_at = None
    receipt.updated_at = now
    await db.flush()
    return TelegramMarketNoticeQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=MARKET_NOTICE_QUEUE_SKIPPED,
        reason=str(reason or "market_notice_payload_invalid")[:120],
    )


async def handoff_next_due_market_channel_notice(
    db: AsyncSession,
    *,
    current_server: str,
    expected_channel_id: int,
    now: datetime | None = None,
) -> TelegramMarketNoticeQueueHandoffResult | None:
    """Bind one eligible market receipt to one main-queue job."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramMarketNoticeQueueHandoffError(
            "telegram_market_notice_queue_handoff_is_foreign_only"
        )
    channel_id = _strict_nonzero_int(expected_channel_id)
    if channel_id is None:
        raise TelegramMarketNoticeQueueHandoffError(
            "telegram_market_notice_queue_channel_invalid"
        )
    current_time = now or utc_now()
    receipt = await _select_next_due_receipt(db, now=current_time)
    if receipt is None:
        return None

    try:
        _transition, transition_at, source_natural_id = _validate_receipt_contract(
            receipt
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            reason=str(exc) or "market_notice_payload_invalid",
            now=current_time,
        )

    freshness_deadline = market_channel_notice_freshness_deadline(transition_at)
    if freshness_deadline is not None and current_time > freshness_deadline:
        receipt.status = MARKET_NOTICE_STATUS_SUPPRESSED_STALE
        receipt.next_retry_at = None
        receipt.last_attempt_at = current_time
        receipt.last_error_class = "stale_transition"
        receipt.last_error = "Market channel notice freshness deadline passed before queue handoff"
        receipt.channel_id = str(channel_id)
        receipt.updated_at = current_time
        await db.flush()
        return TelegramMarketNoticeQueueHandoffResult(
            receipt_id=int(receipt.id),
            disposition=MARKET_NOTICE_QUEUE_SUPPRESSED,
            reason="market_notice_freshness_deadline_passed",
        )

    try:
        action = market_notice_action_for_status(receipt.status)
        payload = build_market_notice_payload(receipt, channel_id=channel_id)
        destination_key = telegram_market_notice_destination_key(channel_id)
    except (TypeError, ValueError, OverflowError) as exc:
        return await _terminalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            reason=str(exc) or "market_notice_payload_invalid",
            now=current_time,
        )

    receipt.channel_id = str(channel_id)
    enqueue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=TelegramFeederKind.MARKET_STATUS,
        source_natural_id=source_natural_id,
        source_version=MARKET_NOTICE_DELIVERY_SOURCE_VERSION,
        action=action,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.CHANNEL,
        method="sendMessage",
        payload=payload,
        template_version=MARKET_NOTICE_TEMPLATE_VERSION,
        campaign_id=MARKET_NOTICE_CAMPAIGN_ID,
        freshness_deadline_at=freshness_deadline,
    )
    job_id = int(enqueue_result.job.id)
    if not enqueue_result.created:
        receipt.queue_reconciliation_required_at = current_time
        receipt.next_retry_at = None
        receipt.last_error_class = "TelegramDeliveryReconciliationRequired"
        receipt.last_error = (
            "Market notice queue job exists without its atomic receipt binding"
        )
        receipt.updated_at = current_time
        await db.flush()
        return TelegramMarketNoticeQueueHandoffResult(
            receipt_id=int(receipt.id),
            disposition=MARKET_NOTICE_QUEUE_REQUIRES_RECONCILIATION,
            job_id=job_id,
            job_created=False,
            reason="market_notice_queue_orphan_requires_reconciliation",
        )

    receipt.queue_job_id = job_id
    receipt.queue_handed_off_at = current_time
    receipt.queue_reconciliation_required_at = None
    receipt.last_error_class = None
    receipt.last_error = None
    receipt.updated_at = current_time
    await db.flush()
    return TelegramMarketNoticeQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=MARKET_NOTICE_QUEUE_HANDOFF,
        job_id=job_id,
        job_created=True,
        reason="market_notice_handed_to_main_queue",
    )
