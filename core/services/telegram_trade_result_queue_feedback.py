"""Atomic queue-to-receipt feedback for private trade-result delivery."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.trade_delivery_receipt_service import (
    TELEGRAM_DESTINATION_SERVER,
    TRADE_COMPLETED_EVENT_TYPE,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_trade_freshness import (
    trade_result_receipt_dedupe_from_source_natural_id,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_receipt_is_bound_to_job,
)
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.trade import Trade
from models.trade_delivery_receipt import (
    TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES,
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User


_ACTIVE_RECEIPT_STATUSES = {
    TradeDeliveryReceiptStatus.PENDING.value,
    TradeDeliveryReceiptStatus.RETRY_PENDING.value,
}
_SUPERSEDED_SKIP_REASONS = {
    "trade_freshness_expired_after_outage",
    "trade_freshness_recipient_access_denied",
    "trade_freshness_recipient_missing",
    "trade_freshness_recipient_unlinked",
    "trade_freshness_recipient_relinked",
    "trade_freshness_trade_not_completed",
}
_DELIVERY_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}


class TradeResultQueueFeedbackError(RuntimeError):
    """Raised to rollback a queue transition when receipt feedback is unsafe."""


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


def _validate_job_family(job: TelegramDeliveryJobRecord) -> None:
    if (
        _enum_value(job.action_kind) != TelegramDeliveryAction.TRADE_RESULT.value
        or _enum_value(job.feeder_kind) != TelegramFeederKind.TRADE.value
    ):
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_family_mismatch"
        )


def _validate_job_route(job: TelegramDeliveryJobRecord) -> None:
    _validate_job_family(job)
    if (
        str(job.method or "") != "sendMessage"
        or str(job.bot_identity or "") != "primary"
        or _enum_value(job.destination_class)
        != TelegramDestinationClass.PRIVATE.value
    ):
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_route_mismatch"
        )


async def _load_bound_receipt(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> TradeDeliveryReceipt:
    dedupe_key = trade_result_receipt_dedupe_from_source_natural_id(
        job.source_natural_id
    )
    if dedupe_key is None:
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_source_invalid"
        )
    receipt = (
        await db.execute(
            select(TradeDeliveryReceipt)
            .where(TradeDeliveryReceipt.dedupe_key == dedupe_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_receipt_missing"
        )
    if (
        str(receipt.event_type or "") != TRADE_COMPLETED_EVENT_TYPE
        or _enum_value(receipt.channel) != TradeDeliveryChannel.TELEGRAM.value
        or str(receipt.destination_server or "") != TELEGRAM_DESTINATION_SERVER
    ):
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_receipt_route_mismatch"
        )
    return receipt


def _require_active_binding(
    receipt: TradeDeliveryReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    if _enum_value(receipt.status) not in _ACTIVE_RECEIPT_STATUSES:
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_receipt_not_active"
        )
    if not trade_result_receipt_is_bound_to_job(receipt, int(job.id)):
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_owner_mismatch"
        )


def _clear_binding(
    receipt: TradeDeliveryReceipt,
    *,
    now: datetime,
    reason: str,
) -> None:
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.reason = str(reason or "telegram_trade_result_queue_released")[:96]
    receipt.updated_at = now


def _accumulate_queue_attempts(
    receipt: TradeDeliveryReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    receipt.attempt_count = int(receipt.attempt_count or 0) + int(
        job.attempt_count or 0
    )


def _finalize_active_receipt(
    receipt: TradeDeliveryReceipt,
    *,
    job: TelegramDeliveryJobRecord,
    target: TradeDeliveryReceiptStatus,
    reason: str,
    now: datetime,
    telegram_message_id: int | None = None,
) -> None:
    current_status = _enum_value(receipt.status)
    target_value = target.value
    if current_status in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES:
        if current_status != target_value:
            raise TradeResultQueueFeedbackError(
                "trade_result_queue_feedback_terminal_conflict"
            )
        if target == TradeDeliveryReceiptStatus.SENT:
            current_message_id = _positive_int(receipt.telegram_message_id)
            if current_message_id != _positive_int(telegram_message_id):
                raise TradeResultQueueFeedbackError(
                    "trade_result_queue_feedback_message_id_conflict"
                )
        _clear_binding(receipt, now=now, reason=reason)
        return

    _require_active_binding(receipt, job=job)
    normalized_message_id = _positive_int(telegram_message_id)
    if target == TradeDeliveryReceiptStatus.SENT and normalized_message_id is None:
        raise TradeResultQueueFeedbackError(
            "trade_result_queue_feedback_message_id_required"
        )
    receipt.status = target
    receipt.reason = str(reason or "telegram_trade_result_queue_terminal")[:96]
    receipt.telegram_message_id = (
        normalized_message_id
        if target == TradeDeliveryReceiptStatus.SENT
        else None
    )
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.next_retry_at = None
    _accumulate_queue_attempts(receipt, job=job)
    receipt.last_error_class = (
        None
        if target == TradeDeliveryReceiptStatus.SENT
        else str(job.last_error_class or "TelegramDeliveryQueue")[:120]
    )
    receipt.last_error = (
        None
        if target == TradeDeliveryReceiptStatus.SENT
        else str(job.last_error_message or reason or "")[:500] or None
    )
    receipt.updated_at = now
    receipt.terminal_at = now
    receipt.sent_at = (
        now if target == TradeDeliveryReceiptStatus.SENT else None
    )


class TradeResultQueueLifecycleFeedback:
    """Feedback adapter invoked inside the queue job transaction."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        _validate_job_route(job)
        receipt = await _load_bound_receipt(db, job=job)
        _require_active_binding(receipt, job=job)
        # Lock the authoritative identity rows for the remainder of the short
        # dispatch-marker transaction. The validator rereads them below, so a
        # terminal receipt, relink, deletion, or trade-state update cannot land
        # between this final check and the durable dispatch marker.
        await db.execute(
            select(Trade)
            .where(Trade.trade_number == int(receipt.trade_number))
            .with_for_update()
        )
        await db.execute(
            select(User)
            .where(User.id == int(receipt.recipient_user_id))
            .with_for_update()
        )
        # Bot access also depends on relation-table projections. Lock every
        # existing relation row for this recipient so a tier/status mutation
        # cannot commit between the final access read and the dispatch marker.
        await db.execute(
            select(AccountantRelation.id)
            .where(
                AccountantRelation.accountant_user_id
                == int(receipt.recipient_user_id)
            )
            .with_for_update()
        )
        await db.execute(
            select(CustomerRelation.id)
            .where(
                CustomerRelation.customer_user_id
                == int(receipt.recipient_user_id)
            )
            .with_for_update()
        )
        from core.telegram_delivery_trade_freshness import (
            validate_trade_result_telegram_delivery_freshness,
        )

        decision = await validate_trade_result_telegram_delivery_freshness(
            db,
            job,
            now,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TradeResultQueueFeedbackError(
                "trade_result_queue_dispatch_guard_rejected:"
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

        # WAIT and QUARANTINED are also the safe outcomes for a missing source,
        # missing receipt, corrupt route, or broken binding. Requiring that same
        # source/binding here would roll back the queue transition and poison the
        # lane. Preserve the job decision and leave any receipt marker untouched
        # for reconciliation instead.
        if outcome in {
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            TelegramFreshnessOutcome.QUARANTINED,
        }:
            return

        _validate_job_route(job)
        receipt = await _load_bound_receipt(db, job=job)

        if outcome == TelegramFreshnessOutcome.RECLASSIFY:
            _require_active_binding(receipt, job=job)
            _accumulate_queue_attempts(receipt, job=job)
            _clear_binding(receipt, now=now, reason=reason)
            receipt.next_retry_at = now
        elif outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if (
                _enum_value(receipt.status)
                != TradeDeliveryReceiptStatus.SENT.value
                or _positive_int(receipt.telegram_message_id) is None
            ):
                raise TradeResultQueueFeedbackError(
                    "trade_result_queue_feedback_sent_noop_without_evidence"
                )
            _clear_binding(receipt, now=now, reason=reason)
        elif outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if reason == "trade_freshness_receipt_terminal":
                if _enum_value(receipt.status) not in TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES:
                    raise TradeResultQueueFeedbackError(
                        "trade_result_queue_feedback_terminal_evidence_missing"
                    )
                _clear_binding(receipt, now=now, reason=reason)
            elif reason in _SUPERSEDED_SKIP_REASONS:
                _finalize_active_receipt(
                    receipt,
                    job=job,
                    target=TradeDeliveryReceiptStatus.SKIPPED,
                    reason=reason,
                    now=now,
                )
            else:
                raise TradeResultQueueFeedbackError(
                    "trade_result_queue_feedback_unknown_supersession"
                )
        else:
            raise TradeResultQueueFeedbackError(
                "trade_result_queue_feedback_freshness_outcome_invalid"
            )
        await db.flush()

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        _validate_job_route(job)
        receipt = await _load_bound_receipt(db, job=job)
        outcome = decision.outcome
        reason = str(decision.reason or outcome.value)

        if outcome == TelegramDeliveryOutcome.SENT:
            _finalize_active_receipt(
                receipt,
                job=job,
                target=TradeDeliveryReceiptStatus.SENT,
                reason="telegram_sent",
                now=now,
                telegram_message_id=job.telegram_message_id,
            )
        elif outcome in _DELIVERY_HOLD_OUTCOMES:
            _require_active_binding(receipt, job=job)
        elif outcome == TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE:
            _finalize_active_receipt(
                receipt,
                job=job,
                target=TradeDeliveryReceiptStatus.SKIPPED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.TERMINAL_FAILED:
            _finalize_active_receipt(
                receipt,
                job=job,
                target=TradeDeliveryReceiptStatus.PERMANENT_FAILED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            # A quarantine can be caused by an adapter/envelope invariant,
            # not by a definitive recipient failure. Keep the domain receipt
            # recoverable until an operator-backed reconciliation resolves it.
            _require_active_binding(receipt, job=job)
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TradeResultQueueFeedbackError(
                "trade_result_queue_feedback_delivery_outcome_invalid"
            )
        await db.flush()
