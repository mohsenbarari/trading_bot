"""Atomic main-queue lifecycle feedback for Telegram admin broadcasts."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_admin_broadcast_delivery_service import (
    finalize_telegram_admin_broadcast_status,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    telegram_admin_broadcast_receipt_dedupe_from_source,
    validate_admin_broadcast_telegram_delivery_freshness,
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
from models.accountant_relation import AccountantRelation
from models.customer_relation import CustomerRelation
from models.telegram_admin_broadcast import (
    TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.user import User


_ACTIVE_RECEIPT_STATUSES = {
    TelegramAdminBroadcastReceiptStatus.PENDING.value,
    TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED.value,
}
_DELIVERY_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}
_SUPERSEDED_SKIP_REASONS = {
    "admin_broadcast_freshness_recipient_missing",
    "admin_broadcast_freshness_recipient_unlinked",
    "admin_broadcast_freshness_recipient_relinked",
    "admin_broadcast_freshness_recipient_access_denied",
}


class TelegramAdminBroadcastQueueFeedbackError(RuntimeError):
    """Roll back a queue transition when broadcast feedback is unsafe."""


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


def _validate_job_route(job: TelegramDeliveryJobRecord) -> None:
    if (
        _enum_value(job.action_kind) != TelegramDeliveryAction.ADMIN_BROADCAST.value
        or _enum_value(job.feeder_kind) != TelegramFeederKind.ADMIN_SYSTEM.value
        or _enum_value(job.destination_class)
        != TelegramDestinationClass.PRIVATE.value
        or str(job.method or "") != "sendMessage"
        or str(job.bot_identity or "") != "primary"
    ):
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_route_mismatch"
        )


async def _load_bound_receipt(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> TelegramAdminBroadcastReceipt:
    dedupe_key = telegram_admin_broadcast_receipt_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_source_invalid"
        )
    receipt = (
        await db.execute(
            select(TelegramAdminBroadcastReceipt)
            .where(TelegramAdminBroadcastReceipt.dedupe_key == dedupe_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_receipt_missing"
        )
    return receipt


def _require_active_binding(
    receipt: TelegramAdminBroadcastReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    if _enum_value(receipt.status) not in _ACTIVE_RECEIPT_STATUSES:
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_receipt_not_active"
        )
    if _positive_int(receipt.queue_job_id) != _positive_int(job.id):
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_owner_mismatch"
        )
    if not isinstance(receipt.queue_handed_off_at, datetime):
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_handoff_evidence_missing"
        )


def _clear_binding(
    receipt: TelegramAdminBroadcastReceipt,
    *,
    now: datetime,
    reason: str,
) -> None:
    receipt.queue_job_id = None
    receipt.queue_handed_off_at = None
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.reason = str(reason or "admin_broadcast_queue_released")[:120]
    receipt.updated_at = now


def _accumulate_queue_attempts(
    receipt: TelegramAdminBroadcastReceipt,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    receipt.attempt_count = int(receipt.attempt_count or 0) + int(
        job.attempt_count or 0
    )


def _payload_chat_id(job: TelegramDeliveryJobRecord) -> int | None:
    payload = getattr(job, "payload", None)
    return _positive_int(payload.get("chat_id")) if isinstance(payload, dict) else None


async def _finalize_active_receipt(
    db: AsyncSession,
    *,
    receipt: TelegramAdminBroadcastReceipt,
    job: TelegramDeliveryJobRecord,
    target: TelegramAdminBroadcastReceiptStatus,
    reason: str,
    now: datetime,
    telegram_message_id: int | None = None,
) -> None:
    current_status = _enum_value(receipt.status)
    target_value = target.value
    if current_status in TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES:
        if current_status != target_value:
            raise TelegramAdminBroadcastQueueFeedbackError(
                "admin_broadcast_queue_feedback_terminal_conflict"
            )
        if target == TelegramAdminBroadcastReceiptStatus.SENT:
            if _positive_int(receipt.telegram_message_id) != _positive_int(
                telegram_message_id
            ):
                raise TelegramAdminBroadcastQueueFeedbackError(
                    "admin_broadcast_queue_feedback_message_id_conflict"
                )
        _clear_binding(receipt, now=now, reason=reason)
        await finalize_telegram_admin_broadcast_status(
            db,
            broadcast_id=int(receipt.broadcast_id),
            now=now,
        )
        return

    _require_active_binding(receipt, job=job)
    normalized_message_id = _positive_int(telegram_message_id)
    if target == TelegramAdminBroadcastReceiptStatus.SENT and normalized_message_id is None:
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_message_id_required"
        )
    telegram_id_at_send = _payload_chat_id(job)
    if telegram_id_at_send is None:
        raise TelegramAdminBroadcastQueueFeedbackError(
            "admin_broadcast_queue_feedback_chat_id_required"
        )
    receipt.status = target
    receipt.telegram_id_at_send = telegram_id_at_send
    receipt.telegram_message_id = (
        normalized_message_id
        if target == TelegramAdminBroadcastReceiptStatus.SENT
        else None
    )
    receipt.next_retry_at = None
    _accumulate_queue_attempts(receipt, job=job)
    receipt.last_error_class = (
        None
        if target == TelegramAdminBroadcastReceiptStatus.SENT
        else str(job.last_error_class or "TelegramDeliveryQueue")[:120]
    )
    receipt.last_error_message = (
        None
        if target == TelegramAdminBroadcastReceiptStatus.SENT
        else str(job.last_error_message or reason or "")[:500] or None
    )
    receipt.sent_at = (
        now if target == TelegramAdminBroadcastReceiptStatus.SENT else None
    )
    receipt.terminal_at = now
    _clear_binding(receipt, now=now, reason=reason)
    # AsyncSessionLocal disables autoflush. Persist the terminal receipt before
    # the aggregate status query or the last recipient would leave the
    # broadcast incorrectly RUNNING.
    await db.flush()
    await finalize_telegram_admin_broadcast_status(
        db,
        broadcast_id=int(receipt.broadcast_id),
        now=now,
    )


class TelegramAdminBroadcastQueueLifecycleFeedback:
    """Feedback adapter invoked inside each fenced queue transaction."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        _validate_job_route(job)
        receipt = await _load_bound_receipt(db, job=job)
        _require_active_binding(receipt, job=job)
        await db.execute(
            select(TelegramAdminBroadcast)
            .where(TelegramAdminBroadcast.id == int(receipt.broadcast_id))
            .with_for_update()
        )
        await db.execute(
            select(User)
            .where(User.id == int(receipt.recipient_user_id))
            .with_for_update()
        )
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
        decision = await validate_admin_broadcast_telegram_delivery_freshness(
            db,
            job,
            now,
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramAdminBroadcastQueueFeedbackError(
                "admin_broadcast_queue_dispatch_guard_rejected:"
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
        _validate_job_route(job)
        receipt = await _load_bound_receipt(db, job=job)
        if outcome == TelegramFreshnessOutcome.RECLASSIFY:
            _require_active_binding(receipt, job=job)
            _accumulate_queue_attempts(receipt, job=job)
            _clear_binding(receipt, now=now, reason=reason)
            receipt.status = TelegramAdminBroadcastReceiptStatus.PENDING
            receipt.next_retry_at = now
        elif outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if (
                _enum_value(receipt.status)
                != TelegramAdminBroadcastReceiptStatus.SENT.value
                or _positive_int(receipt.telegram_message_id) is None
            ):
                raise TelegramAdminBroadcastQueueFeedbackError(
                    "admin_broadcast_queue_feedback_sent_noop_without_evidence"
                )
            _clear_binding(receipt, now=now, reason=reason)
            await finalize_telegram_admin_broadcast_status(
                db,
                broadcast_id=int(receipt.broadcast_id),
                now=now,
            )
        elif outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if reason == "admin_broadcast_freshness_receipt_terminal":
                if (
                    _enum_value(receipt.status)
                    not in TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES
                ):
                    raise TelegramAdminBroadcastQueueFeedbackError(
                        "admin_broadcast_queue_feedback_terminal_evidence_missing"
                    )
                _clear_binding(receipt, now=now, reason=reason)
                await finalize_telegram_admin_broadcast_status(
                    db,
                    broadcast_id=int(receipt.broadcast_id),
                    now=now,
                )
            elif reason in _SUPERSEDED_SKIP_REASONS:
                await _finalize_active_receipt(
                    db,
                    receipt=receipt,
                    job=job,
                    target=TelegramAdminBroadcastReceiptStatus.SKIPPED,
                    reason=reason,
                    now=now,
                )
            elif reason == "admin_broadcast_freshness_broadcast_missing":
                await _finalize_active_receipt(
                    db,
                    receipt=receipt,
                    job=job,
                    target=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
                    reason=reason,
                    now=now,
                )
            else:
                raise TelegramAdminBroadcastQueueFeedbackError(
                    "admin_broadcast_queue_feedback_unknown_supersession"
                )
        else:
            raise TelegramAdminBroadcastQueueFeedbackError(
                "admin_broadcast_queue_feedback_freshness_outcome_invalid"
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
            await _finalize_active_receipt(
                db,
                receipt=receipt,
                job=job,
                target=TelegramAdminBroadcastReceiptStatus.SENT,
                reason="telegram_sent",
                now=now,
                telegram_message_id=job.telegram_message_id,
            )
        elif outcome in _DELIVERY_HOLD_OUTCOMES:
            _require_active_binding(receipt, job=job)
        elif outcome == TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE:
            await _finalize_active_receipt(
                db,
                receipt=receipt,
                job=job,
                target=TelegramAdminBroadcastReceiptStatus.SKIPPED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.TERMINAL_FAILED:
            await _finalize_active_receipt(
                db,
                receipt=receipt,
                job=job,
                target=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            _require_active_binding(receipt, job=job)
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TelegramAdminBroadcastQueueFeedbackError(
                "admin_broadcast_queue_feedback_delivery_outcome_invalid"
            )
        await db.flush()
