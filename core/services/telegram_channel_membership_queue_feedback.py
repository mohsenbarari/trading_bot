"""Atomic source feedback for channel membership ban/unban queue jobs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_channel_membership_freshness import (
    CHANNEL_MEMBERSHIP_FRESHNESS_ACTIONS,
    validate_channel_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_channel_membership_saga import TelegramChannelMembershipSaga
from models.telegram_delivery_job import TelegramDeliveryJobRecord


_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}


class TelegramChannelMembershipQueueFeedbackError(RuntimeError):
    """Roll back a queue transition if saga ownership is inconsistent."""


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


async def _load_bound_saga(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
) -> TelegramChannelMembershipSaga:
    if _enum_value(job.action_kind) not in {
        action.value for action in CHANNEL_MEMBERSHIP_FRESHNESS_ACTIONS
    }:
        raise TelegramChannelMembershipQueueFeedbackError(
            "membership_feedback_action_invalid"
        )
    saga = (
        await db.execute(
            select(TelegramChannelMembershipSaga)
            .where(
                or_(
                    TelegramChannelMembershipSaga.ban_job_id == job.id,
                    TelegramChannelMembershipSaga.unban_job_id == job.id,
                )
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if saga is None:
        raise TelegramChannelMembershipQueueFeedbackError(
            "membership_feedback_saga_missing"
        )
    return saga


def _phase(
    saga: TelegramChannelMembershipSaga,
    job: TelegramDeliveryJobRecord,
) -> str:
    action = TelegramDeliveryAction(_enum_value(job.action_kind))
    if action == TelegramDeliveryAction.CHANNEL_MEMBER_BAN:
        if int(saga.ban_job_id or 0) != int(job.id):
            raise TelegramChannelMembershipQueueFeedbackError(
                "membership_feedback_ban_owner_mismatch"
            )
        return "ban"
    if int(saga.unban_job_id or 0) != int(job.id):
        raise TelegramChannelMembershipQueueFeedbackError(
            "membership_feedback_unban_owner_mismatch"
        )
    return "unban"


class TelegramChannelMembershipQueueLifecycleFeedback:
    def __init__(self, *, expected_channel_id: int) -> None:
        if isinstance(expected_channel_id, bool) or int(expected_channel_id) == 0:
            raise ValueError("membership_feedback_channel_invalid")
        self.expected_channel_id = int(expected_channel_id)

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        saga = await _load_bound_saga(db, job)
        _phase(saga, job)
        decision = await validate_channel_membership_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramChannelMembershipQueueFeedbackError(
                "membership_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        if decision.outcome == TelegramFreshnessOutcome.WAIT_DEPENDENCY:
            return
        saga = await _load_bound_saga(db, job)
        _phase(saga, job)
        if decision.outcome == TelegramFreshnessOutcome.QUARANTINED:
            saga.state = "blocked"
            saga.reason = str(decision.reason or "membership_freshness_quarantined")[:160]
            saga.last_error_class = "TelegramMembershipFreshnessQuarantined"
            saga.last_error_message = saga.reason
        elif decision.outcome == TelegramFreshnessOutcome.SUPERSEDED:
            saga.state = "superseded"
            saga.reason = str(decision.reason or "membership_source_superseded")[:160]
            saga.terminal_at = now
        elif decision.outcome == TelegramFreshnessOutcome.SENT_NOOP:
            # The durable saga already carries the successful fact.
            saga.reason = str(decision.reason or "membership_already_applied")[:160]
        else:
            raise TelegramChannelMembershipQueueFeedbackError(
                "membership_feedback_freshness_outcome_invalid"
            )
        saga.updated_at = now
        await db.flush()

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        saga = await _load_bound_saga(db, job)
        phase = _phase(saga, job)
        outcome = decision.outcome
        reason = str(decision.reason or outcome.value)[:160]
        if outcome in {TelegramDeliveryOutcome.SENT, TelegramDeliveryOutcome.SENT_NOOP}:
            saga.last_error_class = None
            saga.last_error_message = None
            if phase == "ban":
                saga.state = "ban_succeeded"
                saga.ban_succeeded_at = now
                saga.reason = "membership_ban_succeeded"
            else:
                if saga.ban_succeeded_at is None:
                    raise TelegramChannelMembershipQueueFeedbackError(
                        "membership_unban_completed_without_ban_evidence"
                    )
                saga.state = "complete"
                saga.completed_at = now
                saga.terminal_at = now
                saga.reason = "membership_removal_complete"
        elif outcome in _HOLD_OUTCOMES:
            saga.reason = reason
        elif outcome in {
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            TelegramDeliveryOutcome.TERMINAL_FAILED,
        }:
            saga.state = "terminal_failed"
            saga.reason = reason
            saga.last_error_class = str(
                job.last_error_class or "TelegramMembershipDelivery"
            )[:120]
            saga.last_error_message = str(job.last_error_message or reason)[:500]
            saga.terminal_at = now
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            saga.state = "blocked"
            saga.reason = reason
            saga.last_error_class = str(
                job.last_error_class or "TelegramMembershipQuarantined"
            )[:120]
            saga.last_error_message = str(job.last_error_message or reason)[:500]
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TelegramChannelMembershipQueueFeedbackError(
                "membership_feedback_delivery_outcome_invalid"
            )
        saga.updated_at = now
        await db.flush()
