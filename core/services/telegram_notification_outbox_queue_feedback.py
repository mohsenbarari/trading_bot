"""Atomic main-queue lifecycle feedback for the notification outbox."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_new_user_membership_freshness import (
    telegram_notification_outbox_dedupe_from_source,
    validate_new_user_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTION_VALUES,
    telegram_notification_action_policy,
)
from core.telegram_delivery_notification_action_freshness import (
    telegram_notification_action_interaction_result_contract,
    telegram_notification_action_outbox_dedupe_from_source,
    validate_notification_action_telegram_delivery_freshness,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultOutcome,
    apply_interaction_delivery_result,
)
from core.telegram_delivery_offer_success_contract import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
)
from core.telegram_delivery_offer_success_freshness import (
    telegram_offer_success_outbox_dedupe_from_source,
    validate_offer_success_telegram_delivery_freshness,
)
from core.telegram_delivery_repeat_offer_freshness import (
    telegram_repeat_offer_outbox_dedupe_from_source,
    validate_repeat_offer_response_telegram_delivery_freshness,
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
from models.offer import Offer
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState
from models.telegram_notification_outbox import (
    TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES,
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


_ACTIVE_STATUSES = {
    TelegramNotificationOutboxStatus.PENDING.value,
    TelegramNotificationOutboxStatus.RETRYABLE_FAILED.value,
}
_HOLD_OUTCOMES = {
    TelegramDeliveryOutcome.RETRY_PENDING,
    TelegramDeliveryOutcome.DESTINATION_PAUSED,
    TelegramDeliveryOutcome.BOT_PAUSED,
    TelegramDeliveryOutcome.GATEWAY_PAUSED,
    TelegramDeliveryOutcome.AMBIGUOUS,
    TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
}
_SUPERSEDED_SKIP_REASONS = {
    "new_user_membership_freshness_recipient_missing",
    "new_user_membership_freshness_recipient_unlinked",
    "new_user_membership_freshness_recipient_relinked",
    "new_user_membership_freshness_recipient_access_denied",
    "new_user_membership_freshness_customer_excluded",
    "repeat_offer_response_freshness_recipient_missing",
    "repeat_offer_response_freshness_recipient_unlinked",
    "repeat_offer_response_freshness_recipient_relinked",
    "repeat_offer_response_freshness_recipient_access_denied",
    "notification_action_freshness_recipient_missing",
    "notification_action_freshness_recipient_unlinked",
    "notification_action_freshness_recipient_relinked",
    "notification_action_freshness_recipient_access_denied",
    "notification_action_freshness_source_state_changed",
    "notification_action_freshness_anchor_superseded",
    "notification_action_freshness_anchor_route_changed",
    "offer_success_freshness_recipient_missing",
    "offer_success_freshness_recipient_access_denied",
    "offer_success_freshness_source_state_changed",
}
_TERMINAL_SOURCE_REASONS = {
    "new_user_membership_freshness_outbox_terminal",
    "repeat_offer_response_freshness_outbox_terminal",
    "notification_action_freshness_outbox_terminal",
    "offer_success_freshness_outbox_terminal",
}


class TelegramNotificationOutboxQueueFeedbackError(RuntimeError):
    """Roll back queue transitions when outbox feedback is unsafe."""


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
    action = _enum_value(job.action_kind)
    is_offer_success = action == TelegramDeliveryAction.OFFER_SUCCESS.value
    is_notification_action = action in TELEGRAM_NOTIFICATION_ACTION_VALUES
    if is_offer_success:
        method_valid = str(job.method or "") == "editMessageText"
    elif is_notification_action:
        method_valid = str(job.method or "") in {
            "sendMessage",
            "sendDocument",
            "editMessageText",
            "editMessageReplyMarkup",
        }
    else:
        method_valid = str(job.method or "") == "sendMessage"
    common_route_valid = (
        _enum_value(job.destination_class) == TelegramDestinationClass.PRIVATE.value
        and method_valid
        and str(job.bot_identity or "") == "primary"
    )
    action_route_valid = (
        action == TelegramDeliveryAction.NEW_USER_MEMBERSHIP.value
        and _enum_value(job.feeder_kind) == TelegramFeederKind.ADMIN_SYSTEM.value
    ) or (
        action == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value
        and _enum_value(job.feeder_kind) == TelegramFeederKind.OFFER_CONTROL.value
    ) or (
        is_offer_success
        and _enum_value(job.feeder_kind) == TelegramFeederKind.OFFER_CONTROL.value
    )
    if not action_route_valid:
        try:
            policy = telegram_notification_action_policy(action)
        except ValueError:
            policy = None
        action_route_valid = bool(
            policy is not None
            and _enum_value(job.feeder_kind) == policy.feeder.value
        )
    if not common_route_valid or not action_route_valid:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_route_mismatch"
        )


async def _load_bound_outbox(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> TelegramNotificationOutbox:
    action = _enum_value(job.action_kind)
    if action == TelegramDeliveryAction.NEW_USER_MEMBERSHIP.value:
        dedupe_key = telegram_notification_outbox_dedupe_from_source(
            job.source_natural_id
        )
    elif action == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value:
        dedupe_key = telegram_repeat_offer_outbox_dedupe_from_source(
            job.source_natural_id
        )
    elif action in TELEGRAM_NOTIFICATION_ACTION_VALUES:
        dedupe_key = telegram_notification_action_outbox_dedupe_from_source(
            job.source_natural_id
        )
    elif action == TelegramDeliveryAction.OFFER_SUCCESS.value:
        dedupe_key = telegram_offer_success_outbox_dedupe_from_source(
            job.source_natural_id
        )
    else:
        dedupe_key = None
    if dedupe_key is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_source_invalid"
        )
    outbox = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.dedupe_key == dedupe_key)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outbox is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_row_missing"
        )
    return outbox


def _require_active_binding(
    outbox: TelegramNotificationOutbox,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    if _enum_value(outbox.status) not in _ACTIVE_STATUSES:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_row_not_active"
        )
    if _positive_int(outbox.queue_job_id) != _positive_int(job.id):
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_owner_mismatch"
        )
    if not isinstance(outbox.queue_handed_off_at, datetime):
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_handoff_missing"
        )


def _clear_binding(
    outbox: TelegramNotificationOutbox,
    *,
    now: datetime,
    reason: str,
) -> None:
    outbox.queue_job_id = None
    outbox.queue_handed_off_at = None
    outbox.worker_id = None
    outbox.lease_until = None
    outbox.reason = str(reason or "notification_outbox_queue_released")[:120]
    outbox.updated_at = now


def _accumulate_attempts(
    outbox: TelegramNotificationOutbox,
    *,
    job: TelegramDeliveryJobRecord,
) -> None:
    outbox.attempt_count = int(outbox.attempt_count or 0) + int(
        job.attempt_count or 0
    )


def _payload_chat_id(job: TelegramDeliveryJobRecord) -> int | None:
    payload = getattr(job, "payload", None)
    return _positive_int(payload.get("chat_id")) if isinstance(payload, dict) else None


def _payload_message_id(job: TelegramDeliveryJobRecord) -> int | None:
    payload = getattr(job, "payload", None)
    return _positive_int(payload.get("message_id")) if isinstance(payload, dict) else None


async def _finalize_active_outbox(
    db: AsyncSession,
    *,
    outbox: TelegramNotificationOutbox,
    job: TelegramDeliveryJobRecord,
    target: TelegramNotificationOutboxStatus,
    reason: str,
    now: datetime,
    telegram_message_id: int | None = None,
) -> None:
    current_status = _enum_value(outbox.status)
    target_value = target.value
    if current_status in TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES:
        if current_status != target_value:
            raise TelegramNotificationOutboxQueueFeedbackError(
                "notification_outbox_queue_feedback_terminal_conflict"
            )
        if target == TelegramNotificationOutboxStatus.SENT:
            if _positive_int(outbox.telegram_message_id) != _positive_int(
                telegram_message_id
            ):
                raise TelegramNotificationOutboxQueueFeedbackError(
                    "notification_outbox_queue_feedback_message_id_conflict"
                )
        _clear_binding(outbox, now=now, reason=reason)
        return

    _require_active_binding(outbox, job=job)
    normalized_message_id = _positive_int(telegram_message_id)
    if target == TelegramNotificationOutboxStatus.SENT and normalized_message_id is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_message_id_required"
        )
    chat_id = _payload_chat_id(job)
    if chat_id is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_feedback_chat_id_required"
        )
    outbox.status = target
    outbox.telegram_id_at_send = chat_id
    outbox.telegram_message_id = (
        normalized_message_id
        if target == TelegramNotificationOutboxStatus.SENT
        else None
    )
    outbox.next_retry_at = None
    _accumulate_attempts(outbox, job=job)
    outbox.last_error_class = (
        None
        if target == TelegramNotificationOutboxStatus.SENT
        else str(job.last_error_class or "TelegramDeliveryQueue")[:120]
    )
    outbox.last_error_message = (
        None
        if target == TelegramNotificationOutboxStatus.SENT
        else str(job.last_error_message or reason or "")[:500] or None
    )
    outbox.sent_at = now if target == TelegramNotificationOutboxStatus.SENT else None
    outbox.terminal_at = now
    _clear_binding(outbox, now=now, reason=reason)
    await db.flush()


async def _apply_interaction_anchor_result(
    db: AsyncSession,
    *,
    outbox: TelegramNotificationOutbox,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> None:
    if _enum_value(job.action_kind) not in TELEGRAM_NOTIFICATION_ACTION_VALUES:
        return
    contract = telegram_notification_action_interaction_result_contract(outbox)
    if (
        contract is None
        or contract.anchor_effect != TelegramInteractionAnchorEffect.SET_CURRENT
    ):
        return
    chat_id = _payload_chat_id(job)
    if chat_id is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_anchor_chat_id_missing"
        )
    anchor = (
        await db.execute(
            select(TelegramInteractionAnchorState)
            .where(TelegramInteractionAnchorState.chat_id == chat_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if anchor is None:
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_anchor_state_missing"
        )
    if _positive_int(anchor.recipient_user_id) != _positive_int(
        outbox.recipient_user_id
    ):
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_anchor_recipient_mismatch"
        )
    result = apply_interaction_delivery_result(
        contract,
        delivery_state=job.state,
        telegram_message_id=job.telegram_message_id,
        desired_anchor_generation=anchor.desired_generation,
    )
    if result.outcome == TelegramInteractionResultOutcome.APPLIED_STALE_ANCHOR:
        return
    if (
        result.outcome != TelegramInteractionResultOutcome.APPLIED
        or not result.activate_anchor
        or result.telegram_message_id is None
    ):
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_anchor_result_invalid:"
            f"{result.outcome.value}:{result.reason or 'unspecified'}"
        )
    if (
        _positive_int(anchor.desired_outbox_id) != _positive_int(outbox.id)
        or anchor.desired_logical_message_key != contract.logical_message_key
    ):
        raise TelegramNotificationOutboxQueueFeedbackError(
            "notification_outbox_queue_anchor_desired_identity_mismatch"
        )
    anchor.active_generation = contract.anchor_generation
    anchor.active_outbox_id = outbox.id
    anchor.active_message_id = result.telegram_message_id
    anchor.active_logical_message_key = contract.logical_message_key
    anchor.updated_at = now


class TelegramNotificationOutboxQueueLifecycleFeedback:
    """Feedback adapter invoked inside each fenced main-queue transaction."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        _validate_job_route(job)
        outbox = await _load_bound_outbox(db, job=job)
        _require_active_binding(outbox, job=job)
        recipient_user_id = _positive_int(outbox.recipient_user_id)
        if recipient_user_id is None:
            raise TelegramNotificationOutboxQueueFeedbackError(
                "notification_outbox_queue_dispatch_recipient_missing"
            )
        await db.execute(
            select(User).where(User.id == recipient_user_id).with_for_update()
        )
        if (
            _enum_value(job.action_kind)
            == TelegramDeliveryAction.OFFER_SUCCESS.value
        ):
            if (
                str(outbox.source_type or "").strip()
                != TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS
            ):
                raise TelegramNotificationOutboxQueueFeedbackError(
                    "notification_outbox_queue_offer_success_source_mismatch"
                )
            await db.execute(
                select(Offer.id)
                .where(Offer.offer_public_id == str(outbox.source_id or ""))
                .with_for_update()
            )
        if (
            _enum_value(job.action_kind)
            == TelegramDeliveryAction.NEW_USER_MEMBERSHIP.value
        ):
            await db.execute(
                select(AccountantRelation.id)
                .where(AccountantRelation.accountant_user_id == recipient_user_id)
                .with_for_update()
            )
            await db.execute(
                select(CustomerRelation.id)
                .where(CustomerRelation.customer_user_id == recipient_user_id)
                .with_for_update()
            )
            decision = (
                await validate_new_user_membership_telegram_delivery_freshness(
                    db,
                    job,
                    now,
                )
            )
        elif _enum_value(job.action_kind) == TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value:
            decision = (
                await validate_repeat_offer_response_telegram_delivery_freshness(
                    db,
                    job,
                    now,
                )
            )
        elif (
            _enum_value(job.action_kind)
            == TelegramDeliveryAction.OFFER_SUCCESS.value
        ):
            decision = await validate_offer_success_telegram_delivery_freshness(
                db,
                job,
                now,
            )
        else:
            decision = (
                await validate_notification_action_telegram_delivery_freshness(
                    db,
                    job,
                    now,
                )
            )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramNotificationOutboxQueueFeedbackError(
                "notification_outbox_queue_dispatch_guard_rejected:"
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
        outbox = await _load_bound_outbox(db, job=job)
        if outcome == TelegramFreshnessOutcome.RECLASSIFY:
            _require_active_binding(outbox, job=job)
            _accumulate_attempts(outbox, job=job)
            _clear_binding(outbox, now=now, reason=reason)
            outbox.status = TelegramNotificationOutboxStatus.PENDING
            outbox.next_retry_at = now
        elif outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if (
                _enum_value(outbox.status)
                != TelegramNotificationOutboxStatus.SENT.value
                or _positive_int(outbox.telegram_message_id) is None
            ):
                raise TelegramNotificationOutboxQueueFeedbackError(
                    "notification_outbox_queue_feedback_sent_noop_without_evidence"
                )
            _clear_binding(outbox, now=now, reason=reason)
        elif outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if reason in _TERMINAL_SOURCE_REASONS:
                if (
                    _enum_value(outbox.status)
                    not in TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES
                ):
                    raise TelegramNotificationOutboxQueueFeedbackError(
                        "notification_outbox_queue_feedback_terminal_missing"
                    )
                _clear_binding(outbox, now=now, reason=reason)
            elif reason in _SUPERSEDED_SKIP_REASONS:
                await _finalize_active_outbox(
                    db,
                    outbox=outbox,
                    job=job,
                    target=TelegramNotificationOutboxStatus.SKIPPED,
                    reason=reason,
                    now=now,
                )
            else:
                raise TelegramNotificationOutboxQueueFeedbackError(
                    "notification_outbox_queue_feedback_unknown_supersession"
                )
        else:
            raise TelegramNotificationOutboxQueueFeedbackError(
                "notification_outbox_queue_feedback_freshness_outcome_invalid"
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
        outbox = await _load_bound_outbox(db, job=job)
        outcome = decision.outcome
        reason = str(decision.reason or outcome.value)
        if outcome == TelegramDeliveryOutcome.SENT:
            telegram_message_id = _positive_int(job.telegram_message_id)
            if telegram_message_id is None and str(job.method or "").startswith("edit"):
                telegram_message_id = _payload_message_id(job)
            await _finalize_active_outbox(
                db,
                outbox=outbox,
                job=job,
                target=TelegramNotificationOutboxStatus.SENT,
                reason="telegram_sent",
                now=now,
                telegram_message_id=telegram_message_id,
            )
            await _apply_interaction_anchor_result(
                db,
                outbox=outbox,
                job=job,
                now=now,
            )
        elif (
            outcome == TelegramDeliveryOutcome.SENT_NOOP
            and str(job.method or "").startswith("edit")
        ):
            await _finalize_active_outbox(
                db,
                outbox=outbox,
                job=job,
                target=TelegramNotificationOutboxStatus.SENT,
                reason="telegram_edit_already_applied",
                now=now,
                telegram_message_id=_payload_message_id(job),
            )
        elif outcome in _HOLD_OUTCOMES:
            _require_active_binding(outbox, job=job)
        elif outcome == TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE:
            await _finalize_active_outbox(
                db,
                outbox=outbox,
                job=job,
                target=TelegramNotificationOutboxStatus.SKIPPED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.TERMINAL_FAILED:
            await _finalize_active_outbox(
                db,
                outbox=outbox,
                job=job,
                target=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                reason=reason,
                now=now,
            )
        elif outcome == TelegramDeliveryOutcome.QUARANTINED:
            _require_active_binding(outbox, job=job)
        elif outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        else:
            raise TelegramNotificationOutboxQueueFeedbackError(
                "notification_outbox_queue_feedback_delivery_outcome_invalid"
            )
        await db.flush()
