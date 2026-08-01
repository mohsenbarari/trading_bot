"""V3 execution boundary for the V2-only physical Full Matrix.

The historical driver remains a fenced V1 generation.  This module does not
import it and cannot parse its plans or receipts.  It has a fixed V2 catalog,
opaque process-local V2 readiness provenance, and append-only redacted phase
receipts.  No implementation here opens a network connection, invokes a
shell, manages a host, accesses Object Storage, starts Docker, or performs a
database operation.  Those operations, if ever admitted, stay behind injected
root-side phase adapters.

The key anti-precredit rule is encoded in the phase graph: the initial FI ->
IR V2 strict-ACK evidence cannot satisfy phase five.  The Witness promotion
phase must return a newer IR -> FI V2 readiness capability with a strictly
successor witnessed term, and phase five revalidates that fresh capability at
its own clock before an adapter can be called.
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

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2CampaignBinding,
    PhysicalFullMatrixV2CampaignReadinessError,
    VerifiedPhysicalFullMatrixV2CampaignReadiness,
    require_verified_physical_full_matrix_v2_campaign_readiness,
)


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V3_MAX_ORACLE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V3_DESTRUCTIVE_PHASES",
    "PHYSICAL_FULL_MATRIX_V3_DRIVER_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V3_EXECUTION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V3_PLAN_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V3_RECEIPT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V3_PHASES",
    "PhysicalFullMatrixV3ExecutionAdapter",
    "PhysicalFullMatrixV3ExecutionAdapters",
    "PhysicalFullMatrixV3ExecutionBinding",
    "PhysicalFullMatrixV3ExecutionConfig",
    "PhysicalFullMatrixV3ExecutionDriverError",
    "PhysicalFullMatrixV3ExecutionPhase",
    "PhysicalFullMatrixV3ExecutionPlan",
    "PhysicalFullMatrixV3ExecutionRequest",
    "PhysicalFullMatrixV3ExecutionResult",
    "PhysicalFullMatrixV3PhaseClaim",
    "PhysicalFullMatrixV3PhaseOracle",
    "PhysicalFullMatrixV3ReadinessEvidence",
    "PhysicalFullMatrixV3ReceiptJournal",
    "PhysicalFullMatrixV3RunReceipt",
    "build_physical_full_matrix_v3_execution_plan",
    "execute_next_physical_full_matrix_v3_phase",
    "parse_physical_full_matrix_v3_run_receipt",
    "prepare_physical_full_matrix_v3_execution_adapters",
    "require_physical_full_matrix_v3_execution_plan",
)


PHYSICAL_FULL_MATRIX_V3_DRIVER_SCHEMA = (
    "gold-trade-physical-full-matrix-v3-execution-driver-v1"
)
PHYSICAL_FULL_MATRIX_V3_PLAN_SCHEMA = "gold-trade-physical-full-matrix-v3-plan-v1"
PHYSICAL_FULL_MATRIX_V3_RECEIPT_SCHEMA = (
    "gold-trade-physical-full-matrix-v3-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V3_EXECUTION_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_FULL_MATRIX_V3_MAX_ORACLE_AGE_SECONDS = 120

_MAX_ORACLE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_ZERO_SHA256 = "0" * 64
_PLAN_CAPABILITY = object()
_DIRECT_CONTROL_FORBIDDEN = "forbidden"
_LEGACY_COMPATIBILITY_FORBIDDEN = "forbidden"
_STATUS_PLANNED = "planned-not-executed"
_STATUS_COMPLETED = "completed-redacted-phase-receipt"

_NORMAL_DIRECTION = ("webapp_fi", "webapp_ir")
_PROMOTED_DIRECTION = ("webapp_ir", "webapp_fi")
_SUCCESSOR_DIRECTIONS = {
    "witness-promote-ir-v2": _PROMOTED_DIRECTION,
    "witness-restore-fi-writer-v2": _NORMAL_DIRECTION,
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)


class PhysicalFullMatrixV3ExecutionDriverError(ValueError):
    """The V2-only Full-Matrix execution boundary has failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV3ExecutionDriverError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionPhase:
    sequence: int
    name: str
    oracle: str
    destructive: bool
    transport_profile: str


# This private primitive catalog is the authority.  The public projection is
# intentionally detached so mutating a frozen public dataclass cannot alter a
# planned run.
_PHASE_CATALOG: tuple[tuple[int, str, str, bool, str], ...] = (
    (
        1,
        "normal-fi-writer-v2-strict-ack-matrix",
        "normal-fi-writer-v2-strict-ack-oracle-v1",
        True,
        "fi-v2-witness-roundtrip-strict-ack-v1",
    ),
    (
        2,
        "fence-fi-writer-v2",
        "fi-v2-witnessed-writer-fence-oracle-v1",
        True,
        "fi-local-v2-witness-fence-v1",
    ),
    (
        3,
        "recover-ir-through-object-storage-v2",
        "ir-v2-exact-version-recovery-oracle-v1",
        True,
        "ir-private-versioned-object-storage-pull-v2",
    ),
    (
        4,
        "witness-promote-ir-v2",
        "ir-v2-witnessed-promotion-oracle-v1",
        True,
        "ir-local-v2-witness-promotion-v1",
    ),
    (
        5,
        "ir-writer-v2-strict-ack-matrix",
        "ir-writer-v2-strict-ack-oracle-v1",
        True,
        "ir-v2-witness-roundtrip-strict-ack-v1",
    ),
    (
        6,
        "rebuild-fi-through-object-storage-v2",
        "fi-v2-exact-version-standby-rebuild-oracle-v1",
        True,
        "fi-private-versioned-object-storage-pull-v2",
    ),
    (
        7,
        "witness-restore-fi-writer-v2",
        "fi-v2-witnessed-writer-restore-oracle-v1",
        True,
        "fi-local-v2-witness-promotion-v1",
    ),
    (
        8,
        "final-three-site-v2-convergence-oracle",
        "three-site-v2-final-convergence-oracle-v1",
        False,
        "three-site-v2-read-only-evidence-v1",
    ),
)
_PHASES_BY_NAME = {item[1]: item for item in _PHASE_CATALOG}
PHYSICAL_FULL_MATRIX_V3_PHASES = tuple(
    PhysicalFullMatrixV3ExecutionPhase(*item) for item in _PHASE_CATALOG
)
PHYSICAL_FULL_MATRIX_V3_DESTRUCTIVE_PHASES = tuple(
    item[1] for item in _PHASE_CATALOG if item[3]
)


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionBinding:
    """Redacted V2 binding repeated in every plan, request and receipt."""

    campaign_id: str
    release_sha: str
    release_manifest_sha256: str
    readiness_binding_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    source_site: str
    destination_site: str


@dataclass(frozen=True)
class PhysicalFullMatrixV3ReadinessEvidence:
    """Opaque fresh V2 readiness plus its public redacted binding projection."""

    binding: PhysicalFullMatrixV3ExecutionBinding
    readiness: VerifiedPhysicalFullMatrixV2CampaignReadiness


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionConfig:
    """Default-off initial-direction V2 plan policy; no legacy input exists."""

    binding: PhysicalFullMatrixV3ExecutionBinding | None = None
    readiness: VerifiedPhysicalFullMatrixV2CampaignReadiness | None = None
    run_id: UUID | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V3_EXECUTION_DEFAULT_ENABLED
    maximum_oracle_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_V3_MAX_ORACLE_AGE_SECONDS
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionPlan:
    """Process-local redacted V2 plan, never itself an execution permit."""

    canonical_plan: bytes
    plan_sha256: str
    run_id: UUID
    binding: PhysicalFullMatrixV3ExecutionBinding
    phases: tuple[PhysicalFullMatrixV3ExecutionPhase, ...]
    maximum_oracle_age_seconds: int
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V3_PLAN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V3_PLAN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V3_PLAN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _BindingSnapshot:
    campaign_id: str
    release_sha: str
    release_manifest_sha256: str
    readiness_binding_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    source_site: str
    destination_site: str


@dataclass(frozen=True)
class _PhaseSnapshot:
    sequence: int
    name: str
    oracle: str
    destructive: bool
    transport_profile: str


@dataclass(frozen=True)
class _PlanSnapshot:
    canonical_plan: bytes
    plan_sha256: str
    run_id: UUID
    binding: _BindingSnapshot
    phases: tuple[_PhaseSnapshot, ...]
    maximum_oracle_age_seconds: int


@dataclass(frozen=True)
class _PlanProvenance:
    plan_ref: weakref.ReferenceType[PhysicalFullMatrixV3ExecutionPlan]
    snapshot: _PlanSnapshot


_PLAN_PROVENANCE: dict[int, _PlanProvenance] = {}


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionRequest:
    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV3ExecutionPhase
    phase_request_sha256: str
    binding: PhysicalFullMatrixV3ExecutionBinding


@dataclass(frozen=True)
class PhysicalFullMatrixV3PhaseOracle:
    """One injected adapter result; no raw output, endpoint, or secret fits."""

    schema: str
    status: str
    phase: str
    oracle: str
    transport_profile: str
    evidence_sha256: str
    observed_at: datetime
    readiness_evidence: PhysicalFullMatrixV3ReadinessEvidence | None
    direct_fi_to_ir_control: str = _DIRECT_CONTROL_FORBIDDEN
    direct_ir_to_fi_control: str = _DIRECT_CONTROL_FORBIDDEN
    legacy_runner_compatibility: str = _LEGACY_COMPATIBILITY_FORBIDDEN
    successor_readiness_evidence: PhysicalFullMatrixV3ReadinessEvidence | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixV3PhaseClaim:
    run_id: UUID
    plan_sha256: str
    sequence: int
    phase_request_sha256: str
    claim_id: str | None = None
    existing_receipt: bytes | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixV3RunReceipt:
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
    binding: PhysicalFullMatrixV3ExecutionBinding
    successor_binding: PhysicalFullMatrixV3ExecutionBinding | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionResult:
    status: str
    phase: str | None
    receipt: PhysicalFullMatrixV3RunReceipt | None
    next_phase: str | None
    full_matrix_executed: bool = False


class PhysicalFullMatrixV3ExecutionAdapter(Protocol):
    def execute_phase(
        self, *, request: PhysicalFullMatrixV3ExecutionRequest
    ) -> PhysicalFullMatrixV3PhaseOracle: ...


class PhysicalFullMatrixV3ReceiptJournal(Protocol):
    def read_receipts(self, *, run_id: UUID) -> Sequence[bytes]: ...

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
    ) -> PhysicalFullMatrixV3PhaseClaim: ...

    def append_claimed(
        self,
        *,
        claim: PhysicalFullMatrixV3PhaseClaim,
        canonical_receipt: bytes,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV3ExecutionAdapters:
    phase_adapters: Mapping[str, PhysicalFullMatrixV3ExecutionAdapter] | None = None
    receipt_journal: PhysicalFullMatrixV3ReceiptJournal | None = None


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_JSON_INVALID")
        result[key] = value
    return result


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="PHYSICAL_FULL_MATRIX_V3_CLOCK_INVALID").isoformat().replace(
        "+00:00", "Z"
    )


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    result = _utc(parsed, code=code)
    if _render_timestamp(result) != value:
        _fail(code)
    return result


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _id(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _binding(value: object, *, direction: tuple[str, str] | None) -> PhysicalFullMatrixV3ExecutionBinding:
    if type(value) is not PhysicalFullMatrixV3ExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_V3_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V3_CAMPAIGN_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_V3_RELEASE_INVALID")
    for field_name, code in (
        ("release_manifest_sha256", "PHYSICAL_FULL_MATRIX_V3_MANIFEST_INVALID"),
        ("readiness_binding_sha256", "PHYSICAL_FULL_MATRIX_V3_READINESS_BINDING_INVALID"),
        ("route_commitment_sha256", "PHYSICAL_FULL_MATRIX_V3_ROUTE_INVALID"),
        ("four_role_binding_sha256", "PHYSICAL_FULL_MATRIX_V3_FOUR_ROLE_INVALID"),
        ("witnessed_term_proof_sha256", "PHYSICAL_FULL_MATRIX_V3_TERM_INVALID"),
    ):
        _sha256(getattr(value, field_name), code=code)
    if (
        type(value.source_site) is not str
        or type(value.destination_site) is not str
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
        or value.writer_holder_site != value.source_site
        or type(value.writer_epoch) is not int
        or not 1 <= value.writer_epoch <= 2**31 - 1
        or type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_BINDING_INVALID")
    if direction is not None and (value.source_site, value.destination_site) != direction:
        _fail("PHYSICAL_FULL_MATRIX_V3_DIRECTION_INVALID")
    return value


def _binding_snapshot(value: object, *, direction: tuple[str, str] | None) -> _BindingSnapshot:
    checked = _binding(value, direction=direction)
    return _BindingSnapshot(
        campaign_id=checked.campaign_id,
        release_sha=checked.release_sha,
        release_manifest_sha256=checked.release_manifest_sha256,
        readiness_binding_sha256=checked.readiness_binding_sha256,
        route_commitment_sha256=checked.route_commitment_sha256,
        four_role_binding_sha256=checked.four_role_binding_sha256,
        writer_holder_site=checked.writer_holder_site,
        writer_epoch=checked.writer_epoch,
        writer_lease_id=checked.writer_lease_id,
        witnessed_term_proof_sha256=checked.witnessed_term_proof_sha256,
        source_site=checked.source_site,
        destination_site=checked.destination_site,
    )


def _binding_from_snapshot(value: _BindingSnapshot) -> PhysicalFullMatrixV3ExecutionBinding:
    return PhysicalFullMatrixV3ExecutionBinding(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        release_manifest_sha256=value.release_manifest_sha256,
        readiness_binding_sha256=value.readiness_binding_sha256,
        route_commitment_sha256=value.route_commitment_sha256,
        four_role_binding_sha256=value.four_role_binding_sha256,
        writer_holder_site=value.writer_holder_site,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        witnessed_term_proof_sha256=value.witnessed_term_proof_sha256,
        source_site=value.source_site,
        destination_site=value.destination_site,
    )


def _matches_binding(value: object, snapshot: _BindingSnapshot) -> bool:
    return (
        type(value) is PhysicalFullMatrixV3ExecutionBinding
        and value.campaign_id == snapshot.campaign_id
        and value.release_sha == snapshot.release_sha
        and value.release_manifest_sha256 == snapshot.release_manifest_sha256
        and value.readiness_binding_sha256 == snapshot.readiness_binding_sha256
        and value.route_commitment_sha256 == snapshot.route_commitment_sha256
        and value.four_role_binding_sha256 == snapshot.four_role_binding_sha256
        and value.writer_holder_site == snapshot.writer_holder_site
        and value.writer_epoch == snapshot.writer_epoch
        and value.writer_lease_id == snapshot.writer_lease_id
        and value.witnessed_term_proof_sha256 == snapshot.witnessed_term_proof_sha256
        and value.source_site == snapshot.source_site
        and value.destination_site == snapshot.destination_site
    )


def _binding_body(value: _BindingSnapshot) -> dict[str, object]:
    return {
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "release_manifest_sha256": value.release_manifest_sha256,
        "readiness_binding_sha256": value.readiness_binding_sha256,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "writer_holder_site": value.writer_holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        "source_site": value.source_site,
        "destination_site": value.destination_site,
    }


def _v2_readiness_binding_sha(value: _BindingSnapshot) -> str:
    v2_binding = PhysicalFullMatrixV2CampaignBinding(
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        source_site=value.source_site,
        destination_site=value.destination_site,
        route_commitment_sha256=value.route_commitment_sha256,
        four_role_binding_sha256=value.four_role_binding_sha256,
        writer_holder_site=value.writer_holder_site,
        writer_epoch=value.writer_epoch,
        writer_lease_id=value.writer_lease_id,
        witnessed_term_proof_sha256=value.witnessed_term_proof_sha256,
    )
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA,
                "campaign_id": v2_binding.campaign_id,
                "release_sha": v2_binding.release_sha,
                "source_site": v2_binding.source_site,
                "destination_site": v2_binding.destination_site,
                "route_commitment_sha256": v2_binding.route_commitment_sha256,
                "four_role_binding_sha256": v2_binding.four_role_binding_sha256,
                "writer_holder_site": v2_binding.writer_holder_site,
                "writer_epoch": v2_binding.writer_epoch,
                "writer_lease_id": v2_binding.writer_lease_id,
                "witnessed_term_proof_sha256": v2_binding.witnessed_term_proof_sha256,
            },
            code="PHYSICAL_FULL_MATRIX_V3_READINESS_BINDING_INVALID",
        )
    ).hexdigest()


def _validate_readiness(
    value: object,
    *,
    binding: _BindingSnapshot,
    now: datetime | None,
) -> None:
    if type(value) is not VerifiedPhysicalFullMatrixV2CampaignReadiness:
        _fail("PHYSICAL_FULL_MATRIX_V3_READINESS_PROVENANCE_INVALID")
    try:
        report = require_verified_physical_full_matrix_v2_campaign_readiness(value, now=now)
    except PhysicalFullMatrixV2CampaignReadinessError as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_READINESS_PROVENANCE_INVALID"
        ) from exc
    if (
        report.schema != PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_SCHEMA
        or report.status
        != PHYSICAL_FULL_MATRIX_V2_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or report.reason_codes != ()
        or report.observed_slots != PHYSICAL_FULL_MATRIX_V2_REQUIRED_READINESS_SLOTS
        or report.campaign_id != binding.campaign_id
        or report.release_sha != binding.release_sha
        or report.binding_sha256 != binding.readiness_binding_sha256
        or report.binding_sha256 != _v2_readiness_binding_sha(binding)
        or report.external_execution_authorized is not False
        or report.promotion_authorized is not False
        or report.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_READINESS_INCOMPLETE")


def _validate_readiness_evidence(
    value: object,
    *,
    binding: _BindingSnapshot,
    now: datetime,
) -> None:
    if type(value) is not PhysicalFullMatrixV3ReadinessEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_READINESS_REQUIRED")
    observed = _binding_snapshot(value.binding, direction=(binding.source_site, binding.destination_site))
    if observed != binding:
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_READINESS_MISMATCH")
    _validate_readiness(value.readiness, binding=binding, now=now)


def _phase_snapshots() -> tuple[_PhaseSnapshot, ...]:
    return tuple(_PhaseSnapshot(*item) for item in _PHASE_CATALOG)


def _phase_from_snapshot(value: _PhaseSnapshot) -> PhysicalFullMatrixV3ExecutionPhase:
    return PhysicalFullMatrixV3ExecutionPhase(
        sequence=value.sequence,
        name=value.name,
        oracle=value.oracle,
        destructive=value.destructive,
        transport_profile=value.transport_profile,
    )


def _matches_phase(value: object, snapshot: _PhaseSnapshot) -> bool:
    return (
        type(value) is PhysicalFullMatrixV3ExecutionPhase
        and value.sequence == snapshot.sequence
        and value.name == snapshot.name
        and value.oracle == snapshot.oracle
        and value.destructive is snapshot.destructive
        and value.transport_profile == snapshot.transport_profile
    )


def _register_plan(plan: PhysicalFullMatrixV3ExecutionPlan, snapshot: _PlanSnapshot) -> None:
    key = id(plan)

    def discard(reference: weakref.ReferenceType[PhysicalFullMatrixV3ExecutionPlan]) -> None:
        registered = _PLAN_PROVENANCE.get(key)
        if registered is not None and registered.plan_ref is reference:
            _PLAN_PROVENANCE.pop(key, None)

    reference = weakref.ref(plan, discard)
    _PLAN_PROVENANCE[key] = _PlanProvenance(plan_ref=reference, snapshot=snapshot)


def _provenance(value: object) -> _PlanProvenance:
    if type(value) is not PhysicalFullMatrixV3ExecutionPlan or value._capability is not _PLAN_CAPABILITY:
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_UNAUTHORIZED")
    result = _PLAN_PROVENANCE.get(id(value))
    if result is None or result.plan_ref() is not value:
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_UNAUTHORIZED")
    return result


def _legacy(value: object) -> None:
    if value in (None, (), [], ""):
        return
    _fail("PHYSICAL_FULL_MATRIX_V3_LEGACY_RUNNER_REJECTED")


def _maximum_age(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_ORACLE_AGE_SECONDS:
        _fail("PHYSICAL_FULL_MATRIX_V3_MAX_ORACLE_AGE_INVALID")
    return value


def _config(
    value: object,
    *,
    require_enabled: bool,
    readiness_now: datetime | None,
) -> tuple[_BindingSnapshot, UUID, int]:
    if type(value) is not PhysicalFullMatrixV3ExecutionConfig:
        _fail("PHYSICAL_FULL_MATRIX_V3_CONFIG_INVALID")
    if require_enabled:
        if value.enabled is not True:
            _fail("PHYSICAL_FULL_MATRIX_V3_EXECUTION_DISABLED")
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V3_ROOT_RUNTIME_REQUIRED")
    binding = _binding_snapshot(value.binding, direction=_NORMAL_DIRECTION)
    _validate_readiness(value.readiness, binding=binding, now=readiness_now)
    if not isinstance(value.run_id, UUID) or value.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_V3_RUN_ID_INVALID")
    _legacy(value.legacy_runner_artifacts)
    return binding, value.run_id, _maximum_age(value.maximum_oracle_age_seconds)


def _plan_body(snapshot: _PlanSnapshot) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V3_PLAN_SCHEMA,
        "status": _STATUS_PLANNED,
        "run_id": str(snapshot.run_id),
        **_binding_body(snapshot.binding),
        "maximum_oracle_age_seconds": snapshot.maximum_oracle_age_seconds,
        "phases": [
            {
                "sequence": item.sequence,
                "name": item.name,
                "oracle": item.oracle,
                "destructive": item.destructive,
                "transport_profile": item.transport_profile,
                "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
                "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
                "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
            }
            for item in snapshot.phases
        ],
        "materialization_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def _canonical_plan(snapshot: _PlanSnapshot) -> bytes:
    return _canonical(_plan_body(snapshot), code="PHYSICAL_FULL_MATRIX_V3_PLAN_INVALID") + b"\n"


def build_physical_full_matrix_v3_execution_plan(
    *, config: PhysicalFullMatrixV3ExecutionConfig
) -> PhysicalFullMatrixV3ExecutionPlan:
    """Build a root-only V2-only plan without invoking an adapter."""

    binding, run_id, maximum_age = _config(
        config,
        require_enabled=True,
        readiness_now=None,
    )
    provisional = _PlanSnapshot(
        canonical_plan=b"",
        plan_sha256="",
        run_id=run_id,
        binding=binding,
        phases=_phase_snapshots(),
        maximum_oracle_age_seconds=maximum_age,
    )
    canonical = _canonical_plan(provisional)
    snapshot = _PlanSnapshot(
        canonical_plan=canonical,
        plan_sha256=hashlib.sha256(canonical).hexdigest(),
        run_id=run_id,
        binding=binding,
        phases=provisional.phases,
        maximum_oracle_age_seconds=maximum_age,
    )
    result = PhysicalFullMatrixV3ExecutionPlan(
        canonical_plan=snapshot.canonical_plan,
        plan_sha256=snapshot.plan_sha256,
        run_id=snapshot.run_id,
        binding=_binding_from_snapshot(snapshot.binding),
        phases=tuple(_phase_from_snapshot(item) for item in snapshot.phases),
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )
    object.__setattr__(result, "_capability", _PLAN_CAPABILITY)
    _register_plan(result, snapshot)
    return result


def require_physical_full_matrix_v3_execution_plan(
    value: object,
) -> PhysicalFullMatrixV3ExecutionPlan:
    """Recheck process-local plan provenance before a callback sees it."""

    provenance = _provenance(value)
    snapshot = provenance.snapshot
    canonical = _canonical_plan(snapshot)
    if (
        canonical != snapshot.canonical_plan
        or hashlib.sha256(canonical).hexdigest() != snapshot.plan_sha256
        or value.canonical_plan != snapshot.canonical_plan
        or value.plan_sha256 != snapshot.plan_sha256
        or value.run_id != snapshot.run_id
        or not _matches_binding(value.binding, snapshot.binding)
        or type(value.phases) is not tuple
        or len(value.phases) != len(snapshot.phases)
        or any(
            not _matches_phase(item, expected)
            for item, expected in zip(value.phases, snapshot.phases, strict=True)
        )
        or value.maximum_oracle_age_seconds != snapshot.maximum_oracle_age_seconds
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_TAMPERED")
    return value


def _snapshot(value: object) -> _PlanSnapshot:
    provenance = _provenance(value)
    require_physical_full_matrix_v3_execution_plan(value)
    return provenance.snapshot


def _require_snapshot(plan: object, snapshot: _PlanSnapshot) -> None:
    """Fail closed if an untrusted callback mutated the visible plan."""

    if _snapshot(plan) is not snapshot:
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_TAMPERED")


def _request(
    *,
    snapshot: _PlanSnapshot,
    phase: _PhaseSnapshot,
    binding: _BindingSnapshot,
) -> PhysicalFullMatrixV3ExecutionRequest:
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V3_DRIVER_SCHEMA,
        "run_id": str(snapshot.run_id),
        "plan_sha256": snapshot.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        **_binding_body(binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
        "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
    }
    return PhysicalFullMatrixV3ExecutionRequest(
        run_id=snapshot.run_id,
        plan_sha256=snapshot.plan_sha256,
        phase=_phase_from_snapshot(phase),
        phase_request_sha256=hashlib.sha256(
            _canonical(body, code="PHYSICAL_FULL_MATRIX_V3_REQUEST_INVALID")
        ).hexdigest(),
        binding=_binding_from_snapshot(binding),
    )


def _adapter_request_copy(value: PhysicalFullMatrixV3ExecutionRequest) -> PhysicalFullMatrixV3ExecutionRequest:
    return PhysicalFullMatrixV3ExecutionRequest(
        run_id=value.run_id,
        plan_sha256=value.plan_sha256,
        phase=PhysicalFullMatrixV3ExecutionPhase(
            sequence=value.phase.sequence,
            name=value.phase.name,
            oracle=value.phase.oracle,
            destructive=value.phase.destructive,
            transport_profile=value.phase.transport_profile,
        ),
        phase_request_sha256=value.phase_request_sha256,
        binding=_binding_from_snapshot(_binding_snapshot(value.binding, direction=None)),
    )


def _successor(
    value: object,
    *,
    predecessor: _BindingSnapshot,
    phase: _PhaseSnapshot,
    now: datetime | None,
) -> _BindingSnapshot | None:
    direction = _SUCCESSOR_DIRECTIONS.get(phase.name)
    if direction is None:
        if value is not None:
            _fail("PHYSICAL_FULL_MATRIX_V3_UNEXPECTED_SUCCESSOR")
        return None
    if type(value) is not PhysicalFullMatrixV3ReadinessEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V3_SUCCESSOR_REQUIRED")
    successor = _binding_snapshot(value.binding, direction=direction)
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.release_manifest_sha256 != predecessor.release_manifest_sha256
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witnessed_term_proof_sha256
        == predecessor.witnessed_term_proof_sha256
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        # The four IAM identities are campaign-scoped and must not be swapped
        # merely because the writer direction changes.  The route commitment,
        # in contrast, must be a distinct reverse direction commitment.
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_SUCCESSOR_NON_MONOTONIC")
    if now is not None:
        _validate_readiness_evidence(value, binding=successor, now=now)
    return successor


def _validate_oracle(
    *,
    value: object,
    request: PhysicalFullMatrixV3ExecutionRequest,
    phase: _PhaseSnapshot,
    now: datetime,
    maximum_age: int,
) -> _BindingSnapshot | None:
    if type(value) is not PhysicalFullMatrixV3PhaseOracle:
        _fail("PHYSICAL_FULL_MATRIX_V3_ORACLE_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V3_DRIVER_SCHEMA
        or value.status != "oracle-succeeded"
        or value.phase != phase.name
        or value.oracle != phase.oracle
        or value.transport_profile != phase.transport_profile
        or value.direct_fi_to_ir_control != _DIRECT_CONTROL_FORBIDDEN
        or value.direct_ir_to_fi_control != _DIRECT_CONTROL_FORBIDDEN
        or value.legacy_runner_compatibility != _LEGACY_COMPATIBILITY_FORBIDDEN
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_ORACLE_BINDING_MISMATCH")
    _sha256(value.evidence_sha256, code="PHYSICAL_FULL_MATRIX_V3_ORACLE_EVIDENCE_INVALID")
    observed = _utc(value.observed_at, code="PHYSICAL_FULL_MATRIX_V3_ORACLE_CLOCK_INVALID")
    if observed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("PHYSICAL_FULL_MATRIX_V3_ORACLE_FUTURE")
    if now - observed > timedelta(seconds=maximum_age):
        _fail("PHYSICAL_FULL_MATRIX_V3_ORACLE_STALE")
    binding = _binding_snapshot(request.binding, direction=None)
    _validate_readiness_evidence(value.readiness_evidence, binding=binding, now=now)
    return _successor(
        value.successor_readiness_evidence,
        predecessor=binding,
        phase=phase,
        now=now,
    )


def _successor_body(value: _BindingSnapshot | None) -> dict[str, object] | None:
    if value is None:
        return None
    return _binding_body(value)


_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "run_id",
        "plan_sha256",
        "sequence",
        "phase",
        "phase_request_sha256",
        "oracle",
        "oracle_evidence_sha256",
        "previous_receipt_sha256",
        "recorded_at",
        "campaign_id",
        "release_sha",
        "release_manifest_sha256",
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "source_site",
        "destination_site",
        "direct_fi_to_ir_control",
        "direct_ir_to_fi_control",
        "legacy_runner_compatibility",
        "successor_binding",
        "full_matrix_executed",
    }
)
_SUCCESSOR_FIELDS = frozenset(
    {
        "campaign_id",
        "release_sha",
        "release_manifest_sha256",
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "source_site",
        "destination_site",
    }
)


def _receipt_body(
    *,
    request: PhysicalFullMatrixV3ExecutionRequest,
    phase: _PhaseSnapshot,
    oracle: PhysicalFullMatrixV3PhaseOracle,
    successor: _BindingSnapshot | None,
    previous_receipt_sha256: str,
    recorded_at: datetime,
) -> dict[str, object]:
    binding = _binding_snapshot(request.binding, direction=None)
    return {
        "schema": PHYSICAL_FULL_MATRIX_V3_RECEIPT_SCHEMA,
        "status": _STATUS_COMPLETED,
        "run_id": str(request.run_id),
        "plan_sha256": request.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "phase_request_sha256": request.phase_request_sha256,
        "oracle": phase.oracle,
        "oracle_evidence_sha256": oracle.evidence_sha256,
        "previous_receipt_sha256": previous_receipt_sha256,
        "recorded_at": _render_timestamp(recorded_at),
        **_binding_body(binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
        "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
        "successor_binding": _successor_body(successor),
        "full_matrix_executed": False,
    }


def _binding_from_mapping(value: object, *, code: str) -> PhysicalFullMatrixV3ExecutionBinding:
    if type(value) is not dict or set(value) != _SUCCESSOR_FIELDS:
        _fail(code)
    return _binding(
        PhysicalFullMatrixV3ExecutionBinding(
            campaign_id=value["campaign_id"],
            release_sha=value["release_sha"],
            release_manifest_sha256=value["release_manifest_sha256"],
            readiness_binding_sha256=value["readiness_binding_sha256"],
            route_commitment_sha256=value["route_commitment_sha256"],
            four_role_binding_sha256=value["four_role_binding_sha256"],
            writer_holder_site=value["writer_holder_site"],
            writer_epoch=value["writer_epoch"],
            writer_lease_id=value["writer_lease_id"],
            witnessed_term_proof_sha256=value["witnessed_term_proof_sha256"],
            source_site=value["source_site"],
            destination_site=value["destination_site"],
        ),
        direction=None,
    )


def parse_physical_full_matrix_v3_run_receipt(
    value: object,
) -> PhysicalFullMatrixV3RunReceipt:
    """Parse a complete canonical V3 receipt without opening a journal."""

    if type(value) is not bytes or not value.endswith(b"\n"):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: (_fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_JSON_INVALID")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV3ExecutionDriverError):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_ENCODING_INVALID")
    if type(decoded) is not dict or set(decoded) != _RECEIPT_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_FIELDS_INVALID")
    if (
        decoded["schema"] != PHYSICAL_FULL_MATRIX_V3_RECEIPT_SCHEMA
        or decoded["status"] != _STATUS_COMPLETED
        or decoded["direct_fi_to_ir_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["direct_ir_to_fi_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["legacy_runner_compatibility"] != _LEGACY_COMPATIBILITY_FORBIDDEN
        or decoded["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_BINDING_INVALID")
    try:
        run_id = UUID(decoded["run_id"])
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_RUN_ID_INVALID")
    if run_id.int == 0 or str(run_id) != decoded["run_id"]:
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_RUN_ID_INVALID")
    if type(decoded["sequence"]) is not int or decoded["sequence"] not in range(1, len(_PHASE_CATALOG) + 1):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_SEQUENCE_INVALID")
    phase = _PHASES_BY_NAME.get(decoded["phase"])
    if phase is None or phase[0] != decoded["sequence"] or decoded["oracle"] != phase[2]:
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_PHASE_INVALID")
    for name in (
        "plan_sha256",
        "phase_request_sha256",
        "oracle_evidence_sha256",
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "release_manifest_sha256",
    ):
        _sha256(decoded[name], code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_HASH_INVALID")
    _sha256(
        decoded["previous_receipt_sha256"],
        code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_HASH_INVALID",
        permit_zero=True,
    )
    binding = _binding_from_mapping(
        {name: decoded[name] for name in _SUCCESSOR_FIELDS},
        code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_BINDING_INVALID",
    )
    successor_raw = decoded["successor_binding"]
    successor = (
        None
        if successor_raw is None
        else _binding_from_mapping(
            successor_raw,
            code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_SUCCESSOR_INVALID",
        )
    )
    result = PhysicalFullMatrixV3RunReceipt(
        canonical_receipt=_canonical(decoded, code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_INVALID") + b"\n",
        receipt_sha256=hashlib.sha256(value).hexdigest(),
        run_id=run_id,
        plan_sha256=decoded["plan_sha256"],
        sequence=decoded["sequence"],
        phase=decoded["phase"],
        phase_request_sha256=decoded["phase_request_sha256"],
        oracle_evidence_sha256=decoded["oracle_evidence_sha256"],
        previous_receipt_sha256=decoded["previous_receipt_sha256"],
        recorded_at=_timestamp(
            decoded["recorded_at"],
            code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_CLOCK_INVALID",
        ),
        binding=binding,
        successor_binding=successor,
    )
    if result.canonical_receipt != value:
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_NONCANONICAL")
    return result


def _receipt_successor(
    value: PhysicalFullMatrixV3RunReceipt,
    *,
    predecessor: _BindingSnapshot,
    phase: _PhaseSnapshot,
) -> _BindingSnapshot | None:
    expected = _SUCCESSOR_DIRECTIONS.get(phase.name)
    if expected is None:
        if value.successor_binding is not None:
            _fail("PHYSICAL_FULL_MATRIX_V3_UNEXPECTED_SUCCESSOR")
        return None
    successor = _binding_snapshot(value.successor_binding, direction=expected)
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.release_manifest_sha256 != predecessor.release_manifest_sha256
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witnessed_term_proof_sha256
        == predecessor.witnessed_term_proof_sha256
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_SUCCESSOR_NON_MONOTONIC")
    return successor


def _validate_receipt_chain(
    *, snapshot: _PlanSnapshot, raw_receipts: object
) -> tuple[tuple[PhysicalFullMatrixV3RunReceipt, ...], _BindingSnapshot]:
    if not isinstance(raw_receipts, Sequence) or isinstance(raw_receipts, (str, bytes)):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_CHAIN_INVALID")
    if len(raw_receipts) > len(snapshot.phases):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_CHAIN_TOO_LONG")
    receipts = tuple(parse_physical_full_matrix_v3_run_receipt(item) for item in raw_receipts)
    prior = _ZERO_SHA256
    active = snapshot.binding
    for index, receipt in enumerate(receipts):
        phase = snapshot.phases[index]
        request = _request(snapshot=snapshot, phase=phase, binding=active)
        if (
            receipt.run_id != snapshot.run_id
            or receipt.plan_sha256 != snapshot.plan_sha256
            or receipt.sequence != phase.sequence
            or receipt.phase != phase.name
            or receipt.phase_request_sha256 != request.phase_request_sha256
            or receipt.previous_receipt_sha256 != prior
            or not _matches_binding(receipt.binding, active)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_CHAIN_MISMATCH")
        successor = _receipt_successor(receipt, predecessor=active, phase=phase)
        if successor is not None:
            active = successor
        prior = receipt.receipt_sha256
    return receipts, active


def prepare_physical_full_matrix_v3_execution_adapters(
    *,
    plan: PhysicalFullMatrixV3ExecutionPlan,
    adapters: PhysicalFullMatrixV3ExecutionAdapters,
) -> None:
    """Validate all future live adapters before a phase starts."""

    snapshot = _snapshot(plan)
    if type(adapters) is not PhysicalFullMatrixV3ExecutionAdapters:
        _fail("PHYSICAL_FULL_MATRIX_V3_ADAPTERS_INVALID")
    if not isinstance(adapters.phase_adapters, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_ADAPTERS_MISSING")
    if set(adapters.phase_adapters) != {item.name for item in snapshot.phases}:
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_ADAPTER_SET_INVALID")
    for phase in snapshot.phases:
        adapter = adapters.phase_adapters.get(phase.name)
        method = getattr(adapter, "execute_phase", None)
        if not callable(method):
            _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_ADAPTER_INVALID")
    for name in ("read_receipts", "claim_phase", "append_claimed"):
        method = getattr(adapters.receipt_journal, name, None)
        if not callable(method):
            _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_JOURNAL_MISSING")
    if _snapshot(plan) is not snapshot:
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_TAMPERED")


def _claim(value: object, *, request: PhysicalFullMatrixV3ExecutionRequest) -> PhysicalFullMatrixV3PhaseClaim:
    if type(value) is not PhysicalFullMatrixV3PhaseClaim:
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_INVALID")
    if (
        value.run_id != request.run_id
        or value.plan_sha256 != request.plan_sha256
        or value.sequence != request.phase.sequence
        or value.phase_request_sha256 != request.phase_request_sha256
        or (value.claim_id is None and value.existing_receipt is None)
        or (value.claim_id is not None and value.existing_receipt is not None)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_INVALID")
    if value.claim_id is not None:
        _id(value.claim_id, code="PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_INVALID")
    if value.existing_receipt is not None and type(value.existing_receipt) is not bytes:
        _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_INVALID")
    return value


def execute_next_physical_full_matrix_v3_phase(
    *,
    config: PhysicalFullMatrixV3ExecutionConfig,
    plan: PhysicalFullMatrixV3ExecutionPlan,
    adapters: PhysicalFullMatrixV3ExecutionAdapters,
    now: datetime,
) -> PhysicalFullMatrixV3ExecutionResult:
    """Run at most one V2 phase through explicitly injected root adapters."""

    observed_now = _utc(now, code="PHYSICAL_FULL_MATRIX_V3_CLOCK_INVALID")
    configured_binding, configured_run_id, configured_maximum_age = _config(
        config,
        require_enabled=True,
        readiness_now=observed_now,
    )
    snapshot = _snapshot(plan)
    if (
        snapshot.binding != configured_binding
        or snapshot.run_id != configured_run_id
        or snapshot.maximum_oracle_age_seconds != configured_maximum_age
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_PLAN_CONFIG_MISMATCH")
    prepare_physical_full_matrix_v3_execution_adapters(plan=plan, adapters=adapters)
    _require_snapshot(plan, snapshot)
    try:
        raw_receipts = adapters.receipt_journal.read_receipts(run_id=snapshot.run_id)
    except Exception as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_RECEIPT_JOURNAL_READ_FAILED"
        ) from exc
    _require_snapshot(plan, snapshot)
    receipts, active = _validate_receipt_chain(snapshot=snapshot, raw_receipts=raw_receipts)
    _require_snapshot(plan, snapshot)
    if len(receipts) == len(snapshot.phases):
        return PhysicalFullMatrixV3ExecutionResult(
            status="all-phases-already-receipted",
            phase=None,
            receipt=None,
            next_phase=None,
        )
    phase = snapshot.phases[len(receipts)]
    request = _request(snapshot=snapshot, phase=phase, binding=active)
    try:
        raw_claim = adapters.receipt_journal.claim_phase(
            run_id=request.run_id,
            plan_sha256=request.plan_sha256,
            sequence=request.phase.sequence,
            phase_request_sha256=request.phase_request_sha256,
        )
    except Exception as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_FAILED"
        ) from exc
    _require_snapshot(plan, snapshot)
    claim = _claim(raw_claim, request=request)
    if claim.existing_receipt is not None:
        receipt = parse_physical_full_matrix_v3_run_receipt(claim.existing_receipt)
        if (
            receipt.run_id != request.run_id
            or receipt.plan_sha256 != request.plan_sha256
            or receipt.sequence != request.phase.sequence
            or receipt.phase_request_sha256 != request.phase_request_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_RECEIPT_MISMATCH")
        try:
            durable_raw = adapters.receipt_journal.read_receipts(run_id=snapshot.run_id)
        except Exception as exc:
            raise PhysicalFullMatrixV3ExecutionDriverError(
                "PHYSICAL_FULL_MATRIX_V3_RECEIPT_JOURNAL_READ_FAILED"
            ) from exc
        _require_snapshot(plan, snapshot)
        durable_chain, _durable_binding = _validate_receipt_chain(
            snapshot=snapshot,
            raw_receipts=durable_raw,
        )
        if (
            len(durable_chain) != len(receipts) + 1
            or durable_chain[-1].canonical_receipt != receipt.canonical_receipt
        ):
            _fail("PHYSICAL_FULL_MATRIX_V3_PHASE_CLAIM_NOT_DURABLE")
        next_phase = (
            None
            if receipt.sequence == len(snapshot.phases)
            else snapshot.phases[receipt.sequence].name
        )
        return PhysicalFullMatrixV3ExecutionResult(
            status="already-completed-from-append-only-receipt",
            phase=receipt.phase,
            receipt=receipt,
            next_phase=next_phase,
        )
    assert claim.claim_id is not None
    adapter = adapters.phase_adapters[phase.name]
    try:
        oracle = adapter.execute_phase(request=_adapter_request_copy(request))
    except PhysicalFullMatrixV3ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_PHASE_ADAPTER_FAILED"
        ) from exc
    _require_snapshot(plan, snapshot)
    successor = _validate_oracle(
        value=oracle,
        request=request,
        phase=phase,
        now=observed_now,
        maximum_age=snapshot.maximum_oracle_age_seconds,
    )
    previous = _ZERO_SHA256 if not receipts else receipts[-1].receipt_sha256
    canonical = _canonical(
        _receipt_body(
            request=request,
            phase=phase,
            oracle=oracle,
            successor=successor,
            previous_receipt_sha256=previous,
            recorded_at=observed_now,
        ),
        code="PHYSICAL_FULL_MATRIX_V3_RECEIPT_INVALID",
    ) + b"\n"
    try:
        appended = adapters.receipt_journal.append_claimed(
            claim=claim,
            canonical_receipt=canonical,
        )
    except Exception as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_RECEIPT_APPEND_FAILED"
        ) from exc
    _require_snapshot(plan, snapshot)
    if type(appended) is not bytes or appended != canonical:
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_APPEND_MISMATCH")
    receipt = parse_physical_full_matrix_v3_run_receipt(appended)
    try:
        durable_raw = adapters.receipt_journal.read_receipts(run_id=snapshot.run_id)
    except Exception as exc:
        raise PhysicalFullMatrixV3ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V3_RECEIPT_JOURNAL_READ_FAILED"
        ) from exc
    _require_snapshot(plan, snapshot)
    durable_chain, _durable_binding = _validate_receipt_chain(
        snapshot=snapshot,
        raw_receipts=durable_raw,
    )
    if (
        len(durable_chain) != len(receipts) + 1
        or durable_chain[-1].canonical_receipt != receipt.canonical_receipt
    ):
        _fail("PHYSICAL_FULL_MATRIX_V3_RECEIPT_APPEND_NOT_DURABLE")
    next_phase = (
        None if phase.sequence == len(snapshot.phases) else snapshot.phases[phase.sequence].name
    )
    return PhysicalFullMatrixV3ExecutionResult(
        status="completed-redacted-phase-receipt",
        phase=phase.name,
        receipt=receipt,
        next_phase=next_phase,
    )
