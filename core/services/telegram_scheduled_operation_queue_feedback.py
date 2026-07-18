"""Atomic lifecycle feedback for scheduled Telegram source receipts."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_scheduled_operation_freshness import (
    SCHEDULED_OPERATION_FRESHNESS_ACTIONS,
    telegram_scheduled_operation_dedupe_from_source,
    validate_scheduled_operation_telegram_delivery_freshness,
    validate_telegram_scheduled_operation_contract,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_scheduled_operation import TelegramScheduledOperation
from models.user import User


_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}


class TelegramScheduledOperationQueueFeedbackError(RuntimeError):
    """Roll back a queue transition when source ownership is inconsistent."""


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


async def _load_bound_operation(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> TelegramScheduledOperation:
    if _enum_value(job.action_kind) not in {
        action.value for action in SCHEDULED_OPERATION_FRESHNESS_ACTIONS
    }:
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_action_invalid"
        )
    dedupe_key = telegram_scheduled_operation_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_source_invalid"
        )
    operation = (
        await db.execute(
            select(TelegramScheduledOperation)
            .where(TelegramScheduledOperation.dedupe_key == dedupe_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if operation is None:
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_source_missing"
        )
    return operation


def _require_active_binding(
    operation: TelegramScheduledOperation,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    if str(operation.status or "") != "pending":
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_source_not_active"
        )
    if _positive_int(operation.queue_job_id) != _positive_int(job.id):
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_owner_mismatch"
        )
    if not isinstance(operation.queue_handed_off_at, datetime):
        raise TelegramScheduledOperationQueueFeedbackError(
            "scheduled_operation_feedback_handoff_missing"
        )


def _clear_binding(
    operation: TelegramScheduledOperation,
    *,
    now: datetime,
    reason: str,
) -> None:
    operation.queue_job_id = None
    operation.queue_handed_off_at = None
    operation.reconciliation_required_at = None
    operation.reason = str(reason or "scheduled_operation_released")[:160]
    operation.updated_at = now


def _finalize(
    operation: TelegramScheduledOperation,
    *,
    job: TelegramDeliveryJobRecord,
    status: str,
    reason: str,
    now: datetime,
) -> None:
    _require_active_binding(operation, job=job)
    operation.status = status
    operation.attempt_count = int(operation.attempt_count or 0) + int(
        job.attempt_count or 0
    )
    operation.telegram_message_id = (
        _positive_int(job.telegram_message_id) if status == "sent" else None
    )
    operation.sent_at = now if status == "sent" else None
    operation.terminal_at = now
    operation.last_error_class = (
        None if status == "sent" else str(job.last_error_class or "TelegramDeliveryQueue")[:120]
    )
    operation.last_error_message = (
        None if status == "sent" else str(job.last_error_message or reason or "")[:500] or None
    )
    _clear_binding(operation, now=now, reason=reason)


class TelegramScheduledOperationQueueLifecycleFeedback:
    def __init__(self, *, expected_channel_id: int) -> None:
        if isinstance(expected_channel_id, bool) or not isinstance(expected_channel_id, int) or expected_channel_id == 0:
            raise ValueError("scheduled_operation_feedback_channel_invalid")
        self.expected_channel_id = expected_channel_id

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        operation = await _load_bound_operation(db, job=job)
        _require_active_binding(operation, job=job)
        validate_telegram_scheduled_operation_contract(
            operation,
            expected_channel_id=self.expected_channel_id,
        )
        if operation.recipient_user_id is not None:
            await db.execute(
                select(User.id)
                .where(User.id == operation.recipient_user_id)
                .with_for_update()
            )
        decision = await validate_scheduled_operation_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramScheduledOperationQueueFeedbackError(
                "scheduled_operation_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        if decision.outcome in {
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            TelegramFreshnessOutcome.QUARANTINED,
        }:
            return
        operation = await _load_bound_operation(db, job=job)
        reason = str(decision.reason or decision.outcome.value)
        if decision.outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if str(operation.status or "") != "sent":
                raise TelegramScheduledOperationQueueFeedbackError(
                    "scheduled_operation_feedback_sent_noop_without_evidence"
                )
            _clear_binding(operation, now=now, reason=reason)
        elif decision.outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if str(operation.status or "") in {"cancelled", "skipped", "terminal_failed"}:
                _clear_binding(operation, now=now, reason=reason)
            else:
                _finalize(
                    operation,
                    job=job,
                    status="skipped",
                    reason=reason,
                    now=now,
                )
        else:
            raise TelegramScheduledOperationQueueFeedbackError(
                "scheduled_operation_feedback_freshness_outcome_invalid"
            )
        await db.flush()

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        operation = await _load_bound_operation(db, job=job)
        outcome = decision.outcome
        reason = str(decision.reason or outcome.value)
        if outcome in {TelegramDeliveryOutcome.SENT, TelegramDeliveryOutcome.SENT_NOOP}:
            _finalize(
                operation,
                job=job,
                status="sent",
                reason="telegram_sent" if outcome == TelegramDeliveryOutcome.SENT else "telegram_sent_noop",
                now=now,
            )
        elif outcome in _HOLD_OUTCOMES:
            _require_active_binding(operation, job=job)
        elif outcome == TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE:
            _finalize(
                operation,
                job=job,
                status="skipped",
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.TERMINAL_FAILED:
            _finalize(
                operation,
                job=job,
                status="terminal_failed",
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            _require_active_binding(operation, job=job)
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TelegramScheduledOperationQueueFeedbackError(
                "scheduled_operation_feedback_delivery_outcome_invalid"
            )
        await db.flush()
