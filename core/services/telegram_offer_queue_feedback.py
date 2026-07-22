"""Lifecycle feedback for Offer publication and channel-edit queue jobs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.server_routing import current_server
from core.services.offer_publication_state_service import apply_publication_state_update
from core.services.telegram_offer_publication_service import (
    get_or_create_telegram_publication_state,
    mark_telegram_publication_success,
)
from core.services.telegram_offer_queue_service import (
    TelegramOfferQueueError,
    enqueue_current_offer_delivery,
    record_offer_edit_delivery_success,
)
from core.telegram_delivery_offer_freshness import (
    OFFER_FRESHNESS_ACTIONS,
    OFFER_PUBLISH_ACTIONS,
    OFFER_TERMINAL_EDIT_ACTIONS,
    validate_offer_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramFeederKind,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


_HOLD_OUTCOMES = frozenset(
    {
        TelegramDeliveryOutcome.RETRY_PENDING,
        TelegramDeliveryOutcome.DESTINATION_PAUSED,
        TelegramDeliveryOutcome.BOT_PAUSED,
        TelegramDeliveryOutcome.GATEWAY_PAUSED,
        TelegramDeliveryOutcome.AMBIGUOUS,
        TelegramDeliveryOutcome.AMBIGUOUS_UNRESOLVED,
        TelegramDeliveryOutcome.QUARANTINED,
    }
)


class TelegramOfferQueueFeedbackError(ValueError):
    """Raised when queue feedback cannot prove its Offer binding."""


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


def _configured_channel_id() -> int:
    try:
        channel_id = int(settings.channel_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_channel_invalid"
        ) from exc
    if channel_id == 0:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_channel_invalid"
        )
    return channel_id


def _action(job: TelegramDeliveryJobRecord) -> TelegramDeliveryAction:
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError as exc:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_action_invalid"
        ) from exc
    if action not in OFFER_FRESHNESS_ACTIONS:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_action_unsupported"
        )
    return action


async def _load_offer_and_state_for_update(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
) -> tuple[Offer, OfferPublicationState]:
    offer_public_id = str(job.source_natural_id or "").strip()
    if not offer_public_id:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_source_missing"
        )
    offer = (
        await db.execute(
            select(Offer)
            .where(Offer.offer_public_id == offer_public_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if offer is None:
        raise TelegramOfferQueueFeedbackError(
            "telegram_offer_queue_feedback_offer_missing"
        )
    state = (
        await db.execute(
            select(OfferPublicationState)
            .where(
                OfferPublicationState.offer_public_id == offer_public_id,
                OfferPublicationState.surface
                == OfferPublicationSurface.TELEGRAM_CHANNEL,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if state is None:
        state = await get_or_create_telegram_publication_state(db, offer)
    return offer, state


def _mark_terminal_without_publication(
    state: OfferPublicationState,
    offer: Offer,
    *,
    now: datetime,
    reason: str,
) -> None:
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=OfferPublicationStatus.DISABLED,
        now=now,
        error_code=reason[:96],
    )
    state.next_retry_at = None


def _mark_edit_success(
    state: OfferPublicationState,
    offer: Offer,
    *,
    action: TelegramDeliveryAction,
    now: datetime,
) -> None:
    requested_status = (
        OfferPublicationStatus.DISABLED
        if action in OFFER_TERMINAL_EDIT_ACTIONS
        else OfferPublicationStatus.SENT
    )
    apply_publication_state_update(
        state,
        offer_status=getattr(offer, "status", None),
        offer_version_id=getattr(offer, "version_id", None),
        requested_status=requested_status,
        now=now,
        telegram_message_id=_positive_int(getattr(state, "telegram_message_id", None)),
    )
    state.next_retry_at = None


def _record_failure_evidence(
    state: OfferPublicationState,
    job: TelegramDeliveryJobRecord,
    *,
    reason: str,
    now: datetime,
) -> None:
    # Keep the last successfully rendered offer_version_id unchanged so the
    # feeder/reconciliation report continues to see presentation drift.
    state.last_attempt_at = now
    state.error_code = f"telegram_queue:{reason}"[:96]
    state.error_message = str(job.last_error_message or "")[:240] or None


class TelegramOfferQueueLifecycleFeedback:
    """Feedback adapter invoked inside the queue job transaction."""

    async def assert_dispatchable(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> None:
        _action(job)
        await _load_offer_and_state_for_update(db, job=job)
        decision = await validate_offer_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=_configured_channel_id(),
        )
        if decision.outcome != TelegramFreshnessOutcome.SEND:
            raise TelegramOfferQueueFeedbackError(
                "telegram_offer_queue_dispatch_guard_rejected:"
                f"{decision.outcome.value}:{decision.reason or 'unspecified'}"
            )

    async def apply_freshness(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramFreshnessDecision,
        now: datetime,
    ) -> None:
        action = _action(job)
        outcome = decision.outcome
        reason = str(decision.reason or f"freshness_{outcome.value}")
        if outcome in {
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            TelegramFreshnessOutcome.QUARANTINED,
        }:
            return

        offer, state = await _load_offer_and_state_for_update(db, job=job)
        if outcome == TelegramFreshnessOutcome.RECLASSIFY:
            replacement = decision.replacement_action
            if replacement is None:
                return
            try:
                result = await enqueue_current_offer_delivery(
                    db,
                    current_server=current_server(),
                    offer=offer,
                    state=state,
                    expected_channel_id=_configured_channel_id(),
                    offer_expiry_minutes=None,
                    action=replacement,
                    publication_freshness_deadline_at=getattr(
                        job, "freshness_deadline_at", None
                    ),
                    now=now,
                )
            except TelegramOfferQueueError:
                # Keep PENDING_RECONCILE as explicit operational evidence.  A
                # reconciliation cycle can retry after the domain inconsistency
                # is repaired; silently mutating the old immutable job is unsafe.
                return
            if result.queue_result is not None or result.skipped_reason:
                job.state = TelegramDeliveryState.SUPERSEDED
                job.next_retry_at = None
                job.outcome_reason = f"{reason}:replacement_resolved"[:160]
                job.terminal_at = now
        elif outcome == TelegramFreshnessOutcome.SENT_NOOP:
            if action in OFFER_PUBLISH_ACTIONS and _positive_int(
                getattr(state, "telegram_message_id", None)
            ) is None:
                _mark_terminal_without_publication(
                    state,
                    offer,
                    now=now,
                    reason=reason,
                )
        elif outcome == TelegramFreshnessOutcome.SUPERSEDED:
            if action in OFFER_PUBLISH_ACTIONS and _positive_int(
                getattr(state, "telegram_message_id", None)
            ) is None:
                _mark_terminal_without_publication(
                    state,
                    offer,
                    now=now,
                    reason=reason,
                )
        else:
            raise TelegramOfferQueueFeedbackError(
                "telegram_offer_queue_feedback_freshness_outcome_invalid"
            )
        await db.flush()

    async def apply_delivery_result(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        decision: TelegramDeliveryDecision,
        now: datetime,
    ) -> None:
        action = _action(job)
        outcome = decision.outcome
        if outcome == TelegramDeliveryOutcome.STALE_LEASE:
            return
        offer, state = await _load_offer_and_state_for_update(db, job=job)
        reason = str(decision.reason or outcome.value)

        if outcome == TelegramDeliveryOutcome.SENT:
            if action in OFFER_PUBLISH_ACTIONS:
                message_id = _positive_int(job.telegram_message_id)
                if message_id is None:
                    raise TelegramOfferQueueFeedbackError(
                        "telegram_offer_queue_feedback_publish_message_missing"
                    )
                mark_telegram_publication_success(
                    state,
                    offer,
                    message_id=message_id,
                    chat_id=_configured_channel_id(),
                    now=now,
                )
            else:
                _mark_edit_success(
                    state,
                    offer,
                    action=action,
                    now=now,
                )
                if (
                    _enum_value(getattr(job, "feeder_kind", None))
                    == TelegramFeederKind.OFFER_EDIT.value
                ):
                    try:
                        await record_offer_edit_delivery_success(
                            db,
                            job,
                            now=now,
                        )
                    except TelegramOfferQueueError as exc:
                        raise TelegramOfferQueueFeedbackError(
                            "telegram_offer_edit_fairness_feedback_failed"
                        ) from exc
        elif outcome == TelegramDeliveryOutcome.SENT_NOOP:
            if action in OFFER_PUBLISH_ACTIONS:
                if _positive_int(getattr(state, "telegram_message_id", None)) is None:
                    raise TelegramOfferQueueFeedbackError(
                        "telegram_offer_queue_feedback_publish_noop_without_evidence"
                    )
            else:
                _mark_edit_success(
                    state,
                    offer,
                    action=action,
                    now=now,
                )
        elif outcome in _HOLD_OUTCOMES:
            pass
        elif outcome in {
            TelegramDeliveryOutcome.PERMANENT_UNDELIVERABLE,
            TelegramDeliveryOutcome.TERMINAL_FAILED,
        }:
            _record_failure_evidence(state, job, reason=reason, now=now)
        else:
            raise TelegramOfferQueueFeedbackError(
                "telegram_offer_queue_feedback_delivery_outcome_invalid"
            )
        await db.flush()
