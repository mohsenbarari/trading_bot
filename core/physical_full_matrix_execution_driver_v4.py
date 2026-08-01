"""V4 execution boundary for a Witness-mediated V2 Full Matrix.

V4 is a new, isolated generation.  It consumes only opaque
``VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness`` provenance and
its own append-only plan/receipt interfaces.  Historical Gen1 witnessed
readiness is deliberately not imported, adapted, or accepted: the exact Gen2
capability boundary is the only readiness input.  V4 likewise neither imports
nor adapts V1 or V3 plans, receipts, drivers, recovery objects, or
direct-control mechanisms.

This file is deliberately a semantic boundary rather than an operator.  It
never starts a writer, promotes a database, contacts a provider, opens a
network connection, invokes a shell, mutates storage, or accesses a host.
Those effects remain behind injected root-owned phase adapters.  A plan is
non-authorizing; a phase obtains a redacted completion receipt only after the
adapter returns an exact fresh oracle, its separately root-pinned phase owner
verifies an opaque post-effect completion, and the journal makes that receipt
append-only durable.

The phase graph fences precredit: the initial FI -> IR witnessed readiness can
never satisfy phase five.  Phase four must append a fresh IR -> FI successor
readiness with a strictly greater term/epoch, new lease and term proof; phase
five then demands that active reverse binding.  The return phase similarly
requires a new FI -> IR successor readiness rather than replaying either
prior direction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Protocol
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
    require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness,
)


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_MAX_ORACLE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_DESTRUCTIVE_PHASES",
    "PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_EXECUTION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASES",
    "PHYSICAL_FULL_MATRIX_V4_PLAN_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_SCHEMA",
    "PhysicalFullMatrixV4ExecutionAdapter",
    "PhysicalFullMatrixV4ExecutionAdapters",
    "PhysicalFullMatrixV4ExecutionBinding",
    "PhysicalFullMatrixV4CampaignContinuityGate",
    "PhysicalFullMatrixV4ExecutionConfig",
    "PhysicalFullMatrixV4ExecutionDriverError",
    "PhysicalFullMatrixV4ExecutionPhase",
    "PhysicalFullMatrixV4ExecutionPlan",
    "PhysicalFullMatrixV4ExecutionRequest",
    "PhysicalFullMatrixV4ExecutionResult",
    "PhysicalFullMatrixV4EffectStart",
    "PhysicalFullMatrixV4EffectStartAuthority",
    "PhysicalFullMatrixV4EffectStartAnchorProof",
    "PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof",
    "PhysicalFullMatrixV4PhaseClaim",
    "PhysicalFullMatrixV4PhaseOracle",
    "PhysicalFullMatrixV4PhasePostEffectVerifier",
    "PhysicalFullMatrixV4ReadinessEvidence",
    "PhysicalFullMatrixV4ReadinessResolver",
    "PhysicalFullMatrixV4ReceiptJournal",
    "PhysicalFullMatrixV4RunReceipt",
    "PhysicalFullMatrixV4TrustedClock",
    "build_physical_full_matrix_v4_execution_plan",
    "derive_physical_full_matrix_v4_effect_start_identity_sha256",
    "execute_next_physical_full_matrix_v4_phase",
    "parse_physical_full_matrix_v4_run_receipt",
    "prepare_physical_full_matrix_v4_execution_adapters",
    "require_physical_full_matrix_v4_effect_start_authority",
    "require_physical_full_matrix_v4_effect_start_anchor_proof",
    "require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof",
    "require_physical_full_matrix_v4_execution_plan",
)


PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-execution-driver-v1"
)
PHYSICAL_FULL_MATRIX_V4_PLAN_SCHEMA = "gold-trade-physical-full-matrix-v4-plan-v1"
PHYSICAL_FULL_MATRIX_V4_RECEIPT_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-receipt-v1"
)
PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-effect-start-anchor-proof-v1"
)
PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-predecessor-phase-completion-anchor-proof-v1"
)
PHYSICAL_FULL_MATRIX_V4_EXECUTION_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_FULL_MATRIX_V4_MAX_ORACLE_AGE_SECONDS = 120

_MAX_ORACLE_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_MAX_RECEIPT_BYTES = 64 * 1024
_ZERO_SHA256 = "0" * 64
_PLAN_CAPABILITY = object()
_EFFECT_START_AUTHORITY_CAPABILITY = object()
_EFFECT_START_ANCHOR_PROOF_CAPABILITY = object()
_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_CAPABILITY = object()
_DIRECT_CONTROL_FORBIDDEN = "forbidden"
_LEGACY_COMPATIBILITY_FORBIDDEN = "forbidden"
_STATUS_PLANNED = "planned-not-executed"
_STATUS_COMPLETED = "completed-redacted-phase-receipt"
_NORMAL_DIRECTION = ("webapp_fi", "webapp_ir")
_REVERSE_DIRECTION = ("webapp_ir", "webapp_fi")
_SUCCESSOR_DIRECTIONS = {
    "witness-promote-ir-v2": _REVERSE_DIRECTION,
    "witness-restore-fi-writer-v2": _NORMAL_DIRECTION,
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)


class PhysicalFullMatrixV4ExecutionDriverError(ValueError):
    """The V4 Witnessed-readiness execution boundary has failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4ExecutionDriverError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionPhase:
    sequence: int
    name: str
    oracle: str
    destructive: bool
    transport_profile: str


# Keep the eight-phase catalog, but only ACK phases use the explicit portable
# Witness roundtrip profile.  Nothing in these labels starts the operation.
_PHASE_CATALOG: tuple[tuple[int, str, str, bool, str], ...] = (
    (
        1,
        "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix",
        "normal-fi-writer-v2-witness-roundtrip-strict-ack-oracle-v1",
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
        "ir-writer-v2-witness-roundtrip-strict-ack-matrix",
        "ir-writer-v2-witness-roundtrip-strict-ack-oracle-v1",
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
PHYSICAL_FULL_MATRIX_V4_PHASES = tuple(
    PhysicalFullMatrixV4ExecutionPhase(*item) for item in _PHASE_CATALOG
)
PHYSICAL_FULL_MATRIX_V4_DESTRUCTIVE_PHASES = tuple(
    item[1] for item in _PHASE_CATALOG if item[3]
)


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionBinding:
    """Redacted witnessed readiness pins repeated in every V4 semantic input."""

    campaign_id: str
    release_sha: str
    readiness_binding_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    source_site: str
    destination_site: str
    roundtrip_attestation_sha256: str
    roundtrip_configuration_sha256: str
    witness_transition_id: str
    witness_sequence: int


@dataclass(frozen=True)
class PhysicalFullMatrixV4ReadinessEvidence:
    """One opaque Gen2 witnessed readiness plus its public redacted binding.

    Historical Gen1 readiness is intentionally not an alternate input type.
    """

    binding: PhysicalFullMatrixV4ExecutionBinding
    readiness: VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionConfig:
    """Default-off V4 policy; initial Gen2 readiness remains diagnostic only."""

    binding: PhysicalFullMatrixV4ExecutionBinding | None = None
    readiness: VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness | None = None
    run_id: UUID | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_EXECUTION_DEFAULT_ENABLED
    maximum_oracle_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_V4_MAX_ORACLE_AGE_SECONDS
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4ExecutionPlan:
    """Opaque V4 plan; it is not an execution, promotion, or write permit."""

    canonical_plan: bytes
    plan_sha256: str
    run_id: UUID
    binding: PhysicalFullMatrixV4ExecutionBinding
    phases: tuple[PhysicalFullMatrixV4ExecutionPhase, ...]
    maximum_oracle_age_seconds: int
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PLAN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PLAN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PLAN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionRequest:
    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    # Process-local only: never serialized into the request hash or receipt.
    # The root adapter must reflect this exact resolver result in its oracle.
    pre_effect_readiness_evidence: PhysicalFullMatrixV4ReadinessEvidence | None = None
    # This field is deliberately unavailable to callers constructing an
    # ordinary/pre-effect request.  The driver attaches it only to the final
    # private copy delivered to an adapter after the root journal has made the
    # effect-start transition durable.  It never enters any V4 canonical hash
    # or receipt body.
    _effect_start_authority: "PhysicalFullMatrixV4EffectStartAuthority | None" = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    # The root journal attaches this only after it has durably observed the
    # exact externally-attested ``effect-started`` commitment.  Like the
    # authority above, it is process-local and excluded from every request or
    # receipt hash.  It is a correlation/provenance projection, never a
    # writer, promotion, execution, or Full-Matrix permit.
    _effect_start_anchor_proof: "PhysicalFullMatrixV4EffectStartAnchorProof | None" = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    # A root journal attaches this only for a successor phase after it has
    # re-read the durable Witness head and proved that the predecessor's
    # *completion* (not merely its start) is the exact predecessor of this
    # effect-start anchor.  It is opaque process-local evidence correlation,
    # excluded from every request/receipt hash and never an execution permit.
    _predecessor_phase_completion_anchor_proof: (
        "PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof | None"
    ) = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV4PhaseOracle:
    """Redacted injected root-adapter result; no direct-control path is valid.

    ``post_effect_completion`` is deliberately process-local, opaque, and
    omitted from every canonical receipt.  It is not a generic success flag:
    only the independently root-pinned verifier for this exact phase may
    accept it after cross-pinning the private journaled-start correlation and
    owner-specific post-effect evidence.
    """

    schema: str
    status: str
    phase: str
    oracle: str
    transport_profile: str
    effect_key: str
    evidence_sha256: str
    observed_at: datetime
    readiness_evidence: PhysicalFullMatrixV4ReadinessEvidence | None
    direct_fi_to_ir_control: str = _DIRECT_CONTROL_FORBIDDEN
    direct_ir_to_fi_control: str = _DIRECT_CONTROL_FORBIDDEN
    legacy_runner_compatibility: str = _LEGACY_COMPATIBILITY_FORBIDDEN
    successor_readiness_evidence: PhysicalFullMatrixV4ReadinessEvidence | None = None
    post_effect_completion: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV4PhaseClaim:
    run_id: UUID
    plan_sha256: str
    sequence: int
    phase_request_sha256: str
    effect_key: str
    claim_id: str | None = None
    existing_receipt: bytes | None = None
    indeterminate: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4EffectStart:
    """Durable root-journal record made before an effectful adapter call."""

    run_id: UUID
    plan_sha256: str
    sequence: int
    phase_request_sha256: str
    effect_key: str
    claim_id: str


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4EffectStartAuthority:
    """Opaque process-local correlation from a journaled effect start.

    The name intentionally does *not* mean a writer, promotion, traffic, or
    full-matrix permit.  It is minted only by this driver after a validated
    root-journal ``effect-started`` result, and lets a later root-owned phase
    adapter prove that its invocation belongs to that exact in-process start.
    It is non-serializable/non-copyable and has no public constructor.
    """

    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    journaled_effect_start_identity_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        phase: PhysicalFullMatrixV4ExecutionPhase,
        effect_key: str,
        phase_request_sha256: str,
        binding: PhysicalFullMatrixV4ExecutionBinding,
        claim_id: str,
        journaled_effect_start_identity_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _EFFECT_START_AUTHORITY_CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("run_id", run_id),
            ("plan_sha256", plan_sha256),
            ("phase", phase),
            ("effect_key", effect_key),
            ("phase_request_sha256", phase_request_sha256),
            ("binding", binding),
            ("claim_id", claim_id),
            ("journaled_effect_start_identity_sha256", journaled_effect_start_identity_sha256),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("full_matrix_executed", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4EffectStartAnchorProof:
    """Exact process-local projection of an externally-attested V4 start.

    This deliberately carries only non-secret correlation and immutable
    Witness-anchor pins.  In particular it contains no journal path, raw
    local record, provider credential, transport client, host handle, writer
    permit, or promotion/execution authority.  The concrete root journal
    mints it only after its external Witness append has been read back and
    cross-checked against the durable local ``effect-started`` record.

    The proof is useful to a root-owned phase adapter which must place the
    exact start correlation into a later separately-versioned evidence
    grammar.  It is intentionally process-local/nonserializable: a remote
    participant must independently verify the repeated immutable anchor pins
    at the Witness rather than treating this Python object as portable trust.
    """

    schema: str
    run_id: UUID
    plan_sha256: str
    phase: PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    journaled_effect_start_identity_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    anchor_previous_sequence: int
    anchor_previous_head_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    anchor_commitment_sha256: str
    anchor_attestation_sha256: str
    anchor_local_previous_record_sha256: str
    anchor_local_event_sha256: str
    anchor_occurred_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema: str,
        run_id: UUID,
        plan_sha256: str,
        phase: PhysicalFullMatrixV4ExecutionPhase,
        effect_key: str,
        phase_request_sha256: str,
        binding: PhysicalFullMatrixV4ExecutionBinding,
        claim_id: str,
        journaled_effect_start_identity_sha256: str,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        anchor_genesis_sequence: int,
        anchor_genesis_head_sha256: str,
        anchor_previous_sequence: int,
        anchor_previous_head_sha256: str,
        anchor_sequence: int,
        anchor_head_sha256: str,
        anchor_commitment_sha256: str,
        anchor_attestation_sha256: str,
        anchor_local_previous_record_sha256: str,
        anchor_local_event_sha256: str,
        anchor_occurred_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _EFFECT_START_ANCHOR_PROOF_CAPABILITY:
            raise TypeError(
                "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_CONSTRUCTION_FORBIDDEN"
            )
        for name, value in (
            ("schema", schema),
            ("run_id", run_id),
            ("plan_sha256", plan_sha256),
            ("phase", phase),
            ("effect_key", effect_key),
            ("phase_request_sha256", phase_request_sha256),
            ("binding", binding),
            ("claim_id", claim_id),
            (
                "journaled_effect_start_identity_sha256",
                journaled_effect_start_identity_sha256,
            ),
            ("journal_binding_sha256", journal_binding_sha256),
            ("baseline_plan_binding_sha256", baseline_plan_binding_sha256),
            ("anchor_genesis_sequence", anchor_genesis_sequence),
            ("anchor_genesis_head_sha256", anchor_genesis_head_sha256),
            ("anchor_previous_sequence", anchor_previous_sequence),
            ("anchor_previous_head_sha256", anchor_previous_head_sha256),
            ("anchor_sequence", anchor_sequence),
            ("anchor_head_sha256", anchor_head_sha256),
            ("anchor_commitment_sha256", anchor_commitment_sha256),
            ("anchor_attestation_sha256", anchor_attestation_sha256),
            (
                "anchor_local_previous_record_sha256",
                anchor_local_previous_record_sha256,
            ),
            ("anchor_local_event_sha256", anchor_local_event_sha256),
            ("anchor_occurred_at", anchor_occurred_at),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("full_matrix_executed", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
    """Typed bridge from one durable completion to the next effect start.

    A root journal mints this only after it has re-read the current external
    Witness head, validated durable local records for the completed
    predecessor, and cross-checked that this successor's start commitment
    names that completion anchor as its immediate predecessor.  It carries
    redacted correlation pins only: no raw receipt, journal path, host,
    credential, client, writer, promotion, execution, or Full-Matrix permit.

    The object is deliberately process-local/nonserializable.  Its durable
    source is re-derived by the journal on a restart; the Python capability is
    never re-created from a historical in-memory cache.
    """

    schema: str
    run_id: UUID
    plan_sha256: str
    predecessor_phase_name: str
    predecessor_phase_sequence: int
    predecessor_effect_key: str
    predecessor_phase_request_sha256: str
    predecessor_claim_id: str
    predecessor_effect_start_identity_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    predecessor_effect_start_anchor_previous_sequence: int
    predecessor_effect_start_anchor_previous_head_sha256: str
    predecessor_effect_start_anchor_sequence: int
    predecessor_effect_start_anchor_head_sha256: str
    predecessor_effect_start_anchor_commitment_sha256: str
    predecessor_effect_start_anchor_attestation_sha256: str
    predecessor_effect_start_anchor_local_previous_record_sha256: str
    predecessor_effect_start_anchor_local_event_sha256: str
    predecessor_effect_started_at: datetime
    predecessor_completion_receipt_sha256: str
    predecessor_completion_anchor_previous_sequence: int
    predecessor_completion_anchor_previous_head_sha256: str
    predecessor_completion_anchor_sequence: int
    predecessor_completion_anchor_head_sha256: str
    predecessor_completion_anchor_commitment_sha256: str
    predecessor_completion_anchor_attestation_sha256: str
    predecessor_completion_anchor_local_previous_record_sha256: str
    predecessor_completion_anchor_local_event_sha256: str
    predecessor_completed_at: datetime
    successor_phase_name: str
    successor_phase_sequence: int
    successor_effect_key: str
    successor_phase_request_sha256: str
    successor_claim_id: str
    successor_effect_start_identity_sha256: str
    successor_effect_start_anchor_previous_sequence: int
    successor_effect_start_anchor_previous_head_sha256: str
    successor_effect_start_anchor_sequence: int
    successor_effect_start_anchor_head_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema: str,
        run_id: UUID,
        plan_sha256: str,
        predecessor_phase_name: str,
        predecessor_phase_sequence: int,
        predecessor_effect_key: str,
        predecessor_phase_request_sha256: str,
        predecessor_claim_id: str,
        predecessor_effect_start_identity_sha256: str,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        anchor_genesis_sequence: int,
        anchor_genesis_head_sha256: str,
        predecessor_effect_start_anchor_previous_sequence: int,
        predecessor_effect_start_anchor_previous_head_sha256: str,
        predecessor_effect_start_anchor_sequence: int,
        predecessor_effect_start_anchor_head_sha256: str,
        predecessor_effect_start_anchor_commitment_sha256: str,
        predecessor_effect_start_anchor_attestation_sha256: str,
        predecessor_effect_start_anchor_local_previous_record_sha256: str,
        predecessor_effect_start_anchor_local_event_sha256: str,
        predecessor_effect_started_at: datetime,
        predecessor_completion_receipt_sha256: str,
        predecessor_completion_anchor_previous_sequence: int,
        predecessor_completion_anchor_previous_head_sha256: str,
        predecessor_completion_anchor_sequence: int,
        predecessor_completion_anchor_head_sha256: str,
        predecessor_completion_anchor_commitment_sha256: str,
        predecessor_completion_anchor_attestation_sha256: str,
        predecessor_completion_anchor_local_previous_record_sha256: str,
        predecessor_completion_anchor_local_event_sha256: str,
        predecessor_completed_at: datetime,
        successor_phase_name: str,
        successor_phase_sequence: int,
        successor_effect_key: str,
        successor_phase_request_sha256: str,
        successor_claim_id: str,
        successor_effect_start_identity_sha256: str,
        successor_effect_start_anchor_previous_sequence: int,
        successor_effect_start_anchor_previous_head_sha256: str,
        successor_effect_start_anchor_sequence: int,
        successor_effect_start_anchor_head_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_CAPABILITY:
            raise TypeError(
                "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_CONSTRUCTION_FORBIDDEN"
            )
        for name, value in (
            ("schema", schema),
            ("run_id", run_id),
            ("plan_sha256", plan_sha256),
            ("predecessor_phase_name", predecessor_phase_name),
            ("predecessor_phase_sequence", predecessor_phase_sequence),
            ("predecessor_effect_key", predecessor_effect_key),
            ("predecessor_phase_request_sha256", predecessor_phase_request_sha256),
            ("predecessor_claim_id", predecessor_claim_id),
            (
                "predecessor_effect_start_identity_sha256",
                predecessor_effect_start_identity_sha256,
            ),
            ("journal_binding_sha256", journal_binding_sha256),
            ("baseline_plan_binding_sha256", baseline_plan_binding_sha256),
            ("anchor_genesis_sequence", anchor_genesis_sequence),
            ("anchor_genesis_head_sha256", anchor_genesis_head_sha256),
            (
                "predecessor_effect_start_anchor_previous_sequence",
                predecessor_effect_start_anchor_previous_sequence,
            ),
            (
                "predecessor_effect_start_anchor_previous_head_sha256",
                predecessor_effect_start_anchor_previous_head_sha256,
            ),
            (
                "predecessor_effect_start_anchor_sequence",
                predecessor_effect_start_anchor_sequence,
            ),
            (
                "predecessor_effect_start_anchor_head_sha256",
                predecessor_effect_start_anchor_head_sha256,
            ),
            (
                "predecessor_effect_start_anchor_commitment_sha256",
                predecessor_effect_start_anchor_commitment_sha256,
            ),
            (
                "predecessor_effect_start_anchor_attestation_sha256",
                predecessor_effect_start_anchor_attestation_sha256,
            ),
            (
                "predecessor_effect_start_anchor_local_previous_record_sha256",
                predecessor_effect_start_anchor_local_previous_record_sha256,
            ),
            (
                "predecessor_effect_start_anchor_local_event_sha256",
                predecessor_effect_start_anchor_local_event_sha256,
            ),
            ("predecessor_effect_started_at", predecessor_effect_started_at),
            ("predecessor_completion_receipt_sha256", predecessor_completion_receipt_sha256),
            (
                "predecessor_completion_anchor_previous_sequence",
                predecessor_completion_anchor_previous_sequence,
            ),
            (
                "predecessor_completion_anchor_previous_head_sha256",
                predecessor_completion_anchor_previous_head_sha256,
            ),
            (
                "predecessor_completion_anchor_sequence",
                predecessor_completion_anchor_sequence,
            ),
            (
                "predecessor_completion_anchor_head_sha256",
                predecessor_completion_anchor_head_sha256,
            ),
            (
                "predecessor_completion_anchor_commitment_sha256",
                predecessor_completion_anchor_commitment_sha256,
            ),
            (
                "predecessor_completion_anchor_attestation_sha256",
                predecessor_completion_anchor_attestation_sha256,
            ),
            (
                "predecessor_completion_anchor_local_previous_record_sha256",
                predecessor_completion_anchor_local_previous_record_sha256,
            ),
            (
                "predecessor_completion_anchor_local_event_sha256",
                predecessor_completion_anchor_local_event_sha256,
            ),
            ("predecessor_completed_at", predecessor_completed_at),
            ("successor_phase_name", successor_phase_name),
            ("successor_phase_sequence", successor_phase_sequence),
            ("successor_effect_key", successor_effect_key),
            ("successor_phase_request_sha256", successor_phase_request_sha256),
            ("successor_claim_id", successor_claim_id),
            (
                "successor_effect_start_identity_sha256",
                successor_effect_start_identity_sha256,
            ),
            (
                "successor_effect_start_anchor_previous_sequence",
                successor_effect_start_anchor_previous_sequence,
            ),
            (
                "successor_effect_start_anchor_previous_head_sha256",
                successor_effect_start_anchor_previous_head_sha256,
            ),
            (
                "successor_effect_start_anchor_sequence",
                successor_effect_start_anchor_sequence,
            ),
            (
                "successor_effect_start_anchor_head_sha256",
                successor_effect_start_anchor_head_sha256,
            ),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("full_matrix_executed", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_COPY_FORBIDDEN"
        )

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_COPY_FORBIDDEN"
        )


@dataclass(frozen=True)
class PhysicalFullMatrixV4RunReceipt:
    canonical_receipt: bytes
    receipt_sha256: str
    run_id: UUID
    plan_sha256: str
    sequence: int
    phase: str
    effect_key: str
    phase_request_sha256: str
    oracle_evidence_sha256: str
    previous_receipt_sha256: str
    recorded_at: datetime
    binding: PhysicalFullMatrixV4ExecutionBinding
    successor_binding: PhysicalFullMatrixV4ExecutionBinding | None = None
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionResult:
    status: str
    phase: str | None
    receipt: PhysicalFullMatrixV4RunReceipt | None
    next_phase: str | None
    full_matrix_executed: bool = False


class PhysicalFullMatrixV4ExecutionAdapter(Protocol):
    """Root-owned effect adapter; V4 never implements the effect itself.

    The driver gives this callback a private request copy only after the root
    journal has durably recorded ``effect-started``.  An adapter that needs to
    correlate owner-specific evidence may call
    :func:`require_physical_full_matrix_v4_effect_start_authority` on that
    received request and
    :func:`require_physical_full_matrix_v4_effect_start_anchor_proof` for the
    exact externally-attested anchor pins.  Neither returned object is an
    execution, promotion, writer, or full-matrix permit.
    """

    def execute_phase(
        self, *, request: PhysicalFullMatrixV4ExecutionRequest
    ) -> PhysicalFullMatrixV4PhaseOracle: ...


class PhysicalFullMatrixV4PhasePostEffectVerifier(Protocol):
    """One separately root-pinned owner verifier for exactly one V4 phase.

    The adapter may produce an oracle, but it may not self-attest that its
    external effect completed.  The root installs one distinct verifier per
    phase.  That verifier must accept only its own opaque, process-local
    completion capability and bind it to the exact private adapter request,
    effect-start authority/anchor, public oracle evidence hash, and fresh
    root-clock observation.  Returning ``None`` means the capability was
    accepted; any exception fails the phase closed.
    """

    phase_name: str
    phase_sequence: int
    oracle: str
    transport_profile: str

    def require_post_effect_completion(
        self,
        *,
        request: PhysicalFullMatrixV4ExecutionRequest,
        effect_start_authority: PhysicalFullMatrixV4EffectStartAuthority,
        effect_start_anchor_proof: PhysicalFullMatrixV4EffectStartAnchorProof,
        oracle: PhysicalFullMatrixV4PhaseOracle,
        completion: object,
        observed_at: datetime,
        now: datetime,
        maximum_oracle_age_seconds: int,
    ) -> None: ...


class PhysicalFullMatrixV4ReadinessResolver(Protocol):
    """Root-owned resolver for the active, opaque Witnessed-V2 readiness.

    Receipts intentionally retain only redacted pins.  A restart must obtain
    a fresh process-local capability again instead of treating a historical
    successor binding as permission to invoke the next adapter.
    """

    def resolve_readiness(
        self, *, binding: PhysicalFullMatrixV4ExecutionBinding
    ) -> PhysicalFullMatrixV4ReadinessEvidence: ...


class PhysicalFullMatrixV4TrustedClock(Protocol):
    """Root-owned monotonic observation boundary used around callbacks."""

    def now_utc(self) -> datetime: ...


class PhysicalFullMatrixV4CampaignContinuityGate(Protocol):
    """Root-owned durable anchor required to resume a receipted campaign.

    V4 intentionally does not regard its parsed raw receipt bytes as proof of
    authority.  A live implementation must bind this check to the journal's
    durable/signed campaign state before a restarted process invokes a phase.
    """

    def verify_campaign_continuity(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        completed_sequence: int,
        active_binding: PhysicalFullMatrixV4ExecutionBinding,
    ) -> None: ...


class PhysicalFullMatrixV4ReceiptJournal(Protocol):
    """Injected root journal with a durable pre-effect state transition.

    The journal, not a raw receipt byte string, owns the durable authority to
    decide whether an effect is complete, newly started, or indeterminate.
    """

    def read_receipts(self, *, run_id: UUID) -> Sequence[bytes]: ...

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
        effect_key: str,
    ) -> PhysicalFullMatrixV4PhaseClaim: ...

    def mark_effect_started(
        self,
        *,
        claim: PhysicalFullMatrixV4PhaseClaim,
        effect_key: str,
    ) -> PhysicalFullMatrixV4EffectStart: ...

    def project_effect_start_anchor_proof(
        self,
        *,
        effect_start: PhysicalFullMatrixV4EffectStart,
        request: PhysicalFullMatrixV4ExecutionRequest,
    ) -> PhysicalFullMatrixV4EffectStartAnchorProof: ...

    def project_predecessor_phase_completion_anchor_proof(
        self,
        *,
        effect_start: PhysicalFullMatrixV4EffectStart,
        request: PhysicalFullMatrixV4ExecutionRequest,
    ) -> PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof: ...

    def append_started(
        self,
        *,
        effect_start: PhysicalFullMatrixV4EffectStart,
        canonical_receipt: bytes,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4ExecutionAdapters:
    phase_adapters: Mapping[str, PhysicalFullMatrixV4ExecutionAdapter] | None = None
    receipt_journal: PhysicalFullMatrixV4ReceiptJournal | None = None
    readiness_resolver: PhysicalFullMatrixV4ReadinessResolver | None = None
    trusted_clock: PhysicalFullMatrixV4TrustedClock | None = None
    campaign_continuity_gate: PhysicalFullMatrixV4CampaignContinuityGate | None = None
    # This is intentionally not validated by ``prepare_*`` because that
    # helper is used by the default-off, non-operational planning/composition
    # path.  ``execute_next_*`` requires the exact eight-key map before it
    # claims or starts any effect.
    phase_post_effect_verifiers: (
        Mapping[str, PhysicalFullMatrixV4PhasePostEffectVerifier] | None
    ) = None


@dataclass(frozen=True)
class _BindingSnapshot:
    campaign_id: str
    release_sha: str
    readiness_binding_sha256: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    source_site: str
    destination_site: str
    roundtrip_attestation_sha256: str
    roundtrip_configuration_sha256: str
    witness_transition_id: str
    witness_sequence: int


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
class _PlanState:
    snapshot: _PlanSnapshot


@dataclass(frozen=True)
class _EffectStartAuthoritySnapshot:
    """Exact canonical-request facts allowed to reach one phase adapter."""

    run_id: UUID
    plan_sha256: str
    phase: _PhaseSnapshot
    effect_key: str
    phase_request_sha256: str
    binding: _BindingSnapshot
    claim_id: str
    journaled_effect_start_identity_sha256: str


@dataclass(frozen=True)
class _EffectStartAuthorityState:
    """Private provenance retained only while the process owns the handle."""

    snapshot: _EffectStartAuthoritySnapshot
    claim: PhysicalFullMatrixV4PhaseClaim
    effect_start: PhysicalFullMatrixV4EffectStart


@dataclass(frozen=True)
class _EffectStartAnchorProofSnapshot:
    """Exact request/start and immutable-anchor facts for one proof."""

    request: _EffectStartAuthoritySnapshot
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    anchor_previous_sequence: int
    anchor_previous_head_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    anchor_commitment_sha256: str
    anchor_attestation_sha256: str
    anchor_local_previous_record_sha256: str
    anchor_local_event_sha256: str
    anchor_occurred_at: datetime


@dataclass(frozen=True)
class _EffectStartAnchorProofState:
    """Private root-journal provenance retained for one live proof object."""

    snapshot: _EffectStartAnchorProofSnapshot
    effect_start: PhysicalFullMatrixV4EffectStart


@dataclass(frozen=True)
class _PredecessorPhaseCompletionAnchorProofState:
    """Exact, non-public facts for one durable predecessor bridge."""

    request: _EffectStartAuthoritySnapshot
    public_values: tuple[object, ...]


_PLAN_STATES: WeakKeyDictionary[PhysicalFullMatrixV4ExecutionPlan, _PlanState] = (
    WeakKeyDictionary()
)
_EFFECT_START_AUTHORITY_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4EffectStartAuthority, _EffectStartAuthorityState
] = WeakKeyDictionary()
_EFFECT_START_ANCHOR_PROOF_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4EffectStartAnchorProof, _EffectStartAnchorProofState
] = WeakKeyDictionary()
_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof,
    _PredecessorPhaseCompletionAnchorProofState,
] = WeakKeyDictionary()
_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_PUBLIC_FIELDS = tuple(
    name
    for name in PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof.__dataclass_fields__
    if name != "_capability"
)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JSON_INVALID")
        result[key] = value
    return result


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return _utc(value, code="PHYSICAL_FULL_MATRIX_V4_CLOCK_INVALID").isoformat().replace(
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


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _binding(
    value: object,
    *,
    direction: tuple[str, str] | None,
) -> PhysicalFullMatrixV4ExecutionBinding:
    if type(value) is not PhysicalFullMatrixV4ExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_V4_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_CAMPAIGN_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_RELEASE_INVALID")
    for field_name in (
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "roundtrip_attestation_sha256",
        "roundtrip_configuration_sha256",
    ):
        _sha256(getattr(value, field_name), code="PHYSICAL_FULL_MATRIX_V4_BINDING_INVALID")
    if (
        value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
        or value.writer_holder_site != value.source_site
        or type(value.writer_epoch) is not int
        or not 1 <= value.writer_epoch <= 2**31 - 1
        or type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
        or type(value.witness_transition_id) is not str
        or not value.witness_transition_id
        or type(value.witness_sequence) is not int
        or value.witness_sequence < 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_BINDING_INVALID")
    if direction is not None and (value.source_site, value.destination_site) != direction:
        _fail("PHYSICAL_FULL_MATRIX_V4_DIRECTION_INVALID")
    return value


def _snapshot_binding(
    value: object,
    *,
    direction: tuple[str, str] | None,
) -> _BindingSnapshot:
    binding = _binding(value, direction=direction)
    return _BindingSnapshot(
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        readiness_binding_sha256=binding.readiness_binding_sha256,
        route_commitment_sha256=binding.route_commitment_sha256,
        four_role_binding_sha256=binding.four_role_binding_sha256,
        writer_holder_site=binding.writer_holder_site,
        writer_epoch=binding.writer_epoch,
        writer_lease_id=binding.writer_lease_id,
        witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        roundtrip_attestation_sha256=binding.roundtrip_attestation_sha256,
        roundtrip_configuration_sha256=binding.roundtrip_configuration_sha256,
        witness_transition_id=binding.witness_transition_id,
        witness_sequence=binding.witness_sequence,
    )


def _binding_from_snapshot(value: _BindingSnapshot) -> PhysicalFullMatrixV4ExecutionBinding:
    return PhysicalFullMatrixV4ExecutionBinding(**value.__dict__)


def _binding_body(value: _BindingSnapshot) -> dict[str, object]:
    return dict(value.__dict__)


def _matches_binding(value: object, expected: _BindingSnapshot) -> bool:
    return type(value) is PhysicalFullMatrixV4ExecutionBinding and _snapshot_binding(
        value, direction=None
    ) == expected


def _legacy(value: object) -> None:
    if value is None:
        return
    if type(value) is tuple and not value:
        return
    if type(value) is list and not value:
        return
    if type(value) is str and not value:
        return
    _fail("PHYSICAL_FULL_MATRIX_V4_LEGACY_RUNNER_REJECTED")


def _maximum_age(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_ORACLE_AGE_SECONDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_MAX_ORACLE_AGE_INVALID")
    return value


def _validate_readiness(
    value: object,
    *,
    binding: _BindingSnapshot,
    now: datetime | None,
) -> None:
    # An exact-type boundary is intentional.  It rejects historical Gen1
    # readiness, subclasses, reconstructed reports, and every future type
    # until a separately reviewed V4 migration is made; no adapter/fallback
    # path exists here.
    if type(value) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
        _fail("PHYSICAL_FULL_MATRIX_V4_READINESS_PROVENANCE_INVALID")
    try:
        report = require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
            value,
            now=now,
        )
    except PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_READINESS_PROVENANCE_INVALID"
        ) from exc
    if (
        report.schema != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA
        or report.status
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or report.reason_codes != ()
        or report.observed_slots
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS
        or report.campaign_id != binding.campaign_id
        or report.release_sha != binding.release_sha
        or report.binding_sha256 != binding.readiness_binding_sha256
        or report.external_execution_authorized is not False
        or report.promotion_authorized is not False
        or report.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_READINESS_INCOMPLETE")


def _validate_readiness_evidence(
    value: object,
    *,
    binding: _BindingSnapshot,
    now: datetime,
) -> None:
    if type(value) is not PhysicalFullMatrixV4ReadinessEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_READINESS_REQUIRED")
    observed = _snapshot_binding(
        value.binding,
        direction=(binding.source_site, binding.destination_site),
    )
    if observed != binding:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_READINESS_MISMATCH")
    _validate_readiness(value.readiness, binding=binding, now=now)


def _static_config(
    value: object,
    *,
    require_enabled: bool,
) -> tuple[_BindingSnapshot, UUID, int]:
    if type(value) is not PhysicalFullMatrixV4ExecutionConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_EXECUTION_DISABLED")
    binding = _snapshot_binding(value.binding, direction=_NORMAL_DIRECTION)
    if not isinstance(value.run_id, UUID) or value.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_RUN_ID_INVALID")
    _legacy(value.legacy_runner_artifacts)
    return binding, value.run_id, _maximum_age(value.maximum_oracle_age_seconds)


def _config(
    value: object,
    *,
    require_enabled: bool,
    readiness_now: datetime | None,
) -> tuple[_BindingSnapshot, UUID, int]:
    """Validate initial normal readiness for plan construction only."""

    binding, run_id, maximum_age = _static_config(
        value,
        require_enabled=require_enabled,
    )
    assert type(value) is PhysicalFullMatrixV4ExecutionConfig
    _validate_readiness(value.readiness, binding=binding, now=readiness_now)
    return binding, run_id, maximum_age


def _revalidate_static_config_against_snapshot(
    *,
    config: PhysicalFullMatrixV4ExecutionConfig,
    snapshot: _PlanSnapshot,
) -> None:
    """Recheck enabled/static pins without replaying a retired writer term."""

    try:
        binding, run_id, maximum_age = _static_config(
            config,
            require_enabled=True,
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_CONFIG_REVALIDATION_FAILED"
        ) from exc
    if (
        binding != snapshot.binding
        or run_id != snapshot.run_id
        or maximum_age != snapshot.maximum_oracle_age_seconds
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_CONFIG_CHANGED_DURING_PHASE")


def _revalidate_initial_readiness_if_pretransition(
    *,
    config: PhysicalFullMatrixV4ExecutionConfig,
    snapshot: _PlanSnapshot,
    phase: _PhaseSnapshot,
    now: datetime,
) -> None:
    """Use FI-normal readiness only before the first writer transition."""

    if phase.sequence > 4:
        return
    try:
        _validate_readiness(config.readiness, binding=snapshot.binding, now=now)
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_INITIAL_READINESS_REVALIDATION_FAILED"
        ) from exc


def _is_transition_phase(phase: _PhaseSnapshot) -> bool:
    return phase.name in _SUCCESSOR_DIRECTIONS


def _phase_snapshots() -> tuple[_PhaseSnapshot, ...]:
    return tuple(_PhaseSnapshot(*item) for item in _PHASE_CATALOG)


def _phase_from_snapshot(value: _PhaseSnapshot) -> PhysicalFullMatrixV4ExecutionPhase:
    return PhysicalFullMatrixV4ExecutionPhase(
        sequence=value.sequence,
        name=value.name,
        oracle=value.oracle,
        destructive=value.destructive,
        transport_profile=value.transport_profile,
    )


def _matches_phase(value: object, expected: _PhaseSnapshot) -> bool:
    return (
        type(value) is PhysicalFullMatrixV4ExecutionPhase
        and value.sequence == expected.sequence
        and value.name == expected.name
        and value.oracle == expected.oracle
        and value.destructive is expected.destructive
        and value.transport_profile == expected.transport_profile
    )


def _plan_body(snapshot: _PlanSnapshot) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PLAN_SCHEMA,
        "status": _STATUS_PLANNED,
        "run_id": str(snapshot.run_id),
        **_binding_body(snapshot.binding),
        "maximum_oracle_age_seconds": snapshot.maximum_oracle_age_seconds,
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
            for phase in snapshot.phases
        ],
        "materialization_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }


def _canonical_plan(snapshot: _PlanSnapshot) -> bytes:
    return _canonical(_plan_body(snapshot), code="PHYSICAL_FULL_MATRIX_V4_PLAN_INVALID") + b"\n"


def build_physical_full_matrix_v4_execution_plan(
    *, config: PhysicalFullMatrixV4ExecutionConfig
) -> PhysicalFullMatrixV4ExecutionPlan:
    """Build a default-off V4 plan without invoking an adapter or effect."""

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
    result = PhysicalFullMatrixV4ExecutionPlan(
        canonical_plan=snapshot.canonical_plan,
        plan_sha256=snapshot.plan_sha256,
        run_id=snapshot.run_id,
        binding=_binding_from_snapshot(snapshot.binding),
        phases=tuple(_phase_from_snapshot(phase) for phase in snapshot.phases),
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )
    object.__setattr__(result, "_capability", _PLAN_CAPABILITY)
    _PLAN_STATES[result] = _PlanState(snapshot=snapshot)
    return result


def require_physical_full_matrix_v4_execution_plan(
    value: object,
) -> PhysicalFullMatrixV4ExecutionPlan:
    """Confirm process-local plan provenance before a callback sees it."""

    if (
        type(value) is not PhysicalFullMatrixV4ExecutionPlan
        or value._capability is not _PLAN_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_UNAUTHORIZED")
    state = _PLAN_STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_UNAUTHORIZED")
    snapshot = state.snapshot
    if (
        _canonical_plan(snapshot) != snapshot.canonical_plan
        or hashlib.sha256(snapshot.canonical_plan).hexdigest() != snapshot.plan_sha256
        or value.canonical_plan != snapshot.canonical_plan
        or value.plan_sha256 != snapshot.plan_sha256
        or value.run_id != snapshot.run_id
        or not _matches_binding(value.binding, snapshot.binding)
        or type(value.phases) is not tuple
        or len(value.phases) != len(snapshot.phases)
        or any(
            not _matches_phase(actual, expected)
            for actual, expected in zip(value.phases, snapshot.phases, strict=True)
        )
        or value.maximum_oracle_age_seconds != snapshot.maximum_oracle_age_seconds
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_TAMPERED")
    return value


def _snapshot(plan: object) -> _PlanSnapshot:
    require_physical_full_matrix_v4_execution_plan(plan)
    state = _PLAN_STATES.get(plan)
    assert state is not None
    return state.snapshot


def _request(
    *,
    snapshot: _PlanSnapshot,
    phase: _PhaseSnapshot,
    binding: _BindingSnapshot,
    pre_effect_readiness_evidence: PhysicalFullMatrixV4ReadinessEvidence | None = None,
) -> PhysicalFullMatrixV4ExecutionRequest:
    effect_body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
        "purpose": "root-journal-effect-key-v1",
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
    effect_key = hashlib.sha256(
        _canonical(effect_body, code="PHYSICAL_FULL_MATRIX_V4_EFFECT_KEY_INVALID")
    ).hexdigest()
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
        "run_id": str(snapshot.run_id),
        "plan_sha256": snapshot.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        "effect_key": effect_key,
        **_binding_body(binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
        "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
    }
    return PhysicalFullMatrixV4ExecutionRequest(
        run_id=snapshot.run_id,
        plan_sha256=snapshot.plan_sha256,
        phase=_phase_from_snapshot(phase),
        effect_key=effect_key,
        phase_request_sha256=hashlib.sha256(
            _canonical(body, code="PHYSICAL_FULL_MATRIX_V4_REQUEST_INVALID")
        ).hexdigest(),
        binding=_binding_from_snapshot(binding),
        pre_effect_readiness_evidence=pre_effect_readiness_evidence,
    )


def _request_copy(
    value: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4ExecutionRequest:
    return PhysicalFullMatrixV4ExecutionRequest(
        run_id=value.run_id,
        plan_sha256=value.plan_sha256,
        phase=PhysicalFullMatrixV4ExecutionPhase(
            sequence=value.phase.sequence,
            name=value.phase.name,
            oracle=value.phase.oracle,
            destructive=value.phase.destructive,
            transport_profile=value.phase.transport_profile,
        ),
        effect_key=value.effect_key,
        phase_request_sha256=value.phase_request_sha256,
        binding=_binding_from_snapshot(_snapshot_binding(value.binding, direction=None)),
        pre_effect_readiness_evidence=value.pre_effect_readiness_evidence,
    )


def _request_effect_start_authority_snapshot(
    value: object,
    *,
    claim_id: str,
    journaled_effect_start_identity_sha256: str,
) -> _EffectStartAuthoritySnapshot:
    """Extract only the canonical request facts a journaled start can bind."""

    if type(value) is not PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID")
    if type(value.run_id) is not UUID or value.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID")
    _sha256(
        value.plan_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID",
    )
    _sha256(
        value.effect_key,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID",
    )
    _sha256(
        value.phase_request_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID",
    )
    _sha256(
        journaled_effect_start_identity_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID",
    )
    checked_claim = _identifier(
        claim_id,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID",
    )
    phase_matches = tuple(
        item for item in _phase_snapshots() if _matches_phase(value.phase, item)
    )
    if len(phase_matches) != 1:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID")
    try:
        binding = _snapshot_binding(value.binding, direction=None)
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID"
        ) from exc
    return _EffectStartAuthoritySnapshot(
        run_id=value.run_id,
        plan_sha256=value.plan_sha256,
        phase=phase_matches[0],
        effect_key=value.effect_key,
        phase_request_sha256=value.phase_request_sha256,
        binding=binding,
        claim_id=checked_claim,
        journaled_effect_start_identity_sha256=journaled_effect_start_identity_sha256,
    )


def derive_physical_full_matrix_v4_effect_start_identity_sha256(
    value: object,
) -> str:
    """Derive the stable, non-authorizing identity of one V4 start record.

    The digest contains no anchor locator or journal path.  A root journal may
    use it only after it has independently verified that the supplied start
    was durably committed; callers cannot turn this pure derivation into an
    effect permit.
    """

    if type(value) is not PhysicalFullMatrixV4EffectStart:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID")
    if type(value.run_id) is not UUID or value.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID")
    _sha256(
        value.plan_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID",
    )
    if type(value.sequence) is not int or not 1 <= value.sequence <= len(
        PHYSICAL_FULL_MATRIX_V4_PHASES
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID")
    _sha256(
        value.phase_request_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID",
    )
    _sha256(
        value.effect_key,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID",
    )
    _identifier(
        value.claim_id,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_IDENTITY_INVALID",
    )
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
                "purpose": "process-local-journaled-effect-start-identity-v1",
                "run_id": str(value.run_id),
                "plan_sha256": value.plan_sha256,
                "sequence": value.sequence,
                "phase_request_sha256": value.phase_request_sha256,
                "effect_key": value.effect_key,
                "claim_id": value.claim_id,
            },
            code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_INVALID",
        )
    ).hexdigest()


def _journaled_effect_start_identity(
    value: PhysicalFullMatrixV4EffectStart,
) -> str:
    """Private compatibility alias used by existing start-authority checks."""

    return derive_physical_full_matrix_v4_effect_start_identity_sha256(value)


def _mint_effect_start_authority(
    *,
    effect_start: PhysicalFullMatrixV4EffectStart,
    claim: PhysicalFullMatrixV4PhaseClaim,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4EffectStartAuthority:
    """Mint one opaque correlation handle after the journaled start is checked."""

    _claim(claim, request=request)
    checked_start = _effect_start(
        effect_start,
        claim=claim,
        request=request,
    )
    identity = _journaled_effect_start_identity(checked_start)
    snapshot = _request_effect_start_authority_snapshot(
        request,
        claim_id=checked_start.claim_id,
        journaled_effect_start_identity_sha256=identity,
    )
    result = PhysicalFullMatrixV4EffectStartAuthority(
        run_id=snapshot.run_id,
        plan_sha256=snapshot.plan_sha256,
        phase=_phase_from_snapshot(snapshot.phase),
        effect_key=snapshot.effect_key,
        phase_request_sha256=snapshot.phase_request_sha256,
        binding=_binding_from_snapshot(snapshot.binding),
        claim_id=snapshot.claim_id,
        journaled_effect_start_identity_sha256=(
            snapshot.journaled_effect_start_identity_sha256
        ),
        capability=_EFFECT_START_AUTHORITY_CAPABILITY,
    )
    _EFFECT_START_AUTHORITY_STATES[result] = _EffectStartAuthorityState(
        snapshot=snapshot,
        claim=claim,
        effect_start=checked_start,
    )
    return result


def _authority_public_shape_matches(
    *,
    value: PhysicalFullMatrixV4EffectStartAuthority,
    snapshot: _EffectStartAuthoritySnapshot,
) -> bool:
    """Fail closed if an opaque handle's visible immutable projection drifts."""

    try:
        return (
            value.run_id == snapshot.run_id
            and value.plan_sha256 == snapshot.plan_sha256
            and _matches_phase(value.phase, snapshot.phase)
            and value.effect_key == snapshot.effect_key
            and value.phase_request_sha256 == snapshot.phase_request_sha256
            and _matches_binding(value.binding, snapshot.binding)
            and value.claim_id == snapshot.claim_id
            and value.journaled_effect_start_identity_sha256
            == snapshot.journaled_effect_start_identity_sha256
            and value.writer_authorized is False
            and value.promotion_authorized is False
            and value.execution_authorized is False
            and value.full_matrix_authorized is False
            and value.full_matrix_executed is False
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        return False


def _require_effect_start_authority(
    value: object,
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4EffectStartAuthority:
    if (
        type(value) is not PhysicalFullMatrixV4EffectStartAuthority
        or value._capability is not _EFFECT_START_AUTHORITY_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUIRED")
    state = _EFFECT_START_AUTHORITY_STATES.get(value)
    if state is None or request._effect_start_authority is not value:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUIRED")
    if not _authority_public_shape_matches(value=value, snapshot=state.snapshot):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_TAMPERED")
    try:
        request_snapshot = _request_effect_start_authority_snapshot(
            request,
            claim_id=state.snapshot.claim_id,
            journaled_effect_start_identity_sha256=(
                state.snapshot.journaled_effect_start_identity_sha256
            ),
        )
        _claim(state.claim, request=request)
        checked_start = _effect_start(
            state.effect_start,
            claim=state.claim,
            request=request,
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_MISMATCH"
        ) from exc
    if (
        request_snapshot != state.snapshot
        or _journaled_effect_start_identity(checked_start)
        != state.snapshot.journaled_effect_start_identity_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_MISMATCH")
    return value


def require_physical_full_matrix_v4_effect_start_authority(
    *,
    request: object,
) -> PhysicalFullMatrixV4EffectStartAuthority:
    """Return only the exact process-local start correlation given to an adapter.

    Phase adapters may use this to bind later owner-specific evidence to the
    root journal's prior effect-start transition.  It is explicitly not an
    execution permit and is unavailable on all ordinary/pre-effect requests.
    """

    if type(request) is not PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_AUTHORITY_REQUEST_INVALID")
    return _require_effect_start_authority(
        request._effect_start_authority,
        request=request,
    )


def _effect_start_anchor_proof_snapshot(
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
    effect_start: PhysicalFullMatrixV4EffectStart,
    journal_binding_sha256: object,
    baseline_plan_binding_sha256: object,
    anchor_genesis_sequence: object,
    anchor_genesis_head_sha256: object,
    anchor_previous_sequence: object,
    anchor_previous_head_sha256: object,
    anchor_sequence: object,
    anchor_head_sha256: object,
    anchor_commitment_sha256: object,
    anchor_attestation_sha256: object,
    anchor_local_previous_record_sha256: object,
    anchor_local_event_sha256: object,
    anchor_occurred_at: object,
) -> _EffectStartAnchorProofSnapshot:
    """Validate the narrow primitive projection a root journal may supply."""

    if type(effect_start) is not PhysicalFullMatrixV4EffectStart:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    identity = derive_physical_full_matrix_v4_effect_start_identity_sha256(
        effect_start
    )
    request_snapshot = _request_effect_start_authority_snapshot(
        request,
        claim_id=effect_start.claim_id,
        journaled_effect_start_identity_sha256=identity,
    )
    if (
        effect_start.run_id != request_snapshot.run_id
        or effect_start.plan_sha256 != request_snapshot.plan_sha256
        or effect_start.sequence != request_snapshot.phase.sequence
        or effect_start.phase_request_sha256 != request_snapshot.phase_request_sha256
        or effect_start.effect_key != request_snapshot.effect_key
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    checked_journal = _sha256(
        journal_binding_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    checked_baseline = _sha256(
        baseline_plan_binding_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    if type(anchor_genesis_sequence) is not int or anchor_genesis_sequence < 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    if type(anchor_previous_sequence) is not int or anchor_previous_sequence < anchor_genesis_sequence:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    if type(anchor_sequence) is not int or anchor_sequence != anchor_previous_sequence + 1:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    checked_genesis_head = _sha256(
        anchor_genesis_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
        permit_zero=True,
    )
    checked_previous_head = _sha256(
        anchor_previous_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
        permit_zero=True,
    )
    checked_anchor_head = _sha256(
        anchor_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    checked_commitment = _sha256(
        anchor_commitment_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    checked_attestation = _sha256(
        anchor_attestation_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    checked_local_previous = _sha256(
        anchor_local_previous_record_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
        permit_zero=True,
    )
    checked_local_event = _sha256(
        anchor_local_event_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    checked_occurred_at = _utc(
        anchor_occurred_at,
        code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID",
    )
    if (
        anchor_previous_sequence == anchor_genesis_sequence
        and checked_previous_head != checked_genesis_head
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    return _EffectStartAnchorProofSnapshot(
        request=request_snapshot,
        journal_binding_sha256=checked_journal,
        baseline_plan_binding_sha256=checked_baseline,
        anchor_genesis_sequence=anchor_genesis_sequence,
        anchor_genesis_head_sha256=checked_genesis_head,
        anchor_previous_sequence=anchor_previous_sequence,
        anchor_previous_head_sha256=checked_previous_head,
        anchor_sequence=anchor_sequence,
        anchor_head_sha256=checked_anchor_head,
        anchor_commitment_sha256=checked_commitment,
        anchor_attestation_sha256=checked_attestation,
        anchor_local_previous_record_sha256=checked_local_previous,
        anchor_local_event_sha256=checked_local_event,
        anchor_occurred_at=checked_occurred_at,
    )


def _mint_physical_full_matrix_v4_effect_start_anchor_proof(
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
    effect_start: PhysicalFullMatrixV4EffectStart,
    journal_binding_sha256: str,
    baseline_plan_binding_sha256: str,
    anchor_genesis_sequence: int,
    anchor_genesis_head_sha256: str,
    anchor_previous_sequence: int,
    anchor_previous_head_sha256: str,
    anchor_sequence: int,
    anchor_head_sha256: str,
    anchor_commitment_sha256: str,
    anchor_attestation_sha256: str,
    anchor_local_previous_record_sha256: str,
    anchor_local_event_sha256: str,
    anchor_occurred_at: datetime,
) -> PhysicalFullMatrixV4EffectStartAnchorProof:
    """Mint a proof only for a concrete journal after its own anchor checks.

    This is intentionally private.  The driver validates the resulting
    process-local handle before it reaches a phase adapter; the root journal
    remains responsible for proving its external Witness head before calling
    this narrow projection seam.
    """

    snapshot = _effect_start_anchor_proof_snapshot(
        request=request,
        effect_start=effect_start,
        journal_binding_sha256=journal_binding_sha256,
        baseline_plan_binding_sha256=baseline_plan_binding_sha256,
        anchor_genesis_sequence=anchor_genesis_sequence,
        anchor_genesis_head_sha256=anchor_genesis_head_sha256,
        anchor_previous_sequence=anchor_previous_sequence,
        anchor_previous_head_sha256=anchor_previous_head_sha256,
        anchor_sequence=anchor_sequence,
        anchor_head_sha256=anchor_head_sha256,
        anchor_commitment_sha256=anchor_commitment_sha256,
        anchor_attestation_sha256=anchor_attestation_sha256,
        anchor_local_previous_record_sha256=anchor_local_previous_record_sha256,
        anchor_local_event_sha256=anchor_local_event_sha256,
        anchor_occurred_at=anchor_occurred_at,
    )
    request_snapshot = snapshot.request
    result = PhysicalFullMatrixV4EffectStartAnchorProof(
        schema=PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA,
        run_id=request_snapshot.run_id,
        plan_sha256=request_snapshot.plan_sha256,
        phase=_phase_from_snapshot(request_snapshot.phase),
        effect_key=request_snapshot.effect_key,
        phase_request_sha256=request_snapshot.phase_request_sha256,
        binding=_binding_from_snapshot(request_snapshot.binding),
        claim_id=request_snapshot.claim_id,
        journaled_effect_start_identity_sha256=(
            request_snapshot.journaled_effect_start_identity_sha256
        ),
        journal_binding_sha256=snapshot.journal_binding_sha256,
        baseline_plan_binding_sha256=snapshot.baseline_plan_binding_sha256,
        anchor_genesis_sequence=snapshot.anchor_genesis_sequence,
        anchor_genesis_head_sha256=snapshot.anchor_genesis_head_sha256,
        anchor_previous_sequence=snapshot.anchor_previous_sequence,
        anchor_previous_head_sha256=snapshot.anchor_previous_head_sha256,
        anchor_sequence=snapshot.anchor_sequence,
        anchor_head_sha256=snapshot.anchor_head_sha256,
        anchor_commitment_sha256=snapshot.anchor_commitment_sha256,
        anchor_attestation_sha256=snapshot.anchor_attestation_sha256,
        anchor_local_previous_record_sha256=(
            snapshot.anchor_local_previous_record_sha256
        ),
        anchor_local_event_sha256=snapshot.anchor_local_event_sha256,
        anchor_occurred_at=snapshot.anchor_occurred_at,
        capability=_EFFECT_START_ANCHOR_PROOF_CAPABILITY,
    )
    _EFFECT_START_ANCHOR_PROOF_STATES[result] = _EffectStartAnchorProofState(
        snapshot=snapshot,
        effect_start=effect_start,
    )
    return result


def _effect_start_anchor_proof_public_shape_matches(
    *,
    value: PhysicalFullMatrixV4EffectStartAnchorProof,
    snapshot: _EffectStartAnchorProofSnapshot,
) -> bool:
    """Reject monkey-patched visible fields even on a frozen proof object."""

    try:
        request = snapshot.request
        return (
            value.schema == PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA
            and value.run_id == request.run_id
            and value.plan_sha256 == request.plan_sha256
            and _matches_phase(value.phase, request.phase)
            and value.effect_key == request.effect_key
            and value.phase_request_sha256 == request.phase_request_sha256
            and _matches_binding(value.binding, request.binding)
            and value.claim_id == request.claim_id
            and value.journaled_effect_start_identity_sha256
            == request.journaled_effect_start_identity_sha256
            and value.journal_binding_sha256 == snapshot.journal_binding_sha256
            and value.baseline_plan_binding_sha256
            == snapshot.baseline_plan_binding_sha256
            and value.anchor_genesis_sequence == snapshot.anchor_genesis_sequence
            and value.anchor_genesis_head_sha256 == snapshot.anchor_genesis_head_sha256
            and value.anchor_previous_sequence == snapshot.anchor_previous_sequence
            and value.anchor_previous_head_sha256
            == snapshot.anchor_previous_head_sha256
            and value.anchor_sequence == snapshot.anchor_sequence
            and value.anchor_head_sha256 == snapshot.anchor_head_sha256
            and value.anchor_commitment_sha256 == snapshot.anchor_commitment_sha256
            and value.anchor_attestation_sha256 == snapshot.anchor_attestation_sha256
            and value.anchor_local_previous_record_sha256
            == snapshot.anchor_local_previous_record_sha256
            and value.anchor_local_event_sha256 == snapshot.anchor_local_event_sha256
            and value.anchor_occurred_at == snapshot.anchor_occurred_at
            and value.writer_authorized is False
            and value.promotion_authorized is False
            and value.execution_authorized is False
            and value.full_matrix_authorized is False
            and value.full_matrix_executed is False
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        return False


def _require_effect_start_anchor_proof(
    value: object,
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4EffectStartAnchorProof:
    # Report the missing proof itself for an ordinary/pre-effect request.
    # Requiring the authority first would still fail closed, but it obscures
    # the boundary an adapter actually asked for and makes a deliberately
    # proof-less request indistinguishable from an authority-only request.
    # Once a candidate proof exists, the authority is independently required
    # and cross-pinned below.
    if (
        type(value) is not PhysicalFullMatrixV4EffectStartAnchorProof
        or value._capability is not _EFFECT_START_ANCHOR_PROOF_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_REQUIRED")
    state = _EFFECT_START_ANCHOR_PROOF_STATES.get(value)
    if state is None or request._effect_start_anchor_proof is not value:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_REQUIRED")
    if not _effect_start_anchor_proof_public_shape_matches(
        value=value,
        snapshot=state.snapshot,
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_TAMPERED")
    authority = _require_effect_start_authority(
        request._effect_start_authority,
        request=request,
    )
    try:
        request_snapshot = _request_effect_start_authority_snapshot(
            request,
            claim_id=state.snapshot.request.claim_id,
            journaled_effect_start_identity_sha256=(
                state.snapshot.request.journaled_effect_start_identity_sha256
            ),
        )
        identity = derive_physical_full_matrix_v4_effect_start_identity_sha256(
            state.effect_start
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_REQUEST_MISMATCH"
        ) from exc
    if (
        request_snapshot != state.snapshot.request
        or identity != state.snapshot.request.journaled_effect_start_identity_sha256
        or authority.journaled_effect_start_identity_sha256 != identity
        or authority.claim_id != state.snapshot.request.claim_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_REQUEST_MISMATCH")
    return value


def require_physical_full_matrix_v4_effect_start_anchor_proof(
    *,
    request: object,
) -> PhysicalFullMatrixV4EffectStartAnchorProof:
    """Return the exact Witness-attested start projection for an adapter copy.

    It is unavailable on ordinary/pre-effect requests and on a restarted
    process.  Callers receive correlation evidence only; all writer,
    promotion, execution, and Full-Matrix authority flags remain false.
    """

    if type(request) is not PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_REQUEST_INVALID")
    return _require_effect_start_anchor_proof(
        request._effect_start_anchor_proof,
        request=request,
    )


def _predecessor_phase_completion_anchor_proof_public_values(
    value: PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof,
) -> tuple[object, ...]:
    """Return all visible evidence fields in a fixed tamper-check order."""

    return tuple(
        getattr(value, name)
        for name in _PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_PUBLIC_FIELDS
    )


def _anchor_pin_values(
    *,
    previous_sequence: object,
    previous_head_sha256: object,
    sequence: object,
    head_sha256: object,
    commitment_sha256: object,
    attestation_sha256: object,
    local_previous_record_sha256: object,
    local_event_sha256: object,
    occurred_at: object,
    genesis_sequence: int,
    genesis_head_sha256: str,
    code: str,
) -> tuple[int, str, int, str, str, str, str, str, datetime]:
    """Validate one full Witness-anchor pin without inventing a new hash."""

    if (
        type(previous_sequence) is not int
        or previous_sequence < genesis_sequence
        or type(sequence) is not int
        or sequence != previous_sequence + 1
    ):
        _fail(code)
    checked_previous_head = _sha256(
        previous_head_sha256,
        code=code,
        permit_zero=True,
    )
    checked_head = _sha256(head_sha256, code=code)
    checked_commitment = _sha256(commitment_sha256, code=code)
    checked_attestation = _sha256(attestation_sha256, code=code)
    checked_local_previous = _sha256(
        local_previous_record_sha256,
        code=code,
        permit_zero=True,
    )
    checked_local_event = _sha256(local_event_sha256, code=code)
    checked_occurred_at = _utc(occurred_at, code=code)
    if previous_sequence == genesis_sequence and checked_previous_head != genesis_head_sha256:
        _fail(code)
    return (
        previous_sequence,
        checked_previous_head,
        sequence,
        checked_head,
        checked_commitment,
        checked_attestation,
        checked_local_previous,
        checked_local_event,
        checked_occurred_at,
    )


def _mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
    predecessor_effect_start: PhysicalFullMatrixV4EffectStart,
    journal_binding_sha256: object,
    baseline_plan_binding_sha256: object,
    anchor_genesis_sequence: object,
    anchor_genesis_head_sha256: object,
    predecessor_effect_start_anchor_previous_sequence: object,
    predecessor_effect_start_anchor_previous_head_sha256: object,
    predecessor_effect_start_anchor_sequence: object,
    predecessor_effect_start_anchor_head_sha256: object,
    predecessor_effect_start_anchor_commitment_sha256: object,
    predecessor_effect_start_anchor_attestation_sha256: object,
    predecessor_effect_start_anchor_local_previous_record_sha256: object,
    predecessor_effect_start_anchor_local_event_sha256: object,
    predecessor_effect_started_at: object,
    predecessor_completion_receipt_sha256: object,
    predecessor_completion_anchor_previous_sequence: object,
    predecessor_completion_anchor_previous_head_sha256: object,
    predecessor_completion_anchor_sequence: object,
    predecessor_completion_anchor_head_sha256: object,
    predecessor_completion_anchor_commitment_sha256: object,
    predecessor_completion_anchor_attestation_sha256: object,
    predecessor_completion_anchor_local_previous_record_sha256: object,
    predecessor_completion_anchor_local_event_sha256: object,
    predecessor_completed_at: object,
) -> PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
    """Mint only a durable journal's exact predecessor-to-successor bridge.

    This private seam intentionally validates both sides of the bridge.  The
    concrete journal remains responsible for deriving all predecessor values
    from its create-only records and a fresh external-head read; callers
    cannot use this pure constructor as an alternative authority source.
    """

    code = "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_INVALID"
    if type(predecessor_effect_start) is not PhysicalFullMatrixV4EffectStart:
        _fail(code)
    try:
        successor_authority = require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        successor_anchor = require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
        request_snapshot = _request_effect_start_authority_snapshot(
            request,
            claim_id=successor_authority.claim_id,
            journaled_effect_start_identity_sha256=(
                successor_authority.journaled_effect_start_identity_sha256
            ),
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(code) from exc
    predecessor_phase_index = successor_authority.phase.sequence - 2
    phases = _phase_snapshots()
    if predecessor_phase_index < 0 or predecessor_phase_index >= len(phases):
        _fail(code)
    predecessor_phase = phases[predecessor_phase_index]
    try:
        predecessor_identity = derive_physical_full_matrix_v4_effect_start_identity_sha256(
            predecessor_effect_start
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(code) from exc
    if (
        predecessor_effect_start.run_id != successor_authority.run_id
        or predecessor_effect_start.plan_sha256 != successor_authority.plan_sha256
        or predecessor_effect_start.sequence != predecessor_phase.sequence
        or predecessor_effect_start.sequence + 1 != successor_authority.phase.sequence
    ):
        _fail(code)
    checked_journal = _sha256(journal_binding_sha256, code=code)
    checked_baseline = _sha256(baseline_plan_binding_sha256, code=code)
    if type(anchor_genesis_sequence) is not int or anchor_genesis_sequence < 0:
        _fail(code)
    checked_genesis_head = _sha256(
        anchor_genesis_head_sha256,
        code=code,
        permit_zero=True,
    )
    if (
        checked_journal != successor_anchor.journal_binding_sha256
        or checked_baseline != successor_anchor.baseline_plan_binding_sha256
        or anchor_genesis_sequence != successor_anchor.anchor_genesis_sequence
        or checked_genesis_head != successor_anchor.anchor_genesis_head_sha256
    ):
        _fail(code)
    predecessor_start_anchor = _anchor_pin_values(
        previous_sequence=predecessor_effect_start_anchor_previous_sequence,
        previous_head_sha256=predecessor_effect_start_anchor_previous_head_sha256,
        sequence=predecessor_effect_start_anchor_sequence,
        head_sha256=predecessor_effect_start_anchor_head_sha256,
        commitment_sha256=predecessor_effect_start_anchor_commitment_sha256,
        attestation_sha256=predecessor_effect_start_anchor_attestation_sha256,
        local_previous_record_sha256=(
            predecessor_effect_start_anchor_local_previous_record_sha256
        ),
        local_event_sha256=predecessor_effect_start_anchor_local_event_sha256,
        occurred_at=predecessor_effect_started_at,
        genesis_sequence=anchor_genesis_sequence,
        genesis_head_sha256=checked_genesis_head,
        code=code,
    )
    predecessor_completion_anchor = _anchor_pin_values(
        previous_sequence=predecessor_completion_anchor_previous_sequence,
        previous_head_sha256=predecessor_completion_anchor_previous_head_sha256,
        sequence=predecessor_completion_anchor_sequence,
        head_sha256=predecessor_completion_anchor_head_sha256,
        commitment_sha256=predecessor_completion_anchor_commitment_sha256,
        attestation_sha256=predecessor_completion_anchor_attestation_sha256,
        local_previous_record_sha256=(
            predecessor_completion_anchor_local_previous_record_sha256
        ),
        local_event_sha256=predecessor_completion_anchor_local_event_sha256,
        occurred_at=predecessor_completed_at,
        genesis_sequence=anchor_genesis_sequence,
        genesis_head_sha256=checked_genesis_head,
        code=code,
    )
    checked_receipt = _sha256(predecessor_completion_receipt_sha256, code=code)
    if (
        predecessor_completion_anchor[0] != predecessor_start_anchor[2]
        or predecessor_completion_anchor[1] != predecessor_start_anchor[3]
        or predecessor_completion_anchor[2] != predecessor_start_anchor[2] + 1
        or successor_anchor.anchor_previous_sequence
        != predecessor_completion_anchor[2]
        or successor_anchor.anchor_previous_head_sha256
        != predecessor_completion_anchor[3]
        or successor_anchor.anchor_sequence != successor_anchor.anchor_previous_sequence + 1
    ):
        _fail(code)
    result = PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof(
        schema=PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA,
        run_id=successor_authority.run_id,
        plan_sha256=successor_authority.plan_sha256,
        predecessor_phase_name=predecessor_phase.name,
        predecessor_phase_sequence=predecessor_phase.sequence,
        predecessor_effect_key=predecessor_effect_start.effect_key,
        predecessor_phase_request_sha256=(
            predecessor_effect_start.phase_request_sha256
        ),
        predecessor_claim_id=predecessor_effect_start.claim_id,
        predecessor_effect_start_identity_sha256=predecessor_identity,
        journal_binding_sha256=checked_journal,
        baseline_plan_binding_sha256=checked_baseline,
        anchor_genesis_sequence=anchor_genesis_sequence,
        anchor_genesis_head_sha256=checked_genesis_head,
        predecessor_effect_start_anchor_previous_sequence=predecessor_start_anchor[0],
        predecessor_effect_start_anchor_previous_head_sha256=predecessor_start_anchor[1],
        predecessor_effect_start_anchor_sequence=predecessor_start_anchor[2],
        predecessor_effect_start_anchor_head_sha256=predecessor_start_anchor[3],
        predecessor_effect_start_anchor_commitment_sha256=predecessor_start_anchor[4],
        predecessor_effect_start_anchor_attestation_sha256=predecessor_start_anchor[5],
        predecessor_effect_start_anchor_local_previous_record_sha256=(
            predecessor_start_anchor[6]
        ),
        predecessor_effect_start_anchor_local_event_sha256=predecessor_start_anchor[7],
        predecessor_effect_started_at=predecessor_start_anchor[8],
        predecessor_completion_receipt_sha256=checked_receipt,
        predecessor_completion_anchor_previous_sequence=(
            predecessor_completion_anchor[0]
        ),
        predecessor_completion_anchor_previous_head_sha256=(
            predecessor_completion_anchor[1]
        ),
        predecessor_completion_anchor_sequence=predecessor_completion_anchor[2],
        predecessor_completion_anchor_head_sha256=predecessor_completion_anchor[3],
        predecessor_completion_anchor_commitment_sha256=(
            predecessor_completion_anchor[4]
        ),
        predecessor_completion_anchor_attestation_sha256=(
            predecessor_completion_anchor[5]
        ),
        predecessor_completion_anchor_local_previous_record_sha256=(
            predecessor_completion_anchor[6]
        ),
        predecessor_completion_anchor_local_event_sha256=(
            predecessor_completion_anchor[7]
        ),
        predecessor_completed_at=predecessor_completion_anchor[8],
        successor_phase_name=successor_authority.phase.name,
        successor_phase_sequence=successor_authority.phase.sequence,
        successor_effect_key=successor_authority.effect_key,
        successor_phase_request_sha256=successor_authority.phase_request_sha256,
        successor_claim_id=successor_authority.claim_id,
        successor_effect_start_identity_sha256=(
            successor_authority.journaled_effect_start_identity_sha256
        ),
        successor_effect_start_anchor_previous_sequence=(
            successor_anchor.anchor_previous_sequence
        ),
        successor_effect_start_anchor_previous_head_sha256=(
            successor_anchor.anchor_previous_head_sha256
        ),
        successor_effect_start_anchor_sequence=successor_anchor.anchor_sequence,
        successor_effect_start_anchor_head_sha256=successor_anchor.anchor_head_sha256,
        capability=_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_CAPABILITY,
    )
    _PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_STATES[result] = (
        _PredecessorPhaseCompletionAnchorProofState(
            request=request_snapshot,
            public_values=_predecessor_phase_completion_anchor_proof_public_values(
                result
            ),
        )
    )
    return result


def _require_predecessor_phase_completion_anchor_proof(
    value: object,
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
    """Require the exact journal-minted predecessor bridge for one request."""

    if (
        type(value) is not PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
        or value._capability is not _PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_REQUIRED")
    state = _PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_STATES.get(value)
    if state is None or request._predecessor_phase_completion_anchor_proof is not value:
        _fail("PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_REQUIRED")
    if (
        _predecessor_phase_completion_anchor_proof_public_values(value)
        != state.public_values
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_TAMPERED")
    try:
        authority = require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor = require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
        request_snapshot = _request_effect_start_authority_snapshot(
            request,
            claim_id=authority.claim_id,
            journaled_effect_start_identity_sha256=(
                authority.journaled_effect_start_identity_sha256
            ),
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_REQUEST_MISMATCH"
        ) from exc
    if (
        request_snapshot != state.request
        or authority.phase.sequence <= 1
        or value.schema
        != PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA
        or value.run_id != authority.run_id
        or value.plan_sha256 != authority.plan_sha256
        or value.successor_phase_name != authority.phase.name
        or value.successor_phase_sequence != authority.phase.sequence
        or value.successor_effect_key != authority.effect_key
        or value.successor_phase_request_sha256 != authority.phase_request_sha256
        or value.successor_claim_id != authority.claim_id
        or value.successor_effect_start_identity_sha256
        != authority.journaled_effect_start_identity_sha256
        or value.journal_binding_sha256 != anchor.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != anchor.baseline_plan_binding_sha256
        or value.anchor_genesis_sequence != anchor.anchor_genesis_sequence
        or value.anchor_genesis_head_sha256 != anchor.anchor_genesis_head_sha256
        or value.successor_effect_start_anchor_previous_sequence
        != anchor.anchor_previous_sequence
        or value.successor_effect_start_anchor_previous_head_sha256
        != anchor.anchor_previous_head_sha256
        or value.successor_effect_start_anchor_sequence != anchor.anchor_sequence
        or value.successor_effect_start_anchor_head_sha256 != anchor.anchor_head_sha256
        or value.predecessor_phase_sequence + 1 != authority.phase.sequence
        or value.predecessor_completion_anchor_sequence
        != anchor.anchor_previous_sequence
        or value.predecessor_completion_anchor_head_sha256
        != anchor.anchor_previous_head_sha256
        or value.predecessor_completion_anchor_sequence
        != value.predecessor_completion_anchor_previous_sequence + 1
        or value.predecessor_completion_anchor_previous_sequence
        != value.predecessor_effect_start_anchor_sequence
        or value.predecessor_completion_anchor_previous_head_sha256
        != value.predecessor_effect_start_anchor_head_sha256
        or value.predecessor_completion_anchor_sequence
        != value.predecessor_effect_start_anchor_sequence + 1
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_REQUEST_MISMATCH")
    return value


def require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
    *,
    request: object,
) -> PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
    """Return a typed durable predecessor-completion bridge for an adapter.

    It is intentionally unavailable for Phase 1, ordinary/pre-effect
    requests, and any request not given the exact private proof by the
    root-journal-backed execution driver.
    """

    if type(request) is not PhysicalFullMatrixV4ExecutionRequest:
        _fail(
            "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_REQUEST_INVALID"
        )
    return _require_predecessor_phase_completion_anchor_proof(
        request._predecessor_phase_completion_anchor_proof,
        request=request,
    )


def _adapter_request_with_effect_start_authority(
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
    authority: PhysicalFullMatrixV4EffectStartAuthority,
    anchor_proof: PhysicalFullMatrixV4EffectStartAnchorProof,
    predecessor_phase_completion_anchor_proof: (
        PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof | None
    ) = None,
) -> PhysicalFullMatrixV4ExecutionRequest:
    """Attach verified start correlations only to the private adapter copy."""

    result = _request_copy(request)
    object.__setattr__(result, "_effect_start_authority", authority)
    object.__setattr__(result, "_effect_start_anchor_proof", anchor_proof)
    object.__setattr__(
        result,
        "_predecessor_phase_completion_anchor_proof",
        predecessor_phase_completion_anchor_proof,
    )
    _require_effect_start_authority(authority, request=result)
    _require_effect_start_anchor_proof(anchor_proof, request=result)
    if predecessor_phase_completion_anchor_proof is not None:
        _require_predecessor_phase_completion_anchor_proof(
            predecessor_phase_completion_anchor_proof,
            request=result,
        )
    return result


def _successor(
    value: object,
    *,
    predecessor: _BindingSnapshot,
    phase: _PhaseSnapshot,
    now: datetime,
) -> _BindingSnapshot | None:
    expected_direction = _SUCCESSOR_DIRECTIONS.get(phase.name)
    if expected_direction is None:
        if value is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_UNEXPECTED_SUCCESSOR")
        return None
    if type(value) is not PhysicalFullMatrixV4ReadinessEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_REQUIRED")
    successor = _snapshot_binding(value.binding, direction=expected_direction)
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witnessed_term_proof_sha256
        == predecessor.witnessed_term_proof_sha256
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
        or successor.readiness_binding_sha256 == predecessor.readiness_binding_sha256
        or successor.roundtrip_attestation_sha256
        == predecessor.roundtrip_attestation_sha256
        # V4 has no independently signed configuration-transition artifact.
        # Therefore a writer transition may change term/route/attestation but
        # may not silently swap the roundtrip configuration.
        or successor.roundtrip_configuration_sha256
        != predecessor.roundtrip_configuration_sha256
        or successor.witness_transition_id == predecessor.witness_transition_id
        or successor.witness_sequence <= predecessor.witness_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_NON_MONOTONIC")
    _validate_readiness_evidence(value, binding=successor, now=now)
    return successor


def _require_reverse_successor(
    *,
    phase: _PhaseSnapshot,
    active: _BindingSnapshot,
    initial: _BindingSnapshot,
) -> None:
    """Phase five cannot use the normal-direction evidence as precredit."""

    if phase.name != "ir-writer-v2-witness-roundtrip-strict-ack-matrix":
        return
    if (
        (active.source_site, active.destination_site) != _REVERSE_DIRECTION
        or active.writer_epoch <= initial.writer_epoch
        or active.writer_lease_id == initial.writer_lease_id
        or active.witnessed_term_proof_sha256 == initial.witnessed_term_proof_sha256
        or active.roundtrip_attestation_sha256 == initial.roundtrip_attestation_sha256
        or active.witness_sequence <= initial.witness_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_REVERSE_SUCCESSOR_READINESS_REQUIRED")


def _validate_oracle(
    *,
    value: object,
    request: PhysicalFullMatrixV4ExecutionRequest,
    phase: _PhaseSnapshot,
    initial: _BindingSnapshot,
    now: datetime,
    maximum_age: int,
) -> _BindingSnapshot | None:
    if type(value) is not PhysicalFullMatrixV4PhaseOracle:
        _fail("PHYSICAL_FULL_MATRIX_V4_ORACLE_INVALID")
    _sha256(value.effect_key, code="PHYSICAL_FULL_MATRIX_V4_ORACLE_EFFECT_KEY_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA
        or value.status != "oracle-succeeded"
        or value.phase != phase.name
        or value.oracle != phase.oracle
        or value.transport_profile != phase.transport_profile
        or value.effect_key != request.effect_key
        or value.direct_fi_to_ir_control != _DIRECT_CONTROL_FORBIDDEN
        or value.direct_ir_to_fi_control != _DIRECT_CONTROL_FORBIDDEN
        or value.legacy_runner_compatibility != _LEGACY_COMPATIBILITY_FORBIDDEN
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ORACLE_BINDING_MISMATCH")
    _sha256(value.evidence_sha256, code="PHYSICAL_FULL_MATRIX_V4_ORACLE_EVIDENCE_INVALID")
    observed = _utc(value.observed_at, code="PHYSICAL_FULL_MATRIX_V4_ORACLE_CLOCK_INVALID")
    if observed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("PHYSICAL_FULL_MATRIX_V4_ORACLE_FUTURE")
    if now - observed > timedelta(seconds=maximum_age):
        _fail("PHYSICAL_FULL_MATRIX_V4_ORACLE_STALE")
    binding = _snapshot_binding(request.binding, direction=None)
    _require_reverse_successor(phase=phase, active=binding, initial=initial)
    # The active term may be retired by a successful transition.  The oracle
    # must therefore reflect the exact process-local resolver evidence that
    # V4 verified before the adapter call, rather than attempting to replay
    # that old term after the effect.
    if (
        type(request.pre_effect_readiness_evidence)
        is not PhysicalFullMatrixV4ReadinessEvidence
        or value.readiness_evidence is not request.pre_effect_readiness_evidence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ORACLE_PRE_EFFECT_READINESS_MISMATCH")
    return _successor(
        value.successor_readiness_evidence,
        predecessor=binding,
        phase=phase,
        now=now,
    )


_BINDING_FIELDS = frozenset(_BindingSnapshot.__dataclass_fields__)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "run_id",
        "plan_sha256",
        "sequence",
        "phase",
        "effect_key",
        "phase_request_sha256",
        "oracle",
        "oracle_evidence_sha256",
        "previous_receipt_sha256",
        "recorded_at",
        *_BINDING_FIELDS,
        "direct_fi_to_ir_control",
        "direct_ir_to_fi_control",
        "legacy_runner_compatibility",
        "successor_binding",
        "full_matrix_executed",
    }
)


def _receipt_body(
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
    phase: _PhaseSnapshot,
    oracle: PhysicalFullMatrixV4PhaseOracle,
    successor: _BindingSnapshot | None,
    previous_receipt_sha256: str,
    recorded_at: datetime,
) -> dict[str, object]:
    binding = _snapshot_binding(request.binding, direction=None)
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_RECEIPT_SCHEMA,
        "status": _STATUS_COMPLETED,
        "run_id": str(request.run_id),
        "plan_sha256": request.plan_sha256,
        "sequence": phase.sequence,
        "phase": phase.name,
        "effect_key": request.effect_key,
        "phase_request_sha256": request.phase_request_sha256,
        "oracle": phase.oracle,
        "oracle_evidence_sha256": oracle.evidence_sha256,
        "previous_receipt_sha256": previous_receipt_sha256,
        "recorded_at": _render_timestamp(recorded_at),
        **_binding_body(binding),
        "direct_fi_to_ir_control": _DIRECT_CONTROL_FORBIDDEN,
        "direct_ir_to_fi_control": _DIRECT_CONTROL_FORBIDDEN,
        "legacy_runner_compatibility": _LEGACY_COMPATIBILITY_FORBIDDEN,
        "successor_binding": None if successor is None else _binding_body(successor),
        "full_matrix_executed": False,
    }


def _binding_from_mapping(value: object, *, code: str) -> PhysicalFullMatrixV4ExecutionBinding:
    if type(value) is not dict or set(value) != _BINDING_FIELDS:
        _fail(code)
    return _binding(PhysicalFullMatrixV4ExecutionBinding(**value), direction=None)


def parse_physical_full_matrix_v4_run_receipt(
    value: object,
) -> PhysicalFullMatrixV4RunReceipt:
    """Parse one canonical append-only semantic V4 receipt without I/O."""

    if (
        type(value) is not bytes
        or not value.endswith(b"\n")
        or len(value) > _MAX_RECEIPT_BYTES
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_ENCODING_INVALID")
    try:
        decoded = json.loads(
            value[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JSON_INVALID"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV4ExecutionDriverError):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_ENCODING_INVALID")
    if type(decoded) is not dict or set(decoded) != _RECEIPT_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_FIELDS_INVALID")
    if (
        decoded["schema"] != PHYSICAL_FULL_MATRIX_V4_RECEIPT_SCHEMA
        or decoded["status"] != _STATUS_COMPLETED
        or decoded["direct_fi_to_ir_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["direct_ir_to_fi_control"] != _DIRECT_CONTROL_FORBIDDEN
        or decoded["legacy_runner_compatibility"] != _LEGACY_COMPATIBILITY_FORBIDDEN
        or decoded["full_matrix_executed"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_BINDING_INVALID")
    try:
        run_id = UUID(decoded["run_id"])
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_RUN_ID_INVALID")
    if run_id.int == 0 or str(run_id) != decoded["run_id"]:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_RUN_ID_INVALID")
    if type(decoded["sequence"]) is not int or decoded["sequence"] not in range(1, 9):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_SEQUENCE_INVALID")
    phase = _PHASES_BY_NAME.get(decoded["phase"])
    if phase is None or phase[0] != decoded["sequence"] or decoded["oracle"] != phase[2]:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_PHASE_INVALID")
    for name in (
        "plan_sha256",
        "effect_key",
        "phase_request_sha256",
        "oracle_evidence_sha256",
        "previous_receipt_sha256",
    ):
        _sha256(
            decoded[name],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_HASH_INVALID",
            permit_zero=name == "previous_receipt_sha256",
        )
    binding = _binding_from_mapping(
        {name: decoded[name] for name in _BINDING_FIELDS},
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_BINDING_INVALID",
    )
    successor_raw = decoded["successor_binding"]
    successor = (
        None
        if successor_raw is None
        else _binding_from_mapping(
            successor_raw,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_SUCCESSOR_INVALID",
        )
    )
    canonical = _canonical(decoded, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_INVALID") + b"\n"
    if canonical != value:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_NONCANONICAL")
    return PhysicalFullMatrixV4RunReceipt(
        canonical_receipt=canonical,
        receipt_sha256=hashlib.sha256(value).hexdigest(),
        run_id=run_id,
        plan_sha256=decoded["plan_sha256"],
        sequence=decoded["sequence"],
        phase=decoded["phase"],
        effect_key=decoded["effect_key"],
        phase_request_sha256=decoded["phase_request_sha256"],
        oracle_evidence_sha256=decoded["oracle_evidence_sha256"],
        previous_receipt_sha256=decoded["previous_receipt_sha256"],
        recorded_at=_timestamp(
            decoded["recorded_at"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_CLOCK_INVALID",
        ),
        binding=binding,
        successor_binding=successor,
        full_matrix_executed=decoded["full_matrix_executed"],
    )


def _receipt_successor(
    receipt: PhysicalFullMatrixV4RunReceipt,
    *,
    predecessor: _BindingSnapshot,
    phase: _PhaseSnapshot,
) -> _BindingSnapshot | None:
    expected_direction = _SUCCESSOR_DIRECTIONS.get(phase.name)
    if expected_direction is None:
        if receipt.successor_binding is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_UNEXPECTED_SUCCESSOR")
        return None
    successor = _snapshot_binding(receipt.successor_binding, direction=expected_direction)
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witnessed_term_proof_sha256
        == predecessor.witnessed_term_proof_sha256
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
        or successor.readiness_binding_sha256 == predecessor.readiness_binding_sha256
        or successor.roundtrip_attestation_sha256
        == predecessor.roundtrip_attestation_sha256
        or successor.roundtrip_configuration_sha256
        != predecessor.roundtrip_configuration_sha256
        or successor.witness_transition_id == predecessor.witness_transition_id
        or successor.witness_sequence <= predecessor.witness_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_SUCCESSOR_NON_MONOTONIC")
    return successor


def _validate_receipt_chain(
    *,
    snapshot: _PlanSnapshot,
    raw_receipts: object,
    now: datetime | None = None,
) -> tuple[tuple[PhysicalFullMatrixV4RunReceipt, ...], _BindingSnapshot]:
    if not isinstance(raw_receipts, Sequence) or isinstance(raw_receipts, (str, bytes)):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_CHAIN_INVALID")
    if len(raw_receipts) > len(snapshot.phases):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_CHAIN_TOO_LONG")
    receipts = tuple(parse_physical_full_matrix_v4_run_receipt(item) for item in raw_receipts)
    prior = _ZERO_SHA256
    prior_recorded_at: datetime | None = None
    active = snapshot.binding
    for index, receipt in enumerate(receipts):
        phase = snapshot.phases[index]
        _require_reverse_successor(phase=phase, active=active, initial=snapshot.binding)
        request = _request(snapshot=snapshot, phase=phase, binding=active)
        if (
            receipt.run_id != snapshot.run_id
            or receipt.plan_sha256 != snapshot.plan_sha256
            or receipt.sequence != phase.sequence
            or receipt.phase != phase.name
            or receipt.effect_key != request.effect_key
            or receipt.phase_request_sha256 != request.phase_request_sha256
            or receipt.previous_receipt_sha256 != prior
            or not _matches_binding(receipt.binding, active)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_CHAIN_MISMATCH")
        if prior_recorded_at is not None and receipt.recorded_at < prior_recorded_at:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_CLOCK_REGRESSION")
        if now is not None and receipt.recorded_at > now + timedelta(
            seconds=_MAX_FUTURE_SKEW_SECONDS
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_FUTURE")
        successor = _receipt_successor(receipt, predecessor=active, phase=phase)
        if successor is not None:
            active = successor
        prior = receipt.receipt_sha256
        prior_recorded_at = receipt.recorded_at
    return receipts, active


def prepare_physical_full_matrix_v4_execution_adapters(
    *,
    plan: PhysicalFullMatrixV4ExecutionPlan,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
) -> None:
    """Validate semantic adapter/journal interfaces without invoking effects."""

    snapshot = _snapshot(plan)
    if type(adapters) is not PhysicalFullMatrixV4ExecutionAdapters:
        _fail("PHYSICAL_FULL_MATRIX_V4_ADAPTERS_INVALID")
    if not isinstance(adapters.phase_adapters, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_ADAPTERS_MISSING")
    if set(adapters.phase_adapters) != {phase.name for phase in snapshot.phases}:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_ADAPTER_SET_INVALID")
    for phase in snapshot.phases:
        if not callable(getattr(adapters.phase_adapters.get(phase.name), "execute_phase", None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_ADAPTER_INVALID")
    for name in (
        "read_receipts",
        "claim_phase",
        "mark_effect_started",
        "project_effect_start_anchor_proof",
        "project_predecessor_phase_completion_anchor_proof",
        "append_started",
    ):
        if not callable(getattr(adapters.receipt_journal, name, None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_MISSING")
    if not callable(
        getattr(adapters.readiness_resolver, "resolve_readiness", None)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_READINESS_RESOLVER_MISSING")
    if not callable(getattr(adapters.trusted_clock, "now_utc", None)):
        _fail("PHYSICAL_FULL_MATRIX_V4_TRUSTED_CLOCK_MISSING")
    if not callable(
        getattr(adapters.campaign_continuity_gate, "verify_campaign_continuity", None)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_CAMPAIGN_CONTINUITY_GATE_MISSING")
    require_physical_full_matrix_v4_execution_plan(plan)


def _phase_post_effect_verifier_map(
    *,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    snapshot: _PlanSnapshot,
) -> dict[str, PhysicalFullMatrixV4PhasePostEffectVerifier]:
    """Snapshot the exact root-pinned owner-verifier map before any claim.

    This is deliberately an execution-only gate.  Static/default-off planning
    can validate the shape of phase callbacks without inventing post-effect
    proof, but an execution path must never create even an effect-start record
    until every named phase has its own independently installed verifier.
    """

    value = adapters.phase_post_effect_verifiers
    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_MAP_REQUIRED")
    try:
        supplied = dict(value)
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_MAP_INVALID")
    expected = {phase.name for phase in snapshot.phases}
    if set(supplied) != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_SET_INVALID")

    result: dict[str, PhysicalFullMatrixV4PhasePostEffectVerifier] = {}
    seen: set[int] = set()
    for phase in snapshot.phases:
        verifier = supplied.get(phase.name)
        if not callable(getattr(verifier, "require_post_effect_completion", None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_INVALID")
        try:
            matches = (
                type(getattr(verifier, "phase_name")) is str
                and verifier.phase_name == phase.name
                and type(getattr(verifier, "phase_sequence")) is int
                and verifier.phase_sequence == phase.sequence
                and type(getattr(verifier, "oracle")) is str
                and verifier.oracle == phase.oracle
                and type(getattr(verifier, "transport_profile")) is str
                and verifier.transport_profile == phase.transport_profile
            )
        except Exception:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_INVALID")
        if not matches:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_BINDING_MISMATCH")
        if verifier is adapters.phase_adapters.get(phase.name) or id(verifier) in seen:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_ALIAS")
        seen.add(id(verifier))
        result[phase.name] = verifier
    return result


def _require_phase_post_effect_completion(
    *,
    verifier: PhysicalFullMatrixV4PhasePostEffectVerifier,
    request: PhysicalFullMatrixV4ExecutionRequest,
    oracle: PhysicalFullMatrixV4PhaseOracle,
    now: datetime,
    maximum_oracle_age_seconds: int,
) -> None:
    """Require the owner-specific post-effect proof for one exact callback.

    The generic driver intentionally cannot interpret phase implementation
    artifacts.  It does, however, own the journaled-start correlation and the
    public semantic oracle.  It therefore makes the owner verifier prove an
    opaque completion against both sides before the generic receipt can move a
    campaign forward.
    """

    completion = oracle.post_effect_completion
    if completion is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_COMPLETION_REQUIRED")
    try:
        authority = require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor = require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_CORRELATION_REQUIRED"
        ) from exc
    try:
        observed = _utc(
            oracle.observed_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_ORACLE_CLOCK_INVALID",
        )
        _sha256(
            oracle.evidence_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_ORACLE_EVIDENCE_INVALID",
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    # The adapter request is process-local and the two require_* calls above
    # independently validate every run/plan/phase/effect/claim/binding/start
    # anchor pin.  The owner verifier receives those exact opaque handles plus
    # the public oracle hash/time and must reject any foreign, pre-start,
    # stale, or unbound phase capability.
    method = getattr(verifier, "require_post_effect_completion", None)
    if not callable(method):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_INVALID")
    try:
        result = method(
            request=request,
            effect_start_authority=authority,
            effect_start_anchor_proof=anchor,
            oracle=oracle,
            completion=completion,
            observed_at=observed,
            now=now,
            maximum_oracle_age_seconds=maximum_oracle_age_seconds,
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_COMPLETION_INVALID"
        ) from exc
    if result is not None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_POST_EFFECT_VERIFIER_RESULT_INVALID")


def _trusted_now(
    *,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    floor: datetime | None = None,
) -> datetime:
    """Read one root-owned clock value and reject intra-run regression."""

    callback = getattr(adapters.trusted_clock, "now_utc", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_TRUSTED_CLOCK_MISSING")
    try:
        observed = _utc(
            callback(),
            code="PHYSICAL_FULL_MATRIX_V4_TRUSTED_CLOCK_INVALID",
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_TRUSTED_CLOCK_FAILED"
        ) from exc
    if floor is not None and observed < floor:
        _fail("PHYSICAL_FULL_MATRIX_V4_TRUSTED_CLOCK_REGRESSION")
    return observed


def _resolve_active_readiness(
    *,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    binding: _BindingSnapshot,
    now: datetime,
) -> tuple[PhysicalFullMatrixV4ReadinessEvidence, datetime]:
    """Resolve and revalidate active evidence after the resolver callback.

    A resolver is an external root-owned callback just like a journal or phase
    adapter.  Validating its result only against the clock sample from before
    it ran would let a blocked resolver smuggle an already-stale capability
    into the next irreversible operation.  Return the post-callback trusted
    time so every caller can carry that newer monotonic floor forward.
    """

    callback = getattr(adapters.readiness_resolver, "resolve_readiness", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_READINESS_RESOLVER_MISSING")
    try:
        evidence = callback(binding=_binding_from_snapshot(binding))
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_ACTIVE_READINESS_RESOLUTION_FAILED"
        ) from exc
    after_resolution = _trusted_now(adapters=adapters, floor=now)
    _validate_readiness_evidence(evidence, binding=binding, now=after_resolution)
    return evidence, after_resolution


def _require_campaign_continuity(
    *,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    snapshot: _PlanSnapshot,
    completed_sequence: int,
    active: _BindingSnapshot,
    floor: datetime,
) -> datetime:
    """Delegate restart authority, then advance the trusted callback floor."""

    callback = getattr(
        adapters.campaign_continuity_gate,
        "verify_campaign_continuity",
        None,
    )
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_CAMPAIGN_CONTINUITY_GATE_MISSING")
    try:
        callback(
            run_id=snapshot.run_id,
            plan_sha256=snapshot.plan_sha256,
            completed_sequence=completed_sequence,
            active_binding=_binding_from_snapshot(active),
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_CAMPAIGN_CONTINUITY_UNVERIFIED"
        ) from exc
    return _trusted_now(adapters=adapters, floor=floor)


def _claim(
    value: object,
    *,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4PhaseClaim:
    if type(value) is not PhysicalFullMatrixV4PhaseClaim:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_INVALID")
    _sha256(value.effect_key, code="PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_INVALID")
    if (
        value.run_id != request.run_id
        or value.plan_sha256 != request.plan_sha256
        or value.sequence != request.phase.sequence
        or value.phase_request_sha256 != request.phase_request_sha256
        or value.effect_key != request.effect_key
        or type(value.indeterminate) is not bool
        or sum(
            (
                value.claim_id is not None,
                value.existing_receipt is not None,
                value.indeterminate,
            )
        )
        != 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_INVALID")
    if value.claim_id is not None:
        _identifier(value.claim_id, code="PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_INVALID")
    if value.existing_receipt is not None and type(value.existing_receipt) is not bytes:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_INVALID")
    return value


def _effect_start(
    value: object,
    *,
    claim: PhysicalFullMatrixV4PhaseClaim,
    request: PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4EffectStart:
    if type(value) is not PhysicalFullMatrixV4EffectStart or claim.claim_id is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_INVALID")
    _sha256(value.effect_key, code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_INVALID")
    if (
        value.run_id != request.run_id
        or value.plan_sha256 != request.plan_sha256
        or value.sequence != request.phase.sequence
        or value.phase_request_sha256 != request.phase_request_sha256
        or value.effect_key != request.effect_key
        or value.claim_id != claim.claim_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_INVALID")
    _identifier(value.claim_id, code="PHYSICAL_FULL_MATRIX_V4_EFFECT_START_INVALID")
    return value


def _durable_after(
    *,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    snapshot: _PlanSnapshot,
    before_count: int,
    expected: PhysicalFullMatrixV4RunReceipt,
    now: datetime,
) -> tuple[_BindingSnapshot, datetime]:
    """Read the durable receipt callback under a post-callback time fence."""

    try:
        raw = adapters.receipt_journal.read_receipts(run_id=snapshot.run_id)
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_READ_FAILED"
        ) from exc
    after_read = _trusted_now(adapters=adapters, floor=now)
    receipts, active = _validate_receipt_chain(
        snapshot=snapshot,
        raw_receipts=raw,
        now=after_read,
    )
    if (
        len(receipts) != before_count + 1
        or receipts[-1].canonical_receipt != expected.canonical_receipt
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_NOT_DURABLE")
    return active, after_read


def execute_next_physical_full_matrix_v4_phase(
    *,
    config: PhysicalFullMatrixV4ExecutionConfig,
    plan: PhysicalFullMatrixV4ExecutionPlan,
    adapters: PhysicalFullMatrixV4ExecutionAdapters,
    now: datetime,
) -> PhysicalFullMatrixV4ExecutionResult:
    """Run at most one phase under fresh root-owned temporal/readiness fences.

    ``now`` is intentionally non-authoritative.  A caller must not be able to
    extend an oracle or opaque readiness capability by supplying an old clock;
    the injected root-owned clock is sampled before/after every callback that
    can race a policy or term change.
    """

    del now
    snapshot = _snapshot(plan)
    prepare_physical_full_matrix_v4_execution_adapters(plan=plan, adapters=adapters)
    # Snapshot and root-pin every phase-owned completion verifier before a
    # journal claim is even considered.  ``prepare_*`` intentionally does
    # not require this map because it is also used by the default-off planning
    # path, but a live execute path must not create an ambiguous effect-start
    # if it cannot later validate the phase owner's completion capability.
    phase_post_effect_verifiers = _phase_post_effect_verifier_map(
        adapters=adapters,
        snapshot=snapshot,
    )

    before_read = _trusted_now(adapters=adapters)
    configured_binding, configured_run_id, configured_age = _static_config(
        config,
        require_enabled=True,
    )
    if (
        configured_binding != snapshot.binding
        or configured_run_id != snapshot.run_id
        or configured_age != snapshot.maximum_oracle_age_seconds
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PLAN_CONFIG_MISMATCH")
    require_physical_full_matrix_v4_execution_plan(plan)
    try:
        raw = adapters.receipt_journal.read_receipts(run_id=snapshot.run_id)
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_READ_FAILED"
        ) from exc
    after_read = _trusted_now(adapters=adapters, floor=before_read)
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    receipts, active = _validate_receipt_chain(
        snapshot=snapshot,
        raw_receipts=raw,
        now=after_read,
    )
    after_initial_continuity = _require_campaign_continuity(
        adapters=adapters,
        snapshot=snapshot,
        completed_sequence=len(receipts),
        active=active,
        floor=after_read,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    if len(receipts) == len(snapshot.phases):
        return PhysicalFullMatrixV4ExecutionResult(
            status="all-phases-already-receipted",
            phase=None,
            receipt=None,
            next_phase=None,
        )
    phase = snapshot.phases[len(receipts)]
    _require_reverse_successor(phase=phase, active=active, initial=snapshot.binding)

    before_claim = _trusted_now(adapters=adapters, floor=after_initial_continuity)
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    claim_request = _request(snapshot=snapshot, phase=phase, binding=active)
    try:
        claim = _claim(
            adapters.receipt_journal.claim_phase(
                run_id=claim_request.run_id,
                plan_sha256=claim_request.plan_sha256,
                sequence=claim_request.phase.sequence,
                phase_request_sha256=claim_request.phase_request_sha256,
                effect_key=claim_request.effect_key,
            ),
            request=claim_request,
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_CLAIM_FAILED"
        ) from exc
    after_claim = _trusted_now(adapters=adapters, floor=before_claim)
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    if claim.existing_receipt is not None:
        receipt = parse_physical_full_matrix_v4_run_receipt(claim.existing_receipt)
        durable_active, after_durable = _durable_after(
            adapters=adapters,
            snapshot=snapshot,
            before_count=len(receipts),
            expected=receipt,
            now=after_claim,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
        after_existing_continuity = _require_campaign_continuity(
            adapters=adapters,
            snapshot=snapshot,
            completed_sequence=len(receipts) + 1,
            active=durable_active,
            floor=after_durable,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
        _resolved_existing, _after_existing_resolution = _resolve_active_readiness(
            adapters=adapters,
            binding=durable_active,
            now=after_existing_continuity,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
        require_physical_full_matrix_v4_execution_plan(plan)
        next_phase = None if receipt.sequence == 8 else snapshot.phases[receipt.sequence].name
        return PhysicalFullMatrixV4ExecutionResult(
            status="already-completed-from-append-only-receipt",
            phase=receipt.phase,
            receipt=receipt,
            next_phase=next_phase,
        )
    if claim.indeterminate:
        # A durable effect-start without a receipt is never auto-retried.
        _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_INDETERMINATE")
    assert claim.claim_id is not None

    # This is the last fresh active-term evidence before the effect.  It is
    # passed process-locally to the root adapter and must be reflected by
    # identity in its oracle; transition callbacks may retire it afterward.
    _revalidate_initial_readiness_if_pretransition(
        config=config,
        snapshot=snapshot,
        phase=phase,
        now=after_claim,
    )
    pre_effect, after_pre_effect_resolution = _resolve_active_readiness(
        adapters=adapters,
        binding=active,
        now=after_claim,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    _revalidate_initial_readiness_if_pretransition(
        config=config,
        snapshot=snapshot,
        phase=phase,
        now=after_pre_effect_resolution,
    )
    require_physical_full_matrix_v4_execution_plan(plan)
    request = _request(
        snapshot=snapshot,
        phase=phase,
        binding=active,
        pre_effect_readiness_evidence=pre_effect,
    )
    try:
        effect_start = _effect_start(
            adapters.receipt_journal.mark_effect_started(
                claim=claim,
                effect_key=request.effect_key,
            ),
            claim=claim,
            request=request,
        )
        effect_start_authority = _mint_effect_start_authority(
            effect_start=effect_start,
            claim=claim,
            request=request,
        )
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_FAILED"
        ) from exc
    try:
        anchor_proof = adapters.receipt_journal.project_effect_start_anchor_proof(
            effect_start=effect_start,
            request=request,
        )
        if type(anchor_proof) is not PhysicalFullMatrixV4EffectStartAnchorProof:
            _fail("PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_INVALID")
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_FAILED"
        ) from exc
    after_effect_start = _trusted_now(
        adapters=adapters,
        floor=after_pre_effect_resolution,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    _revalidate_initial_readiness_if_pretransition(
        config=config,
        snapshot=snapshot,
        phase=phase,
        now=after_effect_start,
    )
    pre_effect, after_pre_adapter_resolution = _resolve_active_readiness(
        adapters=adapters,
        binding=active,
        now=after_effect_start,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    _revalidate_initial_readiness_if_pretransition(
        config=config,
        snapshot=snapshot,
        phase=phase,
        now=after_pre_adapter_resolution,
    )
    request = _request(
        snapshot=snapshot,
        phase=phase,
        binding=active,
        pre_effect_readiness_evidence=pre_effect,
    )
    require_physical_full_matrix_v4_execution_plan(plan)
    adapter = adapters.phase_adapters[phase.name]
    # Give the journal an exact private start-bound request while it proves
    # the durable predecessor-to-successor link.  This callback occurs after
    # every fresh readiness/configuration fence and before the phase adapter;
    # it is a projection only, never an effect callback.
    adapter_request = _adapter_request_with_effect_start_authority(
        request=request,
        authority=effect_start_authority,
        anchor_proof=anchor_proof,
    )
    predecessor_completion_anchor_proof: (
        PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof | None
    ) = None
    if phase.sequence > 1:
        try:
            predecessor_completion_anchor_proof = (
                adapters.receipt_journal.project_predecessor_phase_completion_anchor_proof(
                    effect_start=effect_start,
                    request=adapter_request,
                )
            )
            if (
                type(predecessor_completion_anchor_proof)
                is not PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
            ):
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_INVALID"
                )
            # Attach only after the exact object returned by the journal has
            # passed its own process-local request/proof cross-pins.
            adapter_request = _adapter_request_with_effect_start_authority(
                request=request,
                authority=effect_start_authority,
                anchor_proof=anchor_proof,
                predecessor_phase_completion_anchor_proof=(
                    predecessor_completion_anchor_proof
                ),
            )
        except PhysicalFullMatrixV4ExecutionDriverError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4ExecutionDriverError(
                "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_FAILED"
            ) from exc
    try:
        oracle = adapter.execute_phase(request=adapter_request)
    except PhysicalFullMatrixV4ExecutionDriverError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_ADAPTER_FAILED"
        ) from exc

    after_callback = _trusted_now(
        adapters=adapters,
        floor=after_pre_adapter_resolution,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    post_callback = after_callback
    if not _is_transition_phase(phase):
        _revalidate_initial_readiness_if_pretransition(
            config=config,
            snapshot=snapshot,
            phase=phase,
            now=post_callback,
        )
        _resolved_active, post_callback = _resolve_active_readiness(
            adapters=adapters,
            binding=active,
            now=post_callback,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    successor = _validate_oracle(
        value=oracle,
        request=request,
        phase=phase,
        initial=snapshot.binding,
        now=post_callback,
        maximum_age=snapshot.maximum_oracle_age_seconds,
    )
    _require_phase_post_effect_completion(
        verifier=phase_post_effect_verifiers[phase.name],
        request=adapter_request,
        oracle=oracle,
        now=post_callback,
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )

    before_append = _trusted_now(adapters=adapters, floor=post_callback)
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    if _is_transition_phase(phase):
        assert successor is not None
        _resolved_successor, before_append = _resolve_active_readiness(
            adapters=adapters,
            binding=successor,
            now=before_append,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    else:
        _revalidate_initial_readiness_if_pretransition(
            config=config,
            snapshot=snapshot,
            phase=phase,
            now=before_append,
        )
        _resolved_active, before_append = _resolve_active_readiness(
            adapters=adapters,
            binding=active,
            now=before_append,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    successor = _validate_oracle(
        value=oracle,
        request=request,
        phase=phase,
        initial=snapshot.binding,
        now=before_append,
        maximum_age=snapshot.maximum_oracle_age_seconds,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    if not _is_transition_phase(phase):
        _revalidate_initial_readiness_if_pretransition(
            config=config,
            snapshot=snapshot,
            phase=phase,
            now=before_append,
        )
    require_physical_full_matrix_v4_execution_plan(plan)
    # A verifier may rely on time-bounded owner evidence or on a capability
    # that can be retired between the callback and append.  Recheck it at the
    # last possible point, after every external callback/freshness fence and
    # immediately before serialising the durable receipt.
    _require_phase_post_effect_completion(
        verifier=phase_post_effect_verifiers[phase.name],
        request=adapter_request,
        oracle=oracle,
        now=before_append,
        maximum_oracle_age_seconds=snapshot.maximum_oracle_age_seconds,
    )
    previous = _ZERO_SHA256 if not receipts else receipts[-1].receipt_sha256
    canonical = _canonical(
        _receipt_body(
            request=request,
            phase=phase,
            oracle=oracle,
            successor=successor,
            previous_receipt_sha256=previous,
            recorded_at=before_append,
        ),
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_INVALID",
    ) + b"\n"
    try:
        appended = adapters.receipt_journal.append_started(
            effect_start=effect_start,
            canonical_receipt=canonical,
        )
    except Exception as exc:
        raise PhysicalFullMatrixV4ExecutionDriverError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_APPEND_FAILED"
        ) from exc
    after_append = _trusted_now(adapters=adapters, floor=before_append)
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    if _is_transition_phase(phase):
        assert successor is not None
        _resolved_successor, after_append = _resolve_active_readiness(
            adapters=adapters,
            binding=successor,
            now=after_append,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    if type(appended) is not bytes or appended != canonical:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_APPEND_MISMATCH")
    receipt = parse_physical_full_matrix_v4_run_receipt(appended)
    durable_active, after_durable = _durable_after(
        adapters=adapters,
        snapshot=snapshot,
        before_count=len(receipts),
        expected=receipt,
        now=after_append,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    after_final_continuity = _require_campaign_continuity(
        adapters=adapters,
        snapshot=snapshot,
        completed_sequence=len(receipts) + 1,
        active=durable_active,
        floor=after_durable,
    )
    _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    if _is_transition_phase(phase):
        _resolved_durable, _after_durable_resolution = _resolve_active_readiness(
            adapters=adapters,
            binding=durable_active,
            now=after_final_continuity,
        )
        _revalidate_static_config_against_snapshot(config=config, snapshot=snapshot)
    require_physical_full_matrix_v4_execution_plan(plan)
    next_phase = None if phase.sequence == 8 else snapshot.phases[phase.sequence].name
    return PhysicalFullMatrixV4ExecutionResult(
        status="completed-redacted-phase-receipt",
        phase=phase.name,
        receipt=receipt,
        next_phase=next_phase,
    )
