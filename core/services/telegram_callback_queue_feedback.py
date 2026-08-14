"""Lifecycle guard for direct callback-answer queue jobs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_callback_freshness import (
    validate_telegram_callback_delivery_freshness,
    validate_telegram_callback_job_contract,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class TelegramCallbackQueueFeedbackError(RuntimeError):
    """Reject callback lifecycle transitions when the source contract drifts."""


def _require_valid_contract(job: TelegramDeliveryJobRecord) -> None:
    decision = validate_telegram_callback_job_contract(job)
    if decision is not None:
        raise TelegramCallbackQueueFeedbackError(
            f"telegram_callback_lifecycle_contract_rejected:{decision.reason}"
        )


class TelegramCallbackQueueLifecycleFeedback:
    """The queue job itself is the durable source for direct callback answers."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        decision = await validate_telegram_callback_delivery_freshness(
            db,
            job,
            now,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramCallbackQueueFeedbackError(
                "telegram_callback_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        del db, now
        if decision.outcome == TelegramFreshnessOutcome.QUARANTINED:
            return
        if decision.outcome != TelegramFreshnessOutcome.EXPIRED_INTERACTION:
            raise TelegramCallbackQueueFeedbackError(
                "telegram_callback_freshness_outcome_invalid"
            )
        _require_valid_contract(job)

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        del db, decision, now
        _require_valid_contract(job)
