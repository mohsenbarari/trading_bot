"""DB-only PostgreSQL transaction boundary for one V1 writer-admission commit.

The caller owns the ``AsyncSession`` transaction.  This adapter never opens a
connection, begins, commits, rolls back, or closes it.  It only takes a
PostgreSQL transaction advisory lock, locks one exact local head, appends one
immutable commit receipt, and advances that head through an exact CAS update.

It is intentionally not a Witness client, writer/traffic controller, Object
Storage authority, file-CAS authority, or FI-to-IR transport.  A caller must
roll its transaction back if this boundary raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import re
from threading import RLock
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.append_only_sync_delta_batch import LEASE_ID_RE
from core import physical_operational_failover_v1_writer_admission as admission
from core.physical_operational_failover_v1_writer_admission_postgres_contract import (
    OperationalWriterAdmissionPostgresContractError,
    operational_writer_admission_postgres_commit_sha256_v1,
    operational_writer_admission_postgres_receipt_sha256_v1,
    operational_writer_admission_postgres_state_sha256_v1,
)
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONTRACT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED",
    "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt",
    "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection",
    "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter",
    "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig",
    "PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError",
    "physical_operational_failover_v1_writer_admission_head_advisory_lock_key",
    "require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONTRACT = (
    "gold-trade-physical-operational-failover-v1-writer-admission-sqlalchemy-transaction-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED = False

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{2,127}$", re.ASCII)
_COMMIT_RECEIPT_CAPABILITY = object()


class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(RuntimeError):
    """The caller-owned local DB transaction must fail closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig:
    """Default-off, pinned local adapter policy; it contains no engine/DSN."""

    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED
    writer_admission_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig | None = None
    control_role_label: str | None = None
    control_policy_sha256: str | None = None


@dataclass(frozen=True, eq=False, init=False)
class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
    """Opaque, process-local provenance for one append-only V1 commit row.

    The public fields remain a non-secret diagnostic projection for existing
    callers, but they are *not* independently authorizing.  A future bridge
    must pass this exact in-process result through
    :func:`require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt`
    with the same enabled policy.  That verifier checks the private registry
    and consumes this capability exactly once.
    """

    commit_id: UUID
    commit_sha256: str
    receipt_sha256: str
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    writer_epoch: int
    writer_lease_id: str
    evidence_id: str
    revalidation_id: str
    admitted_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        commit_id: UUID,
        commit_sha256: str,
        receipt_sha256: str,
        cluster_id: str,
        local_site: str,
        release_sha: str,
        generation_id: str,
        prior_revision: int,
        next_revision: int,
        fence_generation: int,
        writer_epoch: int,
        writer_lease_id: str,
        evidence_id: str,
        revalidation_id: str,
        admitted_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _COMMIT_RECEIPT_CAPABILITY:
            raise TypeError(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "commit_id", commit_id)
        object.__setattr__(self, "commit_sha256", commit_sha256)
        object.__setattr__(self, "receipt_sha256", receipt_sha256)
        object.__setattr__(self, "cluster_id", cluster_id)
        object.__setattr__(self, "local_site", local_site)
        object.__setattr__(self, "release_sha", release_sha)
        object.__setattr__(self, "generation_id", generation_id)
        object.__setattr__(self, "prior_revision", prior_revision)
        object.__setattr__(self, "next_revision", next_revision)
        object.__setattr__(self, "fence_generation", fence_generation)
        object.__setattr__(self, "writer_epoch", writer_epoch)
        object.__setattr__(self, "writer_lease_id", writer_lease_id)
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "revalidation_id", revalidation_id)
        object.__setattr__(self, "admitted_at", admitted_at)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection:
    """Exact non-authorizing parent facts released after one capability use.

    This intentionally contains no adapter/session/engine capability.  A
    later V1-to-V2 bridge may place these immutable public identifiers in its
    own signed receipt, but may not treat the projection as another writer
    admission or as a reusable local transaction authority.
    """

    commit_id: UUID
    commit_sha256: str
    receipt_sha256: str
    cluster_id: str
    local_site: str
    release_sha: str
    generation_id: str
    prior_revision: int
    next_revision: int
    fence_generation: int
    writer_epoch: int
    writer_lease_id: str
    evidence_id: str
    revalidation_id: str
    admitted_at: datetime


@dataclass(frozen=True)
class _Facts:
    writer_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
    runtime_instance_id: str
    safety_margin_seconds: int
    control_role_label: str
    control_policy_sha256: str


@dataclass
class _CommitReceiptState:
    """Private provenance retained for one freshly appended local parent row."""

    facts: _Facts
    projection: PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection
    consumed: bool = False


_COMMIT_RECEIPT_STATES: WeakKeyDictionary[
    PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt,
    _CommitReceiptState,
] = WeakKeyDictionary()
_COMMIT_RECEIPT_STATE_LOCK = RLock()


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        _fail(code)
    try:
        if value.utcoffset() is None:
            _fail(code)
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _digest(value: object, *, code: str, allow_zero: bool = False) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        _fail(code)
    if not allow_zero and value == "0" * 64:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Use the one canonical V1/V2 writer-lease grammar at SQL egress."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _facts(
    config: PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig,
) -> _Facts | None:
    if type(config) is not PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    if config.enabled is False:
        return None
    if (
        config.enabled is not True
        or type(config.writer_admission_config)
        is not admission.PhysicalOperationalFailoverV1WriterAdmissionConfig
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    try:
        parsed = admission._config(config.writer_admission_config)
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONFIG_INVALID"
        ) from exc
    if parsed is None:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_DISABLED")
    binding, runtime_instance_id, margin, _duration, _age = parsed
    if (
        type(config.control_role_label) is not str
        or _ROLE_RE.fullmatch(config.control_role_label) is None
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    return _Facts(
        writer_config=config.writer_admission_config,
        binding=binding,
        runtime_instance_id=runtime_instance_id,
        safety_margin_seconds=margin,
        control_role_label=config.control_role_label,
        control_policy_sha256=_digest(
            config.control_policy_sha256,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONFIG_INVALID",
        ),
    )


def _identity(facts: _Facts) -> dict[str, object]:
    return {
        "cluster_id": facts.binding.cluster_id,
        "local_site": facts.binding.local_site,
        "release_sha": facts.binding.release_sha,
        "generation_id": facts.binding.generation_id,
    }


def _commit_receipt_projection(
    value: object,
    *,
    code: str,
) -> PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection:
    """Read a receipt defensively before comparing it with private provenance.

    The adapter itself creates the initial receipt, but a frozen dataclass is
    not a security boundary: Python code could still use ``object.__setattr__``
    or ``object.__new__``.  The capability registry therefore stores a second,
    normalized projection and every consuming bridge compares against it.
    """

    if type(value) is not PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
        _fail(code)
    try:
        commit_id = value.commit_id
        commit_sha256 = value.commit_sha256
        receipt_sha256 = value.receipt_sha256
        cluster_id = value.cluster_id
        local_site = value.local_site
        release_sha = value.release_sha
        generation_id = value.generation_id
        prior_revision = value.prior_revision
        next_revision = value.next_revision
        fence_generation = value.fence_generation
        writer_epoch = value.writer_epoch
        writer_lease_id = value.writer_lease_id
        evidence_id = value.evidence_id
        revalidation_id = value.revalidation_id
        admitted_at = value.admitted_at
    except AttributeError:
        _fail(code)
    if (
        type(commit_id) is not UUID
        or type(cluster_id) is not str
        or type(local_site) is not str
        or type(release_sha) is not str
        or type(generation_id) is not str
        or type(writer_lease_id) is not str
        or type(evidence_id) is not str
        or type(revalidation_id) is not str
        or not cluster_id
        or not local_site
        or not release_sha
        or not generation_id
        or not writer_lease_id
        or not evidence_id
        or not revalidation_id
        or type(prior_revision) is not int
        or prior_revision < 0
        or type(next_revision) is not int
        or next_revision != prior_revision + 1
        or type(fence_generation) is not int
        or fence_generation < 0
        or type(writer_epoch) is not int
        or writer_epoch < 1
    ):
        _fail(code)
    writer_lease_id = _writer_lease_id(writer_lease_id, code=code)
    return PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection(
        commit_id=commit_id,
        commit_sha256=_digest(commit_sha256, code=code),
        receipt_sha256=_digest(receipt_sha256, code=code),
        cluster_id=cluster_id,
        local_site=local_site,
        release_sha=release_sha,
        generation_id=generation_id,
        prior_revision=prior_revision,
        next_revision=next_revision,
        fence_generation=fence_generation,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        evidence_id=evidence_id,
        revalidation_id=revalidation_id,
        admitted_at=_utc(admitted_at, code=code),
    )


def _mint_commit_receipt(
    *,
    facts: _Facts,
    commit_id: UUID,
    commit_sha256: str,
    receipt_sha256: str,
    cluster_id: str,
    local_site: str,
    release_sha: str,
    generation_id: str,
    prior_revision: int,
    next_revision: int,
    fence_generation: int,
    writer_epoch: int,
    writer_lease_id: str,
    evidence_id: str,
    revalidation_id: str,
    admitted_at: datetime,
) -> PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
    """Mint and register the only locally verifiable parent receipt object."""

    result = PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt(
        commit_id=commit_id,
        commit_sha256=commit_sha256,
        receipt_sha256=receipt_sha256,
        cluster_id=cluster_id,
        local_site=local_site,
        release_sha=release_sha,
        generation_id=generation_id,
        prior_revision=prior_revision,
        next_revision=next_revision,
        fence_generation=fence_generation,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        evidence_id=evidence_id,
        revalidation_id=revalidation_id,
        admitted_at=admitted_at,
        capability=_COMMIT_RECEIPT_CAPABILITY,
    )
    projection = _commit_receipt_projection(
        result,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_RECEIPT_INVALID",
    )
    with _COMMIT_RECEIPT_STATE_LOCK:
        _COMMIT_RECEIPT_STATES[result] = _CommitReceiptState(
            facts=facts,
            projection=projection,
        )
    return result


def require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
    value: object,
    *,
    config: PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig,
) -> PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceiptProjection:
    """Consume one exact parent receipt into a public, non-authorizing projection.

    This is the sole handoff from the V1 SQL boundary to a future bridge.  It
    neither reads the database nor owns a transaction.  Instead it verifies
    that ``value`` was minted by this in-process adapter under the same enabled
    V1 policy, detects object tampering, and marks the capability consumed only
    after all checks pass.  A projection returned here is evidence for a
    bridge's own signed/durable protocol, not another reusable authority.
    """

    requested_facts = _facts(config)
    if requested_facts is None:
        _fail(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_CONFIG_MISMATCH"
        )
    if (
        type(value) is not PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt
        or value._capability is not _COMMIT_RECEIPT_CAPABILITY
    ):
        _fail(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_CAPABILITY_REQUIRED"
        )
    with _COMMIT_RECEIPT_STATE_LOCK:
        state = _COMMIT_RECEIPT_STATES.get(value)
        if state is None:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_CAPABILITY_REQUIRED"
            )
        if requested_facts != state.facts:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_CONFIG_MISMATCH"
            )
        current_projection = _commit_receipt_projection(
            value,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_TAMPERED",
        )
        if current_projection != state.projection:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_TAMPERED"
            )
        if state.consumed:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_COMMIT_RECEIPT_REPLAYED"
            )
        state.consumed = True
        return state.projection


def physical_operational_failover_v1_writer_admission_head_advisory_lock_key(
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
) -> int:
    """Stable signed 64-bit key for the one local binding head."""

    try:
        checked = admission._binding(
            binding,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_BINDING_INVALID",
        )
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_BINDING_INVALID"
        ) from exc
    if checked is not binding:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_BINDING_INVALID")
    payload = (
        PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_CONTRACT
        + "\x00"
        + binding.cluster_id
        + "\x00"
        + binding.local_site
        + "\x00"
        + binding.release_sha
        + "\x00"
        + binding.generation_id
    ).encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], byteorder="big", signed=True)


def _state_values(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
    code: str,
) -> dict[str, object]:
    """Exact active-state projection stored by the reviewed local schema."""

    if type(state) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        _fail(code)
    term = state.active_term
    if (
        state.binding != facts.binding
        or type(term) is not admission.PhysicalOperationalFailoverV1WriterTermSnapshot
        or state.schema != admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA
        or type(state.revision) is not int
        or state.revision < 1
        or type(state.highest_writer_epoch) is not int
        or state.highest_writer_epoch < 1
        or state.highest_writer_epoch != term.writer_epoch
        or type(state.fence_generation) is not int
        or state.fence_generation < 0
        or state.fenced is not False
        or state.fence_reason is not None
        or state.requires_fresh_witness_revalidation is not False
        or state.revalidated_runtime_instance_id != facts.runtime_instance_id
    ):
        _fail(code)
    return {
        **_identity(facts),
        "revision": state.revision,
        "prior_revision": state.revision - 1,
        "highest_writer_epoch": state.highest_writer_epoch,
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "evidence_id": term.evidence_id,
        "revalidation_id": term.revalidation_id,
        "term_issued_at": _utc(term.issued_at, code=code),
        "term_expires_at": _utc(term.expires_at, code=code),
        "revalidated_runtime_instance_id": state.revalidated_runtime_instance_id,
        "clock_floor": _utc(state.clock_floor, code=code),
        "fence_generation": state.fence_generation,
        "fenced": state.fenced,
        "fence_reason": state.fence_reason,
        "requires_fresh_witness_revalidation": state.requires_fresh_witness_revalidation,
    }


def _state_sha256(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
    code: str,
) -> str:
    """Use the shared reviewed state-digest contract, never a local format."""

    term = state.active_term
    active_term = None
    if term is not None:
        active_term = {
            "holder_site": term.holder_site,
            "writer_epoch": term.writer_epoch,
            "writer_lease_id": term.writer_lease_id,
            "evidence_id": term.evidence_id,
            "revalidation_id": term.revalidation_id,
            "issued_at": term.issued_at,
            "expires_at": term.expires_at,
        }
    try:
        value = operational_writer_admission_postgres_state_sha256_v1(
            binding=_identity(facts),
            state={
                "revision": state.revision,
                "highest_writer_epoch": state.highest_writer_epoch,
                "active_term": active_term,
                "revalidated_runtime_instance_id": state.revalidated_runtime_instance_id,
                "clock_floor": state.clock_floor,
                "fence_generation": state.fence_generation,
                "fenced": state.fenced,
                "fence_reason": state.fence_reason,
                "requires_fresh_witness_revalidation": state.requires_fresh_witness_revalidation,
            },
        )
    except OperationalWriterAdmissionPostgresContractError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code) from exc
    return _digest(value, code=code)


def _same_value(actual: object, expected: object, *, code: str) -> bool:
    if expected is None:
        return actual is None
    if type(expected) is datetime:
        try:
            return _utc(actual, code=code) == expected
        except PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError:
            return False
    if type(expected) is bool:
        return type(actual) is bool and actual is expected
    if type(expected) is int:
        return type(actual) is int and actual == expected
    if type(expected) is str:
        return type(actual) is str and actual == expected
    _fail(code)


def _require_head_matches(
    head: OperationalWriterAdmissionHead,
    *,
    prior_values: dict[str, object],
    prior_state_sha256: str,
    facts: _Facts,
) -> None:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_STALE_OR_MISMATCH"
    for field, expected in prior_values.items():
        if not _same_value(getattr(head, field, None), expected, code=code):
            _fail(code)
    state_sha256 = _digest(head.state_sha256, code=code)
    _digest(head.receipt_sha256, code=code)
    _digest(head.current_commit_sha256, code=code)
    if (
        not isinstance(head.id, UUID)
        or state_sha256 != prior_state_sha256
        or not isinstance(head.current_commit_id, UUID)
        or head.control_boundary != OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA
        or head.control_role_label != facts.control_role_label
        or head.control_policy_sha256 != facts.control_policy_sha256
    ):
        _fail(code)
    committed_at = _utc(head.committed_at, code=code)
    term_expires_at = _utc(prior_values["term_expires_at"], code=code)
    if committed_at > term_expires_at:
        _fail(code)


def _validated_admission(
    writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
    *,
    facts: _Facts,
) -> tuple[
    admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    datetime,
]:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID"
    if (
        type(writer_admission) is not admission.PhysicalOperationalFailoverV1WriterAdmission
        or writer_admission._capability is not admission._ADMISSION_CAPABILITY
    ):
        _fail(code)
    transition = writer_admission.state_transition
    if (
        type(transition) is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition
        or transition._capability is not admission._STATE_TRANSITION_CAPABILITY
        or transition.kind != "writer_admission"
    ):
        _fail(code)
    try:
        candidate = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=transition.prior_state,
            transition=transition,
        )
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code) from exc
    prior = transition.prior_state
    if candidate is not transition.next_state:
        _fail(code)
    prior_values = _state_values(prior, facts=facts, code=code)
    candidate_values = _state_values(candidate, facts=facts, code=code)
    if (
        candidate.revision != prior.revision + 1
        or candidate.fence_generation != prior.fence_generation
        or candidate.active_term != prior.active_term
        or candidate.active_term != writer_admission.term
        or candidate.highest_writer_epoch != prior.highest_writer_epoch
        or candidate_values["clock_floor"] != writer_admission.admitted_at
    ):
        _fail(code)
    operation = writer_admission.operation
    term = writer_admission.term
    if (
        type(operation) is not admission.PhysicalOperationalFailoverV1WriterOperation
        or operation._capability is not admission._OPERATION_CAPABILITY
        or operation.operation_kind != admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT
        or operation.runtime_instance_id != facts.runtime_instance_id
        or type(operation.opened_state_revision) is not int
        or operation.opened_state_revision < 0
        or operation.opened_state_revision > prior.revision
        or type(operation.fence_generation) is not int
        or operation.fence_generation != prior.fence_generation
        or operation.evidence_id != term.evidence_id
        or operation.writer_epoch != term.writer_epoch
        or operation.writer_lease_id != term.writer_lease_id
    ):
        _fail(code)
    admitted_at = _utc(writer_admission.admitted_at, code=code)
    if (
        _utc(operation.opened_at, code=code) > admitted_at
        or _utc(term.issued_at, code=code) > admitted_at
        or _utc(term.expires_at, code=code) <= admitted_at + timedelta(seconds=facts.safety_margin_seconds)
        or prior_values["clock_floor"] > admitted_at  # type: ignore[operator]
    ):
        _fail(code)
    return prior, candidate, admitted_at


def _receipt_sha256(
    *,
    facts: _Facts,
    prior_state_sha256: str,
    previous_commit_sha256: str,
    next_state_sha256: str,
    writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
    admitted_at: datetime,
) -> str:
    try:
        operation = writer_admission.operation
        value = operational_writer_admission_postgres_receipt_sha256_v1(
            binding=_identity(facts),
            transition_kind="writer_admission",
            prior_revision=writer_admission.state_transition.prior_state.revision,
            prior_fence_generation=writer_admission.state_transition.prior_state.fence_generation,
            prior_state_sha256=prior_state_sha256,
            previous_commit_sha256=previous_commit_sha256,
            next_state_sha256=next_state_sha256,
            next_fence_generation=writer_admission.state_transition.next_state.fence_generation,
            operation={
                "operation_kind": operation.operation_kind,
                "opened_state_revision": operation.opened_state_revision,
                "fence_generation": operation.fence_generation,
                "evidence_id": operation.evidence_id,
                "writer_epoch": operation.writer_epoch,
                "writer_lease_id": operation.writer_lease_id,
                "opened_at": operation.opened_at,
                "admitted_at": admitted_at,
            },
            control={
                "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
                "control_role_label": facts.control_role_label,
                "control_policy_sha256": facts.control_policy_sha256,
            },
            committed_at=admitted_at,
        )
    except OperationalWriterAdmissionPostgresContractError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID"
        ) from exc
    return _digest(
        value,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
    )


def _commit_sha256(
    *,
    commit_id: UUID,
    head_id: UUID,
    next_state_sha256: str,
    previous_commit_sha256: str,
    receipt_sha256: str,
    admitted_at: datetime,
) -> str:
    try:
        value = operational_writer_admission_postgres_commit_sha256_v1(
            commit_id=commit_id,
            head_id=head_id,
            receipt_sha256=receipt_sha256,
            previous_commit_sha256=previous_commit_sha256,
            state_sha256=next_state_sha256,
            committed_at=admitted_at,
        )
    except OperationalWriterAdmissionPostgresContractError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID"
        ) from exc
    return _digest(
        value,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
    )


def _require_active_transaction(session: object) -> None:
    checker = getattr(session, "in_transaction", None)
    if not callable(checker):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        active = checker()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID"
        ) from exc
    if active is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_REQUIRED")


def _require_postgresql_and_clean_session(session: object) -> None:
    """Reject a non-PostgreSQL or already-mutated caller transaction.

    The guarded application write must be staged *after* this admission
    boundary has locked/advanced the head and before the caller commits the
    same transaction.  Allowing already-pending ORM writes here would let an
    implicit autoflush run them before the V1 admission is established.
    """

    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        bind = get_bind()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID"
        ) from exc
    dialect = getattr(bind, "dialect", None)
    if getattr(dialect, "name", None) != "postgresql":
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_POSTGRES_REQUIRED")
    for name in ("new", "dirty", "deleted"):
        pending = getattr(session, name, None)
        if pending is None:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        try:
            if bool(pending):
                _fail(
                    "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_UNGUARDED_PENDING_MUTATION"
                )
        except PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID"
            ) from exc


async def _execute(session: object, statement: object, *, code: str) -> object:
    execute = getattr(session, "execute", None)
    if not callable(execute):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = execute(statement)
        if not inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        return await result
    except PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code) from exc


async def _flush(session: object, *, code: str) -> None:
    flush = getattr(session, "flush", None)
    if not callable(flush):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = flush()
        if not inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        await result
    except PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError:
        raise
    except IntegrityError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_REPLAY_OR_RECEIPT_CONFLICT"
        ) from exc
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code) from exc


def _add(session: object, value: object) -> None:
    callback = getattr(session, "add", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = callback(value)
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_RECEIPT_INSERT_FAILED"
        ) from exc
    if inspect.isawaitable(result):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_SESSION_INVALID")


def _scalar_one_or_none(result: object, *, code: str) -> object | None:
    callback = getattr(result, "scalar_one_or_none", None)
    if not callable(callback):
        _fail(code)
    try:
        return callback()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(code) from exc


class PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter:
    """Append/couple one V1 transaction-commit admission within an active DB tx."""

    def __init__(
        self,
        config: PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig,
    ) -> None:
        self._config = config

    async def persist_writer_admission(
        self,
        *,
        session: AsyncSession,
        writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
    ) -> PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt | None:
        """Append one commit then atomically advance exactly its locked head.

        This method intentionally supports only ``transaction_commit``.  A
        database transaction cannot make an arbitrary external side effect
        atomic; that needs a separate, stricter boundary.
        """

        facts = _facts(self._config)
        if facts is None:
            return None
        prior, candidate, admitted_at = _validated_admission(writer_admission, facts=facts)
        _require_active_transaction(session)
        _require_postgresql_and_clean_session(session)
        await _execute(
            session,
            select(
                func.pg_advisory_xact_lock(
                    physical_operational_failover_v1_writer_admission_head_advisory_lock_key(
                        facts.binding
                    )
                )
            ),
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_LOCK_FAILED",
        )
        identity = _identity(facts)
        head_result = await _execute(
            session,
            select(OperationalWriterAdmissionHead)
            .where(*[getattr(OperationalWriterAdmissionHead, key) == value for key, value in identity.items()])
            .with_for_update(),
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_LOCK_FAILED",
        )
        head = _scalar_one_or_none(
            head_result,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_INVALID",
        )
        if type(head) is not OperationalWriterAdmissionHead:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_MISSING")
        prior_values = _state_values(
            prior,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
        )
        prior_state_sha256 = _state_sha256(
            prior,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
        )
        _require_head_matches(
            head,
            prior_values=prior_values,
            prior_state_sha256=prior_state_sha256,
            facts=facts,
        )
        next_values = _state_values(
            candidate,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
        )
        next_state_sha256 = _state_sha256(
            candidate,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_ADMISSION_INVALID",
        )
        previous_commit_sha256 = _digest(
            head.current_commit_sha256,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_STALE_OR_MISMATCH",
        )
        commit_id = uuid4()
        receipt_sha256 = _receipt_sha256(
            facts=facts,
            prior_state_sha256=prior_state_sha256,
            previous_commit_sha256=previous_commit_sha256,
            next_state_sha256=next_state_sha256,
            writer_admission=writer_admission,
            admitted_at=admitted_at,
        )
        commit_sha256 = _commit_sha256(
            commit_id=commit_id,
            head_id=head.id,
            next_state_sha256=next_state_sha256,
            previous_commit_sha256=previous_commit_sha256,
            receipt_sha256=receipt_sha256,
            admitted_at=admitted_at,
        )
        operation = writer_admission.operation
        try:
            commit = OperationalWriterAdmissionCommit(
                id=commit_id,
                head_id=head.id,
                **identity,
                transition_kind="writer_admission",
                prior_revision=prior.revision,
                next_revision=candidate.revision,
                prior_fence_generation=prior.fence_generation,
                next_fence_generation=candidate.fence_generation,
                prior_state_sha256=prior_state_sha256,
                previous_commit_sha256=previous_commit_sha256,
                highest_writer_epoch=candidate.highest_writer_epoch,
                holder_site=candidate.active_term.holder_site,
                writer_epoch=candidate.active_term.writer_epoch,
                writer_lease_id=candidate.active_term.writer_lease_id,
                evidence_id=candidate.active_term.evidence_id,
                revalidation_id=candidate.active_term.revalidation_id,
                term_issued_at=candidate.active_term.issued_at,
                term_expires_at=candidate.active_term.expires_at,
                revalidated_runtime_instance_id=candidate.revalidated_runtime_instance_id,
                clock_floor=candidate.clock_floor,
                fenced=candidate.fenced,
                fence_reason=candidate.fence_reason,
                requires_fresh_witness_revalidation=candidate.requires_fresh_witness_revalidation,
                state_sha256=next_state_sha256,
                receipt_sha256=receipt_sha256,
                commit_sha256=commit_sha256,
                operation_kind=operation.operation_kind,
                operation_opened_state_revision=operation.opened_state_revision,
                operation_fence_generation=operation.fence_generation,
                operation_evidence_id=operation.evidence_id,
                operation_writer_epoch=operation.writer_epoch,
                operation_writer_lease_id=operation.writer_lease_id,
                operation_opened_at=operation.opened_at,
                admitted_at=admitted_at,
                control_boundary=OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
                control_role_label=facts.control_role_label,
                control_policy_sha256=facts.control_policy_sha256,
                committed_at=admitted_at,
            )
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_RECEIPT_INVALID"
            ) from exc
        _add(session, commit)
        # The unique immutable receipt/commit constraints fire before the head
        # changes.  On a collision the caller must roll this transaction back.
        await _flush(
            session,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_RECEIPT_INSERT_FAILED",
        )
        expected_head = {
            **prior_values,
            "state_sha256": prior_state_sha256,
            "receipt_sha256": head.receipt_sha256,
            "current_commit_id": head.current_commit_id,
            "current_commit_sha256": previous_commit_sha256,
            "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            "control_role_label": facts.control_role_label,
            "control_policy_sha256": facts.control_policy_sha256,
            "committed_at": head.committed_at,
        }
        next_head = {
            **next_values,
            "state_sha256": next_state_sha256,
            "receipt_sha256": receipt_sha256,
            "current_commit_id": commit_id,
            "current_commit_sha256": commit_sha256,
            "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            "control_role_label": facts.control_role_label,
            "control_policy_sha256": facts.control_policy_sha256,
            "committed_at": admitted_at,
        }
        update_result = await _execute(
            session,
            update(OperationalWriterAdmissionHead)
            .where(*[getattr(OperationalWriterAdmissionHead, key) == value for key, value in expected_head.items()])
            .values(**next_head)
            .execution_options(synchronize_session=False),
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_CAS_FAILED",
        )
        if type(getattr(update_result, "rowcount", None)) is not int or update_result.rowcount != 1:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_CAS_RACED")
        await _flush(
            session,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SQLALCHEMY_TRANSACTION_HEAD_UPDATE_FAILED",
        )
        return _mint_commit_receipt(
            facts=facts,
            commit_id=commit_id,
            commit_sha256=commit_sha256,
            receipt_sha256=receipt_sha256,
            **identity,
            prior_revision=prior.revision,
            next_revision=candidate.revision,
            fence_generation=candidate.fence_generation,
            writer_epoch=candidate.active_term.writer_epoch,
            writer_lease_id=candidate.active_term.writer_lease_id,
            evidence_id=candidate.active_term.evidence_id,
            revalidation_id=candidate.active_term.revalidation_id,
            admitted_at=admitted_at,
        )
