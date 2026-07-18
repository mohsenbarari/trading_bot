"""Sequential subordinate-queue handoff for Telegram admin broadcasts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_admin_broadcast_delivery_service import (
    finalize_telegram_admin_broadcast_status,
)
from core.services.telegram_admin_broadcast_service import (
    validate_telegram_admin_broadcast_content,
)
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_TEMPLATE_VERSION,
    build_telegram_admin_broadcast_payload,
    telegram_admin_broadcast_campaign_id,
    telegram_admin_broadcast_destination_key,
    telegram_admin_broadcast_source_natural_id,
    telegram_admin_broadcast_source_version,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.utils import utc_now
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import User


ADMIN_BROADCAST_QUEUE_HANDOFF = "handed_off"
ADMIN_BROADCAST_QUEUE_SKIPPED = "skipped"
ADMIN_BROADCAST_QUEUE_TERMINAL_FAILED = "terminal_failed"
ADMIN_BROADCAST_QUEUE_REQUIRES_RECONCILIATION = "requires_reconciliation"

_ACTIVE_RECEIPT_STATUSES = (
    TelegramAdminBroadcastReceiptStatus.PENDING,
    TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
)
_ADMIN_BROADCAST_HANDOFF_ADVISORY_LOCK = 7_461_889_230_174_021_119
_ADMIN_BROADCAST_MAX_ACTIVE_CAMPAIGNS = 2


class TelegramAdminBroadcastQueueHandoffError(RuntimeError):
    """Raised before unsafe subordinate-queue adoption or cross-server use."""


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastQueueHandoffResult:
    receipt_id: int
    disposition: str
    job_id: int | None = None
    job_created: bool = False
    reason: str | None = None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


async def _flush(db: AsyncSession | Any) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()


async def _acquire_global_handoff_slot(db: AsyncSession) -> None:
    await db.execute(
        select(func.pg_advisory_xact_lock(_ADMIN_BROADCAST_HANDOFF_ADVISORY_LOCK))
    )


async def _active_bound_broadcast_ids(db: AsyncSession) -> tuple[int, ...]:
    rows = (
        await db.execute(
            select(
                TelegramAdminBroadcastReceipt.broadcast_id,
                func.count(TelegramAdminBroadcastReceipt.id),
            )
            .where(TelegramAdminBroadcastReceipt.queue_job_id.is_not(None))
            .group_by(TelegramAdminBroadcastReceipt.broadcast_id)
            .order_by(TelegramAdminBroadcastReceipt.broadcast_id.asc())
        )
    ).all()
    if any(int(count) != 1 for _broadcast_id, count in rows):
        raise TelegramAdminBroadcastQueueHandoffError(
            "telegram_admin_broadcast_multiple_active_recipients"
        )
    if len(rows) > _ADMIN_BROADCAST_MAX_ACTIVE_CAMPAIGNS:
        raise TelegramAdminBroadcastQueueHandoffError(
            "telegram_admin_broadcast_active_campaign_limit_exceeded"
        )
    return tuple(int(broadcast_id) for broadcast_id, _count in rows)


async def _select_next_due_receipt(
    db: AsyncSession,
    *,
    now: datetime,
    excluded_broadcast_ids: tuple[int, ...],
) -> TelegramAdminBroadcastReceipt | None:
    statement = (
        select(TelegramAdminBroadcastReceipt)
        .join(
            TelegramAdminBroadcast,
            TelegramAdminBroadcast.id
            == TelegramAdminBroadcastReceipt.broadcast_id,
        )
        .where(
            TelegramAdminBroadcastReceipt.status.in_(_ACTIVE_RECEIPT_STATUSES),
            TelegramAdminBroadcastReceipt.queue_job_id.is_(None),
            TelegramAdminBroadcastReceipt.queue_handed_off_at.is_(None),
            TelegramAdminBroadcastReceipt.worker_id.is_(None),
            TelegramAdminBroadcastReceipt.lease_until.is_(None),
            or_(
                TelegramAdminBroadcastReceipt.next_retry_at.is_(None),
                TelegramAdminBroadcastReceipt.next_retry_at <= now,
            ),
        )
    )
    if excluded_broadcast_ids:
        statement = statement.where(
            TelegramAdminBroadcastReceipt.broadcast_id.not_in(
                excluded_broadcast_ids
            )
        )
    # The broadcast timestamp is the durable round-robin cursor. Every handoff
    # moves its campaign to the back; recipient id preserves FIFO within it.
    statement = (
        statement.order_by(
            func.coalesce(
                TelegramAdminBroadcast.queue_last_handed_off_at,
                TelegramAdminBroadcast.queued_at,
                TelegramAdminBroadcast.created_at,
            ).asc(),
            TelegramAdminBroadcast.id.asc(),
            TelegramAdminBroadcastReceipt.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def _finalize_unhandoffable_receipt(
    db: AsyncSession,
    *,
    receipt: TelegramAdminBroadcastReceipt,
    status: TelegramAdminBroadcastReceiptStatus,
    reason: str,
    error_class: str,
    now: datetime,
) -> TelegramAdminBroadcastQueueHandoffResult:
    receipt.status = status
    receipt.reason = reason[:120]
    receipt.telegram_id_at_send = None
    receipt.telegram_message_id = None
    receipt.next_retry_at = None
    receipt.last_error_class = error_class[:120]
    receipt.last_error_message = reason[:500]
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.queue_job_id = None
    receipt.queue_handed_off_at = None
    receipt.sent_at = None
    receipt.terminal_at = now
    receipt.updated_at = now
    await _flush(db)
    await finalize_telegram_admin_broadcast_status(
        db,
        broadcast_id=int(receipt.broadcast_id),
        now=now,
    )
    return TelegramAdminBroadcastQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=(
            ADMIN_BROADCAST_QUEUE_SKIPPED
            if status == TelegramAdminBroadcastReceiptStatus.SKIPPED
            else ADMIN_BROADCAST_QUEUE_TERMINAL_FAILED
        ),
        reason=reason,
    )


async def handoff_next_due_telegram_admin_broadcast_receipt(
    db: AsyncSession,
    *,
    current_server: str,
    now: datetime | None = None,
) -> TelegramAdminBroadcastQueueHandoffResult | None:
    """Hand off at most one recipient after the previous recipient resolves."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramAdminBroadcastQueueHandoffError(
            "telegram_admin_broadcast_queue_handoff_is_foreign_only"
        )
    current_time = now or utc_now()
    await _acquire_global_handoff_slot(db)
    active_broadcast_ids = await _active_bound_broadcast_ids(db)
    if len(active_broadcast_ids) >= _ADMIN_BROADCAST_MAX_ACTIVE_CAMPAIGNS:
        return None
    receipt = await _select_next_due_receipt(
        db,
        now=current_time,
        excluded_broadcast_ids=active_broadcast_ids,
    )
    if receipt is None:
        return None

    broadcast = await db.get(TelegramAdminBroadcast, int(receipt.broadcast_id))
    if broadcast is None:
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason="broadcast_missing",
            error_class="BroadcastMissing",
            now=current_time,
        )
    try:
        validate_telegram_admin_broadcast_content(broadcast.content)
    except Exception:
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason="broadcast_invalid_content",
            error_class="BroadcastPayloadError",
            now=current_time,
        )
    user = await db.get(User, int(receipt.recipient_user_id))
    if user is None:
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            reason="telegram_user_missing_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    if _positive_int(user.telegram_id) is None:
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            reason="telegram_unlinked_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        reason = str(access.reason or "bot_access_denied_current")[:120]
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            reason=reason,
            error_class="BotAccessDenied",
            now=current_time,
        )

    try:
        source_natural_id = telegram_admin_broadcast_source_natural_id(
            receipt,
            broadcast,
        )
        source_version = telegram_admin_broadcast_source_version(user)
        destination_key = telegram_admin_broadcast_destination_key(
            int(receipt.recipient_user_id)
        )
        payload = build_telegram_admin_broadcast_payload(broadcast, user)
        campaign_id = telegram_admin_broadcast_campaign_id(int(broadcast.id))
    except (TypeError, ValueError, OverflowError) as exc:
        return await _finalize_unhandoffable_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason=str(exc)[:120] or "admin_broadcast_queue_payload_invalid",
            error_class="TelegramPayloadError",
            now=current_time,
        )

    enqueue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=TelegramFeederKind.ADMIN_SYSTEM,
        source_natural_id=source_natural_id,
        source_version=source_version,
        action=TelegramDeliveryAction.ADMIN_BROADCAST,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.PRIVATE,
        method="sendMessage",
        payload=payload,
        template_version=ADMIN_BROADCAST_TEMPLATE_VERSION,
        campaign_id=campaign_id,
    )
    job_id = int(enqueue_result.job.id)
    if not enqueue_result.created:
        # Job and receipt binding are committed in one transaction. Seeing the
        # former without the latter is therefore reconciliation work, never a
        # safe runtime adoption path.
        receipt.worker_id = f"telegram-delivery-reconcile:admin:{job_id}"[:128]
        receipt.lease_until = None
        receipt.reason = "admin_broadcast_queue_orphan_requires_reconciliation"
        receipt.next_retry_at = None
        receipt.updated_at = current_time
        await _flush(db)
        return TelegramAdminBroadcastQueueHandoffResult(
            receipt_id=int(receipt.id),
            disposition=ADMIN_BROADCAST_QUEUE_REQUIRES_RECONCILIATION,
            job_id=job_id,
            job_created=False,
            reason="admin_broadcast_queue_orphan_requires_reconciliation",
        )

    receipt.queue_job_id = job_id
    receipt.queue_handed_off_at = current_time
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.reason = "admin_broadcast_handed_to_main_queue"
    receipt.updated_at = current_time
    if broadcast.status == TelegramAdminBroadcastStatus.QUEUED:
        broadcast.status = TelegramAdminBroadcastStatus.RUNNING
        broadcast.updated_at = current_time
    # Durable last-release cursor for cross-process round-robin scheduling.
    broadcast.queue_last_handed_off_at = current_time
    await _flush(db)
    return TelegramAdminBroadcastQueueHandoffResult(
        receipt_id=int(receipt.id),
        disposition=ADMIN_BROADCAST_QUEUE_HANDOFF,
        job_id=job_id,
        job_created=True,
        reason="admin_broadcast_handed_to_main_queue",
    )
