"""Default-off, non-authorizing composition for the three selected P0s.

The selected promotion policies have deliberately separate owners:

* the auth/session participant stages a durable auth epoch;
* the upload participant stages cancellation/expiry of only unfinalized work;
  and
* the external-effect gate carries a term-bound ``complete_no_resend``
  reconciliation decision.

``promotion_continuity_participants`` composes only the first two in a
caller-owned transaction.  The Full-Matrix readiness report can observe all
three inputs, but it is deliberately not a promotion authority and cannot
prove that a database transaction committed.  This module fills neither of
those runtime gaps.  It is a narrow, local preflight seam for a future
root-controlled coordinator: it rejects unless all three already-produced
projections bind to one explicit operation, one exact Writer Witness term,
and one explicitly pinned no-resend receipt.

Nothing here opens a database, reads a file, contacts a Witness, talks to a
peer, performs a provider call, changes traffic, commits a transaction, or
promotes a site.  The default is disabled.  A verified result is
process-local provenance only, not writer, promotion, or external-effect
authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import re
import sys
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from core.application_writer_term import ValidatedWriterTerm
from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_EXECUTION_SCOPES,
    RECONCILIATION_DECISION_COMPLETE_NO_RESEND,
    ExternalEffectExecutionAuthorization,
    ExternalEffectExecutionGateError,
    external_effect_execution_authorization_mapping,
    parse_external_effect_execution_authorization,
)


__all__ = (
    "DEFAULT_PROMOTION_P0_CONTINUITY_MAX_EVIDENCE_AGE_SECONDS",
    "PROMOTION_P0_CONTINUITY_PREFLIGHT_DEFAULT_ENABLED",
    "PROMOTION_P0_CONTINUITY_PREFLIGHT_SCHEMA",
    "PromotionP0ContinuityPreflightBinding",
    "PromotionP0ContinuityPreflightConfig",
    "PromotionP0ContinuityPreflightError",
    "PromotionP0ContinuityPreflightInputs",
    "VerifiedPromotionP0ContinuityPreflight",
    "require_verified_promotion_p0_continuity_preflight",
    "verify_promotion_p0_continuity_preflight",
)


PROMOTION_P0_CONTINUITY_PREFLIGHT_SCHEMA: Final = (
    "gold-trade-promotion-p0-continuity-preflight-v1"
)
PROMOTION_P0_CONTINUITY_PREFLIGHT_DEFAULT_ENABLED: Final = False
DEFAULT_PROMOTION_P0_CONTINUITY_MAX_EVIDENCE_AGE_SECONDS: Final = 300
_MIN_MAX_EVIDENCE_AGE_SECONDS: Final = 1
_MAX_MAX_EVIDENCE_AGE_SECONDS: Final = 300
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SITES = frozenset({"webapp_fi", "webapp_ir"})
_VERIFIED_CAPABILITY = object()


class PromotionP0ContinuityPreflightError(ValueError):
    """The supplied selected-P0 projections cannot be composed safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PromotionP0ContinuityPreflightError(code)


@dataclass(frozen=True)
class PromotionP0ContinuityPreflightConfig:
    """Explicit default-off policy with no endpoint, path, or credential."""

    enabled: bool = PROMOTION_P0_CONTINUITY_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PROMOTION_P0_CONTINUITY_MAX_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PromotionP0ContinuityPreflightBinding:
    """The coordinator-supplied operation and pinned no-resend receipt.

    The external-effect authorization schema intentionally does not carry an
    operation UUID.  The future durable coordinator must therefore persist or
    otherwise independently bind this exact authorization ID/evidence hash to
    its operation before it asks this pure preflight to compare them.  This
    object is not that durable record and cannot replace one.
    """

    operation_id: UUID
    writer_term: ValidatedWriterTerm
    external_effect_authorization_id: str
    external_effect_reconciliation_evidence_sha256: str


@dataclass(frozen=True)
class PromotionP0ContinuityPreflightInputs:
    """Already-produced, non-authorizing selected-P0 projections."""

    auth_upload_result: object
    external_effect_authorization: object


@dataclass(frozen=True, eq=False)
class VerifiedPromotionP0ContinuityPreflight:
    """Opaque local provenance for a matching P0 preflight, never authority."""

    schema: str
    operation_id: UUID
    writer_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    auth_upload_cutover_at: datetime
    external_effect_authorization_id: str
    external_effect_reconciliation_evidence_sha256: str
    external_effect_authorization_expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PROMOTION_P0_CONTINUITY_PREFLIGHT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _VerifiedState:
    config: object
    binding: object
    inputs: object
    projection: VerifiedPromotionP0ContinuityPreflight


_VERIFIED_STATES: WeakKeyDictionary[
    VerifiedPromotionP0ContinuityPreflight,
    _VerifiedState,
] = WeakKeyDictionary()


@dataclass(frozen=True)
class _BindingFacts:
    operation_id: UUID
    writer_term: ValidatedWriterTerm
    issued_at: datetime
    expires_at: datetime
    external_effect_authorization_id: str
    external_effect_reconciliation_evidence_sha256: str


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    offset = value.utcoffset()
    if offset is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _identifier(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _maximum_evidence_age(config: object) -> int:
    if type(config) is not PromotionP0ContinuityPreflightConfig:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_CONFIG_INVALID")
    if config.enabled is False:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_DISABLED")
    if config.enabled is not True:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_CONFIG_INVALID")
    maximum = config.maximum_evidence_age_seconds
    if (
        type(maximum) is not int
        or not _MIN_MAX_EVIDENCE_AGE_SECONDS
        <= maximum
        <= _MAX_MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_CONFIG_INVALID")
    return maximum


def _binding_facts(
    value: object,
    *,
    now: datetime,
) -> _BindingFacts:
    if type(value) is not PromotionP0ContinuityPreflightBinding:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    operation_id = value.operation_id
    if type(operation_id) is not UUID or operation_id.int == 0:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    term = value.writer_term
    if type(term) is not ValidatedWriterTerm:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    if term.holder_site not in _SITES or type(term.writer_epoch) is not int or term.writer_epoch < 1:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    _identifier(term.lease_id, code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    _identifier(
        term.witness_transition_id,
        code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID",
    )
    issued_at = _utc(term.issued_at, code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    expires_at = _utc(term.expires_at, code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID")
    if issued_at > now or expires_at <= now or expires_at <= issued_at:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_TERM_STALE_OR_EXPIRED")
    return _BindingFacts(
        operation_id=operation_id,
        writer_term=term,
        issued_at=issued_at,
        expires_at=expires_at,
        external_effect_authorization_id=_identifier(
            value.external_effect_authorization_id,
            code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID",
        ),
        external_effect_reconciliation_evidence_sha256=_sha256(
            value.external_effect_reconciliation_evidence_sha256,
            code="PROMOTION_P0_CONTINUITY_PREFLIGHT_BINDING_INVALID",
        ),
    )


def _fresh(value: object, *, now: datetime, maximum_age_seconds: int, code: str) -> datetime:
    observed = _utc(value, code=code)
    if observed > now or now - observed > timedelta(seconds=maximum_age_seconds):
        _fail(code)
    return observed


def _is_exact_nominal_type(value: object, *, module: str, name: str) -> bool:
    """Avoid importing DB-owning P0 services merely to inspect a result."""

    owner = sys.modules.get(module)
    expected = None if owner is None else getattr(owner, name, None)
    return isinstance(expected, type) and type(value) is expected


def _require_participants(
    value: object,
    *,
    facts: _BindingFacts,
    now: datetime,
    maximum_age_seconds: int,
) -> datetime:
    """Validate the exact staged auth/upload pair without owning its DB work."""

    if not _is_exact_nominal_type(
        value,
        module="core.services.promotion_continuity_participants",
        name="PromotionContinuityParticipantsResult",
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_INVALID")
    auth = value.auth
    uploads = value.uploads
    if not (
        _is_exact_nominal_type(
            auth,
            module="core.services.promotion_session_invalidation_service",
            name="PromotionSessionInvalidationResult",
        )
        and _is_exact_nominal_type(
            uploads,
            module="core.services.promotion_upload_cleanup_service",
            name="PromotionUploadCleanupResult",
        )
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_INVALID")

    for participant in (auth, uploads):
        if (
            type(participant.operation_id) is not UUID
            or participant.operation_id != facts.operation_id
            or participant.writer_site != facts.writer_term.holder_site
            or type(participant.writer_epoch) is not int
            or participant.writer_epoch != facts.writer_term.writer_epoch
            or participant.writer_lease_id != facts.writer_term.lease_id
            or participant.witness_transition_id != facts.writer_term.witness_transition_id
        ):
            _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_MISMATCH")
    # Auth invalidation must have staged its durable epoch.  Upload cleanup is
    # different: a clean target has no incomplete uploads to mutate, and the
    # owning service correctly reports that successful no-op as ``False``.
    if auth.applied is not True:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_MISMATCH")

    auth_cutover = _fresh(
        auth.cutover_at,
        now=now,
        maximum_age_seconds=maximum_age_seconds,
        code="PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_STALE_OR_EXPIRED",
    )
    upload_cutover = _fresh(
        uploads.cutover_at,
        now=now,
        maximum_age_seconds=maximum_age_seconds,
        code="PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_STALE_OR_EXPIRED",
    )
    if auth_cutover != upload_cutover:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_MISMATCH")
    if (
        type(auth.minimum_token_iat) is not int
        or auth.minimum_token_iat < math.ceil(auth_cutover.timestamp())
        or any(
            type(value) is not int or value < 0
            for value in (
                auth.invalidated_sessions,
                auth.expired_login_requests,
                auth.cancelled_recovery_requests,
            )
        )
        or type(uploads.cancelled_session_ids) is not tuple
        or type(uploads.cancelled_batch_ids) is not tuple
        or any(
            not isinstance(item, str)
            for item in (*uploads.cancelled_session_ids, *uploads.cancelled_batch_ids)
        )
        or any(
            _IDENTIFIER_RE.fullmatch(item) is None
            for item in (*uploads.cancelled_session_ids, *uploads.cancelled_batch_ids)
        )
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_INVALID")
    cancelled_upload_ids = (
        *uploads.cancelled_session_ids,
        *uploads.cancelled_batch_ids,
    )
    if type(uploads.applied) is not bool:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_INVALID")
    if uploads.applied is not bool(cancelled_upload_ids):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_AUTH_UPLOAD_MISMATCH")
    return auth_cutover


def _require_external_effect_authorization(
    value: object,
    *,
    facts: _BindingFacts,
    now: datetime,
    maximum_age_seconds: int,
) -> ExternalEffectExecutionAuthorization:
    if type(value) is not ExternalEffectExecutionAuthorization:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_INVALID")
    try:
        normalized = parse_external_effect_execution_authorization(
            external_effect_execution_authorization_mapping(value)
        )
    except ExternalEffectExecutionGateError as exc:
        raise PromotionP0ContinuityPreflightError(
            "PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_INVALID"
        ) from exc
    if normalized != value:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_TAMPERED")
    if (
        normalized.authorization_id != facts.external_effect_authorization_id
        or normalized.reconciliation_evidence_sha256
        != facts.external_effect_reconciliation_evidence_sha256
        or normalized.holder_site != facts.writer_term.holder_site
        or normalized.writer_epoch != facts.writer_term.writer_epoch
        or normalized.writer_lease_id != facts.writer_term.lease_id
        or normalized.writer_term_issued_at != facts.issued_at
        or normalized.writer_term_expires_at != facts.expires_at
        or normalized.witness_transition_id != facts.writer_term.witness_transition_id
        or normalized.reconciliation_decision
        != RECONCILIATION_DECISION_COMPLETE_NO_RESEND
        or set(normalized.authorized_scopes) != EXTERNAL_EFFECT_EXECUTION_SCOPES
        or len(normalized.authorized_scopes) != len(EXTERNAL_EFFECT_EXECUTION_SCOPES)
        or normalized.reconciliation_evidence_sha256 == "0" * 64
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_MISMATCH")
    _fresh(
        normalized.issued_at,
        now=now,
        maximum_age_seconds=maximum_age_seconds,
        code="PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_STALE_OR_EXPIRED",
    )
    if (
        normalized.expires_at <= now
        or normalized.writer_term_expires_at <= now
        or normalized.reconciliation_completed_at > normalized.issued_at
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_EXTERNAL_EFFECT_STALE_OR_EXPIRED")
    return normalized


def _require_inputs(value: object) -> PromotionP0ContinuityPreflightInputs:
    if type(value) is not PromotionP0ContinuityPreflightInputs:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_INPUTS_INVALID")
    return value


def verify_promotion_p0_continuity_preflight(
    config: object,
    binding: object,
    inputs: object,
    *,
    now: datetime,
) -> VerifiedPromotionP0ContinuityPreflight:
    """Verify all selected P0 projections without performing any operation.

    A future promotion coordinator must invoke this only after the caller-owned
    auth/upload transaction is durably committed and after it has recorded the
    explicit relationship between the promotion operation and the external
    no-resend receipt.  This verifier cannot establish either fact itself.
    """

    maximum_age_seconds = _maximum_evidence_age(config)
    observed_now = _utc(now, code="PROMOTION_P0_CONTINUITY_PREFLIGHT_CLOCK_INVALID")
    facts = _binding_facts(binding, now=observed_now)
    supplied = _require_inputs(inputs)
    cutover_at = _require_participants(
        supplied.auth_upload_result,
        facts=facts,
        now=observed_now,
        maximum_age_seconds=maximum_age_seconds,
    )
    external = _require_external_effect_authorization(
        supplied.external_effect_authorization,
        facts=facts,
        now=observed_now,
        maximum_age_seconds=maximum_age_seconds,
    )
    result = VerifiedPromotionP0ContinuityPreflight(
        schema=PROMOTION_P0_CONTINUITY_PREFLIGHT_SCHEMA,
        operation_id=facts.operation_id,
        writer_site=facts.writer_term.holder_site,
        writer_epoch=facts.writer_term.writer_epoch,
        writer_lease_id=facts.writer_term.lease_id,
        witness_transition_id=facts.writer_term.witness_transition_id,
        auth_upload_cutover_at=cutover_at,
        external_effect_authorization_id=external.authorization_id,
        external_effect_reconciliation_evidence_sha256=(
            external.reconciliation_evidence_sha256
        ),
        external_effect_authorization_expires_at=external.expires_at,
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    _VERIFIED_STATES[result] = _VerifiedState(
        config=config,
        binding=binding,
        inputs=inputs,
        projection=result,
    )
    return result


def _same_projection(
    first: VerifiedPromotionP0ContinuityPreflight,
    second: VerifiedPromotionP0ContinuityPreflight,
) -> bool:
    """Compare public, stable provenance fields without identity equality.

    The verified class deliberately uses ``eq=False`` so it remains an
    identity-keyed capability in the weak state table.  A fresh revalidation
    must therefore compare its immutable projection explicitly.
    """

    return (
        first.schema,
        first.operation_id,
        first.writer_site,
        first.writer_epoch,
        first.writer_lease_id,
        first.witness_transition_id,
        first.auth_upload_cutover_at,
        first.external_effect_authorization_id,
        first.external_effect_reconciliation_evidence_sha256,
        first.external_effect_authorization_expires_at,
    ) == (
        second.schema,
        second.operation_id,
        second.writer_site,
        second.writer_epoch,
        second.writer_lease_id,
        second.witness_transition_id,
        second.auth_upload_cutover_at,
        second.external_effect_authorization_id,
        second.external_effect_reconciliation_evidence_sha256,
        second.external_effect_authorization_expires_at,
    )


def require_verified_promotion_p0_continuity_preflight(
    value: object,
    *,
    now: datetime,
) -> VerifiedPromotionP0ContinuityPreflight:
    """Recheck local provenance and freshness before a future coordinator uses it."""

    if (
        type(value) is not VerifiedPromotionP0ContinuityPreflight
        or value._capability is not _VERIFIED_CAPABILITY
    ):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_CAPABILITY_REQUIRED")
    state = _VERIFIED_STATES.get(value)
    if state is None or state.projection is not value:
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_CAPABILITY_REQUIRED")
    rechecked = verify_promotion_p0_continuity_preflight(
        state.config,
        state.binding,
        state.inputs,
        now=now,
    )
    if not _same_projection(rechecked, value):
        _fail("PROMOTION_P0_CONTINUITY_PREFLIGHT_REVALIDATION_MISMATCH")
    return value
