"""Pure, default-off admission for V4 Phase 6 reverse FI standby rebuild.

Phase 6 must rebuild WA-FI only after the Phase-5 IR-writer effect has a
durable completion receipt and an immutable Witness completion anchor.  The
completion anchor is not interchangeable with the Phase-5 start anchor: the
V4 root journal inserts the completed receipt between the Phase-5 and Phase-6
starts.  This module therefore consumes the driver's typed predecessor-
completion bridge and demands that its exact completion head is the immediate
previous head of the Phase-6 start anchor.

The historical reverse-recovery surfaces are host/path/runtime boundaries.
They are intentionally neither imported nor adapted here.  This module only
parses canonical redacted plan and socket-only input bytes, cross-pins them to
the completed IR term and current Phase-6 effect-start correlations, and
returns non-authorizing evidence.  It never opens a file descriptor, touches
a path, starts PostgreSQL, invokes a runner, contacts Object Storage, or
changes writer/traffic/promotion state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core import physical_full_matrix_execution_driver_v4 as _driver


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_STATUS",
    "PhysicalFullMatrixV4Phase6FailbackRebuildAdmission",
    "PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig",
    "PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError",
    "PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs",
    "PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence",
    "PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs",
    "admit_physical_full_matrix_v4_phase6_failback_rebuild",
    "require_admitted_physical_full_matrix_v4_phase6_failback_rebuild",
)


PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-failback-rebuild-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_STATUS = (
    "phase5-completed-ir-writer-p6-fi-rebuild-admitted-evidence-only"
)

_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[5]
_P5_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[4]
_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_OBJECT_STORAGE_NAMESPACE = "physical-failback"
_FORBIDDEN = "forbidden"
_EVIDENCE_ONLY = "evidence-only"
_ZERO_SHA256 = "0" * 64
_MAX_WIRE_BYTES = 64 * 1024
_IMAGE_REFERENCE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]{1,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_PLAN_SCHEMA = "gold-trade-physical-full-matrix-v4-phase6-reverse-recovery-plan-v1"
_SOCKET_INPUT_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-socket-only-failback-input-v1"
)
_PLAN_STATUS = "canonical-reverse-recovery-plan-evidence-only"
_SOCKET_STATUS = "default-off-socket-only-failback-rebuild-input"
_CAPABILITY = object()

_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)
_OBJECT_VERSION_FIELDS = frozenset({"object_key", "version_id"})
_PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "plan_id",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "object_storage_namespace",
        "route_binding_sha256",
        "four_role_binding_sha256",
        "phase5_completion_receipt_sha256",
        "phase5_completion_anchor_sequence",
        "phase5_completion_anchor_head_sha256",
        "phase5_completion_anchor_commitment_sha256",
        "phase5_completion_anchor_attestation_sha256",
        "writer_term",
        "bundle_id",
        "stage_receipt_sha256",
        "manifest_sha256es",
        "object_versions",
        "terminal_wal_lsn",
        "recovery_evidence_sha256",
        "recovery_bundle_binding_sha256",
    }
)
_SOCKET_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "reverse_recovery_plan_sha256",
        "phase5_completion_receipt_sha256",
        "route_binding_sha256",
        "postgres_image",
        "postgres_major",
        "network_mode",
        "tcp_listener",
        "unix_socket_directory",
        "unix_socket_port",
        "socket_authentication",
        "recovery_mode",
        "direct_site_control",
        "destination_object_ingest",
        "fd_binder_authorized",
        "runner_authorized",
        "materialization_authorized",
        "promotion_authorized",
        "writer_authorized",
        "traffic_switch_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    }
)


class PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(ValueError):
    """One redacted refusal from the non-executing V4 Phase-6 boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig:
    """Default-off policy pinned to one exact Phase-5 IR writer term.

    The plan and socket digests are explicit root policy pins.  A syntactically
    valid alternate reverse plan cannot be substituted merely because it has
    the same campaign or writer term.
    """

    schema: str = PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA
    expected_phase5_ir_writer_binding: _driver.PhysicalFullMatrixV4ExecutionBinding | None = (
        None
    )
    expected_reverse_recovery_plan_sha256: str = ""
    expected_socket_only_input_sha256: str = ""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_DEFAULT_ENABLED
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    legacy_runtime_compatibility: str = _FORBIDDEN
    runner_authority: str = _EVIDENCE_ONLY


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence:
    """Canonical reverse-recovery plan bytes, never a runtime invocation."""

    canonical_plan: bytes
    plan_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs:
    """Canonical socket-only FI input bytes, never an FD/path instruction."""

    canonical_input: bytes
    input_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs:
    """Evidence-only inputs for an already journaled Phase-6 effect start."""

    adapter_request: _driver.PhysicalFullMatrixV4ExecutionRequest | None = field(
        default=None, repr=False, compare=False
    )
    # The caller must provide the exact object currently attached to the
    # private request.  A raw dict/start anchor/object cannot substitute for
    # the driver/journal-minted completion bridge.
    phase5_completion_anchor_proof: object | None = field(
        default=None, repr=False, compare=False
    )
    phase5_completion_receipt: bytes | None = field(
        default=None, repr=False, compare=False
    )
    reverse_recovery_plan: PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence | None = (
        field(default=None, repr=False, compare=False)
    )
    rendered_socket_only_inputs: PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs | None = (
        field(default=None, repr=False, compare=False)
    )


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    """Opaque P6 provenance; never an FI materialization/runner permit."""

    schema: str
    status: str
    admission_sha256: str
    admitted_at: datetime
    run_id: UUID
    plan_sha256: str
    phase6_effect_key: str
    phase6_request_sha256: str
    phase6_claim_id: str
    phase6_effect_start_identity_sha256: str
    phase6_anchor_sequence: int
    phase6_anchor_head_sha256: str
    phase5_completion_receipt_sha256: str
    phase5_effect_start_identity_sha256: str
    phase5_completion_anchor_sequence: int
    phase5_completion_anchor_head_sha256: str
    campaign_id: str
    release_sha: str
    reverse_recovery_plan_sha256: str
    reverse_recovery_plan_id: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    socket_only_failback_input_sha256: str
    phase5_writer_epoch: int
    phase5_writer_lease_id: str
    phase5_witness_transition_id: str
    phase5_witnessed_term_proof_sha256: str
    legacy_runtime_compatible: bool = False
    fd_binder_authorized: bool = False
    runner_authorized: bool = False
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    reverse_plan_sha256: str
    socket_input_sha256: str


@dataclass(frozen=True)
class _AnchorFacts:
    schema: str
    run_id: UUID
    plan_sha256: str
    phase: _driver.PhysicalFullMatrixV4ExecutionPhase
    effect_key: str
    phase_request_sha256: str
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    claim_id: str
    identity_sha256: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    genesis_sequence: int
    genesis_head_sha256: str
    previous_sequence: int
    previous_head_sha256: str
    sequence: int
    head_sha256: str
    commitment_sha256: str
    attestation_sha256: str
    local_previous_record_sha256: str
    local_event_sha256: str
    occurred_at: datetime


@dataclass(frozen=True)
class _RequestFacts:
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    authority: _driver.PhysicalFullMatrixV4EffectStartAuthority
    anchor: _AnchorFacts


@dataclass(frozen=True)
class _CompletionFacts:
    proof: _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
    receipt: _driver.PhysicalFullMatrixV4RunReceipt | None


@dataclass(frozen=True)
class _PlanFacts:
    plan_id: str
    plan_sha256: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    four_role_binding_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    phase5_completion_receipt_sha256: str
    phase5_completion_anchor_sequence: int
    phase5_completion_anchor_head_sha256: str


@dataclass(frozen=True)
class _SocketFacts:
    input_sha256: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str


@dataclass(frozen=True)
class _Facts:
    config: _ConfigFacts
    request: _RequestFacts
    completion: _CompletionFacts
    plan: _PlanFacts
    socket: _SocketFacts
    now: datetime


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase6FailbackRebuildAdmission, _Facts] = (
    WeakKeyDictionary()
)


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _phase_matches(
    value: object, expected: _driver.PhysicalFullMatrixV4ExecutionPhase
) -> bool:
    return (
        type(value) is _driver.PhysicalFullMatrixV4ExecutionPhase
        and value.sequence == expected.sequence
        and value.name == expected.name
        and value.oracle == expected.oracle
        and value.destructive is expected.destructive
        and value.transport_profile == expected.transport_profile
    )


def _binding(
    value: object, *, code: str
) -> _driver.PhysicalFullMatrixV4ExecutionBinding:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionBinding:
        _fail(code)
    try:
        _driver._snapshot_binding(value, direction=(_SOURCE_SITE, _DESTINATION_SITE))
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(code) from exc
    if value.writer_holder_site != _SOURCE_SITE:
        _fail(code)
    return value


def _canonical_mapping(
    raw: object, *, fields: frozenset[str], code: str
) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_WIRE_BYTES:
        _fail(code)

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                _fail(code)
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=no_duplicates)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError,
    ):
        _fail(code)
    if type(payload) is not dict or set(payload) != fields:
        _fail(code)
    try:
        if canonical_json_bytes(payload) != raw:
            _fail(code)
    except (TypeError, ValueError):
        _fail(code)
    return payload


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CONFIG_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_DISABLED")
    if (
        value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or value.legacy_runtime_compatibility != _FORBIDDEN
        or value.runner_authority != _EVIDENCE_ONLY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_POLICY_INVALID")
    binding = _binding(
        value.expected_phase5_ir_writer_binding,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CONFIG_INVALID",
    )
    return _ConfigFacts(
        binding=binding,
        reverse_plan_sha256=_sha256(
            value.expected_reverse_recovery_plan_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CONFIG_INVALID",
        ),
        socket_input_sha256=_sha256(
            value.expected_socket_only_input_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CONFIG_INVALID",
        ),
    )


def _anchor(
    value: object, *, code: str
) -> _AnchorFacts:
    if type(value) is not _driver.PhysicalFullMatrixV4EffectStartAnchorProof:
        _fail(code)
    proof = value
    if proof.schema != _driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA:
        _fail(code)
    if (
        type(proof.run_id) is not UUID
        or proof.run_id.int == 0
        or not _phase_matches(proof.phase, _PHASE)
        or type(proof.claim_id) is not str
        or _IDENTIFIER_RE.fullmatch(proof.claim_id) is None
    ):
        _fail(code)
    binding = _binding(proof.binding, code=code)
    for item in (
        proof.plan_sha256,
        proof.effect_key,
        proof.phase_request_sha256,
        proof.journaled_effect_start_identity_sha256,
        proof.journal_binding_sha256,
        proof.baseline_plan_binding_sha256,
        proof.anchor_head_sha256,
        proof.anchor_commitment_sha256,
        proof.anchor_attestation_sha256,
        proof.anchor_local_event_sha256,
    ):
        _sha256(item, code=code)
    genesis_head = _sha256(proof.anchor_genesis_head_sha256, code=code, permit_zero=True)
    previous_head = _sha256(proof.anchor_previous_head_sha256, code=code, permit_zero=True)
    local_previous = _sha256(
        proof.anchor_local_previous_record_sha256, code=code, permit_zero=True
    )
    if (
        type(proof.anchor_genesis_sequence) is not int
        or proof.anchor_genesis_sequence < 0
        or type(proof.anchor_previous_sequence) is not int
        or proof.anchor_previous_sequence < proof.anchor_genesis_sequence
        or type(proof.anchor_sequence) is not int
        or proof.anchor_sequence != proof.anchor_previous_sequence + 1
        or (
            proof.anchor_previous_sequence == proof.anchor_genesis_sequence
            and previous_head != genesis_head
        )
        or proof.writer_authorized is not False
        or proof.promotion_authorized is not False
        or proof.execution_authorized is not False
        or proof.full_matrix_authorized is not False
        or proof.full_matrix_executed is not False
    ):
        _fail(code)
    return _AnchorFacts(
        schema=proof.schema,
        run_id=proof.run_id,
        plan_sha256=proof.plan_sha256,
        phase=proof.phase,
        effect_key=proof.effect_key,
        phase_request_sha256=proof.phase_request_sha256,
        binding=binding,
        claim_id=proof.claim_id,
        identity_sha256=proof.journaled_effect_start_identity_sha256,
        journal_binding_sha256=proof.journal_binding_sha256,
        baseline_plan_binding_sha256=proof.baseline_plan_binding_sha256,
        genesis_sequence=proof.anchor_genesis_sequence,
        genesis_head_sha256=genesis_head,
        previous_sequence=proof.anchor_previous_sequence,
        previous_head_sha256=previous_head,
        sequence=proof.anchor_sequence,
        head_sha256=proof.anchor_head_sha256,
        commitment_sha256=proof.anchor_commitment_sha256,
        attestation_sha256=proof.anchor_attestation_sha256,
        local_previous_record_sha256=local_previous,
        local_event_sha256=proof.anchor_local_event_sha256,
        occurred_at=_utc(proof.anchor_occurred_at, code=code),
    )


def _request(value: object, *, expected: _ConfigFacts) -> _RequestFacts:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REQUEST_INVALID")
    request = value
    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor_proof = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_EFFECT_START_REQUIRED"
        ) from exc
    if (
        not _phase_matches(request.phase, _PHASE)
        or not _phase_matches(authority.phase, _PHASE)
        or type(request.run_id) is not UUID
        or request.run_id.int == 0
        or request.binding != expected.binding
        or authority.run_id != request.run_id
        or authority.plan_sha256 != request.plan_sha256
        or authority.effect_key != request.effect_key
        or authority.phase_request_sha256 != request.phase_request_sha256
        or authority.binding != request.binding
        or authority.writer_authorized is not False
        or authority.promotion_authorized is not False
        or authority.execution_authorized is not False
        or authority.full_matrix_authorized is not False
        or authority.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_EFFECT_START_MISMATCH")
    for item in (request.plan_sha256, request.effect_key, request.phase_request_sha256):
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REQUEST_INVALID")
    _sha256(
        authority.journaled_effect_start_identity_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_EFFECT_START_MISMATCH",
    )
    anchor = _anchor(
        anchor_proof,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_EFFECT_START_ANCHOR_INVALID",
    )
    if (
        anchor.run_id != authority.run_id
        or anchor.plan_sha256 != authority.plan_sha256
        or anchor.phase != authority.phase
        or anchor.effect_key != authority.effect_key
        or anchor.phase_request_sha256 != authority.phase_request_sha256
        or anchor.binding != authority.binding
        or anchor.claim_id != authority.claim_id
        or anchor.identity_sha256 != authority.journaled_effect_start_identity_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_EFFECT_START_ANCHOR_INVALID")
    return _RequestFacts(request=request, authority=authority, anchor=anchor)


def _completion(
    value: object, *, request: _RequestFacts, expected: _ConfigFacts
) -> _CompletionFacts:
    if value is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_REQUIRED")
    try:
        proof = _driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=request.request
        )
    except (AttributeError, _driver.PhysicalFullMatrixV4ExecutionDriverError) as exc:
        raise PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_UNAVAILABLE"
        ) from exc
    if value is not proof:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_MISMATCH")
    if (
        type(proof) is not _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
        or proof.schema
        != _driver.PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA
        or proof.run_id != request.authority.run_id
        or proof.plan_sha256 != request.authority.plan_sha256
        or proof.predecessor_phase_name != _P5_PHASE.name
        or proof.predecessor_phase_sequence != _P5_PHASE.sequence
        or proof.successor_phase_name != _PHASE.name
        or proof.successor_phase_sequence != _PHASE.sequence
        or proof.successor_effect_key != request.authority.effect_key
        or proof.successor_phase_request_sha256 != request.authority.phase_request_sha256
        or proof.successor_claim_id != request.authority.claim_id
        or proof.successor_effect_start_identity_sha256
        != request.authority.journaled_effect_start_identity_sha256
        or proof.journal_binding_sha256 != request.anchor.journal_binding_sha256
        or proof.baseline_plan_binding_sha256 != request.anchor.baseline_plan_binding_sha256
        or proof.anchor_genesis_sequence != request.anchor.genesis_sequence
        or proof.anchor_genesis_head_sha256 != request.anchor.genesis_head_sha256
        or proof.predecessor_completion_anchor_sequence != request.anchor.previous_sequence
        or proof.predecessor_completion_anchor_head_sha256 != request.anchor.previous_head_sha256
        or proof.writer_authorized is not False
        or proof.promotion_authorized is not False
        or proof.execution_authorized is not False
        or proof.full_matrix_authorized is not False
        or proof.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_INVALID")
    for item in (
        proof.predecessor_effect_key,
        proof.predecessor_phase_request_sha256,
        proof.predecessor_effect_start_identity_sha256,
        proof.predecessor_completion_receipt_sha256,
        proof.predecessor_completion_anchor_head_sha256,
        proof.predecessor_completion_anchor_commitment_sha256,
        proof.predecessor_completion_anchor_attestation_sha256,
    ):
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_INVALID")
    if (
        proof.predecessor_completion_anchor_sequence
        != proof.predecessor_completion_anchor_previous_sequence + 1
        or proof.predecessor_completion_anchor_previous_sequence
        != proof.predecessor_effect_start_anchor_sequence
        or proof.predecessor_completion_anchor_previous_head_sha256
        != proof.predecessor_effect_start_anchor_head_sha256
        or proof.predecessor_completion_anchor_sequence
        != proof.predecessor_effect_start_anchor_sequence + 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_PROOF_INVALID")
    return _CompletionFacts(proof=proof, receipt=None)


def _completion_receipt(
    value: object, *, completion: _CompletionFacts, request: _RequestFacts, expected: _ConfigFacts
) -> _CompletionFacts:
    if type(value) is not bytes:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_RECEIPT_REQUIRED")
    try:
        receipt = _driver.parse_physical_full_matrix_v4_run_receipt(value)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_RECEIPT_INVALID"
        ) from exc
    proof = completion.proof
    if (
        receipt.receipt_sha256 != proof.predecessor_completion_receipt_sha256
        or receipt.run_id != request.authority.run_id
        or receipt.plan_sha256 != request.authority.plan_sha256
        or receipt.sequence != _P5_PHASE.sequence
        or receipt.phase != _P5_PHASE.name
        or receipt.effect_key != proof.predecessor_effect_key
        or receipt.phase_request_sha256 != proof.predecessor_phase_request_sha256
        or receipt.binding != expected.binding
        or receipt.successor_binding is not None
        or receipt.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_RECEIPT_MISMATCH")
    return _CompletionFacts(proof=proof, receipt=receipt)


def _plan(
    value: object, *, expected: _ConfigFacts, completion: _CompletionFacts
) -> _PlanFacts:
    if type(value) is not PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_REQUIRED")
    raw = value.canonical_plan
    payload = _canonical_mapping(
        raw,
        fields=_PLAN_FIELDS,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID",
    )
    plan_sha = _sha256(
        value.plan_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != plan_sha or plan_sha != expected.reverse_plan_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    if (
        payload["schema"] != _PLAN_SCHEMA
        or payload["status"] != _PLAN_STATUS
        or payload["source_site"] != _SOURCE_SITE
        or payload["destination_site"] != _DESTINATION_SITE
        or payload["object_storage_namespace"] != _OBJECT_STORAGE_NAMESPACE
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    for name in (
        "plan_id",
        "route_binding_sha256",
        "four_role_binding_sha256",
        "phase5_completion_receipt_sha256",
        "phase5_completion_anchor_head_sha256",
        "phase5_completion_anchor_commitment_sha256",
        "phase5_completion_anchor_attestation_sha256",
        "bundle_id",
        "stage_receipt_sha256",
        "recovery_evidence_sha256",
        "recovery_bundle_binding_sha256",
    ):
        _sha256(payload[name], code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    if (
        type(payload["campaign_id"]) is not str
        or CAMPAIGN_ID_RE.fullmatch(payload["campaign_id"]) is None
        or type(payload["release_sha"]) is not str
        or RELEASE_SHA_RE.fullmatch(payload["release_sha"]) is None
        or type(payload["phase5_completion_anchor_sequence"]) is not int
        or payload["phase5_completion_anchor_sequence"] < 1
        or type(payload["terminal_wal_lsn"]) is not str
        or _LSN_RE.fullmatch(payload["terminal_wal_lsn"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    term = payload["writer_term"]
    if type(term) is not dict or set(term) != _TERM_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    if (
        term["holder_site"] != _SOURCE_SITE
        or type(term["writer_epoch"]) is not int
        or term["writer_epoch"] < 1
        or type(term["writer_lease_id"]) is not str
        or LEASE_ID_RE.fullmatch(term["writer_lease_id"]) is None
        or type(term["witness_transition_id"]) is not str
        or _IDENTIFIER_RE.fullmatch(term["witness_transition_id"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    _sha256(
        term["witnessed_term_proof_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID",
    )
    manifests = payload["manifest_sha256es"]
    if type(manifests) is not list or not manifests or len(set(manifests)) != len(manifests):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    for manifest in manifests:
        _sha256(manifest, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    versions = payload["object_versions"]
    if type(versions) is not list or not versions:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
    seen: set[tuple[str, str]] = set()
    for item in versions:
        if type(item) is not dict or set(item) != _OBJECT_VERSION_FIELDS:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
        object_key, version_id = item["object_key"], item["version_id"]
        if (
            type(object_key) is not str
            or OBJECT_KEY_RE.fullmatch(object_key) is None
            or ".." in object_key.split("/")
            or type(version_id) is not str
            or VERSION_ID_RE.fullmatch(version_id) is None
            or (object_key, version_id) in seen
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_INVALID")
        seen.add((object_key, version_id))
    receipt = completion.receipt
    if receipt is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_P5_COMPLETION_RECEIPT_REQUIRED")
    proof = completion.proof
    if (
        payload["campaign_id"] != receipt.binding.campaign_id
        or payload["release_sha"] != receipt.binding.release_sha
        or payload["route_binding_sha256"] != receipt.binding.route_commitment_sha256
        or payload["four_role_binding_sha256"] != receipt.binding.four_role_binding_sha256
        or payload["phase5_completion_receipt_sha256"] != receipt.receipt_sha256
        or payload["phase5_completion_anchor_sequence"]
        != proof.predecessor_completion_anchor_sequence
        or payload["phase5_completion_anchor_head_sha256"]
        != proof.predecessor_completion_anchor_head_sha256
        or payload["phase5_completion_anchor_commitment_sha256"]
        != proof.predecessor_completion_anchor_commitment_sha256
        or payload["phase5_completion_anchor_attestation_sha256"]
        != proof.predecessor_completion_anchor_attestation_sha256
        or term["writer_epoch"] != receipt.binding.writer_epoch
        or term["writer_lease_id"] != receipt.binding.writer_lease_id
        or term["witness_transition_id"] != receipt.binding.witness_transition_id
        or term["witnessed_term_proof_sha256"] != receipt.binding.witnessed_term_proof_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_REVERSE_PLAN_CROSS_PIN_MISMATCH")
    return _PlanFacts(
        plan_id=payload["plan_id"],
        plan_sha256=plan_sha,
        campaign_id=payload["campaign_id"],
        release_sha=payload["release_sha"],
        route_binding_sha256=payload["route_binding_sha256"],
        four_role_binding_sha256=payload["four_role_binding_sha256"],
        bundle_id=payload["bundle_id"],
        stage_receipt_sha256=payload["stage_receipt_sha256"],
        writer_epoch=term["writer_epoch"],
        writer_lease_id=term["writer_lease_id"],
        witness_transition_id=term["witness_transition_id"],
        witnessed_term_proof_sha256=term["witnessed_term_proof_sha256"],
        phase5_completion_receipt_sha256=payload["phase5_completion_receipt_sha256"],
        phase5_completion_anchor_sequence=payload["phase5_completion_anchor_sequence"],
        phase5_completion_anchor_head_sha256=payload["phase5_completion_anchor_head_sha256"],
    )


def _socket(
    value: object, *, expected: _ConfigFacts, plan: _PlanFacts
) -> _SocketFacts:
    if type(value) is not PhysicalFullMatrixV4Phase6SocketOnlyFailbackInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_REQUIRED")
    raw = value.canonical_input
    payload = _canonical_mapping(
        raw,
        fields=_SOCKET_INPUT_FIELDS,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_INVALID",
    )
    input_sha = _sha256(
        value.input_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != input_sha or input_sha != expected.socket_input_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_INVALID")
    if (
        payload["schema"] != _SOCKET_INPUT_SCHEMA
        or payload["status"] != _SOCKET_STATUS
        or payload["campaign_id"] != plan.campaign_id
        or payload["release_sha"] != plan.release_sha
        or payload["reverse_recovery_plan_sha256"] != plan.plan_sha256
        or payload["phase5_completion_receipt_sha256"]
        != plan.phase5_completion_receipt_sha256
        or payload["route_binding_sha256"] != plan.route_binding_sha256
        or payload["postgres_major"] != 15
        or payload["network_mode"] != "none"
        or payload["tcp_listener"] != "disabled"
        or payload["unix_socket_directory"] != "/var/run/postgresql"
        or payload["unix_socket_port"] != 5432
        or payload["socket_authentication"] != "peer-local-only"
        or payload["recovery_mode"] != "standby-replay-only"
        or payload["direct_site_control"] != _FORBIDDEN
        or payload["destination_object_ingest"] != "pull-only"
        or payload["fd_binder_authorized"] is not False
        or payload["runner_authorized"] is not False
        or payload["materialization_authorized"] is not False
        or payload["promotion_authorized"] is not False
        or payload["writer_authorized"] is not False
        or payload["traffic_switch_authorized"] is not False
        or payload["execution_authorized"] is not False
        or payload["full_matrix_authorized"] is not False
        or payload["full_matrix_executed"] is not False
        or type(payload["postgres_image"]) is not str
        or _IMAGE_REFERENCE_RE.fullmatch(payload["postgres_image"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_INVALID")
    for name in (
        "reverse_recovery_plan_sha256",
        "phase5_completion_receipt_sha256",
        "route_binding_sha256",
    ):
        _sha256(payload[name], code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SOCKET_INPUT_INVALID")
    return _SocketFacts(
        input_sha256=input_sha,
        campaign_id=payload["campaign_id"],
        release_sha=payload["release_sha"],
        route_binding_sha256=payload["route_binding_sha256"],
    )


def _admission_body(facts: _Facts) -> dict[str, object]:
    request = facts.request
    proof = facts.completion.proof
    plan = facts.plan
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_STATUS,
        "run_id": str(request.authority.run_id),
        "plan_sha256": request.authority.plan_sha256,
        "phase6_effect_key": request.authority.effect_key,
        "phase6_request_sha256": request.authority.phase_request_sha256,
        "phase6_claim_id": request.authority.claim_id,
        "phase6_effect_start_identity_sha256": request.authority.journaled_effect_start_identity_sha256,
        "phase6_anchor_sequence": request.anchor.sequence,
        "phase6_anchor_head_sha256": request.anchor.head_sha256,
        "phase5_completion_receipt_sha256": proof.predecessor_completion_receipt_sha256,
        "phase5_effect_start_identity_sha256": proof.predecessor_effect_start_identity_sha256,
        "phase5_completion_anchor_sequence": proof.predecessor_completion_anchor_sequence,
        "phase5_completion_anchor_head_sha256": proof.predecessor_completion_anchor_head_sha256,
        "campaign_id": plan.campaign_id,
        "release_sha": plan.release_sha,
        "reverse_recovery_plan_sha256": plan.plan_sha256,
        "reverse_recovery_plan_id": plan.plan_id,
        "bundle_id": plan.bundle_id,
        "stage_receipt_sha256": plan.stage_receipt_sha256,
        "route_binding_sha256": plan.route_binding_sha256,
        "socket_only_failback_input_sha256": facts.socket.input_sha256,
        "phase5_writer_epoch": plan.writer_epoch,
        "phase5_writer_lease_id": plan.writer_lease_id,
        "phase5_witness_transition_id": plan.witness_transition_id,
        "phase5_witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
        "admitted_at": facts.now.isoformat(),
        "legacy_runtime_compatible": False,
        "fd_binder_authorized": False,
        "runner_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _projection(facts: _Facts) -> dict[str, object]:
    body = _admission_body(facts)
    try:
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_CANONICAL_INVALID")
    request = facts.request
    proof = facts.completion.proof
    plan = facts.plan
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_STATUS,
        "admission_sha256": digest,
        "admitted_at": facts.now,
        "run_id": request.authority.run_id,
        "plan_sha256": request.authority.plan_sha256,
        "phase6_effect_key": request.authority.effect_key,
        "phase6_request_sha256": request.authority.phase_request_sha256,
        "phase6_claim_id": request.authority.claim_id,
        "phase6_effect_start_identity_sha256": request.authority.journaled_effect_start_identity_sha256,
        "phase6_anchor_sequence": request.anchor.sequence,
        "phase6_anchor_head_sha256": request.anchor.head_sha256,
        "phase5_completion_receipt_sha256": proof.predecessor_completion_receipt_sha256,
        "phase5_effect_start_identity_sha256": proof.predecessor_effect_start_identity_sha256,
        "phase5_completion_anchor_sequence": proof.predecessor_completion_anchor_sequence,
        "phase5_completion_anchor_head_sha256": proof.predecessor_completion_anchor_head_sha256,
        "campaign_id": plan.campaign_id,
        "release_sha": plan.release_sha,
        "reverse_recovery_plan_sha256": plan.plan_sha256,
        "reverse_recovery_plan_id": plan.plan_id,
        "bundle_id": plan.bundle_id,
        "stage_receipt_sha256": plan.stage_receipt_sha256,
        "route_binding_sha256": plan.route_binding_sha256,
        "socket_only_failback_input_sha256": facts.socket.input_sha256,
        "phase5_writer_epoch": plan.writer_epoch,
        "phase5_writer_lease_id": plan.writer_lease_id,
        "phase5_witness_transition_id": plan.witness_transition_id,
        "phase5_witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
        "legacy_runtime_compatible": False,
        "fd_binder_authorized": False,
        "runner_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def admit_physical_full_matrix_v4_phase6_failback_rebuild(
    *,
    config: PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionConfig,
    inputs: PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs,
    now: datetime,
) -> PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    """Cross-pin P5 completion and P6 rebuild evidence without any I/O."""

    expected = _config(config)
    if type(inputs) is not PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_INPUTS_INVALID")
    checked_now = _utc(
        now, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_TIME_INVALID"
    )
    request = _request(inputs.adapter_request, expected=expected)
    completion = _completion(
        inputs.phase5_completion_anchor_proof, request=request, expected=expected
    )
    completion = _completion_receipt(
        inputs.phase5_completion_receipt,
        completion=completion,
        request=request,
        expected=expected,
    )
    plan = _plan(inputs.reverse_recovery_plan, expected=expected, completion=completion)
    socket = _socket(inputs.rendered_socket_only_inputs, expected=expected, plan=plan)
    facts = _Facts(
        config=expected,
        request=request,
        completion=completion,
        plan=plan,
        socket=socket,
        now=checked_now,
    )
    result = PhysicalFullMatrixV4Phase6FailbackRebuildAdmission(**_projection(facts))
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = facts
    return result


def require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(
    value: object,
) -> PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    """Require same-process diagnostic provenance, never a rebuild permit."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase6FailbackRebuildAdmission
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_UNAUTHORIZED")
    facts = _STATES.get(value)
    if facts is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_UNAUTHORIZED")
    expected = _projection(facts)
    for name, expected_value in expected.items():
        if getattr(value, name) != expected_value:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FAILBACK_REBUILD_ADMISSION_TAMPERED")
    return value
