"""Transaction-scoped cleanup of incomplete uploads during a writer promotion.

The selected three-site continuity policy is deliberately conservative:
database-visible files are *not* deleted or silently detached at cutover.
Only resumable upload work which has no ``final_chat_file_id`` may be
cancelled.  Anything that looks like a finalized-but-uncommitted upload is a
hard stop for the coordinator and must be reconciled through the physical
blob-frontier proof rather than guessed away here.

This module is default-dormant: nothing imports or calls it from request,
worker, startup, or routing code.  A future root-controlled promotion
coordinator must first fence normal admission, then call
``invalidate_sessions_on_promotion`` and this primitive in one caller-owned
PostgreSQL transaction before it permits traffic.  This primitive never
commits, rolls back, removes a local file, talks to Object Storage, or changes
routing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.promotion_session_invalidation_service import (
    PromotionSessionInvalidationBinding,
    PromotionSessionInvalidationError,
    PromotionSessionInvalidationTermFacts,
    require_active_promotion_session_invalidation_binding,
)
from models.promotion_auth_epoch import PromotionAuthEpoch
from models.upload_session import (
    UploadBatch,
    UploadBatchStatus,
    UploadSession,
    UploadSessionStatus,
)


# A promotion only changes durable state for this explicitly finite surface.
# ``COMMITTING`` and ``READY`` are excluded because either can have crossed a
# database-visible boundary; treating them as disposable would lose or
# duplicate a user message.
_CANCELLABLE_BATCH_STATUSES = frozenset(
    {
        UploadBatchStatus.COLLECTING,
        UploadBatchStatus.UPLOADING,
        UploadBatchStatus.UPLOADED,
        UploadBatchStatus.FAILED,
    }
)
_AMBIGUOUS_BATCH_STATUSES = frozenset({UploadBatchStatus.COMMITTING})
_TERMINAL_BATCH_STATUSES = frozenset(
    {
        UploadBatchStatus.COMMITTED,
        UploadBatchStatus.CANCELLED,
        UploadBatchStatus.EXPIRED,
    }
)

_CANCELLABLE_SESSION_STATUSES = frozenset(
    {
        UploadSessionStatus.CREATED,
        UploadSessionStatus.UPLOADING,
        UploadSessionStatus.UPLOADED,
        UploadSessionStatus.FINALIZING,
        UploadSessionStatus.FAILED,
    }
)
_TERMINAL_SESSION_STATUSES = frozenset(
    {
        UploadSessionStatus.COMMITTED,
        UploadSessionStatus.CANCELLED,
        UploadSessionStatus.EXPIRED,
    }
)


class PromotionUploadCleanupError(RuntimeError):
    """A promotion cannot safely dispose of its in-flight upload state."""


@dataclass(frozen=True)
class PromotionUploadCleanupResult:
    """Non-secret durable-state result staged in the caller transaction.

    IDs are returned so the coordinator can write a durable campaign receipt;
    temporary paths deliberately are not returned because filesystem cleanup
    is outside the atomic database operation and may only run after commit.
    """

    operation_id: UUID
    writer_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    cutover_at: datetime
    cancelled_session_ids: tuple[str, ...]
    cancelled_batch_ids: tuple[str, ...]
    applied: bool


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionUploadCleanupError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _require_epoch_for_binding(
    epoch: object,
    *,
    facts: PromotionSessionInvalidationTermFacts,
) -> PromotionAuthEpoch:
    """Require the auth P0 to have been staged for this exact operation.

    The auth primitive flushes its singleton before returning.  Requiring this
    row prevents a coordinator from claiming the upload P0 independently of
    the session invalidation P0, and ties both to one Writer Witness term.
    """

    if type(epoch) is not PromotionAuthEpoch:
        raise PromotionUploadCleanupError("promotion upload cleanup requires a staged auth epoch")
    if getattr(epoch, "id", None) != 1:
        raise PromotionUploadCleanupError("promotion upload cleanup auth epoch singleton is invalid")
    if (
        getattr(epoch, "operation_id", None) != str(facts.operation_id)
        or getattr(epoch, "writer_site", None) != facts.writer_site
        or getattr(epoch, "writer_epoch", None) != facts.writer_epoch
        or getattr(epoch, "writer_lease_id", None) != facts.writer_lease_id
        or getattr(epoch, "witness_transition_id", None) != facts.witness_transition_id
    ):
        raise PromotionUploadCleanupError(
            "promotion upload cleanup auth epoch does not match the active promotion operation"
        )
    _utc(getattr(epoch, "cutover_at", None), label="promotion upload cleanup cutover timestamp")
    return epoch


async def _load_locked_auth_epoch(db: AsyncSession) -> PromotionAuthEpoch | None:
    result = await db.execute(
        select(PromotionAuthEpoch)
        .where(PromotionAuthEpoch.id == 1)
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PromotionUploadCleanupError(f"{label} id is invalid")
    return value


def _classify_locked_uploads(
    *,
    batches: list[UploadBatch],
    sessions: list[UploadSession],
) -> tuple[list[UploadBatch], list[UploadSession]]:
    """Return only records known safe to cancel, or fail closed.

    The caller has already acquired a PostgreSQL table-level lock.  A session
    with a final DB file, a ``READY`` state, an unknown state, or an active
    ``COMMITTING`` batch is intentionally never auto-cleaned.
    """

    sessions_by_batch: dict[str, list[UploadSession]] = defaultdict(list)
    cancellable_sessions: list[UploadSession] = []

    active_batch_ids = {_id(batch.id, label="upload batch") for batch in batches}
    for batch in batches:
        status = getattr(batch, "status", None)
        if status in _AMBIGUOUS_BATCH_STATUSES:
            raise PromotionUploadCleanupError(
                "promotion upload cleanup found a batch crossing a database-visible commit boundary"
            )
        if status not in _CANCELLABLE_BATCH_STATUSES:
            raise PromotionUploadCleanupError("promotion upload cleanup batch status is not safely cancellable")

    for session in sessions:
        session_id = _id(getattr(session, "id", None), label="upload session")
        del session_id  # Validate early without exposing a mutable ORM object as authority.
        batch_id = getattr(session, "batch_id", None)
        if batch_id is not None:
            if not isinstance(batch_id, str) or not batch_id:
                raise PromotionUploadCleanupError("promotion upload cleanup session batch id is invalid")
            if batch_id in active_batch_ids:
                sessions_by_batch[batch_id].append(session)

        status = getattr(session, "status", None)
        final_chat_file_id = getattr(session, "final_chat_file_id", None)
        is_active_or_attached = status not in _TERMINAL_SESSION_STATUSES or batch_id in active_batch_ids
        if not is_active_or_attached:
            continue

        # A file id makes the payload database-visible.  That payload belongs
        # to the verified blob frontier, never a cleanup operation.
        if final_chat_file_id is not None:
            raise PromotionUploadCleanupError(
                "promotion upload cleanup found a database-visible finalized upload"
            )
        if status in _CANCELLABLE_SESSION_STATUSES:
            cancellable_sessions.append(session)
            continue
        if status == UploadSessionStatus.READY:
            raise PromotionUploadCleanupError(
                "promotion upload cleanup found a READY upload without a final file"
            )
        if status == UploadSessionStatus.COMMITTED:
            raise PromotionUploadCleanupError(
                "promotion upload cleanup found a committed upload in an active batch"
            )
        if status in _TERMINAL_SESSION_STATUSES:
            # Cancelled/expired rows attached to a safely cancellable batch do
            # not need a second transition.  They will be covered by the
            # parent batch transition below.
            continue
        raise PromotionUploadCleanupError("promotion upload cleanup session status is not safely cancellable")

    # A cancellable active batch may legitimately contain zero sessions.  It
    # is still safe to close, while any session attached to it was checked
    # above.  Sorting makes receipt evidence deterministic independent of DB
    # iteration order.
    return (
        sorted(batches, key=lambda item: _id(item.id, label="upload batch")),
        sorted(cancellable_sessions, key=lambda item: _id(item.id, label="upload session")),
    )


async def cancel_and_expire_unfinalized_uploads_on_promotion(
    db: AsyncSession,
    *,
    binding: PromotionSessionInvalidationBinding,
) -> PromotionUploadCleanupResult:
    """Stage the selected upload P0 in one caller-owned transaction.

    ``invalidate_sessions_on_promotion`` must already have staged its exact
    auth epoch with the same binding in this ``AsyncSession`` transaction.
    Both primitives are deliberately called by, rather than becoming, the
    promotion coordinator.  The coordinator must fence new upload admission
    before entering this transaction; the table lock below then prevents an
    already-running SQL uploader from inserting or advancing state while the
    cleanup inspection and mutations occur.
    """

    if not isinstance(db, AsyncSession):
        raise PromotionUploadCleanupError(
            "promotion upload cleanup requires a caller-owned AsyncSession transaction"
        )

    try:
        facts = require_active_promotion_session_invalidation_binding(binding)
    except PromotionSessionInvalidationError as exc:
        raise PromotionUploadCleanupError("promotion upload cleanup requires an active Writer Witness term") from exc

    # PostgreSQL-specific and intentionally broad: row locks alone do not
    # prevent a concurrent request from inserting a new resumable upload after
    # our SELECT.  The coordinator has already stopped new admission; this
    # lock closes the remaining in-flight SQL race.
    await db.execute(
        text("LOCK TABLE upload_batches, upload_sessions IN SHARE ROW EXCLUSIVE MODE")
    )
    epoch = _require_epoch_for_binding(await _load_locked_auth_epoch(db), facts=facts)
    cutover_at = _utc(epoch.cutover_at, label="promotion upload cleanup cutover timestamp")

    batch_result = await db.execute(
        select(UploadBatch)
        .where(
            UploadBatch.status.in_(
                tuple(_CANCELLABLE_BATCH_STATUSES | _AMBIGUOUS_BATCH_STATUSES)
            )
        )
        .order_by(UploadBatch.id.asc())
        .with_for_update()
    )
    batches = list(batch_result.scalars().all())
    active_batch_ids = [batch.id for batch in batches]
    session_condition = UploadSession.status.not_in(tuple(_TERMINAL_SESSION_STATUSES))
    if active_batch_ids:
        session_condition = or_(session_condition, UploadSession.batch_id.in_(active_batch_ids))
    session_result = await db.execute(
        select(UploadSession)
        .where(session_condition)
        .order_by(UploadSession.id.asc())
        .with_for_update()
    )
    sessions = list(session_result.scalars().all())

    # The table lock may have waited.  Revalidate the exact root-owned term at
    # the real mutation boundary, not just when this helper was entered.
    try:
        post_lock_facts = require_active_promotion_session_invalidation_binding(binding)
    except PromotionSessionInvalidationError as exc:
        raise PromotionUploadCleanupError("promotion upload cleanup lost its active Writer Witness term") from exc
    if post_lock_facts != facts:
        raise PromotionUploadCleanupError("promotion upload cleanup Writer Witness term changed during locking")

    cancellable_batches, cancellable_sessions = _classify_locked_uploads(
        batches=batches,
        sessions=sessions,
    )

    for session in cancellable_sessions:
        session.status = UploadSessionStatus.CANCELLED
        session.expires_at = cutover_at
        session.last_activity_at = cutover_at
        session.last_error = f"cancelled by witnessed promotion operation {facts.operation_id}"
    for batch in cancellable_batches:
        batch.status = UploadBatchStatus.CANCELLED
        batch.expires_at = cutover_at
        batch.last_activity_at = cutover_at

    if cancellable_batches or cancellable_sessions:
        # A flush exposes schema/constraint errors but deliberately leaves
        # commit/rollback and any post-commit file reaper to the coordinator.
        await db.flush()

    return PromotionUploadCleanupResult(
        operation_id=facts.operation_id,
        writer_site=facts.writer_site,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witness_transition_id=facts.witness_transition_id,
        cutover_at=cutover_at,
        cancelled_session_ids=tuple(_id(item.id, label="upload session") for item in cancellable_sessions),
        cancelled_batch_ids=tuple(_id(item.id, label="upload batch") for item in cancellable_batches),
        applied=bool(cancellable_batches or cancellable_sessions),
    )


__all__ = [
    "PromotionUploadCleanupError",
    "PromotionUploadCleanupResult",
    "cancel_and_expire_unfinalized_uploads_on_promotion",
]
