"""Pure local writer-admission boundary for the operational failover runtime.

This is deliberately **not** a Witness client, a file/DB implementation, or
an application integration.  A future root-owned runtime supplies both a
fresh Witness-grant revalidator and durable compare-and-swap persistence for
the state transitions returned here.  The boundary itself only turns typed
inputs into typed state transitions and commit/effect admission instructions.

The important split is intentional:

* a Witness (through the narrow injected protocol) revalidates a fresh term;
* root-owned durable local state remembers the highest epoch, the newest
  evidence/revalidation identifiers, its monotonic wall-clock floor, and a
  local fence generation; and
* a transaction commit or external-effect boundary must consume the returned
  transition atomically with its own durable work.

No function opens a path, checks the effective uid, contacts a peer, changes
traffic, calls a database, starts a writer, or performs an external effect.
It has no V2/V4 dependency.  Its evidence protocol is deliberately small so a
future V1 Witness grant adapter can provide it without coupling this runtime
to campaign evidence types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Protocol

from core.append_only_sync_delta_batch import LEASE_ID_RE


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SCHEMA",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT",
    "PhysicalOperationalFailoverV1WriterCurrentTermProvenanceBinder",
    "PhysicalOperationalFailoverV1WitnessTermRevalidator",
    "PhysicalOperationalFailoverV1WriterAdmission",
    "PhysicalOperationalFailoverV1WriterAdmissionBinding",
    "PhysicalOperationalFailoverV1WriterAdmissionConfig",
    "PhysicalOperationalFailoverV1WriterAdmissionError",
    "PhysicalOperationalFailoverV1WriterAdmissionState",
    "PhysicalOperationalFailoverV1WriterAdmissionStateRestorer",
    "PhysicalOperationalFailoverV1WriterAdmissionStateTransition",
    "PhysicalOperationalFailoverV1WriterAdmissionDurableBoundary",
    "PhysicalOperationalFailoverV1WriterOperation",
    "PhysicalOperationalFailoverV1WriterTermEvidence",
    "PhysicalOperationalFailoverV1WriterTermRevalidationRequest",
    "PhysicalOperationalFailoverV1WriterTermSnapshot",
    "apply_physical_operational_failover_v1_writer_admission_state_transition",
    "begin_physical_operational_failover_v1_writer_operation",
    "fence_physical_operational_failover_v1_writer_admission",
    "new_physical_operational_failover_v1_writer_admission_state",
    "revalidate_physical_operational_failover_v1_writer_admission",
    "require_physical_operational_failover_v1_writer_admission",
    "restore_physical_operational_failover_v1_writer_admission_state",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-writer-admission-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-writer-admission-state-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DEFAULT_ENABLED = False

PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT = (
    "transaction_commit"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT = (
    "external_effect"
)

_WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
_OPERATION_KINDS = frozenset(
    {
        PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
        PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
    }
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$", re.ASCII)
_RELEASE_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.ASCII)

_STATE_CAPABILITY = object()
_STATE_TRANSITION_CAPABILITY = object()
_OPERATION_CAPABILITY = object()
_ADMISSION_CAPABILITY = object()


class PhysicalOperationalFailoverV1WriterAdmissionError(ValueError):
    """A local operational writer admission cannot safely proceed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WriterAdmissionError(code)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionBinding:
    """One exact local writer identity expected by this runtime instance."""

    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionConfig:
    """Default-off policy supplied by the future root-owned runtime."""

    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DEFAULT_ENABLED
    binding: PhysicalOperationalFailoverV1WriterAdmissionBinding | None = None
    runtime_instance_id: str | None = None
    safety_margin_seconds: int = 5
    maximum_term_duration_seconds: int = 90
    maximum_evidence_age_seconds: int = 60


class PhysicalOperationalFailoverV1WriterTermEvidence(Protocol):
    """Minimal structural projection a future V1 Witness-grant adapter exposes.

    The protocol intentionally has no signature, transport, campaign, V2, or
    V4 field.  Its implementation is responsible for authenticating the
    Witness grant before returning it through the injected revalidator.
    """

    cluster_id: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    release_sha: str
    generation_id: str
    evidence_id: str
    revalidation_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterTermRevalidationRequest:
    """Narrow request passed to the injected fresh-Witness revalidator."""

    binding: PhysicalOperationalFailoverV1WriterAdmissionBinding
    runtime_instance_id: str
    revalidation_id: str
    minimum_writer_epoch: int
    previous_writer_lease_id: str | None
    previous_evidence_id: str | None
    previous_revalidation_id: str | None
    clock_floor: datetime | None


class PhysicalOperationalFailoverV1WitnessTermRevalidator(Protocol):
    """Future runtime boundary for one fresh authenticated Witness grant.

    The implementation may be a local root-agent client later.  It is not
    supplied here and this module has no transport implementation.
    """

    def revalidate_writer_term(
        self,
        *,
        request: PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
    ) -> PhysicalOperationalFailoverV1WriterTermEvidence: ...


class PhysicalOperationalFailoverV1WriterCurrentTermProvenanceBinder(Protocol):
    """Optional, in-process identity binder for a verified V1 term.

    This is deliberately separate from :class:`PhysicalOperationalFailoverV1WitnessTermRevalidator`.
    Ordinary V1 writer admission still consumes only its narrow structural
    evidence protocol.  A root-owned Gen2 bridge may explicitly supply a
    binder owned by the same verified-current-term revalidator; the binder
    receives the *exact* evidence object and the just-minted V1 transition so
    it can retain an opaque identity link without putting an attestation or a
    capability into durable V1 state.

    The call is synchronous, local, and side-effect free except for a
    process-local one-shot capability registry.  It must not perform Witness,
    peer, provider, database, filesystem, or traffic I/O; the caller is still
    before the eventual database transaction.
    """

    def bind_revalidated_current_term_provenance(
        self,
        *,
        evidence: PhysicalOperationalFailoverV1WriterTermEvidence,
        state_transition: "PhysicalOperationalFailoverV1WriterAdmissionStateTransition",
        writer_admission_config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
        observed_at: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterTermSnapshot:
    """The non-secret current term facts that durable local state remembers."""

    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    evidence_id: str
    revalidation_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionState:
    """Durable local state a root-owned CAS store must persist exactly.

    It is intentionally a process-local *attested* value object: persistence
    is outside this pure module.  A raw state decoded from durable storage is
    not accepted by writer admission.  It must first pass the explicit
    root-owned restoration protocol below, which re-mints this local
    capability and forces fresh Witness revalidation for the new runtime.
    Every returned transition names the exact prior revision, so the future
    commit/effect adapter can atomically compare-and-swap the corresponding
    durable representation with its own boundary.
    """

    schema: str
    binding: PhysicalOperationalFailoverV1WriterAdmissionBinding
    revision: int
    highest_writer_epoch: int
    active_term: PhysicalOperationalFailoverV1WriterTermSnapshot | None
    revalidated_runtime_instance_id: str | None
    clock_floor: datetime | None
    fence_generation: int
    fenced: bool
    fence_reason: str | None
    requires_fresh_witness_revalidation: bool
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


class PhysicalOperationalFailoverV1WriterAdmissionStateRestorer(Protocol):
    """Explicit root-owned durable-state restore boundary.

    The restorer is the only future adapter allowed to decode a persisted
    state representation.  It must authenticate/authorize that durable local
    record before returning this structural value.  This module then keeps
    the returned value fenced from writer use until a fresh Witness grant is
    revalidated for the current runtime instance.
    """

    def restore_writer_admission_state(
        self,
        *,
        binding: PhysicalOperationalFailoverV1WriterAdmissionBinding,
    ) -> PhysicalOperationalFailoverV1WriterAdmissionState: ...


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionStateTransition:
    """One pure state transition to persist through a root-owned CAS boundary."""

    kind: str
    prior_state: PhysicalOperationalFailoverV1WriterAdmissionState
    next_state: PhysicalOperationalFailoverV1WriterAdmissionState
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterOperation:
    """A local transaction/effect begun under one exact active term.

    This is not a writer permit.  It must be checked again at commit or right
    before external I/O through :func:`require_physical_operational_failover_v1_writer_admission`.
    """

    operation_kind: str
    runtime_instance_id: str
    opened_state_revision: int
    fence_generation: int
    evidence_id: str
    writer_epoch: int
    writer_lease_id: str
    opened_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmission:
    """A local admission instruction for one commit or external effect.

    The returned transition must be persisted atomically at the actual local
    boundary.  This object performs no commit, effect, storage write, or
    network call itself.
    """

    operation: PhysicalOperationalFailoverV1WriterOperation
    state_transition: PhysicalOperationalFailoverV1WriterAdmissionStateTransition
    term: PhysicalOperationalFailoverV1WriterTermSnapshot
    admitted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


class PhysicalOperationalFailoverV1WriterAdmissionDurableBoundary(Protocol):
    """Future root-owned local commit/effect adapter; never called here.

    Its implementation must compare-and-swap the exact prior revision in
    ``admission.state_transition`` to its next state *at* the transaction
    commit or external-effect admission boundary.  It must reject a failed CAS
    rather than use a process-local admission after a concurrent fence.
    """

    def persist_writer_admission(
        self,
        *,
        admission: PhysicalOperationalFailoverV1WriterAdmission,
    ) -> object: ...


@dataclass(frozen=True)
class _EvidenceFacts:
    cluster_id: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    release_sha: str
    generation_id: str
    evidence_id: str
    revalidation_id: str
    issued_at: datetime
    expires_at: datetime


def _identifier(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Validate writer leases with the shared canonical data-plane grammar."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _release_sha(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _RELEASE_SHA_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    try:
        if value.utcoffset() is None:
            _fail(code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _binding(value: object, *, code: str) -> PhysicalOperationalFailoverV1WriterAdmissionBinding:
    if type(value) is not PhysicalOperationalFailoverV1WriterAdmissionBinding:
        _fail(code)
    if value.local_site not in _WEBAPP_SITES:
        _fail(code)
    _identifier(value.cluster_id, code=code)
    _release_sha(value.release_sha, code=code)
    _identifier(value.generation_id, code=code)
    return value


def _config(
    value: object,
) -> tuple[
    PhysicalOperationalFailoverV1WriterAdmissionBinding,
    str,
    int,
    int,
    int,
] | None:
    if type(value) is not PhysicalOperationalFailoverV1WriterAdmissionConfig:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CONFIG_INVALID")
    if value.enabled is False:
        return None
    if value.enabled is not True:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CONFIG_INVALID")
    binding = _binding(
        value.binding,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CONFIG_INVALID",
    )
    runtime_instance_id = _identifier(
        value.runtime_instance_id,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CONFIG_INVALID",
    )
    margin = value.safety_margin_seconds
    maximum_duration = value.maximum_term_duration_seconds
    maximum_age = value.maximum_evidence_age_seconds
    if (
        type(margin) is not int
        or type(maximum_duration) is not int
        or type(maximum_age) is not int
        or not 1 <= margin <= 60
        or not 2 <= maximum_duration <= 300
        or margin >= maximum_duration
        or not 1 <= maximum_age <= 300
    ):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CONFIG_INVALID")
    return binding, runtime_instance_id, margin, maximum_duration, maximum_age


def _term_snapshot(
    value: object,
    *,
    code: str,
) -> PhysicalOperationalFailoverV1WriterTermSnapshot:
    if type(value) is not PhysicalOperationalFailoverV1WriterTermSnapshot:
        _fail(code)
    if value.holder_site not in _WEBAPP_SITES:
        _fail(code)
    if type(value.writer_epoch) is not int or value.writer_epoch < 1:
        _fail(code)
    _writer_lease_id(value.writer_lease_id, code=code)
    _identifier(value.evidence_id, code=code)
    _identifier(value.revalidation_id, code=code)
    issued_at = _utc(value.issued_at, code=code)
    expires_at = _utc(value.expires_at, code=code)
    if expires_at <= issued_at:
        _fail(code)
    return value


def _state(
    value: object,
    *,
    binding: PhysicalOperationalFailoverV1WriterAdmissionBinding,
    allow_unattested: bool = False,
) -> PhysicalOperationalFailoverV1WriterAdmissionState:
    if type(value) is not PhysicalOperationalFailoverV1WriterAdmissionState:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
    if (
        value.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA
        or value.binding != binding
        or type(value.revision) is not int
        or value.revision < 0
        or type(value.highest_writer_epoch) is not int
        or value.highest_writer_epoch < 0
        or type(value.fence_generation) is not int
        or value.fence_generation < 0
        or type(value.fenced) is not bool
        or type(value.requires_fresh_witness_revalidation) is not bool
    ):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
    if not allow_unattested and value._capability is not _STATE_CAPABILITY:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_UNATTESTED")
    if value.clock_floor is not None:
        _utc(
            value.clock_floor,
            code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID",
        )
    if value.active_term is None:
        if value.highest_writer_epoch != 0 or value.revalidated_runtime_instance_id is not None:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
        if value.fenced is not True:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
    else:
        term = _term_snapshot(
            value.active_term,
            code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID",
        )
        if term.holder_site != binding.local_site or term.writer_epoch != value.highest_writer_epoch:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
        if value.revalidated_runtime_instance_id is not None:
            _identifier(
                value.revalidated_runtime_instance_id,
                code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID",
            )
        elif value.requires_fresh_witness_revalidation is not True:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
    if value.fenced:
        _identifier(
            value.fence_reason,
            code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID",
        )
    elif value.fence_reason is not None:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_INVALID")
    return value


def _attest_state(
    value: PhysicalOperationalFailoverV1WriterAdmissionState,
) -> PhysicalOperationalFailoverV1WriterAdmissionState:
    """Mint the process-local state capability after a trusted boundary."""

    object.__setattr__(value, "_capability", _STATE_CAPABILITY)
    return value


def _evidence(value: object) -> _EvidenceFacts:
    try:
        facts = _EvidenceFacts(
            cluster_id=getattr(value, "cluster_id"),
            holder_site=getattr(value, "holder_site"),
            writer_epoch=getattr(value, "writer_epoch"),
            writer_lease_id=getattr(value, "writer_lease_id"),
            release_sha=getattr(value, "release_sha"),
            generation_id=getattr(value, "generation_id"),
            evidence_id=getattr(value, "evidence_id"),
            revalidation_id=getattr(value, "revalidation_id"),
            issued_at=getattr(value, "issued_at"),
            expires_at=getattr(value, "expires_at"),
        )
    except (AttributeError, TypeError):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    if facts.holder_site not in _WEBAPP_SITES or type(facts.writer_epoch) is not int or facts.writer_epoch < 1:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    _identifier(facts.cluster_id, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    _writer_lease_id(
        facts.writer_lease_id,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID",
    )
    _identifier(facts.generation_id, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    _identifier(facts.evidence_id, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    _identifier(facts.revalidation_id, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    _release_sha(facts.release_sha, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    issued_at = _utc(
        facts.issued_at,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID",
    )
    expires_at = _utc(
        facts.expires_at,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID",
    )
    if expires_at <= issued_at:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_INVALID")
    return _EvidenceFacts(
        cluster_id=facts.cluster_id,
        holder_site=facts.holder_site,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        release_sha=facts.release_sha,
        generation_id=facts.generation_id,
        evidence_id=facts.evidence_id,
        revalidation_id=facts.revalidation_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _require_clock_at_or_after(
    *,
    now: datetime,
    floor: datetime | None,
) -> None:
    if floor is not None and now < floor:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_REGRESSION")


def _require_active_term(
    *,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    runtime_instance_id: str,
    safety_margin_seconds: int,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterTermSnapshot:
    _require_clock_at_or_after(now=now, floor=state.clock_floor)
    if state.fenced or state.active_term is None:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_FENCED")
    if state.requires_fresh_witness_revalidation:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_FRESH_REVALIDATION_REQUIRED")
    if state.revalidated_runtime_instance_id != runtime_instance_id:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_FRESH_REVALIDATION_REQUIRED")
    term = state.active_term
    if term.expires_at <= now + timedelta(seconds=safety_margin_seconds):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EXPIRED")
    return term


def _transition(
    *,
    kind: str,
    prior_state: PhysicalOperationalFailoverV1WriterAdmissionState,
    next_state: PhysicalOperationalFailoverV1WriterAdmissionState,
) -> PhysicalOperationalFailoverV1WriterAdmissionStateTransition:
    transition = PhysicalOperationalFailoverV1WriterAdmissionStateTransition(
        kind=kind,
        prior_state=prior_state,
        next_state=next_state,
    )
    object.__setattr__(transition, "_capability", _STATE_TRANSITION_CAPABILITY)
    return transition


def new_physical_operational_failover_v1_writer_admission_state(
    *,
    binding: PhysicalOperationalFailoverV1WriterAdmissionBinding,
) -> PhysicalOperationalFailoverV1WriterAdmissionState:
    """Create the fenced startup state; a fresh Witness revalidation is required."""

    binding = _binding(
        binding,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_BINDING_INVALID",
    )
    return _attest_state(PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        binding=binding,
        revision=0,
        highest_writer_epoch=0,
        active_term=None,
        revalidated_runtime_instance_id=None,
        clock_floor=None,
        fence_generation=0,
        fenced=True,
        fence_reason="startup_requires_fresh_witness",
        requires_fresh_witness_revalidation=True,
    ))


def apply_physical_operational_failover_v1_writer_admission_state_transition(
    *,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    transition: PhysicalOperationalFailoverV1WriterAdmissionStateTransition,
) -> PhysicalOperationalFailoverV1WriterAdmissionState:
    """Purely validate and apply an already-CAS-persistable state transition.

    A future root-owned persistence adapter must compare-and-swap the same
    ``prior_state.revision`` before it adopts the returned ``next_state``.
    """

    if (
        type(transition) is not PhysicalOperationalFailoverV1WriterAdmissionStateTransition
        or transition._capability is not _STATE_TRANSITION_CAPABILITY
        or transition.kind
        not in {"witness_revalidation", "local_fence", "writer_admission"}
    ):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TRANSITION_INVALID")
    prior = transition.prior_state
    if type(prior) is not PhysicalOperationalFailoverV1WriterAdmissionState or state != prior:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TRANSITION_STALE")
    _state(state, binding=state.binding)
    next_state = _state(transition.next_state, binding=state.binding)
    if next_state.revision != state.revision + 1:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TRANSITION_INVALID")
    return next_state


def restore_physical_operational_failover_v1_writer_admission_state(
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
    state_restorer: PhysicalOperationalFailoverV1WriterAdmissionStateRestorer,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterAdmissionState | None:
    """Restore root-owned durable state into a new runtime safely.

    A decoded dataclass is deliberately not an admission state by itself.  A
    future root-owned restorer must authenticate the local durable record and
    provide it through the narrow injected protocol.  Even then, restoration
    invalidates old operation tickets and requires a fresh Witness
    revalidation before this runtime can begin a writer operation.  The
    returned state must replace the raw durable record before use.
    """

    parsed_config = _config(config)
    if parsed_config is None:
        return None
    binding, _, _, _, _ = parsed_config
    if not callable(getattr(state_restorer, "restore_writer_admission_state", None)):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_RESTORER_INVALID")
    try:
        raw_state = state_restorer.restore_writer_admission_state(binding=binding)
    except PhysicalOperationalFailoverV1WriterAdmissionError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionError(
            "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_RESTORE_FAILED"
        ) from exc
    restored = _state(raw_state, binding=binding, allow_unattested=True)
    observed_now = _utc(now, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_INVALID")
    floor = observed_now
    if restored.clock_floor is not None and restored.clock_floor > floor:
        floor = restored.clock_floor
    return _attest_state(PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        binding=binding,
        revision=restored.revision + 1,
        highest_writer_epoch=restored.highest_writer_epoch,
        active_term=restored.active_term,
        revalidated_runtime_instance_id=None,
        clock_floor=floor,
        fence_generation=restored.fence_generation + 1,
        fenced=restored.fenced,
        fence_reason=restored.fence_reason if restored.fenced else None,
        requires_fresh_witness_revalidation=True,
    ))


def revalidate_physical_operational_failover_v1_writer_admission(
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    evidence_revalidator: PhysicalOperationalFailoverV1WitnessTermRevalidator,
    current_term_provenance_binder: (
        PhysicalOperationalFailoverV1WriterCurrentTermProvenanceBinder | None
    ) = None,
    revalidation_id: str,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterAdmissionStateTransition | None:
    """Obtain one fresh typed Witness term through the injected boundary.

    The caller persists the returned transition through a root-owned durable
    compare-and-swap.  Reusing a proof, a revalidation id, a term after a
    local fence, or an older clock value fails closed.
    """

    parsed_config = _config(config)
    if parsed_config is None:
        return None
    binding, runtime_instance_id, margin, maximum_duration, maximum_age = parsed_config
    current = _state(state, binding=binding)
    observed_now = _utc(now, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_INVALID")
    _require_clock_at_or_after(now=observed_now, floor=current.clock_floor)
    revalidation_id = _identifier(
        revalidation_id,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_REVALIDATION_ID_INVALID",
    )
    active = current.active_term
    if active is not None and revalidation_id == active.revalidation_id:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_REVALIDATION_REPLAYED")
    if not callable(getattr(evidence_revalidator, "revalidate_writer_term", None)):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_REVALIDATOR_INVALID")
    request = PhysicalOperationalFailoverV1WriterTermRevalidationRequest(
        binding=binding,
        runtime_instance_id=runtime_instance_id,
        revalidation_id=revalidation_id,
        minimum_writer_epoch=current.highest_writer_epoch,
        previous_writer_lease_id=None if active is None else active.writer_lease_id,
        previous_evidence_id=None if active is None else active.evidence_id,
        previous_revalidation_id=None if active is None else active.revalidation_id,
        clock_floor=current.clock_floor,
    )
    try:
        raw_evidence = evidence_revalidator.revalidate_writer_term(request=request)
    except PhysicalOperationalFailoverV1WriterAdmissionError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionError(
            "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_WITNESS_REVALIDATION_FAILED"
        ) from exc
    evidence = _evidence(raw_evidence)
    if evidence.cluster_id != binding.cluster_id:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_CLUSTER_MISMATCH")
    if evidence.holder_site != binding.local_site:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_SITE_MISMATCH")
    if evidence.release_sha != binding.release_sha:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_RELEASE_MISMATCH")
    if evidence.generation_id != binding.generation_id:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_GENERATION_MISMATCH")
    if evidence.revalidation_id != revalidation_id:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_REVALIDATION_ID_MISMATCH")
    if evidence.issued_at > observed_now:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_NOT_ACTIVE")
    if evidence.expires_at - evidence.issued_at > timedelta(seconds=maximum_duration):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_DURATION_INVALID")
    if observed_now - evidence.issued_at > timedelta(seconds=maximum_age):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_STALE")
    if evidence.expires_at <= observed_now + timedelta(seconds=margin):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EXPIRED")
    if active is None:
        if evidence.writer_epoch <= current.highest_writer_epoch:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EPOCH_REPLAYED")
    else:
        if evidence.evidence_id == active.evidence_id:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_REPLAYED")
        if evidence.issued_at <= active.issued_at:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_CLOCK_REGRESSION")
        if evidence.writer_epoch < current.highest_writer_epoch:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EPOCH_REPLAYED")
        if evidence.writer_epoch == current.highest_writer_epoch:
            if current.fenced:
                _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_FENCED_TERM_REACTIVATION")
            if evidence.writer_lease_id != active.writer_lease_id:
                _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_LEASE_MISMATCH")
            if evidence.issued_at <= active.issued_at:
                _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_EVIDENCE_REPLAYED")
        elif evidence.writer_lease_id == active.writer_lease_id:
            _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_TERM_LEASE_NOT_ROTATED")
    term = PhysicalOperationalFailoverV1WriterTermSnapshot(
        holder_site=evidence.holder_site,
        writer_epoch=evidence.writer_epoch,
        writer_lease_id=evidence.writer_lease_id,
        evidence_id=evidence.evidence_id,
        revalidation_id=evidence.revalidation_id,
        issued_at=evidence.issued_at,
        expires_at=evidence.expires_at,
    )
    next_state = _attest_state(PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        binding=binding,
        revision=current.revision + 1,
        highest_writer_epoch=evidence.writer_epoch,
        active_term=term,
        revalidated_runtime_instance_id=runtime_instance_id,
        clock_floor=observed_now,
        fence_generation=current.fence_generation,
        fenced=False,
        fence_reason=None,
        requires_fresh_witness_revalidation=False,
    ))
    transition = _transition(
        kind="witness_revalidation",
        prior_state=current,
        next_state=next_state,
    )
    if current_term_provenance_binder is not None:
        callback = getattr(
            current_term_provenance_binder,
            "bind_revalidated_current_term_provenance",
            None,
        )
        if not callable(callback):
            _fail(
                "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CURRENT_TERM_PROVENANCE_BINDER_INVALID"
            )
        try:
            result = callback(
                evidence=raw_evidence,
                state_transition=transition,
                writer_admission_config=config,
                observed_at=observed_now,
            )
        except PhysicalOperationalFailoverV1WriterAdmissionError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WriterAdmissionError(
                "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CURRENT_TERM_PROVENANCE_BIND_FAILED"
            ) from exc
        if result is not None:
            _fail(
                "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CURRENT_TERM_PROVENANCE_BIND_INVALID"
            )
    return transition


def fence_physical_operational_failover_v1_writer_admission(
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    fence_reason: str,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterAdmissionStateTransition | None:
    """Return a durable local fence transition without taking any live action.

    A clock regression must never prevent a local fence.  The next state's
    floor therefore remains the maximum safe observed value, while every
    subsequent writer admission fails until a newer Witness epoch is freshly
    revalidated.
    """

    parsed_config = _config(config)
    if parsed_config is None:
        return None
    binding, _, _, _, _ = parsed_config
    current = _state(state, binding=binding)
    observed_now = _utc(now, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_INVALID")
    reason = _identifier(
        fence_reason,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_FENCE_REASON_INVALID",
    )
    floor = observed_now
    if current.clock_floor is not None and current.clock_floor > floor:
        floor = current.clock_floor
    next_state = _attest_state(PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        binding=binding,
        revision=current.revision + 1,
        highest_writer_epoch=current.highest_writer_epoch,
        active_term=current.active_term,
        revalidated_runtime_instance_id=current.revalidated_runtime_instance_id,
        clock_floor=floor,
        fence_generation=current.fence_generation + 1,
        fenced=True,
        fence_reason=reason,
        requires_fresh_witness_revalidation=True,
    ))
    return _transition(kind="local_fence", prior_state=current, next_state=next_state)


def begin_physical_operational_failover_v1_writer_operation(
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    operation_kind: str,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterOperation | None:
    """Capture a transaction/effect start under the current local term.

    It has no authority at commit time; a fence or term change invalidates it.
    """

    parsed_config = _config(config)
    if parsed_config is None:
        return None
    binding, runtime_instance_id, margin, _, _ = parsed_config
    current = _state(state, binding=binding)
    observed_now = _utc(now, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_INVALID")
    if operation_kind not in _OPERATION_KINDS:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_KIND_INVALID")
    term = _require_active_term(
        state=current,
        runtime_instance_id=runtime_instance_id,
        safety_margin_seconds=margin,
        now=observed_now,
    )
    operation = PhysicalOperationalFailoverV1WriterOperation(
        operation_kind=operation_kind,
        runtime_instance_id=runtime_instance_id,
        opened_state_revision=current.revision,
        fence_generation=current.fence_generation,
        evidence_id=term.evidence_id,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        opened_at=observed_now,
    )
    object.__setattr__(operation, "_capability", _OPERATION_CAPABILITY)
    return operation


def require_physical_operational_failover_v1_writer_admission(
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionConfig,
    state: PhysicalOperationalFailoverV1WriterAdmissionState,
    operation: PhysicalOperationalFailoverV1WriterOperation,
    now: datetime,
) -> PhysicalOperationalFailoverV1WriterAdmission | None:
    """Recheck a started operation at the actual commit/effect boundary.

    The next state advances the durable clock floor.  A root-owned runtime
    must compare-and-swap it with the actual transaction/effect admission;
    merely retaining this process-local result is not sufficient.
    """

    parsed_config = _config(config)
    if parsed_config is None:
        return None
    binding, runtime_instance_id, margin, _, _ = parsed_config
    current = _state(state, binding=binding)
    observed_now = _utc(now, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_INVALID")
    if (
        type(operation) is not PhysicalOperationalFailoverV1WriterOperation
        or operation._capability is not _OPERATION_CAPABILITY
        or operation.operation_kind not in _OPERATION_KINDS
        or operation.runtime_instance_id != runtime_instance_id
        or type(operation.opened_state_revision) is not int
        or operation.opened_state_revision < 0
        or type(operation.fence_generation) is not int
        or operation.fence_generation < 0
        or type(operation.writer_epoch) is not int
        or operation.writer_epoch < 1
    ):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_INVALID")
    _identifier(operation.evidence_id, code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_INVALID")
    _writer_lease_id(
        operation.writer_lease_id,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_INVALID",
    )
    opened_at = _utc(
        operation.opened_at,
        code="PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_INVALID",
    )
    if observed_now < opened_at:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_CLOCK_REGRESSION")
    if current.revision < operation.opened_state_revision:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_ROLLBACK")
    term = _require_active_term(
        state=current,
        runtime_instance_id=runtime_instance_id,
        safety_margin_seconds=margin,
        now=observed_now,
    )
    if current.fence_generation != operation.fence_generation:
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_FENCED")
    if (
        term.evidence_id != operation.evidence_id
        or term.writer_epoch != operation.writer_epoch
        or term.writer_lease_id != operation.writer_lease_id
    ):
        _fail("PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_OPERATION_TERM_CHANGED")
    next_state = _attest_state(PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        binding=binding,
        revision=current.revision + 1,
        highest_writer_epoch=current.highest_writer_epoch,
        active_term=term,
        revalidated_runtime_instance_id=runtime_instance_id,
        clock_floor=observed_now,
        fence_generation=current.fence_generation,
        fenced=False,
        fence_reason=None,
        requires_fresh_witness_revalidation=False,
    ))
    transition = _transition(
        kind="writer_admission",
        prior_state=current,
        next_state=next_state,
    )
    admission = PhysicalOperationalFailoverV1WriterAdmission(
        operation=operation,
        state_transition=transition,
        term=term,
        admitted_at=observed_now,
    )
    object.__setattr__(admission, "_capability", _ADMISSION_CAPABILITY)
    return admission
