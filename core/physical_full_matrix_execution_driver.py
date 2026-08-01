"""Default-off phase boundary for the physical three-site Full Matrix.

This is intentionally a small orchestration contract, not a production test
runner.  It cannot create a worktree, inspect a host, open SSH/SCP/rsync,
start Docker, call PostgreSQL, contact Object Storage, promote a site, alter a
route, or invoke a historical two-server runner.  A future root-side runtime
must inject every phase adapter and an append-only receipt journal explicitly.

The only information retained in plans and receipts is a redacted set of
campaign/release/manifest/term/route hashes and phase oracle hashes.  It is
therefore safe to persist as run evidence, but never an authorization to
deploy, promote, write, or claim that the Full Matrix completed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any, Protocol
from uuid import UUID
import weakref

from core.physical_full_matrix_campaign_readiness import (
    LEGACY_FULL_MATRIX_RUNNER_PATHS,
    LEGACY_FULL_MATRIX_RUNNER_SCHEMAS,
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PhysicalFullMatrixCampaignReadiness,
    PhysicalFullMatrixCampaignReadinessError,
    VerifiedPhysicalFullMatrixCampaignReadiness,
    require_verified_physical_full_matrix_campaign_readiness,
)


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_EXECUTION_MAX_ORACLE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_DESTRUCTIVE_PHASES",
    "PHYSICAL_FULL_MATRIX_EXECUTION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA",
    "PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_SCHEMA",
    "PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS",
    "PHYSICAL_FULL_MATRIX_PHASES",
    "PhysicalFullMatrixExecutionAdapter",
    "PhysicalFullMatrixExecutionAdapters",
    "PhysicalFullMatrixExecutionBinding",
    "PhysicalFullMatrixExecutionConfig",
    "PhysicalFullMatrixExecutionDriverError",
    "PhysicalFullMatrixExecutionPhase",
    "PhysicalFullMatrixExecutionPlan",
    "PhysicalFullMatrixExecutionRequest",
    "PhysicalFullMatrixExecutionResult",
    "PhysicalFullMatrixExecutionSuccessorBinding",
    "PhysicalFullMatrixPhaseClaim",
    "PhysicalFullMatrixPhaseOracle",
    "PhysicalFullMatrixReceiptJournal",
    "PhysicalFullMatrixRunReceipt",
    "build_physical_full_matrix_execution_plan",
    "execute_next_physical_full_matrix_phase",
    "parse_physical_full_matrix_run_receipt",
    "prepare_physical_full_matrix_execution_adapters",
    "require_physical_full_matrix_execution_plan",
)


PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA = (
    "gold-trade-physical-full-matrix-execution-driver-v2"
)
PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_SCHEMA = (
    "gold-trade-physical-full-matrix-execution-plan-v2"
)
PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SCHEMA = (
    "gold-trade-physical-full-matrix-execution-receipt-v2"
)
PHYSICAL_FULL_MATRIX_EXECUTION_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_FULL_MATRIX_EXECUTION_MAX_ORACLE_AGE_SECONDS = 120
_MAX_ORACLE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_STATUS_PLANNED = "planned-not-executed"
_STATUS_COMPLETED = "completed-redacted-phase-receipt"
_STATUS_ALREADY_COMPLETED = "already-completed-from-append-only-receipt"
_DIRECT_CONTROL_FORBIDDEN = "forbidden"
_LEGACY_COMPATIBILITY_FORBIDDEN = "forbidden"
_ZERO_SHA256 = "0" * 64
_PLAN_CAPABILITY = object()

_NORMAL_DIRECTION = ("webapp_fi", "webapp_ir")
_PROMOTED_DIRECTION = ("webapp_ir", "webapp_fi")
_SUCCESSOR_PHASE_DIRECTIONS = {
    "witness-promote-ir": _PROMOTED_DIRECTION,
    "witness-restore-fi-writer": _NORMAL_DIRECTION,
}

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$", re.ASCII)
_PHASE_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$", re.ASCII)
_PROFILE_RE = re.compile(r"^[a-z][a-z0-9-]{2,95}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)


class PhysicalFullMatrixExecutionDriverError(ValueError):
    """One stable fail-closed error from the physical execution boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixExecutionDriverError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionPhase:
    """One fixed phase and its only accepted redacted oracle profile."""

    sequence: int
    name: str
    oracle: str
    destructive: bool
    transport_profile: str


# This primitive catalog is the immutable source of truth.  The exported
# ``PHYSICAL_FULL_MATRIX_PHASES`` is a detached display/testing projection;
# execution, planning, and receipt parsing never use public phase objects as
# authority because callers can mutate even frozen dataclasses with
# ``object.__setattr__``.
_PHYSICAL_FULL_MATRIX_PHASE_CATALOG: tuple[tuple[int, str, str, bool, str], ...] = (
    (
        1,
        "normal-fi-writer-durable-ack-matrix",
        "normal-fi-writer-durable-ack-oracle-v1",
        True,
        "fi-local-transaction-object-storage-ack-v1",
    ),
    (
        2,
        "fence-fi-writer",
        "fi-witnessed-writer-fence-oracle-v1",
        True,
        "fi-local-witness-fence-v1",
    ),
    (
        3,
        "recover-ir-through-object-storage",
        "ir-exact-version-object-storage-recovery-oracle-v1",
        True,
        "ir-private-versioned-object-storage-pull-v1",
    ),
    (
        4,
        "witness-promote-ir",
        "ir-witnessed-promotion-oracle-v1",
        True,
        "ir-local-witnessed-promotion-v1",
    ),
    (
        5,
        "ir-writer-durable-ack-matrix",
        "ir-writer-durable-ack-oracle-v1",
        True,
        "ir-local-transaction-object-storage-ack-v1",
    ),
    (
        6,
        "rebuild-fi-through-object-storage",
        "fi-exact-version-object-storage-standby-rebuild-oracle-v1",
        True,
        "fi-private-versioned-object-storage-pull-v1",
    ),
    (
        7,
        "witness-restore-fi-writer",
        "fi-witnessed-writer-restore-oracle-v1",
        True,
        "fi-local-witnessed-promotion-v1",
    ),
    (
        8,
        "final-three-site-convergence-oracle",
        "three-site-final-convergence-oracle-v1",
        False,
        "three-site-read-only-evidence-v1",
    ),
)
_PHASE_CATALOG_BY_NAME = {
    phase[1]: phase for phase in _PHYSICAL_FULL_MATRIX_PHASE_CATALOG
}
PHYSICAL_FULL_MATRIX_PHASES = tuple(
    PhysicalFullMatrixExecutionPhase(*phase)
    for phase in _PHYSICAL_FULL_MATRIX_PHASE_CATALOG
)
PHYSICAL_FULL_MATRIX_DESTRUCTIVE_PHASES = tuple(
    phase[1] for phase in _PHYSICAL_FULL_MATRIX_PHASE_CATALOG if phase[3]
)

# A positive readiness report is not itself authority.  It is nevertheless
# insufficient if even one of its typed evidence slots was omitted.
PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS = tuple(
    sorted(
        {
            "physical-wal-bundle",
            "physical-wal-recovery-observation",
            "remote-ack-evidence",
            "remote-ack-receiver-recovery",
            "remote-ack-durable-ledger",
            "strict-remote-ack-writer-response",
            # The retired two-role normal-direction observation is explicitly
            # rejected by campaign readiness.  A reversible campaign can only
            # reach this boundary after the four independent directional
            # identities have supplied one fresh, cross-bound Object-Lock
            # observation for both directions.
            "four-role-arvan-object-storage-immutability-preflight",
            # Readiness separately rechecks the reverse route proof against
            # the same four-role binding.  Keep that observed sub-slot in the
            # exact driver set until it is replaced atomically by one future
            # direction-aware V2 recovery capability; otherwise an otherwise
            # valid readiness report would be rejected merely for containing
            # its mandatory reverse evidence.
            "arvan-object-storage-failback-preflight",
            "receiver-ready-v2-blob-promotion-evidence",
            "current-witness-term",
            "current-role-activation",
            "deployment-preflight-posture",
            "selected-p0-auth-upload-result",
            "external-effect-reconciliation-decision",
            "source-write-fence-recovery-route",
        }
    )
)

@dataclass(frozen=True)
class PhysicalFullMatrixExecutionBinding:
    """Pins that every phase receipt must repeat exactly, without secrets."""

    campaign_id: str
    release_sha: str
    readiness_binding_sha256: str
    release_manifest_sha256: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    source_site: str = _NORMAL_DIRECTION[0]
    destination_site: str = _NORMAL_DIRECTION[1]


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionSuccessorBinding:
    """One phase-owned, term-bound successor for the next direction.

    The promoted IR term does not exist when the campaign starts.  It must be
    produced by the witnessed promotion phase and then carried in the
    append-only receipt chain.  This projection contains no credential,
    endpoint, hostname, or command; the owning phase adapter remains
    responsible for proving the Witness transition and reverse Object-Storage
    route before it may return this value.
    """

    source_site: str
    destination_site: str
    readiness_binding_sha256: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    transition_evidence_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionConfig:
    """Default-off local policy.  It carries no credential, host, or path.

    ``readiness`` must be the opaque process-local provenance returned by the
    readiness boundary, not its public diagnostic report.
    """

    binding: PhysicalFullMatrixExecutionBinding | None = None
    readiness: VerifiedPhysicalFullMatrixCampaignReadiness | None = None
    run_id: UUID | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_EXECUTION_DEFAULT_ENABLED
    maximum_oracle_age_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_EXECUTION_MAX_ORACLE_AGE_SECONDS
    )
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionPlan:
    """Opaque process-local plan metadata, never a live execution permit.

    The visible fields are deliberately only a redacted display projection.
    A plan is accepted only while its exact object identity remains registered
    against a private canonical snapshot made by the builder.  Copying or
    serializing it must not manufacture a second usable plan.
    """

    canonical_plan: bytes
    plan_sha256: str
    run_id: UUID
    binding: PhysicalFullMatrixExecutionBinding
    phases: tuple[PhysicalFullMatrixExecutionPhase, ...]
    maximum_oracle_age_seconds: int
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _PhysicalFullMatrixExecutionBindingSnapshot:
    """Primitive-only binding facts never shared with public plan objects."""

    campaign_id: str
    release_sha: str
    readiness_binding_sha256: str
    release_manifest_sha256: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    source_site: str
    destination_site: str


@dataclass(frozen=True)
class _PhysicalFullMatrixExecutionPhaseSnapshot:
    """Primitive-only phase facts never shared with adapter request objects."""

    sequence: int
    name: str
    oracle: str
    destructive: bool
    transport_profile: str


@dataclass(frozen=True)
class _PhysicalFullMatrixExecutionPlanSnapshot:
    """Private canonical state used by the effectful execution boundary."""

    canonical_plan: bytes
    plan_sha256: str
    run_id: UUID
    binding: _PhysicalFullMatrixExecutionBindingSnapshot
    phases: tuple[_PhysicalFullMatrixExecutionPhaseSnapshot, ...]
    maximum_oracle_age_seconds: int


@dataclass(frozen=True)
class _PhysicalFullMatrixExecutionPlanProvenance:
    plan_ref: weakref.ReferenceType[PhysicalFullMatrixExecutionPlan]
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot


# A plan is intentionally process-local.  This registry is keyed by identity
# rather than the dataclass hash so an attempted ``object.__setattr__`` cannot
# redirect lookup to a forged structurally equal plan.
_PLAN_PROVENANCE: dict[int, _PhysicalFullMatrixExecutionPlanProvenance] = {}


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionRequest:
    """Redacted one-phase request handed only to the matching injected adapter."""

    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixExecutionPhase
    phase_request_sha256: str
    binding: PhysicalFullMatrixExecutionBinding


@dataclass(frozen=True)
class PhysicalFullMatrixPhaseOracle:
    """Adapter result shape; it cannot carry raw output, endpoints, or secrets."""

    schema: str
    status: str
    phase: str
    oracle: str
    transport_profile: str
    campaign_id: str
    release_sha: str
    release_manifest_sha256: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    evidence_sha256: str
    observed_at: datetime
    source_site: str = _NORMAL_DIRECTION[0]
    destination_site: str = _NORMAL_DIRECTION[1]
    direct_fi_to_ir_control: str = _DIRECT_CONTROL_FORBIDDEN
    direct_ir_to_fi_control: str = _DIRECT_CONTROL_FORBIDDEN
    legacy_runner_compatibility: str = _LEGACY_COMPATIBILITY_FORBIDDEN
    successor_binding: PhysicalFullMatrixExecutionSuccessorBinding | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixPhaseClaim:
    """One journal-owned atomic claim, or an already durable phase receipt.

    ``claim_id`` is an opaque safe identifier.  It is never included in a
    receipt and must be usable only by the journal that minted it.  Returning
    an existing receipt prevents a second adapter invocation after a retry or
    a concurrent controller reaches the same phase.  A claim with neither a
    receipt nor an id means another controller still owns the live claim and
    must fail closed; it is never a permission to retry the adapter.
    """

    run_id: UUID
    plan_sha256: str
    sequence: int
    phase_request_sha256: str
    claim_id: str | None = None
    existing_receipt: bytes | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixRunReceipt:
    """One append-only redacted phase completion receipt."""

    canonical_receipt: bytes
    receipt_sha256: str
    run_id: UUID
    plan_sha256: str
    sequence: int
    phase: str
    phase_request_sha256: str
    oracle_evidence_sha256: str
    previous_receipt_sha256: str
    recorded_at: datetime
    binding: PhysicalFullMatrixExecutionBinding | None = None
    successor_binding: PhysicalFullMatrixExecutionSuccessorBinding | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionResult:
    """A one-phase result, never a claim that the whole campaign completed."""

    status: str
    phase: str | None
    receipt: PhysicalFullMatrixRunReceipt | None
    next_phase: str | None
    full_matrix_executed: bool = False


class PhysicalFullMatrixExecutionAdapter(Protocol):
    """A future root-side adapter for precisely one declared phase."""

    def execute_phase(
        self, *, request: PhysicalFullMatrixExecutionRequest
    ) -> PhysicalFullMatrixPhaseOracle:
        """Execute one phase without a direct FI-to-IR control channel."""


class PhysicalFullMatrixReceiptJournal(Protocol):
    """Future append-only, idempotent, root-owned receipt state adapter."""

    def read_receipts(self, *, run_id: UUID) -> Sequence[bytes]:
        """Return the complete ordered receipt chain for exactly one run."""

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
    ) -> PhysicalFullMatrixPhaseClaim:
        """Atomically reserve one phase or return its already durable receipt."""

    def append_claimed(
        self,
        *,
        claim: PhysicalFullMatrixPhaseClaim,
        canonical_receipt: bytes,
    ) -> bytes:
        """Append only the receipt bound to a live journal claim."""


@dataclass(frozen=True)
class PhysicalFullMatrixExecutionAdapters:
    """Every phase adapter and journal are explicit; defaults do nothing."""

    phase_adapters: Mapping[str, PhysicalFullMatrixExecutionAdapter] | None = None
    receipt_journal: PhysicalFullMatrixReceiptJournal | None = None


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixExecutionDriverError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_JSON_INVALID")
        value[key] = item
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    result = _utc(parsed, code=code)
    if result.isoformat().replace("+00:00", "Z") != value:
        _fail(code)
    return result


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="PHYSICAL_FULL_MATRIX_EXECUTION_CLOCK_INVALID").isoformat().replace(
        "+00:00", "Z"
    )


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _normalise_binding(value: object) -> PhysicalFullMatrixExecutionBinding:
    if type(value) is not PhysicalFullMatrixExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_BINDING_INVALID")
    if type(value.campaign_id) is not str or _ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_CAMPAIGN_INVALID")
    if type(value.release_sha) is not str or _HEX40_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RELEASE_INVALID")
    _sha256(value.readiness_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_READINESS_BINDING_INVALID")
    _sha256(value.release_manifest_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_MANIFEST_INVALID")
    _sha256(value.route_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_ROUTE_INVALID")
    if type(value.writer_epoch) is not int or not 1 <= value.writer_epoch <= 2**31 - 1:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_WRITER_EPOCH_INVALID")
    _identifier(value.writer_lease_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_WRITER_LEASE_INVALID")
    _identifier(value.witness_transition_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_WITNESS_TRANSITION_INVALID")
    _sha256(value.witnessed_term_proof_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_TERM_INVALID")
    if (value.source_site, value.destination_site) != _NORMAL_DIRECTION:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_INITIAL_DIRECTION_INVALID")
    return value


def _binding_body(binding: PhysicalFullMatrixExecutionBinding) -> dict[str, object]:
    """Return the complete redacted projection used in plans/requests/receipts."""

    return _binding_body_from_snapshot(_binding_snapshot(binding, direction=None))


def _binding_body_from_snapshot(
    checked: _PhysicalFullMatrixExecutionBindingSnapshot,
) -> dict[str, object]:
    """Render a binding body without consulting a public binding object."""

    return {
        "campaign_id": checked.campaign_id,
        "release_sha": checked.release_sha,
        "readiness_binding_sha256": checked.readiness_binding_sha256,
        "release_manifest_sha256": checked.release_manifest_sha256,
        "source_site": checked.source_site,
        "destination_site": checked.destination_site,
        "route_binding_sha256": checked.route_binding_sha256,
        "writer_epoch": checked.writer_epoch,
        "writer_lease_id": checked.writer_lease_id,
        "witness_transition_id": checked.witness_transition_id,
        "witnessed_term_proof_sha256": checked.witnessed_term_proof_sha256,
    }


def _normalise_binding_for_direction(
    value: object,
    *,
    direction: tuple[str, str] | None,
) -> PhysicalFullMatrixExecutionBinding:
    """Validate one existing chain binding without assuming it is the initial one."""

    if type(value) is not PhysicalFullMatrixExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_BINDING_INVALID")
    if type(value.campaign_id) is not str or _ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_CAMPAIGN_INVALID")
    if type(value.release_sha) is not str or _HEX40_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RELEASE_INVALID")
    _sha256(value.readiness_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_READINESS_BINDING_INVALID")
    _sha256(value.release_manifest_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_MANIFEST_INVALID")
    _sha256(value.route_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_ROUTE_INVALID")
    if type(value.writer_epoch) is not int or not 1 <= value.writer_epoch <= 2**31 - 1:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_WRITER_EPOCH_INVALID")
    _identifier(value.writer_lease_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_WRITER_LEASE_INVALID")
    _identifier(value.witness_transition_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_WITNESS_TRANSITION_INVALID")
    _sha256(value.witnessed_term_proof_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_TERM_INVALID")
    if (value.source_site, value.destination_site) not in {_NORMAL_DIRECTION, _PROMOTED_DIRECTION}:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_DIRECTION_INVALID")
    if direction is not None and (value.source_site, value.destination_site) != direction:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_DIRECTION_INVALID")
    return value


def _binding_snapshot(
    value: object,
    *,
    direction: tuple[str, str] | None,
) -> _PhysicalFullMatrixExecutionBindingSnapshot:
    """Copy validated primitive binding facts away from any public object."""

    checked = _normalise_binding_for_direction(value, direction=direction)
    return _PhysicalFullMatrixExecutionBindingSnapshot(
        campaign_id=checked.campaign_id,
        release_sha=checked.release_sha,
        readiness_binding_sha256=checked.readiness_binding_sha256,
        release_manifest_sha256=checked.release_manifest_sha256,
        route_binding_sha256=checked.route_binding_sha256,
        writer_epoch=checked.writer_epoch,
        writer_lease_id=checked.writer_lease_id,
        witness_transition_id=checked.witness_transition_id,
        witnessed_term_proof_sha256=checked.witnessed_term_proof_sha256,
        source_site=checked.source_site,
        destination_site=checked.destination_site,
    )


def _binding_from_snapshot(
    value: _PhysicalFullMatrixExecutionBindingSnapshot,
) -> PhysicalFullMatrixExecutionBinding:
    """Return one detached public binding projection from private facts."""

    return PhysicalFullMatrixExecutionBinding(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        readiness_binding_sha256=value.readiness_binding_sha256,
        release_manifest_sha256=value.release_manifest_sha256,
        route_binding_sha256=value.route_binding_sha256,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        witness_transition_id=value.witness_transition_id,
        witnessed_term_proof_sha256=value.witnessed_term_proof_sha256,
        source_site=value.source_site,
        destination_site=value.destination_site,
    )


def _matches_binding_snapshot(
    value: object,
    snapshot: _PhysicalFullMatrixExecutionBindingSnapshot,
) -> bool:
    return (
        type(value) is PhysicalFullMatrixExecutionBinding
        and type(value.campaign_id) is str
        and value.campaign_id == snapshot.campaign_id
        and type(value.release_sha) is str
        and value.release_sha == snapshot.release_sha
        and type(value.readiness_binding_sha256) is str
        and value.readiness_binding_sha256 == snapshot.readiness_binding_sha256
        and type(value.release_manifest_sha256) is str
        and value.release_manifest_sha256 == snapshot.release_manifest_sha256
        and type(value.route_binding_sha256) is str
        and value.route_binding_sha256 == snapshot.route_binding_sha256
        and type(value.writer_epoch) is int
        and value.writer_epoch == snapshot.writer_epoch
        and type(value.writer_lease_id) is str
        and value.writer_lease_id == snapshot.writer_lease_id
        and type(value.witness_transition_id) is str
        and value.witness_transition_id == snapshot.witness_transition_id
        and type(value.witnessed_term_proof_sha256) is str
        and value.witnessed_term_proof_sha256 == snapshot.witnessed_term_proof_sha256
        and type(value.source_site) is str
        and value.source_site == snapshot.source_site
        and type(value.destination_site) is str
        and value.destination_site == snapshot.destination_site
    )


def _phase_snapshot(
    value: object,
) -> _PhysicalFullMatrixExecutionPhaseSnapshot:
    if (
        type(value) is not PhysicalFullMatrixExecutionPhase
        or type(value.sequence) is not int
        or type(value.name) is not str
        or type(value.oracle) is not str
        or type(value.destructive) is not bool
        or type(value.transport_profile) is not str
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_PHASE_GRAPH_TAMPERED")
    return _PhysicalFullMatrixExecutionPhaseSnapshot(
        sequence=value.sequence,
        name=value.name,
        oracle=value.oracle,
        destructive=value.destructive,
        transport_profile=value.transport_profile,
    )


def _phase_from_snapshot(
    value: _PhysicalFullMatrixExecutionPhaseSnapshot,
) -> PhysicalFullMatrixExecutionPhase:
    return PhysicalFullMatrixExecutionPhase(
        sequence=value.sequence,
        name=value.name,
        oracle=value.oracle,
        destructive=value.destructive,
        transport_profile=value.transport_profile,
    )


def _matches_phase_snapshot(
    value: object,
    snapshot: _PhysicalFullMatrixExecutionPhaseSnapshot,
) -> bool:
    return (
        type(value) is PhysicalFullMatrixExecutionPhase
        and type(value.sequence) is int
        and value.sequence == snapshot.sequence
        and type(value.name) is str
        and value.name == snapshot.name
        and type(value.oracle) is str
        and value.oracle == snapshot.oracle
        and type(value.destructive) is bool
        and value.destructive is snapshot.destructive
        and type(value.transport_profile) is str
        and value.transport_profile == snapshot.transport_profile
    )


def _phase_snapshots() -> tuple[_PhysicalFullMatrixExecutionPhaseSnapshot, ...]:
    return tuple(
        _PhysicalFullMatrixExecutionPhaseSnapshot(*value)
        for value in _PHYSICAL_FULL_MATRIX_PHASE_CATALOG
    )


def _matches_phase_snapshots(
    value: object,
    snapshots: tuple[_PhysicalFullMatrixExecutionPhaseSnapshot, ...],
) -> bool:
    return (
        type(value) is tuple
        and len(value) == len(snapshots)
        and all(
            _matches_phase_snapshot(phase, snapshot)
            for phase, snapshot in zip(value, snapshots, strict=True)
        )
    )


def _register_plan_provenance(
    plan: PhysicalFullMatrixExecutionPlan,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
) -> None:
    key = id(plan)

    def _discard(reference: weakref.ReferenceType[PhysicalFullMatrixExecutionPlan]) -> None:
        registered = _PLAN_PROVENANCE.get(key)
        if registered is not None and registered.plan_ref is reference:
            _PLAN_PROVENANCE.pop(key, None)

    reference = weakref.ref(plan, _discard)
    _PLAN_PROVENANCE[key] = _PhysicalFullMatrixExecutionPlanProvenance(
        plan_ref=reference,
        snapshot=snapshot,
    )


def _plan_provenance(
    value: object,
) -> _PhysicalFullMatrixExecutionPlanProvenance:
    if (
        type(value) is not PhysicalFullMatrixExecutionPlan
        or value._capability is not _PLAN_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_UNAUTHORIZED")
    registered = _PLAN_PROVENANCE.get(id(value))
    if registered is None or registered.plan_ref() is not value:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_UNAUTHORIZED")
    return registered


def _successor_mapping(value: PhysicalFullMatrixExecutionSuccessorBinding) -> dict[str, object]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "readiness_binding_sha256": value.readiness_binding_sha256,
        "route_binding_sha256": value.route_binding_sha256,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witness_transition_id": value.witness_transition_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        "transition_evidence_sha256": value.transition_evidence_sha256,
    }


def _successor_from_mapping(value: object) -> PhysicalFullMatrixExecutionSuccessorBinding | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {
        "source_site",
        "destination_site",
        "readiness_binding_sha256",
        "route_binding_sha256",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
        "transition_evidence_sha256",
    }:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_INVALID")
    successor = PhysicalFullMatrixExecutionSuccessorBinding(
        source_site=value["source_site"],
        destination_site=value["destination_site"],
        readiness_binding_sha256=value["readiness_binding_sha256"],
        route_binding_sha256=value["route_binding_sha256"],
        writer_epoch=value["writer_epoch"],
        writer_lease_id=value["writer_lease_id"],
        witness_transition_id=value["witness_transition_id"],
        witnessed_term_proof_sha256=value["witnessed_term_proof_sha256"],
        transition_evidence_sha256=value["transition_evidence_sha256"],
    )
    _normalise_successor(successor, predecessor=None, phase=None)
    return successor


def _normalise_successor(
    value: object,
    *,
    predecessor: PhysicalFullMatrixExecutionBinding | None,
    phase: PhysicalFullMatrixExecutionPhase | None,
) -> PhysicalFullMatrixExecutionBinding | None:
    """Validate/mint the next chain binding only at an explicit witness phase."""

    expected_direction = None if phase is None else _SUCCESSOR_PHASE_DIRECTIONS.get(phase.name)
    if phase is not None and expected_direction is None:
        if value is not None:
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_UNEXPECTED_SUCCESSOR")
        return None
    if type(value) is not PhysicalFullMatrixExecutionSuccessorBinding:
        _fail(
            "PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_REQUIRED"
            if phase is not None
            else "PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_INVALID"
        )
    if phase is not None and (value.source_site, value.destination_site) != expected_direction:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_DIRECTION_INVALID")
    if phase is None and (value.source_site, value.destination_site) not in {
        _NORMAL_DIRECTION,
        _PROMOTED_DIRECTION,
    }:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_DIRECTION_INVALID")
    _sha256(value.readiness_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_READINESS_INVALID")
    _sha256(value.route_binding_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_ROUTE_INVALID")
    _sha256(value.witnessed_term_proof_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_TERM_INVALID")
    _sha256(value.transition_evidence_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_EVIDENCE_INVALID")
    if type(value.writer_epoch) is not int or not 1 <= value.writer_epoch <= 2**31 - 1:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_EPOCH_INVALID")
    _identifier(value.writer_lease_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_LEASE_INVALID")
    _identifier(value.witness_transition_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_TRANSITION_INVALID")
    if predecessor is None:
        return None
    checked = _normalise_binding_for_direction(predecessor, direction=None)
    if (
        value.writer_epoch <= checked.writer_epoch
        or value.writer_lease_id == checked.writer_lease_id
        or value.witness_transition_id == checked.witness_transition_id
        or value.witnessed_term_proof_sha256 == checked.witnessed_term_proof_sha256
        or value.route_binding_sha256 == checked.route_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_SUCCESSOR_NON_MONOTONIC")
    return PhysicalFullMatrixExecutionBinding(
        campaign_id=checked.campaign_id,
        release_sha=checked.release_sha,
        readiness_binding_sha256=value.readiness_binding_sha256,
        release_manifest_sha256=checked.release_manifest_sha256,
        route_binding_sha256=value.route_binding_sha256,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        witness_transition_id=value.witness_transition_id,
        witnessed_term_proof_sha256=value.witnessed_term_proof_sha256,
        source_site=value.source_site,
        destination_site=value.destination_site,
    )


def _normalise_legacy_artifacts(value: object) -> None:
    if value in (None, (), [], ""):
        return
    # Do not parse a legacy object: any nonempty collection, generator, path,
    # or schema is rejected before phase construction.
    if isinstance(value, str) and (
        value in LEGACY_FULL_MATRIX_RUNNER_PATHS
        or value in LEGACY_FULL_MATRIX_RUNNER_SCHEMAS
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_LEGACY_RUNNER_REJECTED")
    _fail("PHYSICAL_FULL_MATRIX_EXECUTION_LEGACY_RUNNER_REJECTED")


def _normalise_readiness(
    value: object,
    *,
    binding: PhysicalFullMatrixExecutionBinding,
    now: datetime | None = None,
) -> PhysicalFullMatrixCampaignReadiness:
    try:
        report = require_verified_physical_full_matrix_campaign_readiness(value, now=now)
    except PhysicalFullMatrixCampaignReadinessError as exc:
        raise PhysicalFullMatrixExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_EXECUTION_READINESS_PROVENANCE_INVALID"
        ) from exc
    if (
        report.schema != PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA
        or report.status != PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or report.reason_codes != ()
        or report.campaign_id != binding.campaign_id
        or report.release_sha != binding.release_sha
        or report.binding_sha256 != binding.readiness_binding_sha256
        or tuple(report.observed_slots) != PHYSICAL_FULL_MATRIX_EXECUTION_REQUIRED_READINESS_SLOTS
        or report.external_execution_authorized is not False
        or report.promotion_authorized is not False
        or report.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_READINESS_INCOMPLETE")
    return report


def _maximum_age(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_ORACLE_AGE_SECONDS:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_MAX_ORACLE_AGE_INVALID")
    return value


def _normalise_config(
    config: object,
    *,
    require_enabled: bool,
    readiness_now: datetime | None = None,
) -> tuple[PhysicalFullMatrixExecutionBinding, PhysicalFullMatrixCampaignReadiness, UUID, int]:
    if type(config) is not PhysicalFullMatrixExecutionConfig:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_CONFIG_INVALID")
    if require_enabled:
        if config.enabled is not True:
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_DISABLED")
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ROOT_RUNTIME_REQUIRED")
    binding = _normalise_binding(config.binding)
    readiness = _normalise_readiness(
        config.readiness,
        binding=binding,
        now=readiness_now,
    )
    if not isinstance(config.run_id, UUID) or config.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RUN_ID_INVALID")
    _normalise_legacy_artifacts(config.legacy_runner_artifacts)
    return binding, readiness, config.run_id, _maximum_age(config.maximum_oracle_age_seconds)


def _plan_body(
    *, binding: PhysicalFullMatrixExecutionBinding, run_id: UUID, maximum_age: int
) -> dict[str, object]:
    return _plan_body_from_snapshot(
        binding=_binding_snapshot(binding, direction=_NORMAL_DIRECTION),
        run_id=run_id,
        maximum_age=maximum_age,
        phases=_phase_snapshots(),
    )


def _plan_body_from_snapshot(
    *,
    binding: _PhysicalFullMatrixExecutionBindingSnapshot,
    run_id: UUID,
    maximum_age: int,
    phases: tuple[_PhysicalFullMatrixExecutionPhaseSnapshot, ...],
) -> dict[str, object]:
    """Render a plan only from detached primitive snapshot facts."""

    return {
        "schema": PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_SCHEMA,
        "status": _STATUS_PLANNED,
        "run_id": str(run_id),
        **_binding_body_from_snapshot(binding),
        "maximum_oracle_age_seconds": maximum_age,
        "phases": [
            {
                "sequence": phase.sequence,
                "name": phase.name,
                "oracle": phase.oracle,
                "destructive": phase.destructive,
                "transport_profile": phase.transport_profile,
                "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
                "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
                "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
            }
            for phase in phases
        ],
        "materialization_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def _canonical_plan_from_snapshot(
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
) -> bytes:
    return _canonical(
        _plan_body_from_snapshot(
            binding=snapshot.binding,
            run_id=snapshot.run_id,
            maximum_age=snapshot.maximum_oracle_age_seconds,
            phases=snapshot.phases,
        ),
        code="PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_INVALID",
    ) + b"\n"


def build_physical_full_matrix_execution_plan(
    *, config: PhysicalFullMatrixExecutionConfig
) -> PhysicalFullMatrixExecutionPlan:
    """Build a root-only, default-off plan without invoking a phase adapter."""

    binding, _readiness, run_id, maximum_age = _normalise_config(
        config, require_enabled=True
    )
    binding_snapshot = _binding_snapshot(binding, direction=_NORMAL_DIRECTION)
    phase_snapshots = _phase_snapshots()
    unsigned_snapshot = _PhysicalFullMatrixExecutionPlanSnapshot(
        canonical_plan=b"",
        plan_sha256="",
        run_id=run_id,
        binding=binding_snapshot,
        phases=phase_snapshots,
        maximum_oracle_age_seconds=maximum_age,
    )
    canonical_plan = _canonical_plan_from_snapshot(unsigned_snapshot)
    snapshot = _PhysicalFullMatrixExecutionPlanSnapshot(
        canonical_plan=canonical_plan,
        plan_sha256=hashlib.sha256(canonical_plan).hexdigest(),
        run_id=run_id,
        binding=binding_snapshot,
        phases=phase_snapshots,
        maximum_oracle_age_seconds=maximum_age,
    )
    result = PhysicalFullMatrixExecutionPlan(
        canonical_plan=snapshot.canonical_plan,
        plan_sha256=snapshot.plan_sha256,
        run_id=snapshot.run_id,
        # Public projections are intentionally separate objects: mutation of
        # a plan cannot mutate the config binding or the private snapshot.
        binding=_binding_from_snapshot(snapshot.binding),
        phases=tuple(_phase_from_snapshot(phase) for phase in snapshot.phases),
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )
    object.__setattr__(result, "_capability", _PLAN_CAPABILITY)
    _register_plan_provenance(result, snapshot)
    return result


def require_physical_full_matrix_execution_plan(
    value: object,
) -> PhysicalFullMatrixExecutionPlan:
    """Recheck exact process-local plan provenance before any adapter sees it."""

    provenance = _plan_provenance(value)
    snapshot = provenance.snapshot
    canonical = _canonical_plan_from_snapshot(snapshot)
    if (
        canonical != snapshot.canonical_plan
        or hashlib.sha256(canonical).hexdigest() != snapshot.plan_sha256
        or type(value.canonical_plan) is not bytes
        or value.canonical_plan != snapshot.canonical_plan
        or type(value.plan_sha256) is not str
        or value.plan_sha256 != snapshot.plan_sha256
        or type(value.run_id) is not UUID
        or value.run_id != snapshot.run_id
        or not _matches_binding_snapshot(value.binding, snapshot.binding)
        or not _matches_phase_snapshots(value.phases, snapshot.phases)
        or type(value.maximum_oracle_age_seconds) is not int
        or value.maximum_oracle_age_seconds != snapshot.maximum_oracle_age_seconds
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_TAMPERED")
    return value


def _current_plan_snapshot(
    value: object,
) -> _PhysicalFullMatrixExecutionPlanSnapshot:
    provenance = _plan_provenance(value)
    require_physical_full_matrix_execution_plan(value)
    return provenance.snapshot


def _require_current_plan_snapshot(
    plan: object,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
) -> None:
    """Fail closed if any callback changed the public plan since entry."""

    if _current_plan_snapshot(plan) is not snapshot:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_TAMPERED")


def _request_for_snapshot(
    *,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
    phase: _PhysicalFullMatrixExecutionPhaseSnapshot,
    binding: _PhysicalFullMatrixExecutionBindingSnapshot | None = None,
) -> PhysicalFullMatrixExecutionRequest:
    """Create one private expected request from detached snapshot values."""

    effective_binding = snapshot.binding if binding is None else binding
    request_body = {
        "schema": PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA,
        "run_id": str(snapshot.run_id),
        "plan_sha256": snapshot.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        **_binding_body_from_snapshot(effective_binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
    }
    return PhysicalFullMatrixExecutionRequest(
        run_id=snapshot.run_id,
        plan_sha256=snapshot.plan_sha256,
        phase=_phase_from_snapshot(phase),
        phase_request_sha256=hashlib.sha256(
            _canonical(request_body, code="PHYSICAL_FULL_MATRIX_EXECUTION_REQUEST_INVALID")
        ).hexdigest(),
        binding=_binding_from_snapshot(effective_binding),
    )


def _adapter_request_copy(
    request: PhysicalFullMatrixExecutionRequest,
) -> PhysicalFullMatrixExecutionRequest:
    """Return an adapter-owned request copy distinct from the expected one."""

    return PhysicalFullMatrixExecutionRequest(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        phase=_phase_from_snapshot(_phase_snapshot(request.phase)),
        phase_request_sha256=request.phase_request_sha256,
        binding=_binding_from_snapshot(_binding_snapshot(request.binding, direction=None)),
    )


def _request_for(
    *,
    plan: PhysicalFullMatrixExecutionPlan,
    phase: PhysicalFullMatrixExecutionPhase,
    binding: PhysicalFullMatrixExecutionBinding | None = None,
) -> PhysicalFullMatrixExecutionRequest:
    """Compatibility helper that never makes the public plan request source."""

    snapshot = _current_plan_snapshot(plan)
    selected = next(
        (
            item
            for item in snapshot.phases
            if _matches_phase_snapshot(phase, item)
        ),
        None,
    )
    if selected is None:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_PHASE_GRAPH_TAMPERED")
    return _request_for_snapshot(
        snapshot=snapshot,
        phase=selected,
        binding=(None if binding is None else _binding_snapshot(binding, direction=None)),
    )


def _validate_oracle(
    *,
    value: object,
    request: PhysicalFullMatrixExecutionRequest,
    now: datetime,
    maximum_age: int,
) -> PhysicalFullMatrixPhaseOracle:
    if type(value) is not PhysicalFullMatrixPhaseOracle:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_INVALID")
    phase = request.phase
    if (
        value.schema != PHYSICAL_FULL_MATRIX_EXECUTION_DRIVER_SCHEMA
        or value.status != "oracle-succeeded"
        or value.phase != phase.name
        or value.oracle != phase.oracle
        or value.transport_profile != phase.transport_profile
        or value.campaign_id != request.binding.campaign_id
        or value.release_sha != request.binding.release_sha
        or value.release_manifest_sha256 != request.binding.release_manifest_sha256
        or value.source_site != request.binding.source_site
        or value.destination_site != request.binding.destination_site
        or value.direct_ir_to_fi_control != _DIRECT_CONTROL_FORBIDDEN
        or value.route_binding_sha256 != request.binding.route_binding_sha256
        or value.writer_epoch != request.binding.writer_epoch
        or value.writer_lease_id != request.binding.writer_lease_id
        or value.witness_transition_id != request.binding.witness_transition_id
        or value.witnessed_term_proof_sha256 != request.binding.witnessed_term_proof_sha256
        or value.direct_fi_to_ir_control != _DIRECT_CONTROL_FORBIDDEN
        or value.legacy_runner_compatibility != _LEGACY_COMPATIBILITY_FORBIDDEN
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_BINDING_MISMATCH")
    _sha256(value.evidence_sha256, code="PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_EVIDENCE_INVALID")
    observed_at = _utc(value.observed_at, code="PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_CLOCK_INVALID")
    if observed_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_FUTURE")
    if now - observed_at > timedelta(seconds=maximum_age):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ORACLE_STALE")
    _normalise_successor(
        value.successor_binding,
        predecessor=request.binding,
        phase=phase,
    )
    return value


def _receipt_body(
    *,
    request: PhysicalFullMatrixExecutionRequest,
    oracle: PhysicalFullMatrixPhaseOracle,
    previous_receipt_sha256: str,
    recorded_at: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SCHEMA,
        "status": _STATUS_COMPLETED,
        "run_id": str(request.run_id),
        "plan_sha256": request.plan_sha256,
        "sequence": request.phase.sequence,
        "phase": request.phase.name,
        "phase_request_sha256": request.phase_request_sha256,
        "oracle": request.phase.oracle,
        "oracle_evidence_sha256": oracle.evidence_sha256,
        "previous_receipt_sha256": previous_receipt_sha256,
        "recorded_at": _render_timestamp(recorded_at),
        **_binding_body(request.binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
        "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
        "successor_binding": (
            None
            if oracle.successor_binding is None
            else _successor_mapping(oracle.successor_binding)
        ),
        "full_matrix_executed": False,
    }


def _receipt_from_body(body: dict[str, object]) -> PhysicalFullMatrixRunReceipt:
    canonical = _canonical(body, code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_INVALID") + b"\n"
    try:
        run_id = UUID(str(body["run_id"]))
    except (KeyError, ValueError, TypeError):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_RUN_ID_INVALID")
    binding = _normalise_binding_for_direction(
        PhysicalFullMatrixExecutionBinding(
            campaign_id=body["campaign_id"],
            release_sha=body["release_sha"],
            readiness_binding_sha256=body["readiness_binding_sha256"],
            release_manifest_sha256=body["release_manifest_sha256"],
            route_binding_sha256=body["route_binding_sha256"],
            writer_epoch=body["writer_epoch"],
            writer_lease_id=body["writer_lease_id"],
            witness_transition_id=body["witness_transition_id"],
            witnessed_term_proof_sha256=body["witnessed_term_proof_sha256"],
            source_site=body["source_site"],
            destination_site=body["destination_site"],
        ),
        direction=None,
    )
    return PhysicalFullMatrixRunReceipt(
        canonical_receipt=canonical,
        receipt_sha256=hashlib.sha256(canonical).hexdigest(),
        run_id=run_id,
        plan_sha256=str(body["plan_sha256"]),
        sequence=int(body["sequence"]),
        phase=str(body["phase"]),
        phase_request_sha256=str(body["phase_request_sha256"]),
        oracle_evidence_sha256=str(body["oracle_evidence_sha256"]),
        previous_receipt_sha256=str(body["previous_receipt_sha256"]),
        recorded_at=_timestamp(body["recorded_at"], code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_CLOCK_INVALID"),
        binding=binding,
        successor_binding=_successor_from_mapping(body.get("successor_binding")),
    )


_RECEIPT_FIELDS = frozenset(
    {
        "schema", "status", "run_id", "plan_sha256", "sequence", "phase",
        "phase_request_sha256", "oracle", "oracle_evidence_sha256",
        "previous_receipt_sha256", "recorded_at", "campaign_id", "release_sha",
        "readiness_binding_sha256", "release_manifest_sha256", "source_site",
        "destination_site", "route_binding_sha256", "writer_epoch",
        "writer_lease_id", "witness_transition_id", "witnessed_term_proof_sha256",
        "direct_fi_to_ir_control", "direct_ir_to_fi_control",
        "legacy_runner_compatibility", "successor_binding", "full_matrix_executed",
    }
)


def parse_physical_full_matrix_run_receipt(value: object) -> PhysicalFullMatrixRunReceipt:
    """Parse a complete canonical redacted receipt; no journal is opened."""

    if not isinstance(value, bytes) or not value.endswith(b"\n"):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _: (_fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_JSON_INVALID")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixExecutionDriverError):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_ENCODING_INVALID")
    if not isinstance(decoded, dict) or set(decoded) != _RECEIPT_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_FIELDS_INVALID")
    if (
        decoded["schema"] != PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SCHEMA
        or decoded["status"] != _STATUS_COMPLETED
        or decoded["direct_fi_to_ir_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["direct_ir_to_fi_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["legacy_runner_compatibility"] != _LEGACY_COMPATIBILITY_FORBIDDEN
        or decoded["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_BINDING_INVALID")
    try:
        run_id = UUID(decoded["run_id"])
    except (ValueError, TypeError):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_RUN_ID_INVALID")
    if run_id.int == 0 or str(run_id) != decoded["run_id"]:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_RUN_ID_INVALID")
    for field_name in (
        "plan_sha256", "phase_request_sha256", "oracle_evidence_sha256", "previous_receipt_sha256",
        "readiness_binding_sha256", "release_manifest_sha256", "route_binding_sha256",
        "witnessed_term_proof_sha256",
    ):
        if field_name == "previous_receipt_sha256" and decoded[field_name] == _ZERO_SHA256:
            continue
        _sha256(decoded[field_name], code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_HASH_INVALID")
    if type(decoded["sequence"]) is not int or decoded["sequence"] not in range(
        1,
        len(_PHYSICAL_FULL_MATRIX_PHASE_CATALOG) + 1,
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_SEQUENCE_INVALID")
    phase_facts = _PHASE_CATALOG_BY_NAME.get(decoded["phase"])
    if (
        phase_facts is None
        or phase_facts[0] != decoded["sequence"]
        or decoded["oracle"] != phase_facts[2]
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_PHASE_INVALID")
    phase = PhysicalFullMatrixExecutionPhase(*phase_facts)
    if (
        _identifier(decoded["campaign_id"], code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_CAMPAIGN_INVALID") is None
        or type(decoded["release_sha"]) is not str
        or _HEX40_RE.fullmatch(decoded["release_sha"]) is None
        or type(decoded["writer_epoch"]) is not int
        or decoded["writer_epoch"] < 1
        or (decoded["source_site"], decoded["destination_site"])
        not in {_NORMAL_DIRECTION, _PROMOTED_DIRECTION}
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_BINDING_INVALID")
    _identifier(decoded["writer_lease_id"], code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_BINDING_INVALID")
    _identifier(decoded["witness_transition_id"], code="PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_BINDING_INVALID")
    result = _receipt_from_body(decoded)
    _normalise_successor(
        result.successor_binding,
        predecessor=result.binding,
        phase=phase,
    )
    if result.canonical_receipt != value:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_NONCANONICAL")
    return result


def _validate_receipt_chain(
    *,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
    raw_receipts: object,
) -> tuple[
    tuple[PhysicalFullMatrixRunReceipt, ...],
    _PhysicalFullMatrixExecutionBindingSnapshot,
]:
    if not isinstance(raw_receipts, Sequence) or isinstance(raw_receipts, (str, bytes)):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_CHAIN_INVALID")
    if len(raw_receipts) > len(snapshot.phases):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_CHAIN_TOO_LONG")
    receipts = tuple(parse_physical_full_matrix_run_receipt(value) for value in raw_receipts)
    previous = _ZERO_SHA256
    active_binding = snapshot.binding
    for index, receipt in enumerate(receipts, start=1):
        phase = snapshot.phases[index - 1]
        request = _request_for_snapshot(
            snapshot=snapshot,
            phase=phase,
            binding=active_binding,
        )
        if (
            receipt.run_id != snapshot.run_id
            or receipt.plan_sha256 != snapshot.plan_sha256
            or receipt.sequence != phase.sequence
            or receipt.phase != phase.name
            or receipt.phase_request_sha256 != request.phase_request_sha256
            or receipt.previous_receipt_sha256 != previous
            or not _matches_binding_snapshot(receipt.binding, active_binding)
        ):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_CHAIN_MISMATCH")
        successor = _normalise_successor(
            receipt.successor_binding,
            predecessor=_binding_from_snapshot(active_binding),
            phase=_phase_from_snapshot(phase),
        )
        if successor is not None:
            active_binding = _binding_snapshot(successor, direction=None)
        previous = receipt.receipt_sha256
    return receipts, active_binding


def prepare_physical_full_matrix_execution_adapters(
    *,
    plan: PhysicalFullMatrixExecutionPlan,
    adapters: PhysicalFullMatrixExecutionAdapters,
) -> None:
    """Expose all missing live adapters before any phase method can be called."""

    snapshot = _current_plan_snapshot(plan)
    if type(adapters) is not PhysicalFullMatrixExecutionAdapters:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ADAPTERS_INVALID")
    if not isinstance(adapters.phase_adapters, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTERS_MISSING")
    if set(adapters.phase_adapters) != {phase.name for phase in snapshot.phases}:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTER_SET_INVALID")
    _require_current_plan_snapshot(plan, snapshot)
    for phase in snapshot.phases:
        try:
            adapter = adapters.phase_adapters[phase.name]
        except (KeyError, TypeError):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTER_SET_INVALID")
        _require_current_plan_snapshot(plan, snapshot)
        method = getattr(adapter, "execute_phase", None)
        # ``getattr`` is attacker-controlled callback territory: a descriptor
        # may mutate the visible frozen plan, but never the private snapshot.
        _require_current_plan_snapshot(plan, snapshot)
        if not callable(method):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTER_INVALID")
    for name in ("read_receipts", "claim_phase", "append_claimed"):
        method = getattr(adapters.receipt_journal, name, None)
        _require_current_plan_snapshot(plan, snapshot)
        if not callable(method):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_JOURNAL_MISSING")
    _require_current_plan_snapshot(plan, snapshot)


def _phase_adapter_method(
    *,
    plan: PhysicalFullMatrixExecutionPlan,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
    adapters: PhysicalFullMatrixExecutionAdapters,
    phase: _PhysicalFullMatrixExecutionPhaseSnapshot,
) -> Any:
    """Lookup an adapter method and guard against descriptor-time mutation."""

    _require_current_plan_snapshot(plan, snapshot)
    if type(adapters) is not PhysicalFullMatrixExecutionAdapters:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ADAPTERS_INVALID")
    if not isinstance(adapters.phase_adapters, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTERS_MISSING")
    try:
        adapter = adapters.phase_adapters[phase.name]
    except (KeyError, TypeError):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTER_SET_INVALID")
    _require_current_plan_snapshot(plan, snapshot)
    method = getattr(adapter, "execute_phase", None)
    _require_current_plan_snapshot(plan, snapshot)
    if not callable(method):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_ADAPTER_INVALID")
    return method


def _journal_method(
    *,
    plan: PhysicalFullMatrixExecutionPlan,
    snapshot: _PhysicalFullMatrixExecutionPlanSnapshot,
    adapters: PhysicalFullMatrixExecutionAdapters,
    name: str,
) -> Any:
    """Lookup a journal method and guard against descriptor-time mutation."""

    _require_current_plan_snapshot(plan, snapshot)
    if type(adapters) is not PhysicalFullMatrixExecutionAdapters:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_ADAPTERS_INVALID")
    method = getattr(adapters.receipt_journal, name, None)
    _require_current_plan_snapshot(plan, snapshot)
    if not callable(method):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_JOURNAL_MISSING")
    return method


def _normalise_phase_claim(
    *,
    value: object,
    request: PhysicalFullMatrixExecutionRequest,
) -> PhysicalFullMatrixPhaseClaim:
    if type(value) is not PhysicalFullMatrixPhaseClaim:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_INVALID")
    if (
        value.run_id != request.run_id
        or value.plan_sha256 != request.plan_sha256
        or value.sequence != request.phase.sequence
        or value.phase_request_sha256 != request.phase_request_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_MISMATCH")
    if value.existing_receipt is not None:
        if value.claim_id is not None or not isinstance(value.existing_receipt, bytes):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_INVALID")
        return value
    if value.claim_id is None:
        # A journal-owned live claim exists elsewhere.  Continuing here could
        # invoke a destructive phase twice, so no lease takeover/retry exists
        # at this generic boundary.
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_BUSY")
    _identifier(value.claim_id, code="PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_INVALID")
    return value


def execute_next_physical_full_matrix_phase(
    *,
    config: PhysicalFullMatrixExecutionConfig,
    plan: PhysicalFullMatrixExecutionPlan,
    adapters: PhysicalFullMatrixExecutionAdapters,
    now: datetime,
) -> PhysicalFullMatrixExecutionResult:
    """Run at most one injected phase; no phase runs unless explicitly enabled.

    This function is intentionally the only location that invokes an injected
    adapter.  It never derives an adapter, command, URL, host, or credential.
    """

    binding, readiness, run_id, maximum_age = _normalise_config(config, require_enabled=True)
    observed_now = _utc(now, code="PHYSICAL_FULL_MATRIX_EXECUTION_CLOCK_INVALID")
    # A plan is non-authorizing and therefore checked only for opaque
    # membership/provenance above.  Before this effectful boundary can even
    # prepare adapters, re-assess the retained evidence at the execution
    # clock; a stale or newly blocked report must never reach a journal or an
    # adapter.
    readiness = _normalise_readiness(
        config.readiness,
        binding=binding,
        now=observed_now,
    )
    plan_snapshot = _current_plan_snapshot(plan)
    config_binding_snapshot = _binding_snapshot(binding, direction=_NORMAL_DIRECTION)
    if (
        plan_snapshot.binding != config_binding_snapshot
        or plan_snapshot.run_id != run_id
        or plan_snapshot.maximum_oracle_age_seconds != maximum_age
        or readiness.binding_sha256 != binding.readiness_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PLAN_CONFIG_MISMATCH")

    # From this point every request, receipt, chain decision, and next-phase
    # decision comes from ``plan_snapshot`` only.  The public plan is merely
    # rechecked before/after each untrusted callback so a frozen-object bypass
    # cannot alter a later effect or completion result.
    _require_current_plan_snapshot(plan, plan_snapshot)
    prepare_physical_full_matrix_execution_adapters(plan=plan, adapters=adapters)
    _require_current_plan_snapshot(plan, plan_snapshot)

    read_receipts = _journal_method(
        plan=plan,
        snapshot=plan_snapshot,
        adapters=adapters,
        name="read_receipts",
    )
    raw_receipts = read_receipts(run_id=plan_snapshot.run_id)
    _require_current_plan_snapshot(plan, plan_snapshot)
    receipts, active_binding = _validate_receipt_chain(
        snapshot=plan_snapshot,
        raw_receipts=raw_receipts,
    )
    _require_current_plan_snapshot(plan, plan_snapshot)
    if len(receipts) == len(plan_snapshot.phases):
        return PhysicalFullMatrixExecutionResult(
            status=_STATUS_ALREADY_COMPLETED,
            phase=None,
            receipt=None,
            next_phase=None,
        )

    phase = plan_snapshot.phases[len(receipts)]
    request = _request_for_snapshot(
        snapshot=plan_snapshot,
        phase=phase,
        binding=active_binding,
    )
    claim_phase = _journal_method(
        plan=plan,
        snapshot=plan_snapshot,
        adapters=adapters,
        name="claim_phase",
    )
    raw_claim = claim_phase(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        sequence=request.phase.sequence,
        phase_request_sha256=request.phase_request_sha256,
    )
    _require_current_plan_snapshot(plan, plan_snapshot)
    claim = _normalise_phase_claim(value=raw_claim, request=request)
    if claim.existing_receipt is not None:
        stored = parse_physical_full_matrix_run_receipt(claim.existing_receipt)
        if (
            stored.run_id != request.run_id
            or stored.plan_sha256 != request.plan_sha256
            or stored.sequence != request.phase.sequence
            or stored.phase != request.phase.name
            or stored.phase_request_sha256 != request.phase_request_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_EXISTING_RECEIPT_MISMATCH")
        read_receipts = _journal_method(
            plan=plan,
            snapshot=plan_snapshot,
            adapters=adapters,
            name="read_receipts",
        )
        raw_receipts = read_receipts(run_id=plan_snapshot.run_id)
        _require_current_plan_snapshot(plan, plan_snapshot)
        durable_chain, _durable_binding = _validate_receipt_chain(
            snapshot=plan_snapshot,
            raw_receipts=raw_receipts,
        )
        _require_current_plan_snapshot(plan, plan_snapshot)
        if (
            len(durable_chain) != len(receipts) + 1
            or durable_chain[-1].canonical_receipt != stored.canonical_receipt
        ):
            _fail("PHYSICAL_FULL_MATRIX_EXECUTION_PHASE_CLAIM_NOT_DURABLE")
        next_phase = (
            plan_snapshot.phases[len(receipts) + 1].name
            if len(receipts) + 1 < len(plan_snapshot.phases)
            else None
        )
        _require_current_plan_snapshot(plan, plan_snapshot)
        return PhysicalFullMatrixExecutionResult(
            status=_STATUS_ALREADY_COMPLETED,
            phase=phase.name,
            receipt=stored,
            next_phase=next_phase,
        )

    execute_phase = _phase_adapter_method(
        plan=plan,
        snapshot=plan_snapshot,
        adapters=adapters,
        phase=phase,
    )
    # The adapter gets a detached request.  Its ``object.__setattr__`` calls
    # cannot change the expected request used to validate the oracle/receipt.
    adapter_request = _adapter_request_copy(request)
    _require_current_plan_snapshot(plan, plan_snapshot)
    raw_oracle = execute_phase(request=adapter_request)
    _require_current_plan_snapshot(plan, plan_snapshot)
    oracle = _validate_oracle(
        value=raw_oracle,
        request=request,
        now=observed_now,
        maximum_age=maximum_age,
    )
    body = _receipt_body(
        request=request,
        oracle=oracle,
        previous_receipt_sha256=(receipts[-1].receipt_sha256 if receipts else _ZERO_SHA256),
        recorded_at=observed_now,
    )
    receipt = _receipt_from_body(body)
    append_claimed = _journal_method(
        plan=plan,
        snapshot=plan_snapshot,
        adapters=adapters,
        name="append_claimed",
    )
    persisted = append_claimed(
        claim=claim,
        canonical_receipt=receipt.canonical_receipt,
    )
    _require_current_plan_snapshot(plan, plan_snapshot)
    stored = parse_physical_full_matrix_run_receipt(persisted)
    if stored.canonical_receipt != receipt.canonical_receipt:
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_APPEND_CONFLICT")
    read_receipts = _journal_method(
        plan=plan,
        snapshot=plan_snapshot,
        adapters=adapters,
        name="read_receipts",
    )
    raw_receipts = read_receipts(run_id=plan_snapshot.run_id)
    _require_current_plan_snapshot(plan, plan_snapshot)
    durable_chain, _durable_binding = _validate_receipt_chain(
        snapshot=plan_snapshot,
        raw_receipts=raw_receipts,
    )
    _require_current_plan_snapshot(plan, plan_snapshot)
    if (
        len(durable_chain) != len(receipts) + 1
        or durable_chain[-1].canonical_receipt != receipt.canonical_receipt
    ):
        _fail("PHYSICAL_FULL_MATRIX_EXECUTION_RECEIPT_APPEND_NOT_DURABLE")
    next_phase = (
        plan_snapshot.phases[len(receipts) + 1].name
        if len(receipts) + 1 < len(plan_snapshot.phases)
        else None
    )
    _require_current_plan_snapshot(plan, plan_snapshot)
    return PhysicalFullMatrixExecutionResult(
        status=_STATUS_COMPLETED,
        phase=phase.name,
        receipt=stored,
        next_phase=next_phase,
    )
