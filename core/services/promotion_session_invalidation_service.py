"""Transaction-scoped authentication invalidation for a promoted writer.

This module is intentionally narrow.  It does **not** promote a database,
acquire/renew a Witness lease, alter routing, start traffic, publish an event,
or commit a transaction.  A separately reviewed coordinator must stop or
fence admission, prove its local Writer Witness term, call this primitive in
the same database transaction as its other cutover state, and commit only
when the complete coordinator operation is ready.

Before the first durable epoch exists, token admission remains backward
compatible.  Once an epoch exists, every access token must carry a valid JWT
``iat`` at or after its logical whole-second cutoff.  That catches legacy
sessionless JWTs as well as session-bound tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Mapping
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.application_writer_term import ApplicationWriterTermError, ValidatedWriterTerm
from core.db import require_application_writer_term
from models.promotion_auth_epoch import PromotionAuthEpoch, PromotionAuthEpochOperation
from models.session import (
    LoginRequestStatus,
    SessionLoginRequest,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
    UserSession,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SITES = frozenset({"webapp_fi", "webapp_ir"})
_SINGLETON_ID = 1
MAX_FUTURE_TOKEN_IAT_SKEW_SECONDS = 5


class PromotionSessionInvalidationError(RuntimeError):
    """Raised when a coordinator cannot safely establish an auth cutover."""


class PromotionAccessTokenEpochError(PromotionSessionInvalidationError):
    """Raised when an access JWT is older than the durable auth epoch."""


@dataclass(frozen=True)
class PromotionSessionInvalidationBinding:
    """Typed, one-operation binding supplied by a promotion coordinator.

    The term is intentionally retained in full rather than reducing it to an
    epoch number.  ``invalidate_sessions_on_promotion`` re-reads the local
    root-owned lease immediately before mutation and requires every immutable
    term field to match this binding exactly.
    """

    operation_id: UUID
    writer_term: ValidatedWriterTerm


@dataclass(frozen=True)
class PromotionSessionInvalidationResult:
    """Non-secret result of staging one auth epoch in the caller transaction."""

    operation_id: UUID
    writer_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    cutover_at: datetime
    minimum_token_iat: int
    invalidated_sessions: int
    expired_login_requests: int
    cancelled_recovery_requests: int
    applied: bool


@dataclass(frozen=True)
class PromotionSessionInvalidationTermFacts:
    """The non-secret, exact live term facts accepted for one operation.

    A companion cutover participant may call
    :func:`require_active_promotion_session_invalidation_binding` before and
    after its own database locks.  It remains a validation-only API: it does
    not mutate the epoch, sessions, traffic, or the Witness lease.
    """

    operation_id: UUID
    writer_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str


def _utc_now(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PromotionSessionInvalidationError("promotion auth epoch clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _canonical_operation_id(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PromotionSessionInvalidationError("promotion auth epoch operation id is invalid") from exc
    if str(parsed) != str(value):
        raise PromotionSessionInvalidationError("promotion auth epoch operation id must be canonical")
    return parsed


def _validate_term(value: object, *, label: str) -> ValidatedWriterTerm:
    if type(value) is not ValidatedWriterTerm:
        raise PromotionSessionInvalidationError(f"{label} writer term is invalid")
    if value.holder_site not in _SITES:
        raise PromotionSessionInvalidationError(f"{label} writer site is invalid")
    if type(value.writer_epoch) is not int or value.writer_epoch < 1:
        raise PromotionSessionInvalidationError(f"{label} writer epoch is invalid")
    if not isinstance(value.lease_id, str) or not _IDENTIFIER_RE.fullmatch(value.lease_id):
        raise PromotionSessionInvalidationError(f"{label} writer lease id is invalid")
    if (
        not isinstance(value.witness_transition_id, str)
        or not _IDENTIFIER_RE.fullmatch(value.witness_transition_id)
    ):
        raise PromotionSessionInvalidationError(f"{label} Witness transition id is invalid")
    issued_at = _utc_now(value.issued_at)
    expires_at = _utc_now(value.expires_at)
    if expires_at <= issued_at:
        raise PromotionSessionInvalidationError(f"{label} writer term window is invalid")
    return value


def _term_identity(term: ValidatedWriterTerm) -> tuple[object, ...]:
    return (
        term.holder_site,
        term.writer_epoch,
        term.lease_id,
        term.issued_at.astimezone(timezone.utc),
        term.expires_at.astimezone(timezone.utc),
        term.witness_transition_id,
    )


def require_active_promotion_session_invalidation_binding(
    binding: PromotionSessionInvalidationBinding,
) -> PromotionSessionInvalidationTermFacts:
    """Revalidate the exact local term; disabled policy is never authority."""

    if type(binding) is not PromotionSessionInvalidationBinding:
        raise PromotionSessionInvalidationError("promotion auth epoch binding is invalid")
    operation_id = _canonical_operation_id(binding.operation_id)
    bound = _validate_term(binding.writer_term, label="promotion auth epoch binding")
    try:
        live = require_application_writer_term()
    except ApplicationWriterTermError as exc:
        raise PromotionSessionInvalidationError(
            "promotion auth epoch requires an active local Writer Witness term"
        ) from exc
    if live is None:
        raise PromotionSessionInvalidationError(
            "promotion auth epoch cannot run while Writer Witness enforcement is disabled"
        )
    live = _validate_term(live, label="active")
    if _term_identity(live) != _term_identity(bound):
        raise PromotionSessionInvalidationError(
            "promotion auth epoch binding does not match the active local Writer Witness term"
        )
    return PromotionSessionInvalidationTermFacts(
        operation_id=operation_id,
        writer_site=live.holder_site,
        writer_epoch=live.writer_epoch,
        writer_lease_id=live.lease_id,
        witness_transition_id=live.witness_transition_id,
    )


def bind_promotion_session_invalidation(
    *,
    operation_id: UUID | str,
) -> PromotionSessionInvalidationBinding:
    """Capture a typed operation binding from the *currently* active term.

    This does not mutate any database state and is not a promotion approval.
    The mutation function independently rechecks this same term immediately
    before it changes session state.
    """

    normalized_operation_id = _canonical_operation_id(operation_id)
    try:
        live = require_application_writer_term()
    except ApplicationWriterTermError as exc:
        raise PromotionSessionInvalidationError(
            "promotion auth epoch requires an active local Writer Witness term"
        ) from exc
    if live is None:
        raise PromotionSessionInvalidationError(
            "promotion auth epoch cannot bind while Writer Witness enforcement is disabled"
        )
    _validate_term(live, label="active")
    return PromotionSessionInvalidationBinding(
        operation_id=normalized_operation_id,
        writer_term=live,
    )


def _row_count(value: object) -> int:
    rowcount = getattr(value, "rowcount", 0)
    if type(rowcount) is int and rowcount >= 0:
        return rowcount
    # Some drivers cannot provide a reliable count for a bulk UPDATE.  Do not
    # manufacture one; the durable changes are still made in the transaction.
    return 0


def _epoch_cutover_at(
    *,
    observed_at: datetime,
    prior: PromotionAuthEpoch | None,
) -> datetime:
    if prior is None:
        return observed_at
    prior_cutover = _utc_now(getattr(prior, "cutover_at", None))
    # A logical microsecond floor makes cutover state strictly monotonic even
    # if the local clock has moved backwards.  It can only reject more old
    # tokens; it never creates an acceptance gap.
    return max(observed_at, prior_cutover + timedelta(microseconds=1))


def _minimum_token_iat(*, cutover_at: datetime, prior: PromotionAuthEpoch | None) -> int:
    value = math.ceil(cutover_at.timestamp())
    if prior is None:
        return value
    prior_minimum = getattr(prior, "minimum_token_iat", None)
    if type(prior_minimum) is not int or prior_minimum < 0:
        raise PromotionSessionInvalidationError("stored promotion auth epoch cutoff is invalid")
    return max(value, prior_minimum + 1)


def _validate_existing_epoch(epoch: object) -> PromotionAuthEpoch:
    if type(epoch) is not PromotionAuthEpoch:
        raise PromotionSessionInvalidationError("stored promotion auth epoch is invalid")
    if getattr(epoch, "id", None) != _SINGLETON_ID:
        raise PromotionSessionInvalidationError("stored promotion auth epoch singleton is invalid")
    if getattr(epoch, "writer_site", None) not in _SITES:
        raise PromotionSessionInvalidationError("stored promotion auth epoch writer site is invalid")
    if type(getattr(epoch, "writer_epoch", None)) is not int or epoch.writer_epoch < 1:
        raise PromotionSessionInvalidationError("stored promotion auth epoch writer term is invalid")
    if not isinstance(getattr(epoch, "writer_lease_id", None), str) or not _IDENTIFIER_RE.fullmatch(
        epoch.writer_lease_id
    ):
        raise PromotionSessionInvalidationError("stored promotion auth epoch writer lease is invalid")
    if not isinstance(getattr(epoch, "witness_transition_id", None), str) or not _IDENTIFIER_RE.fullmatch(
        epoch.witness_transition_id
    ):
        raise PromotionSessionInvalidationError("stored promotion auth epoch Witness transition is invalid")
    _canonical_operation_id(getattr(epoch, "operation_id", None))
    _utc_now(getattr(epoch, "cutover_at", None))
    if type(getattr(epoch, "minimum_token_iat", None)) is not int or epoch.minimum_token_iat < 0:
        raise PromotionSessionInvalidationError("stored promotion auth epoch cutoff is invalid")
    return epoch


def _validate_existing_operation(epoch_operation: object) -> PromotionAuthEpochOperation:
    if type(epoch_operation) is not PromotionAuthEpochOperation:
        raise PromotionSessionInvalidationError("stored promotion auth epoch operation is invalid")
    _canonical_operation_id(getattr(epoch_operation, "operation_id", None))
    if getattr(epoch_operation, "writer_site", None) not in _SITES:
        raise PromotionSessionInvalidationError("stored promotion auth epoch operation writer site is invalid")
    if (
        type(getattr(epoch_operation, "writer_epoch", None)) is not int
        or epoch_operation.writer_epoch < 1
    ):
        raise PromotionSessionInvalidationError("stored promotion auth epoch operation writer term is invalid")
    if not isinstance(getattr(epoch_operation, "writer_lease_id", None), str) or not _IDENTIFIER_RE.fullmatch(
        epoch_operation.writer_lease_id
    ):
        raise PromotionSessionInvalidationError("stored promotion auth epoch operation writer lease is invalid")
    if not isinstance(
        getattr(epoch_operation, "witness_transition_id", None), str
    ) or not _IDENTIFIER_RE.fullmatch(epoch_operation.witness_transition_id):
        raise PromotionSessionInvalidationError(
            "stored promotion auth epoch operation Witness transition is invalid"
        )
    _utc_now(getattr(epoch_operation, "cutover_at", None))
    if (
        type(getattr(epoch_operation, "minimum_token_iat", None)) is not int
        or epoch_operation.minimum_token_iat < 0
    ):
        raise PromotionSessionInvalidationError("stored promotion auth epoch operation cutoff is invalid")
    return epoch_operation


async def _load_epoch(
    db: AsyncSession,
    *,
    for_update: bool,
) -> PromotionAuthEpoch | None:
    statement = select(PromotionAuthEpoch).where(PromotionAuthEpoch.id == _SINGLETON_ID)
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    value = result.scalar_one_or_none()
    if value is None:
        return None
    return _validate_existing_epoch(value)


async def _load_operation(
    db: AsyncSession,
    *,
    operation_id: UUID,
    for_update: bool,
) -> PromotionAuthEpochOperation | None:
    statement = select(PromotionAuthEpochOperation).where(
        PromotionAuthEpochOperation.operation_id == str(operation_id)
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    value = result.scalar_one_or_none()
    if value is None:
        return None
    return _validate_existing_operation(value)


def _result_from_epoch(
    epoch: PromotionAuthEpoch,
    *,
    applied: bool,
    invalidated_sessions: int = 0,
    expired_login_requests: int = 0,
    cancelled_recovery_requests: int = 0,
) -> PromotionSessionInvalidationResult:
    return PromotionSessionInvalidationResult(
        operation_id=_canonical_operation_id(epoch.operation_id),
        writer_site=epoch.writer_site,
        writer_epoch=epoch.writer_epoch,
        writer_lease_id=epoch.writer_lease_id,
        witness_transition_id=epoch.witness_transition_id,
        cutover_at=_utc_now(epoch.cutover_at),
        minimum_token_iat=epoch.minimum_token_iat,
        invalidated_sessions=invalidated_sessions,
        expired_login_requests=expired_login_requests,
        cancelled_recovery_requests=cancelled_recovery_requests,
        applied=applied,
    )


async def invalidate_sessions_on_promotion(
    db: AsyncSession,
    *,
    binding: PromotionSessionInvalidationBinding,
    now: datetime | None = None,
) -> PromotionSessionInvalidationResult:
    """Stage a durable auth cutover and invalidate login state atomically.

    The caller owns the transaction and must call ``commit`` or ``rollback``;
    this function never commits, rolls back, emits Redis messages, or does
    network I/O.  It validates the active local Witness term before lock
    acquisition and *again* immediately before bulk mutation.  Calling this
    while the Writer Witness enforcement policy is disabled is a hard error.

    Repeating the exact operation against the exact same Writer Witness term
    is idempotent.  A same/lower term with another operation, a reused
    operation ID, or any term regression fails closed.
    """

    if any(not callable(getattr(db, name, None)) for name in ("execute", "add", "flush")):
        # Keep the contract narrow without depending on an exact concrete
        # class: an application may wrap AsyncSession, but it must still pass
        # one caller-owned async transaction surface and cannot be an
        # auto-committing repository abstraction.
        raise PromotionSessionInvalidationError("promotion auth epoch requires an AsyncSession transaction")

    facts = require_active_promotion_session_invalidation_binding(binding)
    observed_at = _utc_now(now)
    existing = await _load_epoch(db, for_update=True)
    consumed_operation = await _load_operation(
        db,
        operation_id=facts.operation_id,
        for_update=True,
    )
    requested_term = (
        facts.writer_site,
        facts.writer_epoch,
        facts.writer_lease_id,
        facts.witness_transition_id,
    )

    if consumed_operation is not None:
        consumed_term = (
            consumed_operation.writer_site,
            consumed_operation.writer_epoch,
            consumed_operation.writer_lease_id,
            consumed_operation.witness_transition_id,
        )
        if consumed_term != requested_term:
            raise PromotionSessionInvalidationError(
                "promotion auth epoch operation id replay conflicts with the Writer Witness term"
            )
        if existing is None or _canonical_operation_id(existing.operation_id) != facts.operation_id:
            raise PromotionSessionInvalidationError(
                "promotion auth epoch operation id was already consumed by a prior Writer Witness term"
            )
        current_term = (
            existing.writer_site,
            existing.writer_epoch,
            existing.writer_lease_id,
            existing.witness_transition_id,
        )
        if current_term != requested_term or (
            existing.cutover_at != consumed_operation.cutover_at
            or existing.minimum_token_iat != consumed_operation.minimum_token_iat
        ):
            raise PromotionSessionInvalidationError(
                "current promotion auth epoch conflicts with its append-only operation ledger"
            )
        # Recheck after both locks even for the idempotent path; a former
        # writer cannot use a stale binding as an observation API.
        require_active_promotion_session_invalidation_binding(binding)
        return _result_from_epoch(existing, applied=False)

    if existing is not None:
        existing_operation_id = _canonical_operation_id(existing.operation_id)
        if existing_operation_id == facts.operation_id:
            raise PromotionSessionInvalidationError(
                "current promotion auth epoch is missing its append-only operation ledger"
            )
        if facts.writer_epoch < existing.writer_epoch:
            raise PromotionSessionInvalidationError("promotion auth epoch Writer Witness term regressed")
        if facts.writer_epoch == existing.writer_epoch:
            raise PromotionSessionInvalidationError(
                "promotion auth epoch Writer Witness term is already bound to another operation"
            )

    # The lock may have waited.  Re-read the root-owned lease at the actual
    # write boundary and require the typed binding still matches it exactly.
    facts = require_active_promotion_session_invalidation_binding(binding)
    cutover_at = _epoch_cutover_at(observed_at=observed_at, prior=existing)
    minimum_token_iat = _minimum_token_iat(cutover_at=cutover_at, prior=existing)

    if existing is None:
        epoch = PromotionAuthEpoch(
            id=_SINGLETON_ID,
            operation_id=str(facts.operation_id),
            writer_site=facts.writer_site,
            writer_epoch=facts.writer_epoch,
            writer_lease_id=facts.writer_lease_id,
            witness_transition_id=facts.witness_transition_id,
            cutover_at=cutover_at,
            minimum_token_iat=minimum_token_iat,
        )
        db.add(epoch)
    else:
        epoch = existing
        epoch.operation_id = str(facts.operation_id)
        epoch.writer_site = facts.writer_site
        epoch.writer_epoch = facts.writer_epoch
        epoch.writer_lease_id = facts.writer_lease_id
        epoch.witness_transition_id = facts.witness_transition_id
        epoch.cutover_at = cutover_at
        epoch.minimum_token_iat = minimum_token_iat

    db.add(
        PromotionAuthEpochOperation(
            operation_id=str(facts.operation_id),
            writer_site=facts.writer_site,
            writer_epoch=facts.writer_epoch,
            writer_lease_id=facts.writer_lease_id,
            witness_transition_id=facts.witness_transition_id,
            cutover_at=cutover_at,
            minimum_token_iat=minimum_token_iat,
        )
    )

    session_update = await db.execute(
        update(UserSession)
        .where(UserSession.is_active.is_(True))
        .values(
            is_active=False,
            is_primary=False,
            last_active_at=cutover_at,
        )
    )
    login_request_update = await db.execute(
        update(SessionLoginRequest)
        .where(SessionLoginRequest.status == LoginRequestStatus.PENDING)
        .values(
            status=LoginRequestStatus.EXPIRED,
            expires_at=cutover_at,
            resolved_by_session_id=None,
        )
    )
    recovery_update = await db.execute(
        update(SingleSessionRecoveryRequest)
        .where(
            SingleSessionRecoveryRequest.status.in_(
                (
                    SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
                    SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
                    SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
                )
            )
        )
        .values(
            status=SingleSessionRecoveryStatus.CANCELLED,
            cancelled_at=cutover_at,
            inline_action_expires_at=cutover_at,
            chat_action_expires_at=cutover_at,
        )
    )
    # Flush makes SQL constraint failures visible to the coordinator while
    # retaining its transaction ownership.  A later rollback undoes the epoch
    # and every bulk change together.
    await db.flush()
    return _result_from_epoch(
        epoch,
        applied=True,
        invalidated_sessions=_row_count(session_update),
        expired_login_requests=_row_count(login_request_update),
        cancelled_recovery_requests=_row_count(recovery_update),
    )


def _validated_token_iat(
    payload: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> int:
    if not isinstance(payload, Mapping):
        raise PromotionAccessTokenEpochError("access token payload is invalid")
    issued_at = payload.get("iat")
    if type(issued_at) is not int or issued_at < 0:
        raise PromotionAccessTokenEpochError("access token is missing a valid issued-at claim")
    current = _utc_now(now)
    if issued_at > math.floor(current.timestamp()) + MAX_FUTURE_TOKEN_IAT_SKEW_SECONDS:
        raise PromotionAccessTokenEpochError("access token issued-at claim is in the future")
    return issued_at


def require_access_token_current_for_epoch(
    payload: Mapping[str, object],
    epoch: PromotionAuthEpoch | None,
    *,
    now: datetime | None = None,
) -> None:
    """Fail closed only after a durable epoch exists.

    This function is pure after the supplied row has been loaded, which lets
    HTTP and WebSocket paths make the same decision without using separate
    JWT parsing rules.
    """

    if epoch is None:
        return
    epoch = _validate_existing_epoch(epoch)
    if payload.get("type") != "access":
        raise PromotionAccessTokenEpochError("JWT is not an access token")
    issued_at = _validated_token_iat(payload, now=now)
    if issued_at < epoch.minimum_token_iat:
        raise PromotionAccessTokenEpochError("access token predates the active promotion auth epoch")


async def enforce_access_token_auth_epoch(
    db: AsyncSession,
    payload: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> PromotionAuthEpoch | None:
    """Load the durable epoch and enforce it without committing a request."""

    epoch = await _load_epoch(db, for_update=False)
    require_access_token_current_for_epoch(payload, epoch, now=now)
    return epoch


__all__ = [
    "MAX_FUTURE_TOKEN_IAT_SKEW_SECONDS",
    "PromotionAccessTokenEpochError",
    "PromotionSessionInvalidationBinding",
    "PromotionSessionInvalidationError",
    "PromotionSessionInvalidationResult",
    "PromotionSessionInvalidationTermFacts",
    "bind_promotion_session_invalidation",
    "enforce_access_token_auth_epoch",
    "invalidate_sessions_on_promotion",
    "require_active_promotion_session_invalidation_binding",
    "require_access_token_current_for_epoch",
]
