"""Operational reconciliation for durable Telegram delivery jobs.

The reconciler never infers absence from silence. Unknown ``sendMessage``
outcomes become unresolved and require positive, audited evidence before they
can be marked delivered or made retryable.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.telegram_delivery_queue_contract import (
    FINAL_DELIVERY_STATES,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
    apply_freshness_decision,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_reconciliation_evidence import (
    TelegramDeliveryReconciliationEvidence,
)

from .telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    _enum_value,
    _record_to_contract,
    _require_foreign,
)


RECONCILIATION_CONFIRMED_APPLIED = "confirmed_applied"
RECONCILIATION_CONFIRMED_ABSENT = "confirmed_absent"
RECONCILIATION_INCONCLUSIVE = "inconclusive"
SAFE_RECONCILIATION_RETRY_METHODS = frozenset(
    {"editMessageText", "editMessageReplyMarkup", "deleteMessage", "answerCallbackQuery"}
)

TelegramReconciliationFreshnessValidator = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[TelegramFreshnessDecision],
]
TelegramReconciliationFreshnessFeedback = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, TelegramFreshnessDecision, datetime],
    Awaitable[None],
]
TelegramReconciliationResultFeedback = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, TelegramDeliveryDecision, datetime],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class TelegramReconciliationObservation:
    outcome: str
    evidence_reference: str
    reason_code: str
    telegram_message_id: int | None = None


TelegramAmbiguousObserver = Callable[
    [AsyncSession, TelegramDeliveryJobRecord, datetime],
    Awaitable[TelegramReconciliationObservation],
]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryReconciliationReport:
    inspected_count: int
    safe_retry_count: int
    resolved_sent_count: int
    freshness_terminal_count: int
    unresolved_count: int
    configuration_blocked_count: int
    pending_provider_outcome_count: int
    oldest_pending_provider_outcome_at: datetime | None
    job_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TelegramDeliveryReconciliationHealth:
    pending_reconcile_count: int
    ambiguous_count: int
    unresolved_count: int
    oldest_active_at: datetime | None
    pending_provider_outcome_count: int
    oldest_pending_provider_outcome_at: datetime | None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _hash_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


async def _append_evidence(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
    observed_state: str,
    evidence_kind: str,
    evidence_reference: str,
    decision_action: str,
    actor_kind: str,
    actor_reference: str | None,
    reason_code: str,
    now: datetime,
) -> TelegramDeliveryReconciliationEvidence:
    reference_hash = _hash_text(evidence_reference)
    canonical = json.dumps(
        {
            "job_id": int(job.id),
            "lease_token": int(job.lease_token or 0),
            "observed_state": observed_state,
            "evidence_kind": evidence_kind,
            "reference_hash": reference_hash,
            "decision_action": decision_action,
            "reason_code": reason_code,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = (
        await db.execute(
            select(TelegramDeliveryReconciliationEvidence).where(
                TelegramDeliveryReconciliationEvidence.job_id == int(job.id),
                TelegramDeliveryReconciliationEvidence.evidence_hash == evidence_hash,
                TelegramDeliveryReconciliationEvidence.decision_action == decision_action,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    evidence = TelegramDeliveryReconciliationEvidence(
        job_id=int(job.id),
        observed_job_state=observed_state,
        evidence_kind=evidence_kind[:64],
        evidence_hash=evidence_hash,
        decision_action=decision_action[:64],
        actor_kind=actor_kind,
        actor_ref_hash=_hash_text(actor_reference) if actor_reference else None,
        reason_code=reason_code[:160],
        details={"evidence_reference_hash": reference_hash},
        created_at=now,
    )
    db.add(evidence)
    await db.flush()
    return evidence


async def default_telegram_ambiguous_observer(
    _db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    _now: datetime,
) -> TelegramReconciliationObservation:
    message_id = _positive_int(job.telegram_message_id)
    if message_id is None and isinstance(job.provider_response, Mapping):
        result = job.provider_response.get("result")
        if isinstance(result, Mapping):
            message_id = _positive_int(result.get("message_id"))
    if message_id is not None:
        return TelegramReconciliationObservation(
            outcome=RECONCILIATION_CONFIRMED_APPLIED,
            evidence_reference=f"durable-provider-message-id:{message_id}",
            reason_code="durable_provider_message_id",
            telegram_message_id=message_id,
        )
    return TelegramReconciliationObservation(
        outcome=RECONCILIATION_INCONCLUSIVE,
        evidence_reference=(
            f"no-positive-evidence:job:{int(job.id)}:lease:{int(job.lease_token or 0)}"
        ),
        reason_code="telegram_send_acceptance_unknown",
    )


def _reset_for_safe_retry(job: TelegramDeliveryJobRecord, *, now: datetime, reason: str) -> None:
    job.state = TelegramDeliveryState.PENDING_RETRY
    job.next_retry_at = now
    job.worker_id = None
    job.lease_until = None
    job.dispatch_started_at = None
    job.rate_limit_probe = False
    job.outcome_reason = reason[:160]
    job.updated_at = now


async def _apply_confirmed_delivery(
    db: AsyncSession,
    *,
    job: TelegramDeliveryJobRecord,
    message_id: int,
    feedback: TelegramReconciliationResultFeedback,
    now: datetime,
) -> None:
    decision = TelegramDeliveryDecision(
        outcome=TelegramDeliveryOutcome.SENT,
        reason="reconciliation_confirmed_applied",
    )
    job.state = TelegramDeliveryState.SENT
    job.telegram_message_id = int(message_id)
    job.sent_at = now
    job.terminal_at = now
    job.next_retry_at = None
    job.worker_id = None
    job.lease_until = None
    job.rate_limit_probe = False
    job.outcome_reason = decision.reason
    job.updated_at = now
    await feedback(db, job, decision, now)


async def resolve_ambiguous_telegram_delivery_job(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    resolution: str,
    evidence_reference: str,
    operator_reference: str,
    reason_code: str,
    result_feedback: TelegramReconciliationResultFeedback | None = None,
    telegram_message_id: int | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> TelegramDeliveryDecision:
    """Break-glass resolution with positive evidence and append-only audit."""
    _require_foreign(current_server)
    current_time = now or utc_now()
    if not str(evidence_reference).strip() or not str(operator_reference).strip():
        raise TelegramDeliveryQueueValidationError("reconciliation_evidence_and_actor_required")
    job = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None or _enum_value(job.state) not in {
        TelegramDeliveryState.AMBIGUOUS.value,
        TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value,
    }:
        return TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.ALREADY_RESOLVED,
            reason="reconciliation_job_not_ambiguous",
        )
    observed_state = _enum_value(job.state)
    normalized_resolution = str(resolution).strip().lower()
    if normalized_resolution == RECONCILIATION_CONFIRMED_APPLIED:
        message_id = _positive_int(telegram_message_id)
        if message_id is None or result_feedback is None:
            raise TelegramDeliveryQueueValidationError(
                "reconciliation_confirmed_applied_requires_message_and_feedback"
            )
        decision = TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.SENT,
            reason="reconciliation_confirmed_applied",
        )
        action = "confirm_applied"
    elif normalized_resolution == RECONCILIATION_CONFIRMED_ABSENT:
        decision = TelegramDeliveryDecision(
            outcome=TelegramDeliveryOutcome.RETRY_PENDING,
            next_retry_at=current_time,
            reason="reconciliation_confirmed_absent",
        )
        action = "confirm_absent_retry"
    else:
        raise TelegramDeliveryQueueValidationError("reconciliation_resolution_invalid")

    if dry_run:
        return decision
    await _append_evidence(
        db,
        job=job,
        observed_state=observed_state,
        evidence_kind=normalized_resolution,
        evidence_reference=evidence_reference,
        decision_action=action,
        actor_kind="operator",
        actor_reference=operator_reference,
        reason_code=reason_code,
        now=current_time,
    )
    if normalized_resolution == RECONCILIATION_CONFIRMED_APPLIED:
        await _apply_confirmed_delivery(
            db,
            job=job,
            message_id=int(telegram_message_id),
            feedback=result_feedback,
            now=current_time,
        )
    else:
        _reset_for_safe_retry(
            job,
            now=current_time,
            reason="reconciliation_confirmed_absent",
        )
    await db.flush()
    return decision


async def reconcile_telegram_delivery_jobs(
    db: AsyncSession,
    *,
    current_server: str,
    freshness_validators: Mapping[str, TelegramReconciliationFreshnessValidator],
    freshness_feedbacks: Mapping[str, TelegramReconciliationFreshnessFeedback],
    result_feedbacks: Mapping[str, TelegramReconciliationResultFeedback],
    ambiguous_observer: TelegramAmbiguousObserver = default_telegram_ambiguous_observer,
    ambiguity_grace_seconds: float = 30.0,
    max_rows: int = 100,
    now: datetime | None = None,
) -> TelegramDeliveryReconciliationReport:
    _require_foreign(current_server)
    current_time = now or utc_now()
    ambiguous_cutoff = current_time - timedelta(
        seconds=max(1.0, float(ambiguity_grace_seconds))
    )
    records = list(
        (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(
                    or_(
                        TelegramDeliveryJobRecord.state
                        == TelegramDeliveryState.PENDING_RECONCILE,
                        (
                            TelegramDeliveryJobRecord.state == TelegramDeliveryState.AMBIGUOUS
                        )
                        & (TelegramDeliveryJobRecord.updated_at <= ambiguous_cutoff),
                    ),
                    ~select(TelegramDeliveryProviderOutcomeRecord.id)
                    .where(
                        TelegramDeliveryProviderOutcomeRecord.job_id
                        == TelegramDeliveryJobRecord.id,
                        TelegramDeliveryProviderOutcomeRecord.apply_state
                        == TELEGRAM_PROVIDER_OUTCOME_PENDING,
                    )
                    .exists(),
                )
                .order_by(TelegramDeliveryJobRecord.updated_at.asc(), TelegramDeliveryJobRecord.id.asc())
                .with_for_update(skip_locked=True)
                .limit(max(1, int(max_rows)))
            )
        ).scalars()
    )
    safe_retry_count = 0
    resolved_sent_count = 0
    freshness_terminal_count = 0
    unresolved_count = 0
    configuration_blocked_count = 0

    for job in records:
        observed_state = _enum_value(job.state)
        identity = str(job.bot_identity)
        if observed_state == TelegramDeliveryState.PENDING_RECONCILE.value:
            validator = freshness_validators.get(identity)
            freshness_feedback = freshness_feedbacks.get(identity)
            if validator is None or freshness_feedback is None:
                configuration_blocked_count += 1
                continue
            freshness = await validator(db, job, current_time)
            if (
                freshness.outcome == TelegramFreshnessOutcome.SEND
                and str(job.method) in SAFE_RECONCILIATION_RETRY_METHODS
            ):
                await _append_evidence(
                    db,
                    job=job,
                    observed_state=observed_state,
                    evidence_kind="authoritative_freshness_current",
                    evidence_reference=(
                        f"freshness:{freshness.reason or 'send'}:lease:{int(job.lease_token or 0)}"
                    ),
                    decision_action="safe_idempotent_retry",
                    actor_kind="worker",
                    actor_reference=None,
                    reason_code=freshness.reason or "freshness_send",
                    now=current_time,
                )
                _reset_for_safe_retry(
                    job,
                    now=current_time,
                    reason="reconciliation_idempotent_retry",
                )
                safe_retry_count += 1
                continue
            if freshness.outcome == TelegramFreshnessOutcome.SEND:
                await _append_evidence(
                    db,
                    job=job,
                    observed_state=observed_state,
                    evidence_kind="unsafe_pending_reconcile_method",
                    evidence_reference=(
                        f"method:{job.method}:lease:{int(job.lease_token or 0)}"
                    ),
                    decision_action="quarantine_invalid_reconcile_state",
                    actor_kind="worker",
                    actor_reference=None,
                    reason_code="pending_reconcile_method_not_idempotent",
                    now=current_time,
                )
                job.state = TelegramDeliveryState.QUARANTINED
                job.worker_id = None
                job.lease_until = None
                job.next_retry_at = None
                job.rate_limit_probe = False
                job.outcome_reason = "pending_reconcile_method_not_idempotent"
                job.terminal_at = current_time
                job.updated_at = current_time
                configuration_blocked_count += 1
                continue

            contract_job = _record_to_contract(job)
            apply_freshness_decision(contract_job, freshness)
            if (
                freshness.outcome == TelegramFreshnessOutcome.RECLASSIFY
                and freshness.replacement_action is not None
            ):
                job.state = TelegramDeliveryState.PENDING_RECONCILE
                job.next_retry_at = None
            elif freshness.outcome == TelegramFreshnessOutcome.WAIT_DEPENDENCY:
                job.state = TelegramDeliveryState.PENDING_RETRY
                job.next_retry_at = current_time + timedelta(seconds=1)
            else:
                job.state = contract_job.state
                job.next_retry_at = None
            job.worker_id = None
            job.lease_until = None
            job.dispatch_started_at = None
            job.rate_limit_probe = False
            job.outcome_reason = (
                freshness.reason or f"reconciliation_{freshness.outcome.value}"
            )[:160]
            job.updated_at = current_time
            if TelegramDeliveryState(_enum_value(job.state)) in FINAL_DELIVERY_STATES:
                job.terminal_at = current_time
                freshness_terminal_count += 1
            await freshness_feedback(db, job, freshness, current_time)
            await _append_evidence(
                db,
                job=job,
                observed_state=observed_state,
                evidence_kind=f"freshness_{freshness.outcome.value}",
                evidence_reference=(
                    f"freshness:{freshness.reason or freshness.outcome.value}:"
                    f"lease:{int(job.lease_token or 0)}"
                ),
                decision_action=f"freshness_{_enum_value(job.state)}",
                actor_kind="worker",
                actor_reference=None,
                reason_code=freshness.reason or freshness.outcome.value,
                now=current_time,
            )
            continue

        observation = await ambiguous_observer(db, job, current_time)
        if observation.outcome == RECONCILIATION_CONFIRMED_APPLIED:
            result_feedback = result_feedbacks.get(identity)
            message_id = _positive_int(observation.telegram_message_id)
            if result_feedback is None or message_id is None:
                configuration_blocked_count += 1
                continue
            await _append_evidence(
                db,
                job=job,
                observed_state=observed_state,
                evidence_kind=observation.outcome,
                evidence_reference=observation.evidence_reference,
                decision_action="observer_confirm_applied",
                actor_kind="worker",
                actor_reference=None,
                reason_code=observation.reason_code,
                now=current_time,
            )
            await _apply_confirmed_delivery(
                db,
                job=job,
                message_id=message_id,
                feedback=result_feedback,
                now=current_time,
            )
            resolved_sent_count += 1
        elif observation.outcome == RECONCILIATION_CONFIRMED_ABSENT:
            await _append_evidence(
                db,
                job=job,
                observed_state=observed_state,
                evidence_kind=observation.outcome,
                evidence_reference=observation.evidence_reference,
                decision_action="observer_confirm_absent_retry",
                actor_kind="worker",
                actor_reference=None,
                reason_code=observation.reason_code,
                now=current_time,
            )
            _reset_for_safe_retry(
                job,
                now=current_time,
                reason="reconciliation_observer_confirmed_absent",
            )
            safe_retry_count += 1
        else:
            await _append_evidence(
                db,
                job=job,
                observed_state=observed_state,
                evidence_kind=RECONCILIATION_INCONCLUSIVE,
                evidence_reference=observation.evidence_reference,
                decision_action="escalate_unresolved",
                actor_kind="worker",
                actor_reference=None,
                reason_code=observation.reason_code,
                now=current_time,
            )
            job.state = TelegramDeliveryState.AMBIGUOUS_UNRESOLVED
            job.worker_id = None
            job.lease_until = None
            job.next_retry_at = None
            job.outcome_reason = observation.reason_code[:160]
            job.updated_at = current_time
            unresolved_count += 1

    pending_count, oldest_pending = (
        await db.execute(
            select(
                func.count(TelegramDeliveryProviderOutcomeRecord.id),
                func.min(TelegramDeliveryProviderOutcomeRecord.created_at),
            ).where(
                TelegramDeliveryProviderOutcomeRecord.apply_state
                == TELEGRAM_PROVIDER_OUTCOME_PENDING
            )
        )
    ).one()
    await db.flush()
    return TelegramDeliveryReconciliationReport(
        inspected_count=len(records),
        safe_retry_count=safe_retry_count,
        resolved_sent_count=resolved_sent_count,
        freshness_terminal_count=freshness_terminal_count,
        unresolved_count=unresolved_count,
        configuration_blocked_count=configuration_blocked_count,
        pending_provider_outcome_count=int(pending_count or 0),
        oldest_pending_provider_outcome_at=oldest_pending,
        job_ids=tuple(int(job.id) for job in records),
    )


async def inspect_telegram_delivery_reconciliation_health(
    db: AsyncSession,
    *,
    current_server: str,
) -> TelegramDeliveryReconciliationHealth:
    _require_foreign(current_server)
    rows = (
        await db.execute(
            select(
                TelegramDeliveryJobRecord.state,
                func.count(TelegramDeliveryJobRecord.id),
                func.min(TelegramDeliveryJobRecord.updated_at),
            )
            .where(
                TelegramDeliveryJobRecord.state.in_(
                    {
                        TelegramDeliveryState.PENDING_RECONCILE,
                        TelegramDeliveryState.AMBIGUOUS,
                        TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
                    }
                )
            )
            .group_by(TelegramDeliveryJobRecord.state)
        )
    ).all()
    counts = {_enum_value(state): int(count) for state, count, _oldest in rows}
    oldest_active = min(
        (oldest for _state, _count, oldest in rows if oldest is not None),
        default=None,
    )
    pending_count, oldest_pending = (
        await db.execute(
            select(
                func.count(TelegramDeliveryProviderOutcomeRecord.id),
                func.min(TelegramDeliveryProviderOutcomeRecord.created_at),
            ).where(
                TelegramDeliveryProviderOutcomeRecord.apply_state
                == TELEGRAM_PROVIDER_OUTCOME_PENDING
            )
        )
    ).one()
    return TelegramDeliveryReconciliationHealth(
        pending_reconcile_count=counts.get(TelegramDeliveryState.PENDING_RECONCILE.value, 0),
        ambiguous_count=counts.get(TelegramDeliveryState.AMBIGUOUS.value, 0),
        unresolved_count=counts.get(TelegramDeliveryState.AMBIGUOUS_UNRESOLVED.value, 0),
        oldest_active_at=oldest_active,
        pending_provider_outcome_count=int(pending_count or 0),
        oldest_pending_provider_outcome_at=oldest_pending,
    )
