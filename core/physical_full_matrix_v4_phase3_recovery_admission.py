"""Pure V4 phase-3 recovery admission after a retired FI predecessor.

Phase 2 must retire the active WA-FI writer term before Phase 3 begins.  The
older WA-IR recovery runtime predates that ordering and deliberately demands a
*live* FI term, so it cannot be reused for this V4 path.  This module is the
separate, default-off admission seam for a future replacement owner: it
requires verified P2 retirement evidence, an exact detached P3 bootstrap
plan, socket-only rendered inputs, and the exact V4 Phase-3 effect-start
authority plus immutable Witness-anchor proof.

It is intentionally not a bootstrap runner, FD binder, recovery runtime,
Object-Storage client, PostgreSQL client, host installer, or promotion path.
In particular it never invokes the legacy WA-IR runtime and never interprets
this evidence as permission to materialize, start, promote, write, or switch
traffic.  A future root-owned P3 executor must consume a fresh result from
this admission in a separately reviewed FD-only ABI.
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
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as _retired


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_STATUS",
    "PhysicalFullMatrixV4Phase3RecoveryAdmission",
    "PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig",
    "PhysicalFullMatrixV4Phase3RecoveryAdmissionError",
    "PhysicalFullMatrixV4Phase3BootstrapPlanEvidence",
    "PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs",
    "PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs",
    "admit_physical_full_matrix_v4_phase3_recovery",
    "require_admitted_physical_full_matrix_v4_phase3_recovery",
)


PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase3-retired-fi-recovery-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_STATUS = (
    "retired-fi-predecessor-p3-recovery-admitted-evidence-only"
)

_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[2]
_P2_PHASE = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[1]
_SOURCE_SITE = "webapp_fi"
_RECEIVER_SITE = "webapp_ir"
_FORBIDDEN = "forbidden"
_EVIDENCE_ONLY = "evidence-only"
_ZERO_SHA256 = "0" * 64
_MAX_PLAN_BYTES = 64 * 1024
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IMAGE_REFERENCE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/:-]{1,511}@sha256:[0-9a-f]{64}$", re.ASCII
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_CAPABILITY = object()

# These immutable wire grammars are intentionally reproduced here instead of
# importing either retired V1 activation module.  Phase-3 V4 accepts only the
# canonical *evidence projection* of a detached bootstrap plan and fixed
# socket-only inputs; it cannot call, configure, or accidentally reactivate
# their historical materializer/runtime.
_BOOTSTRAP_PLAN_SCHEMA = "gold-trade-physical-postgres-standby-bootstrap-plan-v1"
_SOCKET_INPUT_SCHEMA = "gold-trade-physical-wa-ir-postgres-socket-only-recovery-input-v1"
_BOOTSTRAP_PLAN_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "bootstrap_id",
        "source_site",
        "receiver_site",
        "receiver_role",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "object_versions",
        "terminal_wal_lsn",
        "writer_term",
        "recovery_evidence_sha256",
        "source_stage_device",
        "source_stage_inode",
        "target_pgdata_device",
        "target_pgdata_inode",
        "recovery_signal_seed_sha256",
    }
)
_BOOTSTRAP_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)
_BOOTSTRAP_STAGE_OBJECT_FIELDS = frozenset({"object_key", "version_id"})
_SOCKET_INPUT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "sealed_release_descriptor_sha256",
        "deployment_manifest_lock_sha256",
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
        "promotion_authorized",
        "full_matrix_authorized",
    }
)


class PhysicalFullMatrixV4Phase3RecoveryAdmissionError(ValueError):
    """One redacted refusal from the non-executing V4 P3 admission seam."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase3RecoveryAdmissionError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig:
    """Default-off policy with exactly one preconfigured P2 fence verifier."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA
    retired_fi_predecessor_fence_config: (
        _retired.RetiredFiPredecessorFenceVerificationConfig | None
    ) = field(default=None, repr=False, compare=False)
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_DEFAULT_ENABLED
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    legacy_runtime_compatibility: str = _FORBIDDEN
    runner_authority: str = _EVIDENCE_ONLY


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase3BootstrapPlanEvidence:
    """Exact detached bootstrap-plan bytes, not a legacy materializer plan.

    These fields are intentionally only a wire projection.  The V4 admission
    parses and canonical-validates them itself, so accepting them does not
    make any retired bootstrap class, FD boundary, filesystem path, or runner
    reachable from the V4 candidate.
    """

    canonical_plan: bytes
    plan_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs:
    """Exact canonical socket-only input bytes, never a launch instruction."""

    canonical_input: bytes
    input_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs:
    """Exact evidence-only inputs for one already journaled Phase-3 effect."""

    adapter_request: _driver.PhysicalFullMatrixV4ExecutionRequest | None = field(
        default=None, repr=False, compare=False
    )
    retired_fi_predecessor_fence: object | None = field(
        default=None, repr=False, compare=False
    )
    bootstrap_plan: PhysicalFullMatrixV4Phase3BootstrapPlanEvidence | None = field(
        default=None, repr=False, compare=False
    )
    rendered_socket_only_inputs: PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase3RecoveryAdmission:
    """Opaque P3 provenance, explicitly not a materialization/runner permit."""

    schema: str
    status: str
    admission_sha256: str
    admitted_at: datetime
    run_id: UUID
    plan_sha256: str
    phase3_effect_key: str
    phase3_request_sha256: str
    phase3_claim_id: str
    phase3_effect_start_identity_sha256: str
    phase3_anchor_sequence: int
    phase3_anchor_head_sha256: str
    predecessor_fence_replay_key_sha256: str
    predecessor_fence_effect_start_identity_sha256: str
    predecessor_fence_anchor_sequence: int
    predecessor_fence_anchor_head_sha256: str
    predecessor_completion_receipt_sha256: str
    predecessor_completion_anchor_sequence: int
    predecessor_completion_anchor_head_sha256: str
    predecessor_completion_anchor_commitment_sha256: str
    predecessor_completion_anchor_attestation_sha256: str
    predecessor_completion_anchor_local_previous_record_sha256: str
    predecessor_completion_anchor_local_event_sha256: str
    predecessor_completed_at: datetime
    campaign_id: str
    release_sha: str
    bootstrap_id: str
    bootstrap_plan_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    socket_only_recovery_input_sha256: str
    predecessor_writer_epoch: int
    predecessor_writer_lease_id: str
    predecessor_witness_transition_id: str
    predecessor_witnessed_term_proof_sha256: str
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
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _PlanFacts:
    bootstrap_id: str
    plan_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class _RenderedFacts:
    input_sha256: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str


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
    journaled_effect_start_identity_sha256: str
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
    authority: object
    anchor: _AnchorFacts


@dataclass(frozen=True)
class _Facts:
    request: _RequestFacts
    retired: _retired.VerifiedRetiredFiPredecessorFence
    completion: _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
    plan: _PlanFacts
    rendered: _RenderedFacts
    now: datetime


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase3RecoveryAdmission, _Facts] = (
    WeakKeyDictionary()
)


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or _HEX64_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _positive(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)


def _canonical_mapping(
    raw: object,
    *,
    fields: frozenset[str],
    code: str,
) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_PLAN_BYTES:
        _fail(code)

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                _fail(code)
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, PhysicalFullMatrixV4Phase3RecoveryAdmissionError):
        _fail(code)
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    try:
        if canonical_json_bytes(value) != raw:
            _fail(code)
    except (TypeError, ValueError):
        _fail(code)
    return value


def _config(value: object) -> PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig:
    if type(value) is not PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_CONFIG_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_DISABLED")
    if type(value.retired_fi_predecessor_fence_config) is not _retired.RetiredFiPredecessorFenceVerificationConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_FENCE_CONFIG_REQUIRED")
    if (
        value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or value.legacy_runtime_compatibility != _FORBIDDEN
        or value.runner_authority != _EVIDENCE_ONLY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_POLICY_INVALID")
    return value


def _phase_matches(value: object, expected: _driver.PhysicalFullMatrixV4ExecutionPhase) -> bool:
    return (
        type(value) is _driver.PhysicalFullMatrixV4ExecutionPhase
        and value.sequence == expected.sequence
        and value.name == expected.name
        and value.oracle == expected.oracle
        and value.destructive is expected.destructive
        and value.transport_profile == expected.transport_profile
    )


def _binding_matches(
    left: object,
    right: object,
) -> bool:
    return type(left) is _driver.PhysicalFullMatrixV4ExecutionBinding and left == right


def _anchor_matches_effect_start(
    anchor: _AnchorFacts,
    *,
    run_id: UUID,
    plan_sha256: str,
    phase: _driver.PhysicalFullMatrixV4ExecutionPhase,
    effect_key: str,
    phase_request_sha256: str,
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding,
    claim_id: str,
    journaled_effect_start_identity_sha256: str,
) -> bool:
    """Require the portable anchor to repeat its exact effect-start pin."""

    return (
        anchor.run_id == run_id
        and anchor.plan_sha256 == plan_sha256
        and _phase_matches(anchor.phase, phase)
        and anchor.effect_key == effect_key
        and anchor.phase_request_sha256 == phase_request_sha256
        and _binding_matches(anchor.binding, binding)
        and anchor.claim_id == claim_id
        and anchor.journaled_effect_start_identity_sha256
        == journaled_effect_start_identity_sha256
    )


def _anchor(value: object, *, code: str) -> _AnchorFacts:
    required = (
        "schema",
        "run_id",
        "plan_sha256",
        "phase",
        "effect_key",
        "phase_request_sha256",
        "binding",
        "claim_id",
        "journaled_effect_start_identity_sha256",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "anchor_previous_sequence",
        "anchor_previous_head_sha256",
        "anchor_sequence",
        "anchor_head_sha256",
        "anchor_commitment_sha256",
        "anchor_attestation_sha256",
        "anchor_local_previous_record_sha256",
        "anchor_local_event_sha256",
        "anchor_occurred_at",
    )
    try:
        values = {name: getattr(value, name) for name in required}
    except AttributeError:
        _fail(code)
    if values["schema"] != _driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA:
        _fail(code)
    if type(values["run_id"]) is not UUID or values["run_id"].int == 0:
        _fail(code)
    if (
        type(values["phase"]) is not _driver.PhysicalFullMatrixV4ExecutionPhase
        or type(values["binding"]) is not _driver.PhysicalFullMatrixV4ExecutionBinding
        or type(values["claim_id"]) is not str
        or _IDENTIFIER_RE.fullmatch(values["claim_id"]) is None
    ):
        _fail(code)
    for name in (
        "plan_sha256",
        "effect_key",
        "phase_request_sha256",
        "journaled_effect_start_identity_sha256",
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "anchor_head_sha256",
        "anchor_commitment_sha256",
        "anchor_attestation_sha256",
        "anchor_local_event_sha256",
    ):
        _sha256(values[name], code=code)
    genesis_head = _sha256(
        values["anchor_genesis_head_sha256"], code=code, permit_zero=True
    )
    previous_head = _sha256(
        values["anchor_previous_head_sha256"], code=code, permit_zero=True
    )
    local_previous = _sha256(
        values["anchor_local_previous_record_sha256"], code=code, permit_zero=True
    )
    genesis = values["anchor_genesis_sequence"]
    previous = values["anchor_previous_sequence"]
    sequence = values["anchor_sequence"]
    if (
        type(genesis) is not int
        or genesis < 0
        or type(previous) is not int
        or previous < genesis
        or type(sequence) is not int
        or sequence != previous + 1
        or (previous == genesis and previous_head != genesis_head)
    ):
        _fail(code)
    return _AnchorFacts(
        schema=values["schema"],
        run_id=values["run_id"],
        plan_sha256=values["plan_sha256"],
        phase=values["phase"],
        effect_key=values["effect_key"],
        phase_request_sha256=values["phase_request_sha256"],
        binding=values["binding"],
        claim_id=values["claim_id"],
        journaled_effect_start_identity_sha256=(
            values["journaled_effect_start_identity_sha256"]
        ),
        journal_binding_sha256=values["journal_binding_sha256"],
        baseline_plan_binding_sha256=values["baseline_plan_binding_sha256"],
        genesis_sequence=genesis,
        genesis_head_sha256=genesis_head,
        previous_sequence=previous,
        previous_head_sha256=previous_head,
        sequence=sequence,
        head_sha256=values["anchor_head_sha256"],
        commitment_sha256=values["anchor_commitment_sha256"],
        attestation_sha256=values["anchor_attestation_sha256"],
        local_previous_record_sha256=local_previous,
        local_event_sha256=values["anchor_local_event_sha256"],
        occurred_at=_utc(values["anchor_occurred_at"], code=code),
    )


def _request(value: object) -> _RequestFacts:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_REQUEST_REQUIRED")
    request = value
    if not _phase_matches(request.phase, _PHASE):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_PHASE_INVALID")
    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        proof = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase3RecoveryAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_EFFECT_START_REQUIRED"
        ) from exc
    if (
        authority.run_id != request.run_id
        or authority.plan_sha256 != request.plan_sha256
        or not _phase_matches(authority.phase, _PHASE)
        or authority.effect_key != request.effect_key
        or authority.phase_request_sha256 != request.phase_request_sha256
        or not _binding_matches(authority.binding, request.binding)
        or authority.writer_authorized is not False
        or authority.promotion_authorized is not False
        or authority.execution_authorized is not False
        or authority.full_matrix_authorized is not False
        or authority.full_matrix_executed is not False
        or proof.run_id != request.run_id
        or proof.plan_sha256 != request.plan_sha256
        or not _phase_matches(proof.phase, _PHASE)
        or proof.effect_key != request.effect_key
        or proof.phase_request_sha256 != request.phase_request_sha256
        or not _binding_matches(proof.binding, request.binding)
        or proof.claim_id != authority.claim_id
        or proof.journaled_effect_start_identity_sha256
        != authority.journaled_effect_start_identity_sha256
        or proof.writer_authorized is not False
        or proof.promotion_authorized is not False
        or proof.execution_authorized is not False
        or proof.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_EFFECT_START_MISMATCH")
    _sha256(request.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_REQUEST_INVALID")
    _sha256(request.effect_key, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_REQUEST_INVALID")
    _sha256(request.phase_request_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_REQUEST_INVALID")
    _sha256(
        authority.journaled_effect_start_identity_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_EFFECT_START_MISMATCH",
    )
    anchor = _anchor(
        proof,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_EFFECT_START_ANCHOR_INVALID",
    )
    if not _anchor_matches_effect_start(
        anchor,
        run_id=authority.run_id,
        plan_sha256=authority.plan_sha256,
        phase=authority.phase,
        effect_key=authority.effect_key,
        phase_request_sha256=authority.phase_request_sha256,
        binding=authority.binding,
        claim_id=authority.claim_id,
        journaled_effect_start_identity_sha256=(
            authority.journaled_effect_start_identity_sha256
        ),
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_EFFECT_START_ANCHOR_INVALID")
    return _RequestFacts(request=request, authority=authority, anchor=anchor)


def _plan(value: object) -> _PlanFacts:
    if type(value) is not PhysicalFullMatrixV4Phase3BootstrapPlanEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_REQUIRED")
    plan = value
    raw = plan.canonical_plan
    payload = _canonical_mapping(
        raw,
        fields=_BOOTSTRAP_PLAN_FIELDS,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID",
    )
    plan_sha256 = _sha256(
        plan.plan_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != plan_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    if (
        payload["schema"] != _BOOTSTRAP_PLAN_SCHEMA
        or payload["kind"] != "local_standby_bootstrap_materialization_intent"
        or payload["source_site"] != _SOURCE_SITE
        or payload["receiver_site"] != _RECEIVER_SITE
        or payload["receiver_role"] != "standby"
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    bootstrap_id = _sha256(
        payload["bootstrap_id"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID",
    )
    for item in (
        payload["bundle_id"],
        payload["stage_receipt_sha256"],
        payload["route_binding_sha256"],
        payload["recovery_signal_seed_sha256"],
        payload["recovery_evidence_sha256"],
    ):
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    if (
        type(payload["terminal_wal_lsn"]) is not str
        or _LSN_RE.fullmatch(payload["terminal_wal_lsn"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    for item in (
        payload["source_stage_device"],
        payload["source_stage_inode"],
        payload["target_pgdata_device"],
        payload["target_pgdata_inode"],
    ):
        _positive(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    term = payload["writer_term"]
    if type(term) is not dict or set(term) != _BOOTSTRAP_TERM_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    if (
        term["holder_site"] != _SOURCE_SITE
        or type(term["writer_epoch"]) is not int
        or term["writer_epoch"] < 1
        or type(term["writer_lease_id"]) is not str
        or LEASE_ID_RE.fullmatch(term["writer_lease_id"]) is None
        or type(term["witness_transition_id"]) is not str
        or _IDENTIFIER_RE.fullmatch(term["witness_transition_id"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    _sha256(
        term["witnessed_term_proof_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID",
    )
    manifests = payload["manifest_sha256es"]
    if (
        type(manifests) is not list
        or not manifests
        or len(set(manifests)) != len(manifests)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    for item in manifests:
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    versions = payload["object_versions"]
    if type(versions) is not list or not versions:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
    seen_versions: set[tuple[str, str]] = set()
    for item in versions:
        if type(item) is not dict or set(item) != _BOOTSTRAP_STAGE_OBJECT_FIELDS:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
        key, version = item["object_key"], item["version_id"]
        if (
            type(key) is not str
            or OBJECT_KEY_RE.fullmatch(key) is None
            or ".." in key.split("/")
            or type(version) is not str
            or VERSION_ID_RE.fullmatch(version) is None
            or (key, version) in seen_versions
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_BOOTSTRAP_PLAN_INVALID")
        seen_versions.add((key, version))
    return _PlanFacts(
        bootstrap_id=bootstrap_id,
        plan_sha256=plan_sha256,
        bundle_id=payload["bundle_id"],
        stage_receipt_sha256=payload["stage_receipt_sha256"],
        route_binding_sha256=payload["route_binding_sha256"],
        writer_epoch=term["writer_epoch"],
        writer_lease_id=term["writer_lease_id"],
        witness_transition_id=term["witness_transition_id"],
        witnessed_term_proof_sha256=term["witnessed_term_proof_sha256"],
    )


def _rendered(value: object, *, plan: _PlanFacts) -> _RenderedFacts:
    if type(value) is not PhysicalFullMatrixV4Phase3SocketOnlyRecoveryInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_REQUIRED")
    rendered = value
    raw = rendered.canonical_input
    payload = _canonical_mapping(
        raw,
        fields=_SOCKET_INPUT_FIELDS,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID",
    )
    input_sha256 = _sha256(
        rendered.input_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != input_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID")
    if (
        payload["schema"] != _SOCKET_INPUT_SCHEMA
        or payload["status"] != "default-off-socket-only-recovery-input"
        or payload["postgres_major"] != 15
        or payload["network_mode"] != "none"
        or payload["tcp_listener"] != "disabled"
        or payload["unix_socket_directory"] != "/var/run/postgresql"
        or payload["unix_socket_port"] != 5432
        or payload["socket_authentication"] != "peer-local-only"
        or payload["recovery_mode"] != "standby-replay-only"
        or payload["direct_site_control"] != _FORBIDDEN
        or payload["destination_object_ingest"] != "pull-only"
        or payload["promotion_authorized"] is not False
        or payload["full_matrix_authorized"] is not False
        or payload["route_binding_sha256"] != plan.route_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID")
    if (
        type(payload["campaign_id"]) is not str
        or CAMPAIGN_ID_RE.fullmatch(payload["campaign_id"]) is None
        or type(payload["release_sha"]) is not str
        or RELEASE_SHA_RE.fullmatch(payload["release_sha"]) is None
        or type(payload["postgres_image"]) is not str
        or _IMAGE_REFERENCE_RE.fullmatch(payload["postgres_image"]) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID")
    for item in (
        payload["route_binding_sha256"],
        payload["deployment_manifest_lock_sha256"],
        payload["sealed_release_descriptor_sha256"],
    ):
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SOCKET_INPUT_INVALID")
    return _RenderedFacts(
        input_sha256=input_sha256,
        campaign_id=payload["campaign_id"],
        release_sha=payload["release_sha"],
        route_binding_sha256=payload["route_binding_sha256"],
    )


def _require_retired_fence(
    value: object,
    *,
    config: _retired.RetiredFiPredecessorFenceVerificationConfig,
    now: datetime,
) -> _retired.VerifiedRetiredFiPredecessorFence:
    try:
        result = _retired.require_verified_retired_fi_predecessor_fence(
            value,
            config=config,
            now=now,
        )
    except _retired.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4Phase3RecoveryAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID"
        ) from exc
    if type(result) is not _retired.VerifiedRetiredFiPredecessorFence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID")
    if (
        result.writer_authorized is not False
        or result.promotion_authorized is not False
        or result.traffic_switch_authorized is not False
        or result.external_effect_authorized is not False
        or result.execution_authorized is not False
        or result.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID")
    return result


def _require_phase2_completion_anchor_proof(
    *,
    request: _RequestFacts,
    retired: _retired.VerifiedRetiredFiPredecessorFence,
) -> _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
    """Require the journal's exact P2-completed → P3-start bridge.

    The input is intentionally not an independently parseable receipt.  It
    must be the exact process-local object already attached to the P3 adapter
    request by the root driver.  The driver validates its successor pins;
    this P3 seam additionally binds its predecessor start *and every P2
    start-anchor pin* to the independently verified FI retirement fence.
    This makes a P2 start→P3 start shortcut, a completion from another P2
    effect, or an intervening Witness head fail closed.
    """

    code = "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_P2_COMPLETION_ANCHOR_INVALID"
    try:
        proof = _driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
            request=request.request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        if exc.code.endswith("_REQUIRED"):
            _fail(
                "PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_P2_COMPLETION_ANCHOR_REQUIRED"
            )
        raise PhysicalFullMatrixV4Phase3RecoveryAdmissionError(code) from exc

    p2 = retired.effect_start
    p2_anchor = retired.effect_start_anchor
    p3_anchor = request.anchor
    if (
        proof.predecessor_phase_name != _P2_PHASE.name
        or proof.predecessor_phase_sequence != _P2_PHASE.sequence
        or proof.run_id != p2.run_id
        or proof.plan_sha256 != p2.plan_sha256
        or proof.predecessor_effect_key != p2.effect_key
        or proof.predecessor_phase_request_sha256 != p2.phase_request_sha256
        or proof.predecessor_claim_id != p2.claim_id
        or proof.predecessor_effect_start_identity_sha256
        != p2.journaled_effect_start_identity_sha256
        or proof.journal_binding_sha256 != p2_anchor.journal_binding_sha256
        or proof.baseline_plan_binding_sha256
        != p2_anchor.baseline_plan_binding_sha256
        or proof.anchor_genesis_sequence != p2_anchor.anchor_genesis_sequence
        or proof.anchor_genesis_head_sha256 != p2_anchor.anchor_genesis_head_sha256
        or proof.predecessor_effect_start_anchor_previous_sequence
        != p2_anchor.anchor_previous_sequence
        or proof.predecessor_effect_start_anchor_previous_head_sha256
        != p2_anchor.anchor_previous_head_sha256
        or proof.predecessor_effect_start_anchor_sequence
        != p2_anchor.anchor_sequence
        or proof.predecessor_effect_start_anchor_head_sha256
        != p2_anchor.anchor_head_sha256
        or proof.predecessor_effect_start_anchor_commitment_sha256
        != p2_anchor.anchor_commitment_sha256
        or proof.predecessor_effect_start_anchor_attestation_sha256
        != p2_anchor.anchor_attestation_sha256
        or proof.predecessor_effect_start_anchor_local_previous_record_sha256
        != p2_anchor.anchor_local_previous_record_sha256
        or proof.predecessor_effect_start_anchor_local_event_sha256
        != p2_anchor.anchor_local_event_sha256
        or proof.predecessor_effect_started_at != p2_anchor.anchor_occurred_at
        or proof.successor_phase_name != _PHASE.name
        or proof.successor_phase_sequence != _PHASE.sequence
        or proof.successor_effect_key != request.authority.effect_key
        or proof.successor_phase_request_sha256
        != request.authority.phase_request_sha256
        or proof.successor_claim_id != request.authority.claim_id
        or proof.successor_effect_start_identity_sha256
        != request.authority.journaled_effect_start_identity_sha256
        or proof.successor_effect_start_anchor_previous_sequence
        != p3_anchor.previous_sequence
        or proof.successor_effect_start_anchor_previous_head_sha256
        != p3_anchor.previous_head_sha256
        or proof.successor_effect_start_anchor_sequence != p3_anchor.sequence
        or proof.successor_effect_start_anchor_head_sha256 != p3_anchor.head_sha256
        or proof.predecessor_completion_anchor_sequence != p3_anchor.previous_sequence
        or proof.predecessor_completion_anchor_head_sha256 != p3_anchor.previous_head_sha256
        or proof.predecessor_completion_anchor_sequence
        != proof.predecessor_effect_start_anchor_sequence + 1
        or proof.predecessor_completion_anchor_previous_sequence
        != proof.predecessor_effect_start_anchor_sequence
        or proof.predecessor_completion_anchor_previous_head_sha256
        != proof.predecessor_effect_start_anchor_head_sha256
        or proof.predecessor_effect_started_at > proof.predecessor_completed_at
        or _utc(
            retired.retired_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID",
        )
        > proof.predecessor_completed_at
        or _utc(
            retired.admitted_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID",
        )
        > proof.predecessor_completed_at
        or proof.predecessor_completed_at > p3_anchor.occurred_at
        or proof.writer_authorized is not False
        or proof.promotion_authorized is not False
        or proof.execution_authorized is not False
        or proof.full_matrix_authorized is not False
        or proof.full_matrix_executed is not False
    ):
        _fail(code)
    return proof


def _cross_pin(
    *,
    request: _RequestFacts,
    retired: _retired.VerifiedRetiredFiPredecessorFence,
    plan: _PlanFacts,
    rendered: _RenderedFacts,
    now: datetime,
) -> None:
    p3 = request.request
    p2 = retired.effect_start
    if (
        not _phase_matches(p2.phase, _P2_PHASE)
        or p2.run_id != p3.run_id
        or p2.plan_sha256 != p3.plan_sha256
        or not _binding_matches(p2.binding, p3.binding)
        or p2.effect_key == p3.effect_key
        or p2.phase_request_sha256 == p3.phase_request_sha256
        or p2.claim_id == request.authority.claim_id
        or p2.journaled_effect_start_identity_sha256
        == request.authority.journaled_effect_start_identity_sha256
        or p2.binding.campaign_id != rendered.campaign_id
        or p2.binding.release_sha != rendered.release_sha
        or p2.binding.writer_holder_site != _SOURCE_SITE
        or p2.binding.source_site != _SOURCE_SITE
        or p2.binding.destination_site != _RECEIVER_SITE
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_PHASE_CROSS_PIN_MISMATCH")
    predecessor = retired.predecessor_term
    if (
        predecessor.holder_site != _SOURCE_SITE
        or predecessor.writer_epoch != plan.writer_epoch
        or predecessor.writer_lease_id != plan.writer_lease_id
        or predecessor.witness_transition_id != plan.witness_transition_id
        or predecessor.witnessed_term_proof_sha256 != plan.witnessed_term_proof_sha256
        or p2.binding.writer_epoch != plan.writer_epoch
        or p2.binding.writer_lease_id != plan.writer_lease_id
        or p2.binding.witness_transition_id != plan.witness_transition_id
        or p2.binding.witnessed_term_proof_sha256 != plan.witnessed_term_proof_sha256
        or p2.binding.route_commitment_sha256 != plan.route_binding_sha256
        or rendered.route_binding_sha256 != plan.route_binding_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_PREDECESSOR_TERM_MISMATCH")
    p2_anchor = _anchor(
        retired.effect_start_anchor,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_ANCHOR_INVALID",
    )
    if not _anchor_matches_effect_start(
        p2_anchor,
        run_id=p2.run_id,
        plan_sha256=p2.plan_sha256,
        phase=p2.phase,
        effect_key=p2.effect_key,
        phase_request_sha256=p2.phase_request_sha256,
        binding=p2.binding,
        claim_id=p2.claim_id,
        journaled_effect_start_identity_sha256=(
            p2.journaled_effect_start_identity_sha256
        ),
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_ANCHOR_INVALID")
    p3_anchor = request.anchor
    if (
        p2_anchor.journal_binding_sha256 != p3_anchor.journal_binding_sha256
        or p2_anchor.baseline_plan_binding_sha256
        != p3_anchor.baseline_plan_binding_sha256
        or p2_anchor.genesis_sequence != p3_anchor.genesis_sequence
        or p2_anchor.genesis_head_sha256 != p3_anchor.genesis_head_sha256
        or p3_anchor.head_sha256 == p2_anchor.head_sha256
        or p2_anchor.occurred_at > p3_anchor.occurred_at
        or _utc(retired.retired_at, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID")
        > p3_anchor.occurred_at
        or _utc(retired.admitted_at, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID")
        > p3_anchor.occurred_at
        or _utc(retired.expires_at, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_FENCE_INVALID")
        <= now
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_ANCHOR_CROSS_PIN_MISMATCH")
    # Do not infer a predecessor relation from ``p2_anchor.sequence <
    # p3_anchor.sequence``.  The journal inserts P2's completed receipt
    # anchor between the two starts, so only the reserved typed completion
    # proof establishes the exact P2-completion → P3-start adjacency.


def _phase_body(value: _driver.PhysicalFullMatrixV4ExecutionPhase) -> dict[str, object]:
    return {
        "sequence": value.sequence,
        "name": value.name,
        "oracle": value.oracle,
        "destructive": value.destructive,
        "transport_profile": value.transport_profile,
    }


def _binding_body(value: _driver.PhysicalFullMatrixV4ExecutionBinding) -> dict[str, object]:
    return {
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "readiness_binding_sha256": value.readiness_binding_sha256,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "writer_holder_site": value.writer_holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "roundtrip_attestation_sha256": value.roundtrip_attestation_sha256,
        "roundtrip_configuration_sha256": value.roundtrip_configuration_sha256,
        "witness_transition_id": value.witness_transition_id,
        "witness_sequence": value.witness_sequence,
    }


def _anchor_body(value: _AnchorFacts) -> dict[str, object]:
    return {
        "schema": value.schema,
        "run_id": str(value.run_id),
        "plan_sha256": value.plan_sha256,
        "phase": _phase_body(value.phase),
        "effect_key": value.effect_key,
        "phase_request_sha256": value.phase_request_sha256,
        "binding": _binding_body(value.binding),
        "claim_id": value.claim_id,
        "journaled_effect_start_identity_sha256": (
            value.journaled_effect_start_identity_sha256
        ),
        "journal_binding_sha256": value.journal_binding_sha256,
        "baseline_plan_binding_sha256": value.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": value.genesis_sequence,
        "anchor_genesis_head_sha256": value.genesis_head_sha256,
        "anchor_previous_sequence": value.previous_sequence,
        "anchor_previous_head_sha256": value.previous_head_sha256,
        "anchor_sequence": value.sequence,
        "anchor_head_sha256": value.head_sha256,
        "anchor_commitment_sha256": value.commitment_sha256,
        "anchor_attestation_sha256": value.attestation_sha256,
        "anchor_local_previous_record_sha256": value.local_previous_record_sha256,
        "anchor_local_event_sha256": value.local_event_sha256,
        "anchor_occurred_at": value.occurred_at.isoformat(),
    }


def _admission_body(facts: _Facts) -> dict[str, object]:
    request = facts.request.request
    retired = facts.retired
    plan = facts.plan
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_STATUS,
        "run_id": str(request.run_id),
        "plan_sha256": request.plan_sha256,
        "phase": _phase_body(request.phase),
        "phase3_effect_key": request.effect_key,
        "phase3_request_sha256": request.phase_request_sha256,
        "phase3_claim_id": facts.request.authority.claim_id,
        "phase3_effect_start_identity_sha256": facts.request.authority.journaled_effect_start_identity_sha256,
        "phase3_anchor": _anchor_body(facts.request.anchor),
        "predecessor_fence_replay_key_sha256": retired.replay_key_sha256,
        "predecessor_fence_effect_start_identity_sha256": retired.effect_start.journaled_effect_start_identity_sha256,
        "predecessor_fence_anchor": _anchor_body(
            _anchor(
                retired.effect_start_anchor,
                code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_ANCHOR_INVALID",
            )
        ),
        "predecessor_completion": {
            "receipt_sha256": facts.completion.predecessor_completion_receipt_sha256,
            "anchor_sequence": facts.completion.predecessor_completion_anchor_sequence,
            "anchor_head_sha256": facts.completion.predecessor_completion_anchor_head_sha256,
            "anchor_commitment_sha256": (
                facts.completion.predecessor_completion_anchor_commitment_sha256
            ),
            "anchor_attestation_sha256": (
                facts.completion.predecessor_completion_anchor_attestation_sha256
            ),
            "anchor_local_previous_record_sha256": (
                facts.completion.predecessor_completion_anchor_local_previous_record_sha256
            ),
            "anchor_local_event_sha256": (
                facts.completion.predecessor_completion_anchor_local_event_sha256
            ),
            "completed_at": facts.completion.predecessor_completed_at.isoformat(),
        },
        "campaign_id": facts.rendered.campaign_id,
        "release_sha": facts.rendered.release_sha,
        "bootstrap_id": plan.bootstrap_id,
        "bootstrap_plan_sha256": plan.plan_sha256,
        "bundle_id": plan.bundle_id,
        "stage_receipt_sha256": plan.stage_receipt_sha256,
        "route_binding_sha256": plan.route_binding_sha256,
        "socket_only_recovery_input_sha256": facts.rendered.input_sha256,
        "predecessor_term": {
            "writer_epoch": plan.writer_epoch,
            "writer_lease_id": plan.writer_lease_id,
            "witness_transition_id": plan.witness_transition_id,
            "witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
        },
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


def _result_public_projection(facts: _Facts) -> dict[str, object]:
    """Recompute the complete visible result without minting another handle."""

    request = facts.request.request
    retired = facts.retired
    plan = facts.plan
    body = _admission_body(facts)
    try:
        digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_CANONICAL_INVALID")
    p2_anchor = _anchor(
        retired.effect_start_anchor,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_RETIRED_FI_ANCHOR_INVALID",
    )
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_STATUS,
        "admission_sha256": digest,
        "admitted_at": facts.now,
        "run_id": request.run_id,
        "plan_sha256": request.plan_sha256,
        "phase3_effect_key": request.effect_key,
        "phase3_request_sha256": request.phase_request_sha256,
        "phase3_claim_id": facts.request.authority.claim_id,
        "phase3_effect_start_identity_sha256": (
            facts.request.authority.journaled_effect_start_identity_sha256
        ),
        "phase3_anchor_sequence": facts.request.anchor.sequence,
        "phase3_anchor_head_sha256": facts.request.anchor.head_sha256,
        "predecessor_fence_replay_key_sha256": retired.replay_key_sha256,
        "predecessor_fence_effect_start_identity_sha256": (
            retired.effect_start.journaled_effect_start_identity_sha256
        ),
        "predecessor_fence_anchor_sequence": p2_anchor.sequence,
        "predecessor_fence_anchor_head_sha256": p2_anchor.head_sha256,
        "predecessor_completion_receipt_sha256": (
            facts.completion.predecessor_completion_receipt_sha256
        ),
        "predecessor_completion_anchor_sequence": (
            facts.completion.predecessor_completion_anchor_sequence
        ),
        "predecessor_completion_anchor_head_sha256": (
            facts.completion.predecessor_completion_anchor_head_sha256
        ),
        "predecessor_completion_anchor_commitment_sha256": (
            facts.completion.predecessor_completion_anchor_commitment_sha256
        ),
        "predecessor_completion_anchor_attestation_sha256": (
            facts.completion.predecessor_completion_anchor_attestation_sha256
        ),
        "predecessor_completion_anchor_local_previous_record_sha256": (
            facts.completion.predecessor_completion_anchor_local_previous_record_sha256
        ),
        "predecessor_completion_anchor_local_event_sha256": (
            facts.completion.predecessor_completion_anchor_local_event_sha256
        ),
        "predecessor_completed_at": facts.completion.predecessor_completed_at,
        "campaign_id": facts.rendered.campaign_id,
        "release_sha": facts.rendered.release_sha,
        "bootstrap_id": plan.bootstrap_id,
        "bootstrap_plan_sha256": plan.plan_sha256,
        "bundle_id": plan.bundle_id,
        "stage_receipt_sha256": plan.stage_receipt_sha256,
        "route_binding_sha256": plan.route_binding_sha256,
        "socket_only_recovery_input_sha256": facts.rendered.input_sha256,
        "predecessor_writer_epoch": plan.writer_epoch,
        "predecessor_writer_lease_id": plan.writer_lease_id,
        "predecessor_witness_transition_id": plan.witness_transition_id,
        "predecessor_witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
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


def _result_from_facts(facts: _Facts) -> PhysicalFullMatrixV4Phase3RecoveryAdmission:
    result = PhysicalFullMatrixV4Phase3RecoveryAdmission(
        **_result_public_projection(facts)
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = facts
    return result


def admit_physical_full_matrix_v4_phase3_recovery(
    *,
    config: PhysicalFullMatrixV4Phase3RecoveryAdmissionConfig,
    inputs: PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs,
    now: datetime,
) -> PhysicalFullMatrixV4Phase3RecoveryAdmission:
    """Cross-pin P2 retirement and Phase-3 recovery evidence without I/O.

    This function intentionally does not call a runner or legacy recovery
    helper.  It only emits a non-authorizing provenance object after the
    predecessor term is proven retired and every P3 input is exact.
    """

    checked_config = _config(config)
    if type(inputs) is not PhysicalFullMatrixV4Phase3RecoveryAdmissionInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_INPUTS_INVALID")
    checked_now = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_TIME_INVALID")
    request = _request(inputs.adapter_request)
    retired = _require_retired_fence(
        inputs.retired_fi_predecessor_fence,
        config=checked_config.retired_fi_predecessor_fence_config,
        now=checked_now,
    )
    plan = _plan(inputs.bootstrap_plan)
    rendered = _rendered(inputs.rendered_socket_only_inputs, plan=plan)
    _cross_pin(
        request=request,
        retired=retired,
        plan=plan,
        rendered=rendered,
        now=checked_now,
    )
    completion = _require_phase2_completion_anchor_proof(
        request=request,
        retired=retired,
    )
    facts = _Facts(
        request=request,
        retired=retired,
        completion=completion,
        plan=plan,
        rendered=rendered,
        now=checked_now,
    )
    return _result_from_facts(facts)


def require_admitted_physical_full_matrix_v4_phase3_recovery(
    value: object,
) -> PhysicalFullMatrixV4Phase3RecoveryAdmission:
    """Require only a same-process diagnostic result, never a runner permit."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase3RecoveryAdmission
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_UNAUTHORIZED")
    facts = _STATES.get(value)
    if facts is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_UNAUTHORIZED")
    expected = _result_public_projection(facts)
    # Compare the recomputed public immutable projection without minting a
    # second capability handle just to validate the first one.
    for field_name in (
        "schema",
        "status",
        "admission_sha256",
        "admitted_at",
        "run_id",
        "plan_sha256",
        "phase3_effect_key",
        "phase3_request_sha256",
        "phase3_claim_id",
        "phase3_effect_start_identity_sha256",
        "phase3_anchor_sequence",
        "phase3_anchor_head_sha256",
        "predecessor_fence_replay_key_sha256",
        "predecessor_fence_effect_start_identity_sha256",
        "predecessor_fence_anchor_sequence",
        "predecessor_fence_anchor_head_sha256",
        "predecessor_completion_receipt_sha256",
        "predecessor_completion_anchor_sequence",
        "predecessor_completion_anchor_head_sha256",
        "predecessor_completion_anchor_commitment_sha256",
        "predecessor_completion_anchor_attestation_sha256",
        "predecessor_completion_anchor_local_previous_record_sha256",
        "predecessor_completion_anchor_local_event_sha256",
        "predecessor_completed_at",
        "campaign_id",
        "release_sha",
        "bootstrap_id",
        "bootstrap_plan_sha256",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "socket_only_recovery_input_sha256",
        "predecessor_writer_epoch",
        "predecessor_writer_lease_id",
        "predecessor_witness_transition_id",
        "predecessor_witnessed_term_proof_sha256",
        "legacy_runtime_compatible",
        "fd_binder_authorized",
        "runner_authorized",
        "materialization_authorized",
        "promotion_authorized",
        "writer_authorized",
        "traffic_switch_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "full_matrix_executed",
    ):
        if getattr(value, field_name) != expected[field_name]:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE3_RECOVERY_ADMISSION_TAMPERED")
    return value
