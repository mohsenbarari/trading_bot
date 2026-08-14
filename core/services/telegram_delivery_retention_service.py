"""Bounded, idempotent retention for foreign-local Telegram queue data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.metrics import record_telegram_delivery_retention
from core.server_routing import SERVER_FOREIGN
from core.telegram_delivery_queue_contract import (
    FINAL_DELIVERY_STATES,
    TelegramDeliveryState,
)
from core.utils import utc_now
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.telegram_admin_broadcast import (
    TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
    TelegramAdminBroadcastReceipt,
)
from models.telegram_channel_membership_saga import TelegramChannelMembershipSaga
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_notification_outbox import (
    TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES,
    TelegramNotificationOutbox,
)
from models.telegram_scheduled_operation import TelegramScheduledOperation


TELEGRAM_DELIVERY_PAYLOAD_RETENTION_DAYS = 7
TELEGRAM_DELIVERY_HOT_RETENTION_DAYS = 30
TELEGRAM_DELIVERY_RETENTION_BATCH_SIZE = 500
TELEGRAM_DELIVERY_REDACTED_PAYLOAD = {
    "redacted": True,
    "schema_version": "telegram_delivery_payload_redacted_v1",
}

_HOLD_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_TERMINAL_JOB_STATES = tuple(state.value for state in FINAL_DELIVERY_STATES)
_TERMINAL_SCHEDULED_STATUSES = (
    "sent",
    "skipped",
    "terminal_failed",
    "cancelled",
)
_TERMINAL_MARKET_STATUSES = ("sent", "skipped", "suppressed_stale")
_MEMBERSHIP_ACTIONS = ("channel_member_ban", "channel_member_unban")


class TelegramDeliveryRetentionError(RuntimeError):
    """Reject unsafe retention mutation or invalid legal-hold metadata."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryRetentionReport:
    dry_run: bool
    payload_cutoff: datetime
    terminal_cutoff: datetime
    payload_candidates: int
    payload_redacted: int
    provider_outcomes_redacted: int
    terminal_candidates: int
    terminal_purged: int
    legal_hold_due: int
    unresolved_due: int
    source_blocked_due: int
    oldest_terminal_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "telegram_delivery_retention_report_v1",
            "dry_run": self.dry_run,
            "payload_cutoff": self.payload_cutoff.isoformat(),
            "terminal_cutoff": self.terminal_cutoff.isoformat(),
            "payload_candidates": self.payload_candidates,
            "payload_redacted": self.payload_redacted,
            "provider_outcomes_redacted": self.provider_outcomes_redacted,
            "terminal_candidates": self.terminal_candidates,
            "terminal_purged": self.terminal_purged,
            "legal_hold_due": self.legal_hold_due,
            "unresolved_due": self.unresolved_due,
            "source_blocked_due": self.source_blocked_due,
            "oldest_terminal_at": (
                self.oldest_terminal_at.isoformat()
                if self.oldest_terminal_at is not None
                else None
            ),
        }


def _require_foreign(current_server: str) -> None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_is_foreign_only"
        )


def _bounded_positive(value: int, *, maximum: int = 5000) -> int:
    if isinstance(value, bool):
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_limit_invalid"
        )
    parsed = int(value)
    if parsed <= 0 or parsed > maximum:
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_limit_invalid"
        )
    return parsed


async def set_telegram_delivery_retention_legal_hold(
    db: AsyncSession,
    *,
    current_server: str,
    job_id: int,
    enabled: bool,
    reason_code: str | None,
    now: datetime | None = None,
) -> TelegramDeliveryJobRecord:
    """Set or clear a bounded-code legal hold under a row lock."""
    _require_foreign(current_server)
    if isinstance(job_id, bool) or int(job_id) <= 0:
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_job_id_invalid"
        )
    normalized_reason = str(reason_code or "").strip().lower()
    if enabled and not _HOLD_REASON_RE.fullmatch(normalized_reason):
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_hold_reason_invalid"
        )
    job = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(TelegramDeliveryJobRecord.id == int(job_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_job_missing"
        )
    job.retention_legal_hold = bool(enabled)
    job.retention_hold_reason_code = normalized_reason if enabled else None
    job.retention_hold_set_at = (now or utc_now()) if enabled else None
    await db.flush()
    return job


async def _has_pending_provider_outcome(
    db: AsyncSession,
    job_ids: tuple[int, ...],
) -> bool:
    if not job_ids:
        return False
    pending = await db.scalar(
        select(func.count(TelegramDeliveryProviderOutcomeRecord.id)).where(
            TelegramDeliveryProviderOutcomeRecord.job_id.in_(job_ids),
            TelegramDeliveryProviderOutcomeRecord.apply_state
            == TELEGRAM_PROVIDER_OUTCOME_PENDING,
        )
    )
    return bool(int(pending or 0))


async def _detach_terminal_source_bindings(
    db: AsyncSession,
    *,
    job_id: int,
    mutate: bool,
) -> bool:
    """Detach only terminal sources; active sources are an implicit legal hold."""
    outboxes = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.queue_job_id == job_id)
            .with_for_update()
        )
    ).scalars().all()
    broadcasts = (
        await db.execute(
            select(TelegramAdminBroadcastReceipt)
            .where(TelegramAdminBroadcastReceipt.queue_job_id == job_id)
            .with_for_update()
        )
    ).scalars().all()
    scheduled = (
        await db.execute(
            select(TelegramScheduledOperation)
            .where(TelegramScheduledOperation.queue_job_id == job_id)
            .with_for_update()
        )
    ).scalars().all()
    market = (
        await db.execute(
            select(MarketChannelNoticeReceipt)
            .where(MarketChannelNoticeReceipt.queue_job_id == job_id)
            .with_for_update()
        )
    ).scalars().all()

    if any(
        str(row.status.value if hasattr(row.status, "value") else row.status)
        not in TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES
        for row in outboxes
    ):
        return False
    if any(
        str(row.status.value if hasattr(row.status, "value") else row.status)
        not in TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES
        for row in broadcasts
    ):
        return False
    if any(str(row.status) not in _TERMINAL_SCHEDULED_STATUSES for row in scheduled):
        return False
    if any(str(row.status) not in _TERMINAL_MARKET_STATUSES for row in market):
        return False

    if mutate:
        for row in (*outboxes, *broadcasts, *scheduled, *market):
            row.queue_job_id = None
            row.queue_handed_off_at = None
    return True


async def _redact_payload_batch(
    db: AsyncSession,
    *,
    cutoff: datetime,
    now: datetime,
    limit: int,
    dry_run: bool,
) -> tuple[int, int]:
    jobs = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(
                TelegramDeliveryJobRecord.state.in_(_TERMINAL_JOB_STATES),
                TelegramDeliveryJobRecord.terminal_at.is_not(None),
                TelegramDeliveryJobRecord.terminal_at < cutoff,
                TelegramDeliveryJobRecord.payload_redacted_at.is_(None),
                TelegramDeliveryJobRecord.retention_legal_hold.is_(False),
            )
            .order_by(
                TelegramDeliveryJobRecord.terminal_at.asc(),
                TelegramDeliveryJobRecord.id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    if dry_run or not jobs:
        return len(jobs), 0
    job_ids = tuple(int(job.id) for job in jobs)
    terminal_outboxes = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(
                TelegramNotificationOutbox.queue_job_id.in_(job_ids),
                TelegramNotificationOutbox.status.in_(
                    tuple(TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES)
                ),
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for outbox in terminal_outboxes:
        extra_payload = getattr(outbox, "extra_payload", None)
        if (
            isinstance(extra_payload, dict)
            and extra_payload.get("document_base64") is not None
        ):
            redacted = dict(extra_payload)
            redacted["document_base64"] = None
            outbox.extra_payload = redacted
    for job in jobs:
        job.payload = dict(TELEGRAM_DELIVERY_REDACTED_PAYLOAD)
        job.provider_response = None
        job.last_error_message = None
        job.payload_redacted_at = now
    outcome_result = await db.execute(
        update(TelegramDeliveryProviderOutcomeRecord)
        .where(
            TelegramDeliveryProviderOutcomeRecord.job_id.in_(job_ids),
            TelegramDeliveryProviderOutcomeRecord.payload_redacted_at.is_(None),
        )
        .values(
            provider_response=None,
            last_apply_error_message=None,
            payload_redacted_at=now,
        )
    )
    await db.execute(
        update(TelegramChannelMembershipSaga)
        .where(
            or_(
                TelegramChannelMembershipSaga.ban_job_id.in_(job_ids),
                TelegramChannelMembershipSaga.unban_job_id.in_(job_ids),
            ),
            TelegramChannelMembershipSaga.payload_redacted_at.is_(None),
        )
        .values(last_error_message=None, payload_redacted_at=now, updated_at=now)
    )
    await db.flush()
    return len(jobs), max(int(outcome_result.rowcount or 0), 0)


async def _purge_membership_pairs(
    db: AsyncSession,
    *,
    cutoff: datetime,
    limit: int,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    sagas = (
        await db.execute(
            select(TelegramChannelMembershipSaga)
            .join(
                TelegramDeliveryJobRecord,
                TelegramDeliveryJobRecord.id
                == TelegramChannelMembershipSaga.ban_job_id,
            )
            .where(
                TelegramChannelMembershipSaga.state.in_(
                    ("complete", "terminal_failed", "superseded")
                ),
                TelegramChannelMembershipSaga.terminal_at.is_not(None),
                TelegramChannelMembershipSaga.terminal_at < cutoff,
            )
            .order_by(TelegramChannelMembershipSaga.terminal_at.asc())
            .limit(max(1, limit // 2))
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    purged = 0
    unresolved = 0
    blocked_source = 0
    for saga in sagas:
        ids = (int(saga.ban_job_id or 0), int(saga.unban_job_id or 0))
        if 0 in ids:
            unresolved += 1
            continue
        jobs = (
            await db.execute(
                select(TelegramDeliveryJobRecord)
                .where(TelegramDeliveryJobRecord.id.in_(ids))
                .with_for_update()
            )
        ).scalars().all()
        if len(jobs) != 2 or any(
            str(job.state.value if hasattr(job.state, "value") else job.state)
            not in _TERMINAL_JOB_STATES
            or job.terminal_at is None
            or job.terminal_at >= cutoff
            or job.payload_redacted_at is None
            or bool(job.retention_legal_hold)
            for job in jobs
        ):
            continue
        if await _has_pending_provider_outcome(db, ids):
            unresolved += 2
            continue
        sources_are_terminal = all(
            [
                await _detach_terminal_source_bindings(
                    db,
                    job_id=ids[0],
                    mutate=False,
                ),
                await _detach_terminal_source_bindings(
                    db,
                    job_id=ids[1],
                    mutate=False,
                ),
            ]
        )
        if not sources_are_terminal:
            blocked_source += 2
            continue
        purged += 2
        if not dry_run:
            await _detach_terminal_source_bindings(
                db,
                job_id=ids[0],
                mutate=True,
            )
            await _detach_terminal_source_bindings(
                db,
                job_id=ids[1],
                mutate=True,
            )
            await db.delete(saga)
            for job in jobs:
                await db.delete(job)
    return len(sagas) * 2, purged, unresolved, blocked_source


async def _purge_terminal_batch(
    db: AsyncSession,
    *,
    cutoff: datetime,
    limit: int,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    jobs = (
        await db.execute(
            select(TelegramDeliveryJobRecord)
            .where(
                TelegramDeliveryJobRecord.state.in_(_TERMINAL_JOB_STATES),
                TelegramDeliveryJobRecord.terminal_at.is_not(None),
                TelegramDeliveryJobRecord.terminal_at < cutoff,
                TelegramDeliveryJobRecord.payload_redacted_at.is_not(None),
                TelegramDeliveryJobRecord.retention_legal_hold.is_(False),
                TelegramDeliveryJobRecord.action_kind.not_in(_MEMBERSHIP_ACTIONS),
            )
            .order_by(
                TelegramDeliveryJobRecord.terminal_at.asc(),
                TelegramDeliveryJobRecord.id.asc(),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    purged = 0
    unresolved = 0
    blocked_source = 0
    for job in jobs:
        job_id = int(job.id)
        if await _has_pending_provider_outcome(db, (job_id,)):
            unresolved += 1
            continue
        if not await _detach_terminal_source_bindings(
            db,
            job_id=job_id,
            mutate=not dry_run,
        ):
            blocked_source += 1
            continue
        purged += 1
        if not dry_run:
            await db.delete(job)

    remaining = limit - len(jobs)
    if remaining >= 2:
        (
            pair_candidates,
            pair_purged,
            pair_unresolved,
            pair_source_blocked,
        ) = await _purge_membership_pairs(
            db,
            cutoff=cutoff,
            limit=remaining,
            dry_run=dry_run,
        )
    else:
        pair_candidates = pair_purged = 0
        pair_unresolved = pair_source_blocked = 0
    return (
        len(jobs) + pair_candidates,
        purged + pair_purged,
        unresolved + pair_unresolved,
        blocked_source + pair_source_blocked,
    )


async def run_telegram_delivery_retention_cycle(
    db: AsyncSession,
    *,
    current_server: str,
    now: datetime | None = None,
    payload_retention_days: int = TELEGRAM_DELIVERY_PAYLOAD_RETENTION_DAYS,
    terminal_retention_days: int = TELEGRAM_DELIVERY_HOT_RETENTION_DAYS,
    limit: int = TELEGRAM_DELIVERY_RETENTION_BATCH_SIZE,
    dry_run: bool = False,
) -> TelegramDeliveryRetentionReport:
    """Redact at seven days and purge safe terminal rows after thirty days."""
    _require_foreign(current_server)
    batch_limit = _bounded_positive(limit)
    if int(payload_retention_days) <= 0 or int(terminal_retention_days) <= 0:
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_days_invalid"
        )
    if int(payload_retention_days) >= int(terminal_retention_days):
        raise TelegramDeliveryRetentionError(
            "telegram_delivery_retention_window_invalid"
        )
    current_time = now or utc_now()
    payload_cutoff = current_time - timedelta(days=int(payload_retention_days))
    terminal_cutoff = current_time - timedelta(days=int(terminal_retention_days))

    payload_candidates, outcomes_redacted = await _redact_payload_batch(
        db,
        cutoff=payload_cutoff,
        now=current_time,
        limit=batch_limit,
        dry_run=dry_run,
    )
    terminal_candidates, terminal_purged, unresolved_due, source_blocked = (
        await _purge_terminal_batch(
            db,
            cutoff=terminal_cutoff,
            limit=batch_limit,
            dry_run=dry_run,
        )
    )
    legal_hold_due = int(
        await db.scalar(
            select(func.count(TelegramDeliveryJobRecord.id)).where(
                TelegramDeliveryJobRecord.terminal_at.is_not(None),
                TelegramDeliveryJobRecord.terminal_at < terminal_cutoff,
                TelegramDeliveryJobRecord.retention_legal_hold.is_(True),
            )
        )
        or 0
    )
    oldest_terminal_at = await db.scalar(
        select(func.min(TelegramDeliveryJobRecord.terminal_at)).where(
            TelegramDeliveryJobRecord.terminal_at.is_not(None)
        )
    )
    report = TelegramDeliveryRetentionReport(
        dry_run=bool(dry_run),
        payload_cutoff=payload_cutoff,
        terminal_cutoff=terminal_cutoff,
        payload_candidates=payload_candidates,
        payload_redacted=0 if dry_run else payload_candidates,
        provider_outcomes_redacted=0 if dry_run else outcomes_redacted,
        terminal_candidates=terminal_candidates,
        terminal_purged=0 if dry_run else terminal_purged,
        legal_hold_due=legal_hold_due,
        unresolved_due=unresolved_due,
        source_blocked_due=source_blocked,
        oldest_terminal_at=oldest_terminal_at,
    )
    record_telegram_delivery_retention(report.as_dict())
    return report
