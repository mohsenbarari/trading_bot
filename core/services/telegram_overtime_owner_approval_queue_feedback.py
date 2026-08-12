"""Lifecycle feedback: start the 30s clock only after a message id lands."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.offer_lifecycle import read_normal_lifetime_minutes
from core.services.offer_overtime_request_service import (
    invalidate_request,
    load_overtime_request_by_public_id,
    mark_presented,
    promote_next_for_owner,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    payload_request_public_id,
)
from core.telegram_delivery_overtime_owner_approval_freshness import (
    validate_overtime_owner_approval_delivery_freshness,
    validate_overtime_owner_approval_job_contract,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.trading_settings import get_trading_settings_async
from models.offer_request import OfferRequestStatus
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class OvertimeOwnerApprovalQueueFeedbackError(RuntimeError):
    """Reject overtime approval lifecycle transitions on contract drift."""


def _require_valid_contract(job: TelegramDeliveryJobRecord) -> None:
    decision = validate_overtime_owner_approval_job_contract(job)
    if decision is not None:
        raise OvertimeOwnerApprovalQueueFeedbackError(
            f"overtime_owner_approval_lifecycle_contract_rejected:{decision.reason}"
        )


def _positive_message_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


class TelegramOvertimeOwnerApprovalQueueLifecycleFeedback:
    """Bind queue SENT evidence to OfferRequest.presented_at / decision clock."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        decision = await validate_overtime_owner_approval_delivery_freshness(
            db,
            job,
            now,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        if decision.outcome == TelegramFreshnessOutcome.QUARANTINED:
            return
        if decision.outcome in {
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            TelegramFreshnessOutcome.RECLASSIFY,
            TelegramFreshnessOutcome.SENT_NOOP,
            TelegramFreshnessOutcome.SUPERSEDED,
            TelegramFreshnessOutcome.SEND,
        }:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_freshness_outcome_invalid:"
                f"{decision.outcome.value}"
            )
        if decision.outcome != TelegramFreshnessOutcome.EXPIRED_INTERACTION:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_freshness_outcome_invalid"
            )
        _require_valid_contract(job)
        request_public_id = payload_request_public_id(job.payload)
        if request_public_id is None:
            return
        ledger = await load_overtime_request_by_public_id(
            db,
            request_public_id,
            for_update=True,
        )
        if ledger is None:
            return
        status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
        if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
            return
        owner_id = getattr(ledger, "offer_owner_user_id", None)
        home = getattr(ledger, "request_home_server", None)
        await invalidate_request(
            db,
            ledger,
            reason=str(decision.reason or "overtime_owner_approval_delivery_expired"),
            now=now,
            flush=True,
        )
        if owner_id is None or not home:
            return
        settings = await get_trading_settings_async()
        await promote_next_for_owner(
            db,
            request_home_server=str(home),
            offer_owner_user_id=int(owner_id),
            normal_lifetime_minutes=read_normal_lifetime_minutes(settings),
            now=now,
            flush=True,
        )

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        _require_valid_contract(job)
        if decision.outcome != TelegramDeliveryOutcome.SENT:
            return
        message_id = _positive_message_id(getattr(job, "telegram_message_id", None))
        if message_id is None:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_sent_requires_message_id"
            )
        request_public_id = payload_request_public_id(job.payload)
        if request_public_id is None:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_sent_request_id_missing"
            )
        ledger = await load_overtime_request_by_public_id(
            db,
            request_public_id,
            for_update=True,
        )
        if ledger is None:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                "overtime_owner_approval_sent_request_missing"
            )
        status = str(getattr(ledger.result_status, "value", ledger.result_status) or "")
        if status == OfferRequestStatus.OVERTIME_PRESENTED.value:
            # Idempotent replay after crash recovery.
            if (
                _positive_message_id(getattr(ledger, "telegram_message_id", None))
                not in {None, message_id}
            ):
                raise OvertimeOwnerApprovalQueueFeedbackError(
                    "overtime_owner_approval_message_id_conflict"
                )
            return
        if status != OfferRequestStatus.OVERTIME_DELIVERING.value:
            raise OvertimeOwnerApprovalQueueFeedbackError(
                f"overtime_owner_approval_sent_status_invalid:{status}"
            )
        await mark_presented(
            db,
            ledger,
            presented_at=now,
            telegram_message_id=message_id,
            flush=True,
        )
