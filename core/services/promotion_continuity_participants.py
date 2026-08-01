"""One-transaction composition of the selected auth and upload promotion P0s.

This deliberately is not a promotion authority.  It does not recover or
promote PostgreSQL, acquire/consume a Witness term, alter traffic, authorize
an external-effect executor, perform filesystem cleanup, or commit a
transaction.  It merely makes it hard for a future root-controlled coordinator
to accidentally execute the chosen session and in-flight-upload policies with
different operation IDs or writer terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.services.promotion_session_invalidation_service import (
    PromotionSessionInvalidationBinding,
    PromotionSessionInvalidationError,
    PromotionSessionInvalidationResult,
    require_active_promotion_session_invalidation_binding,
    invalidate_sessions_on_promotion,
)
from core.services.promotion_upload_cleanup_service import (
    PromotionUploadCleanupError,
    PromotionUploadCleanupResult,
    cancel_and_expire_unfinalized_uploads_on_promotion,
)


class PromotionContinuityParticipantsError(RuntimeError):
    """The selected P0 participants could not be staged as one operation."""


@dataclass(frozen=True)
class PromotionContinuityParticipantsResult:
    """The two local P0 results staged in the caller-owned transaction."""

    auth: PromotionSessionInvalidationResult
    uploads: PromotionUploadCleanupResult


def _same_operation_and_term(
    auth: PromotionSessionInvalidationResult,
    uploads: PromotionUploadCleanupResult,
) -> bool:
    return (
        auth.operation_id == uploads.operation_id
        and auth.writer_site == uploads.writer_site
        and auth.writer_epoch == uploads.writer_epoch
        and auth.writer_lease_id == uploads.writer_lease_id
        and auth.witness_transition_id == uploads.witness_transition_id
        and auth.cutover_at == uploads.cutover_at
    )


def _require_result_shape(
    auth: object,
    uploads: object,
) -> tuple[PromotionSessionInvalidationResult, PromotionUploadCleanupResult]:
    """Reject substituted participant projections before comparing them.

    This helper is a coordinator boundary, so dataclass equality is not enough:
    Python would otherwise accept values such as ``True == 1`` in a forged
    result.  Both concrete participants already return these exact types; a
    different type means the caller cannot rely on their transaction contract.
    """

    if type(auth) is not PromotionSessionInvalidationResult:
        raise PromotionContinuityParticipantsError("promotion auth participant result is invalid")
    if type(uploads) is not PromotionUploadCleanupResult:
        raise PromotionContinuityParticipantsError("promotion upload participant result is invalid")
    for label, value in (("auth", auth), ("uploads", uploads)):
        if not isinstance(value.operation_id, UUID):
            raise PromotionContinuityParticipantsError(f"promotion {label} operation id is invalid")
        if value.writer_site not in {"webapp_fi", "webapp_ir"}:
            raise PromotionContinuityParticipantsError(f"promotion {label} writer site is invalid")
        if type(value.writer_epoch) is not int or value.writer_epoch < 1:
            raise PromotionContinuityParticipantsError(f"promotion {label} writer term is invalid")
        if not isinstance(value.writer_lease_id, str) or not value.writer_lease_id:
            raise PromotionContinuityParticipantsError(f"promotion {label} writer lease is invalid")
        if not isinstance(value.witness_transition_id, str) or not value.witness_transition_id:
            raise PromotionContinuityParticipantsError(f"promotion {label} Witness transition is invalid")
        if (
            not isinstance(value.cutover_at, datetime)
            or value.cutover_at.tzinfo is None
            or value.cutover_at.utcoffset() is None
        ):
            raise PromotionContinuityParticipantsError(f"promotion {label} cutover timestamp is invalid")
    return auth, uploads


async def stage_promotion_auth_and_upload_cleanup(
    db: AsyncSession,
    *,
    binding: PromotionSessionInvalidationBinding,
    now: datetime | None = None,
) -> PromotionContinuityParticipantsResult:
    """Stage selected session/upload P0s under one exact live Witness term.

    The caller must already have fenced normal traffic and must retain
    ownership of the transaction.  The sequence is intentionally fixed:

    1. validate the exact local Writer Witness term;
    2. stage and flush the durable auth epoch/session invalidation;
    3. stage upload cleanup, which locks and verifies that same epoch; and
    4. revalidate the exact term once more before returning to the caller.

    If any later coordinator participant fails, its caller *must roll back*;
    neither local primitive commits or rolls back on its own.
    """

    try:
        before = require_active_promotion_session_invalidation_binding(binding)
        auth = await invalidate_sessions_on_promotion(db, binding=binding, now=now)
        uploads = await cancel_and_expire_unfinalized_uploads_on_promotion(
            db,
            binding=binding,
        )
        after = require_active_promotion_session_invalidation_binding(binding)
    except (PromotionSessionInvalidationError, PromotionUploadCleanupError) as exc:
        raise PromotionContinuityParticipantsError(
            "promotion continuity participants require one active Writer Witness term"
        ) from exc

    auth, uploads = _require_result_shape(auth, uploads)
    if before != after:
        raise PromotionContinuityParticipantsError(
            "Writer Witness term changed while promotion continuity participants were staged"
        )
    if not _same_operation_and_term(auth, uploads):
        raise PromotionContinuityParticipantsError(
            "promotion continuity participants returned mismatched operation or Writer Witness evidence"
        )
    return PromotionContinuityParticipantsResult(auth=auth, uploads=uploads)


__all__ = [
    "PromotionContinuityParticipantsError",
    "PromotionContinuityParticipantsResult",
    "stage_promotion_auth_and_upload_cleanup",
]
