"""Atomic lifecycle feedback for queued market-channel notices."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.market_transition_service import (
    MARKET_NOTICE_STATUS_FAILED,
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_STATUS_SENT,
    MARKET_NOTICE_STATUS_SKIPPED,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
)
from core.telegram_delivery_market_freshness import (
    MARKET_NOTICE_CAMPAIGN_ID,
    MARKET_NOTICE_DELIVERY_SOURCE_VERSION,
    MARKET_NOTICE_FRESHNESS_ACTIONS,
    MARKET_NOTICE_TEMPLATE_VERSION,
    telegram_market_notice_destination_key,
    validate_market_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.market_runtime_state import MarketRuntimeState
from models.telegram_delivery_job import TelegramDeliveryJobRecord


_ACTIVE_STATUSES = {MARKET_NOTICE_STATUS_PENDING, MARKET_NOTICE_STATUS_FAILED}
_TERMINAL_STATUSES = {
    MARKET_NOTICE_STATUS_SENT,
    MARKET_NOTICE_STATUS_SKIPPED,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
}
_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}
_STALE_REASONS = {
    "market_freshness_transition_no_longer_current",
    "market_freshness_deadline_passed",
    "market_freshness_notice_template_changed",
}


class TelegramMarketNoticeQueueFeedbackError(RuntimeError):
    """Roll back queue transitions when receipt feedback is unsafe."""


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _channel_id_from_job(job: TelegramDeliveryJobRecord) -> int:
    payload = getattr(job, "payload", None)
    channel_id = (
        payload.get("chat_id") if isinstance(payload, dict) else None
    )
    if isinstance(channel_id, bool) or not isinstance(channel_id, int) or channel_id == 0:
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_channel_invalid"
        )
    return channel_id


def _validate_job_route(
    job: TelegramDeliveryJobRecord,
    *,
    expected_channel_id: int,
) -> None:
    try:
        action = next(
            item
            for item in MARKET_NOTICE_FRESHNESS_ACTIONS
            if item.value == _enum_value(job.action_kind)
        )
    except StopIteration as exc:
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_action_mismatch"
        ) from exc
    if (
        action not in MARKET_NOTICE_FRESHNESS_ACTIONS
        or _enum_value(job.feeder_kind) != TelegramFeederKind.MARKET_STATUS.value
        or _enum_value(job.destination_class)
        != TelegramDestinationClass.CHANNEL.value
        or str(job.method or "") != "sendMessage"
        or str(job.bot_identity or "") != "primary"
        or str(job.template_version or "") != MARKET_NOTICE_TEMPLATE_VERSION
        or str(job.campaign_id or "") != MARKET_NOTICE_CAMPAIGN_ID
        or _positive_int(job.source_version)
        != MARKET_NOTICE_DELIVERY_SOURCE_VERSION
        or str(job.destination_key)
        != telegram_market_notice_destination_key(expected_channel_id)
        or _channel_id_from_job(job) != expected_channel_id
        or job.delivery_deadline_at is not None
        or job.run_id is not None
    ):
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_route_mismatch"
        )


async def _load_bound_receipt(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> MarketChannelNoticeReceipt:
    receipt = (
        await db.execute(
            select(MarketChannelNoticeReceipt)
            .where(
                MarketChannelNoticeReceipt.dedupe_key
                == str(job.source_natural_id or "")
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_receipt_missing"
        )
    return receipt


def _require_active_binding(
    receipt: MarketChannelNoticeReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    if str(receipt.status or "").strip().lower() not in _ACTIVE_STATUSES:
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_receipt_not_active"
        )
    if _positive_int(receipt.queue_job_id) != _positive_int(job.id):
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_owner_mismatch"
        )
    if not isinstance(receipt.queue_handed_off_at, datetime):
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_handoff_missing"
        )
    if receipt.queue_reconciliation_required_at is not None:
        raise TelegramMarketNoticeQueueFeedbackError(
            "market_notice_queue_feedback_reconciliation_conflict"
        )


def _clear_binding(
    receipt: MarketChannelNoticeReceipt,
    *,
    now: datetime,
) -> None:
    receipt.queue_job_id = None
    receipt.queue_handed_off_at = None
    receipt.updated_at = now


def _accumulate_attempts(
    receipt: MarketChannelNoticeReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    receipt.attempt_count = int(receipt.attempt_count or 0) + int(
        job.attempt_count or 0
    )


def _mark_stale(
    receipt: MarketChannelNoticeReceipt,
    *,
    job: TelegramDeliveryJobRecord,
    reason: str,
    now: datetime,
) -> None:
    _require_active_binding(receipt, job=job)
    _accumulate_attempts(receipt, job=job)
    receipt.status = MARKET_NOTICE_STATUS_SUPPRESSED_STALE
    receipt.next_retry_at = None
    receipt.last_attempt_at = now
    receipt.last_error_class = "stale_transition"
    receipt.last_error = reason[:500]
    _clear_binding(receipt, now=now)


class TelegramMarketNoticeQueueLifecycleFeedback:
    """Feedback adapter invoked inside each fenced main-queue transaction."""

    def __init__(self, *, expected_channel_id: int):
        if (
            isinstance(expected_channel_id, bool)
            or not isinstance(expected_channel_id, int)
            or expected_channel_id == 0
        ):
            raise ValueError("telegram_market_notice_feedback_channel_invalid")
        self.expected_channel_id = expected_channel_id

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        _validate_job_route(job, expected_channel_id=self.expected_channel_id)
        receipt = await _load_bound_receipt(db, job=job)
        _require_active_binding(receipt, job=job)
        await db.execute(
            select(MarketRuntimeState)
            .order_by(MarketRuntimeState.id.asc())
            .with_for_update()
            .limit(1)
        )
        decision = await validate_market_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramMarketNoticeQueueFeedbackError(
                "market_notice_queue_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        outcome = decision.outcome
        reason = str(decision.reason or f"freshness_{outcome.value}")
        if outcome in {
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            TelegramFreshnessOutcome.QUARANTINED,
        }:
            return
        _validate_job_route(job, expected_channel_id=self.expected_channel_id)
        receipt = await _load_bound_receipt(db, job=job)
        if outcome == TelegramFreshnessOutcome.RECLASSIFY:
            _require_active_binding(receipt, job=job)
            _accumulate_attempts(receipt, job=job)
            _clear_binding(receipt, now=now)
            receipt.next_retry_at = now
            receipt.last_error_class = None
            receipt.last_error = reason[:500]
        elif outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if (
                str(receipt.status or "").strip().lower()
                != MARKET_NOTICE_STATUS_SENT
                or _positive_int(receipt.telegram_message_id) is None
            ):
                raise TelegramMarketNoticeQueueFeedbackError(
                    "market_notice_queue_feedback_sent_noop_without_evidence"
                )
            _clear_binding(receipt, now=now)
        elif outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if reason == "market_freshness_receipt_terminal":
                if str(receipt.status or "").strip().lower() not in _TERMINAL_STATUSES:
                    raise TelegramMarketNoticeQueueFeedbackError(
                        "market_notice_queue_feedback_terminal_missing"
                    )
                _clear_binding(receipt, now=now)
            elif reason in _STALE_REASONS:
                _mark_stale(
                    receipt,
                    job=job,
                    reason=reason,
                    now=now,
                )
            else:
                raise TelegramMarketNoticeQueueFeedbackError(
                    "market_notice_queue_feedback_unknown_supersession"
                )
        else:
            raise TelegramMarketNoticeQueueFeedbackError(
                "market_notice_queue_feedback_freshness_outcome_invalid"
            )
        await db.flush()

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        _validate_job_route(job, expected_channel_id=self.expected_channel_id)
        receipt = await _load_bound_receipt(db, job=job)
        outcome = decision.outcome
        reason = str(decision.reason or outcome.value)
        if outcome == TelegramDeliveryOutcome.SENT:
            _require_active_binding(receipt, job=job)
            message_id = _positive_int(job.telegram_message_id)
            if message_id is None:
                raise TelegramMarketNoticeQueueFeedbackError(
                    "market_notice_queue_feedback_message_id_required"
                )
            _accumulate_attempts(receipt, job=job)
            receipt.status = MARKET_NOTICE_STATUS_SENT
            receipt.channel_id = str(_channel_id_from_job(job))
            receipt.telegram_message_id = message_id
            receipt.last_attempt_at = now
            receipt.next_retry_at = None
            receipt.sent_at = now
            receipt.last_error_class = None
            receipt.last_error = None
            _clear_binding(receipt, now=now)
        elif outcome in _HOLD_OUTCOMES:
            _require_active_binding(receipt, job=job)
        elif outcome in {
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            TelegramDeliveryOutcome.TERMINAL_FAILED,
        }:
            _require_active_binding(receipt, job=job)
            _accumulate_attempts(receipt, job=job)
            receipt.status = MARKET_NOTICE_STATUS_SKIPPED
            receipt.channel_id = str(_channel_id_from_job(job))
            receipt.telegram_message_id = None
            receipt.last_attempt_at = now
            receipt.next_retry_at = None
            receipt.sent_at = None
            receipt.last_error_class = str(
                job.last_error_class or "TelegramDeliveryQueue"
            )[:120]
            receipt.last_error = str(
                job.last_error_message or reason or ""
            )[:500] or None
            _clear_binding(receipt, now=now)
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            _require_active_binding(receipt, job=job)
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TelegramMarketNoticeQueueFeedbackError(
                "market_notice_queue_feedback_delivery_outcome_invalid"
            )
        await db.flush()
