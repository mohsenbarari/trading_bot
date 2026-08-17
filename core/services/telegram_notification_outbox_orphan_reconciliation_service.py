"""Fail-closed reconciliation for notification outbox rows without recipients.

The notification outbox is a durable source intent.  When its recipient is
deleted after the intent has either not been handed off or its Queue-v1 job is
already terminal, the intent can no longer be delivered.  Reconciliation
preserves the row as an audited ``skipped`` terminal record; it never calls a
provider and never deletes queue history.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import (
    NON_TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES,
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)

from .telegram_delivery_queue_service import _enum_value, _require_foreign


@dataclass(frozen=True, slots=True)
class TelegramNotificationOutboxOrphanReconciliationReport:
    inspected_count: int
    reconciled_count: int
    preserved_non_reconcilable_count: int
    remaining_reconcilable_count: int
    dry_run: bool
    provider_network_calls: int = 0


_RECONCILABLE_TERMINAL_JOB_STATES = frozenset(
    {
        "superseded",
        "expired_interaction",
        "permanent_undeliverable",
        "terminal_failed",
        "quarantined",
    }
)


def _is_reconcilable_terminal_job_state(value: Any) -> bool:
    normalized = _enum_value(value)
    return normalized in _RECONCILABLE_TERMINAL_JOB_STATES


def _recipientless_nonterminal_predicates() -> tuple[Any, ...]:
    return (
        TelegramNotificationOutbox.recipient_user_id.is_(None),
        TelegramNotificationOutbox.status.in_(
            tuple(NON_TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES)
        ),
    )


def _reconcilable_predicate() -> Any:
    return or_(
        TelegramNotificationOutbox.queue_job_id.is_(None),
        TelegramDeliveryJobRecord.state.in_(
            tuple(sorted(_RECONCILABLE_TERMINAL_JOB_STATES))
        ),
    )


async def reconcile_orphaned_telegram_notification_outbox(
    db: AsyncSession,
    *,
    current_server: str,
    dry_run: bool = True,
    limit: int = 1_000,
    now: datetime | None = None,
) -> TelegramNotificationOutboxOrphanReconciliationReport:
    """Terminalize recipient-less outbox rows only when no live job owns them."""

    _require_foreign(current_server)
    bounded_limit = max(1, min(int(limit), 10_000))
    current_time = now or utc_now()
    base_statement = (
        select(
            TelegramNotificationOutbox,
            TelegramDeliveryJobRecord.state.label("queue_job_state"),
        )
        .outerjoin(
            TelegramDeliveryJobRecord,
            TelegramDeliveryJobRecord.id
            == TelegramNotificationOutbox.queue_job_id,
        )
        .where(*_recipientless_nonterminal_predicates())
        .order_by(TelegramNotificationOutbox.id.asc())
        .limit(bounded_limit)
    )
    if not dry_run:
        base_statement = base_statement.with_for_update(
            of=TelegramNotificationOutbox,
            skip_locked=True,
        )
    rows = (await db.execute(base_statement)).all()

    reconciled_count = 0
    preserved_non_reconcilable_count = 0
    for outbox, queue_job_state in rows:
        has_job = getattr(outbox, "queue_job_id", None) is not None
        if has_job and not _is_reconcilable_terminal_job_state(queue_job_state):
            preserved_non_reconcilable_count += 1
            continue
        if dry_run:
            reconciled_count += 1
            continue

        outbox.status = TelegramNotificationOutboxStatus.SKIPPED
        outbox.reason = (
            "recipient_missing_after_terminal_queue"
            if has_job
            else "recipient_missing_before_queue_handoff"
        )
        outbox.next_retry_at = None
        outbox.worker_id = None
        outbox.lease_until = None
        outbox.queue_job_id = None
        outbox.queue_handed_off_at = None
        outbox.terminal_at = current_time
        outbox.updated_at = current_time
        if not str(outbox.last_error_class or "").strip():
            outbox.last_error_class = "TelegramNotificationRecipientMissing"
        reconciled_count += 1

    if not dry_run:
        await db.flush()
    remaining_reconcilable_count = int(
        (
            await db.execute(
                select(func.count(TelegramNotificationOutbox.id))
                .outerjoin(
                    TelegramDeliveryJobRecord,
                    TelegramDeliveryJobRecord.id
                    == TelegramNotificationOutbox.queue_job_id,
                )
                .where(
                    *_recipientless_nonterminal_predicates(),
                    _reconcilable_predicate(),
                )
            )
        ).scalar_one()
        or 0
    )
    return TelegramNotificationOutboxOrphanReconciliationReport(
        inspected_count=len(rows),
        reconciled_count=reconciled_count,
        preserved_non_reconcilable_count=preserved_non_reconcilable_count,
        remaining_reconcilable_count=remaining_reconcilable_count,
        dry_run=bool(dry_run),
    )
