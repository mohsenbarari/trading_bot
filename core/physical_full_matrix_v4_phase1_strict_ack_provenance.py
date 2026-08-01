"""Pure, non-authorizing provenance for V4 Phase 1 Strict-ACK evidence.

V4 phase one is named ``normal-fi-writer-v2-witness-roundtrip-strict-ack``.
The existing Gen2 ACK chain already verifies the full Witness-mediated
roundtrip and the Gen2 bound strict-writer response already proves the local
post-commit response.  Neither owner, however, carries a V4 run/plan/effect
identifier.  Consequently an old (but otherwise valid) ACK must never be
relabeled as the result of a V4 phase merely by hashing it in a generic
``PhysicalFullMatrixV4PhaseOracle``.

This module is deliberately the *provenance half* of the future Phase-1
adapter, not that adapter.  It does no I/O and accepts no runner, capture
handoff, database/session, Object Storage, process, host, or transport
dependency.  It proves only all of the following at one supplied root-clock
sample:

* the supplied request has the exact canonical Phase-1 V4 effect/request
  digests and a fresh, opaque pre-effect readiness capability;
* a fresh existing Gen2 ACK chain cross-pins every V4 binding scalar; and
* the full flat ``strict_*`` trace in that chain exactly matches a separately
  revalidated opaque bound strict-writer observation.

The returned process-local capability intentionally says that the strict ACK
is *not proved post-effect-bound*.  Existing V2 wire/commit contracts contain
no V4 effect key or phase-request digest.  The V4 driver supplies an adapter
only a process-local effect-start correlation; this pure verifier neither
consumes nor binds that correlation to the V2 evidence.  A later effectful
Phase-1 coordinator must add that causality and the capture
checkpoint/reconciliation boundary before it may return ``oracle-succeeded``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v2_gen2_witnessed_ack_chain as _ack_chain
from core import (
    physical_wal_v2_witness_roundtrip_strict_writer_bound_response as _bound_response,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS",
    "PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig",
    "PhysicalFullMatrixV4Phase1StrictAckProvenanceError",
    "VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance",
    "mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance",
    "require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase1-strict-ack-provenance-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_DEFAULT_ENABLED = False

# The current signed Gen2 strict-ACK/response grammar has no V4 post-effect
# correlation at all.  These are the minimum *separately signed* pins a new
# successor grammar must carry before a future adapter can prove that a Gen2
# ACK was produced for this exact journaled V4 Phase-1 start.  A same-term,
# fresh timestamp, or matching V4 request is not a substitute: every one can
# describe an ACK created before the V4 effect-start record.
PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS = (
    "run_id",
    "plan_sha256",
    "phase_sequence",
    "effect_key",
    "phase_request_sha256",
    "claim_id",
    "journaled_effect_start_identity_sha256",
    "journal_binding_sha256",
    "baseline_plan_binding_sha256",
    "anchor_sequence",
    "anchor_head_sha256",
    "anchor_commitment_sha256",
    "anchor_attestation_sha256",
)

_STATUS = "verified-gen2-strict-ack-provenance-unsequenced-not-v4-phase-success"
_ZERO_SHA256 = "0" * 64
_CAPABILITY = object()
_PHASE_NAME = "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix"
_PHASE_SEQUENCE = 1
_PHASE_ORACLE = "normal-fi-writer-v2-witness-roundtrip-strict-ack-oracle-v1"
_TRANSPORT_PROFILE = "fi-v2-witness-roundtrip-strict-ack-v1"


class PhysicalFullMatrixV4Phase1StrictAckProvenanceError(ValueError):
    """A Phase-1 strict-ACK provenance check failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig:
    """Default-off verifier configuration for one existing Gen2 ACK policy.

    This configuration is intentionally limited to the two existing owner
    policies.  It does not include a V4 plan, a runner, a transaction, a
    capture bridge, or a source of new ACK work.
    """

    gen2_witnessed_ack_chain_config: (
        _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainConfig | None
    ) = None
    bound_strict_writer_response_config: (
        _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
        | None
    ) = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance:
    """Fresh cross-pinned Strict-ACK provenance, never a Phase-1 completion.

    ``strict_ack_post_effect_bound`` remains false by construction: V2 has no
    V4 effect-key field and this pure boundary does not bind the driver's
    process-local effect-start correlation into its evidence.  The capability
    is non-serializable and its verifier retains the actual opaque owner
    capabilities in process-local state, rather than trusting the public
    projection alone.
    """

    schema: str
    status: str
    provenance_sha256: str
    v4_request_correlation_sha256: str
    run_id: UUID
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    oracle: str
    transport_profile: str
    effect_key: str
    phase_request_sha256: str
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    pre_effect_readiness_binding_sha256: str
    strict_ack_chain: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection
    observed_at: datetime
    canonical_provenance: bytes = field(repr=False)
    strict_ack_post_effect_bound: bool = False
    strict_ack_post_effect_missing_correlation_fields: tuple[str, ...] = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS
    )
    capture_handoff_verified: bool = False
    phase_effect_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    chain_config: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainConfig
    bound_config: _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig


@dataclass(frozen=True)
class _RequestFacts:
    run_id: UUID
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    oracle: str
    transport_profile: str
    effect_key: str
    phase_request_sha256: str
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    binding_snapshot: _driver._BindingSnapshot
    pre_effect_readiness: _driver.PhysicalFullMatrixV4ReadinessEvidence
    request_correlation_sha256: str


@dataclass(frozen=True)
class _Derived:
    request: _RequestFacts
    chain_projection: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection
    observed_at: datetime
    canonical_provenance: bytes
    provenance_sha256: str


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig
    chain: _ack_chain.VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain
    bound_observation: (
        _bound_response.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation
    )
    pre_effect_readiness: _driver.PhysicalFullMatrixV4ReadinessEvidence


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance, _State] = (
    WeakKeyDictionary()
)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(code) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(code) from exc


def _render_timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat().replace("+00:00", "Z")


def _config(value: object) -> _ConfigFacts:
    if (
        type(value) is not PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig
        or value.enabled is not True
        or type(value.gen2_witnessed_ack_chain_config)
        is not _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainConfig
        or type(value.bound_strict_writer_response_config)
        is not _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CONFIG_INVALID")
    chain_config = value.gen2_witnessed_ack_chain_config
    bound_config = value.bound_strict_writer_response_config
    if (
        chain_config.enabled is not True
        or bound_config.enabled is not True
        or chain_config.bound_response_config != bound_config
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CONFIG_MISMATCH")
    return _ConfigFacts(chain_config=chain_config, bound_config=bound_config)


def _phase_request_body(
    *,
    run_id: UUID,
    plan_sha256: str,
    binding: _driver._BindingSnapshot,
) -> tuple[str, str]:
    """Recompute the exact public V4 Phase-1 digests without a plan callback.

    The V4 driver intentionally hands an adapter a public request rather than
    its opaque plan.  This duplicated canonical body is therefore a verifier
    for that public request, not a substitute for plan provenance.  The
    driver's own plan capability remains the authority for invoking an
    adapter.
    """

    binding_body = dict(binding.__dict__)
    effect_body = {
        "schema": _driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
        "purpose": "root-journal-effect-key-v1",
        "run_id": str(run_id),
        "plan_sha256": plan_sha256,
        "sequence": _PHASE_SEQUENCE,
        "phase": _PHASE_NAME,
        "oracle": _PHASE_ORACLE,
        "transport_profile": _TRANSPORT_PROFILE,
        **binding_body,
        "direct_fi_to_ir_control": "forbidden",
        "direct_ir_to_fi_control": "forbidden",
        "legacy_runner_compatibility": "forbidden",
    }
    effect_key = hashlib.sha256(
        _canonical(effect_body, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    ).hexdigest()
    request_body = {
        "schema": _driver.PHYSICAL_FULL_MATRIX_V4_DRIVER_SCHEMA,
        "run_id": str(run_id),
        "plan_sha256": plan_sha256,
        "sequence": _PHASE_SEQUENCE,
        "phase": _PHASE_NAME,
        "oracle": _PHASE_ORACLE,
        "transport_profile": _TRANSPORT_PROFILE,
        "effect_key": effect_key,
        **binding_body,
        "direct_fi_to_ir_control": "forbidden",
        "direct_ir_to_fi_control": "forbidden",
        "legacy_runner_compatibility": "forbidden",
    }
    request_sha = hashlib.sha256(
        _canonical(request_body, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    ).hexdigest()
    return effect_key, request_sha


def _request_facts(
    value: object,
    *,
    now: datetime,
) -> _RequestFacts:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    request = value
    if type(request.run_id) is not UUID or request.run_id.int == 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    _sha256(request.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    _sha256(request.effect_key, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID")
    _sha256(
        request.phase_request_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID",
    )
    expected_phase = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
    if (
        type(request.phase) is not _driver.PhysicalFullMatrixV4ExecutionPhase
        or request.phase.sequence != expected_phase.sequence
        or request.phase.name != expected_phase.name
        or request.phase.oracle != expected_phase.oracle
        or request.phase.destructive is not expected_phase.destructive
        or request.phase.transport_profile != expected_phase.transport_profile
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_PHASE_INVALID")
    try:
        binding_snapshot = _driver._snapshot_binding(
            request.binding,
            direction=("webapp_fi", "webapp_ir"),
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_BINDING_INVALID"
        ) from exc
    expected_effect_key, expected_request_sha = _phase_request_body(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        binding=binding_snapshot,
    )
    if (
        request.effect_key != expected_effect_key
        or request.phase_request_sha256 != expected_request_sha
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_HASH_MISMATCH")
    if type(request.pre_effect_readiness_evidence) is not _driver.PhysicalFullMatrixV4ReadinessEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_PRE_EFFECT_REQUIRED")
    try:
        _driver._validate_readiness_evidence(
            request.pre_effect_readiness_evidence,
            binding=binding_snapshot,
            now=now,
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_PRE_EFFECT_INVALID"
        ) from exc
    return _RequestFacts(
        run_id=request.run_id,
        plan_sha256=request.plan_sha256,
        phase_name=request.phase.name,
        phase_sequence=request.phase.sequence,
        oracle=request.phase.oracle,
        transport_profile=request.phase.transport_profile,
        effect_key=request.effect_key,
        phase_request_sha256=request.phase_request_sha256,
        binding=_driver.PhysicalFullMatrixV4ExecutionBinding(**binding_snapshot.__dict__),
        binding_snapshot=binding_snapshot,
        pre_effect_readiness=request.pre_effect_readiness_evidence,
        request_correlation_sha256=hashlib.sha256(
            _canonical(
                {
                    "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA,
                    "run_id": str(request.run_id),
                    "plan_sha256": request.plan_sha256,
                    "phase_name": request.phase.name,
                    "phase_sequence": request.phase.sequence,
                    "oracle": request.phase.oracle,
                    "transport_profile": request.phase.transport_profile,
                    "effect_key": request.effect_key,
                    "phase_request_sha256": request.phase_request_sha256,
                    "binding": dict(binding_snapshot.__dict__),
                },
                code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_REQUEST_INVALID",
            )
        ).hexdigest(),
    )


def _strict_trace_from_bound_projection(
    value: object,
) -> dict[str, object]:
    """Render every strict field the Gen2 owner stores in its flat chain."""

    if type(value) is not _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_BOUND_RESPONSE_INVALID")
    instruction = value.instruction
    if type(instruction) is not _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_BOUND_RESPONSE_INVALID")
    return {
        "strict_observation_schema": value.schema,
        "strict_observation_sha256": value.observation_sha256,
        "strict_runtime_commit_receipt_sha256": value.runtime_commit_receipt_sha256,
        "strict_instruction_schema": instruction.schema,
        "strict_configuration_sha256": instruction.configuration_sha256,
        "strict_v2_base_configuration_sha256": instruction.v2_base_configuration_sha256,
        "strict_atomic_commit_boundary": instruction.atomic_commit_boundary,
        "strict_commit_id": instruction.commit_id,
        "strict_v2_base_commit_id": instruction.v2_base_commit_id,
        "strict_local_commit_record_id": value.local_commit_record_id,
        "strict_local_response_id": value.local_response_id,
        "strict_attestation_consumption_id": value.attestation_consumption_id,
        "strict_committed_at": value.committed_at,
        "strict_issued_at": instruction.issued_at,
        "strict_v1_parent_cluster_id": instruction.v1_parent_cluster_id,
        "strict_v1_parent_local_site": instruction.v1_parent_local_site,
        "strict_v1_parent_release_sha": instruction.v1_parent_release_sha,
        "strict_v1_parent_generation_id": instruction.v1_parent_generation_id,
        "strict_v1_writer_admission_commit_id": instruction.v1_writer_admission_commit_id,
        "strict_v1_writer_admission_commit_sha256": instruction.v1_writer_admission_commit_sha256,
        "strict_v1_writer_admission_receipt_sha256": instruction.v1_writer_admission_receipt_sha256,
        "strict_v1_parent_prior_revision": instruction.v1_parent_prior_revision,
        "strict_v1_parent_next_revision": instruction.v1_parent_next_revision,
        "strict_v1_parent_fence_generation": instruction.v1_parent_fence_generation,
        "strict_v1_parent_holder_site": instruction.v1_parent_holder_site,
        "strict_v1_parent_evidence_id": instruction.v1_parent_evidence_id,
        "strict_v1_parent_revalidation_id": instruction.v1_parent_revalidation_id,
        "strict_v1_parent_writer_epoch": instruction.v1_parent_writer_epoch,
        "strict_v1_parent_writer_lease_id": instruction.v1_parent_writer_lease_id,
        "strict_v1_parent_term_issued_at": instruction.v1_parent_term_issued_at,
        "strict_v1_parent_term_expires_at": instruction.v1_parent_term_expires_at,
        "strict_v1_parent_admitted_at": instruction.v1_parent_admitted_at,
        "strict_v1_v2_writer_term_bridge_certificate_id": instruction.v1_v2_writer_term_bridge_certificate_id,
        "strict_v1_v2_writer_term_bridge_intent_sha256": instruction.v1_v2_writer_term_bridge_intent_sha256,
        "strict_v1_v2_writer_term_bridge_certificate_sha256": instruction.v1_v2_writer_term_bridge_certificate_sha256,
        "strict_v1_v2_writer_term_bridge_parent_binding_sha256": instruction.v1_v2_writer_term_bridge_parent_binding_sha256,
    }


_STRICT_CHAIN_FIELDS = tuple(
    name
    for name in _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__
    if name.startswith("strict_")
)


def _json_value(value: object, *, code: str) -> object:
    if type(value) is datetime:
        return _render_timestamp(value, code=code)
    if type(value) in {str, int, bool} or value is None:
        return value
    _fail(code)


def _chain_body(
    value: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection,
) -> dict[str, object]:
    if type(value) is not _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CHAIN_INVALID")
    body = {
        name: _json_value(
            getattr(value, name),
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CHAIN_INVALID",
        )
        for name in _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__
    }
    if (
        value.recovery_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CHAIN_INVALID")
    return body


def _cross_pin_chain_to_request(
    *,
    request: _RequestFacts,
    chain: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection,
) -> None:
    binding = request.binding
    expected = {
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "writer_holder_site": binding.writer_holder_site,
        "writer_epoch": binding.writer_epoch,
        "writer_lease_id": binding.writer_lease_id,
        "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        "roundtrip_attestation_sha256": binding.roundtrip_attestation_sha256,
        "roundtrip_configuration_sha256": binding.roundtrip_configuration_sha256,
        "witness_transition_id": binding.witness_transition_id,
        "witness_sequence": binding.witness_sequence,
    }
    if (
        any(getattr(chain, name) != expected_value for name, expected_value in expected.items())
        or chain.activation_mode != "normal_fi_writer"
        or chain.recovery_authorized is not False
        or chain.promotion_authorized is not False
        or chain.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CHAIN_BINDING_MISMATCH")


def _cross_pin_bound_to_chain(
    *,
    chain: _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection,
    bound_projection: _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseProjection,
) -> None:
    trace = _strict_trace_from_bound_projection(bound_projection)
    if set(trace) != set(_STRICT_CHAIN_FIELDS):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_INTERNAL_STRICT_TRACE_INVALID")
    if any(getattr(chain, name) != trace[name] for name in _STRICT_CHAIN_FIELDS):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_BOUND_CHAIN_MISMATCH")


def _derive(
    *,
    config: PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    gen2_witnessed_ack_chain: _ack_chain.VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain,
    bound_strict_writer_response: _bound_response.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    now: datetime,
    provenance_observed_at: datetime | None = None,
) -> _Derived:
    facts = _config(config)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CLOCK_INVALID")
    recorded_at = (
        observed
        if provenance_observed_at is None
        else _utc(
            provenance_observed_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CLOCK_INVALID",
        )
    )
    request_facts = _request_facts(request, now=observed)
    try:
        _ack_chain.require_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
            gen2_witnessed_ack_chain,
            config=facts.chain_config,
            now=observed,
        )
        chain_projection = _ack_chain.project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
            gen2_witnessed_ack_chain,
            config=facts.chain_config,
            now=observed,
        )
        _bound_response.require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
            bound_strict_writer_response,
            config=facts.bound_config,
        )
        bound_projection = _bound_response.project_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
            bound_strict_writer_response,
            config=facts.bound_config,
        )
    except (
        _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainError,
        _bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
        TypeError,
        ValueError,
    ) as exc:
        raise PhysicalFullMatrixV4Phase1StrictAckProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_OWNER_INVALID"
        ) from exc
    if type(chain_projection) is not _ack_chain.PhysicalFullMatrixV2Gen2WitnessedAckChainProjection:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CHAIN_INVALID")
    _cross_pin_chain_to_request(request=request_facts, chain=chain_projection)
    _cross_pin_bound_to_chain(chain=chain_projection, bound_projection=bound_projection)
    chain_body = _chain_body(chain_projection)
    payload = {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA,
        "status": _STATUS,
        "v4_request": {
            "run_id": str(request_facts.run_id),
            "plan_sha256": request_facts.plan_sha256,
            "phase_name": request_facts.phase_name,
            "phase_sequence": request_facts.phase_sequence,
            "oracle": request_facts.oracle,
            "transport_profile": request_facts.transport_profile,
            "effect_key": request_facts.effect_key,
            "phase_request_sha256": request_facts.phase_request_sha256,
            "binding": dict(request_facts.binding_snapshot.__dict__),
            "pre_effect_readiness_binding_sha256": request_facts.binding.readiness_binding_sha256,
            "request_correlation_sha256": request_facts.request_correlation_sha256,
        },
        "strict_ack_chain": chain_body,
        "observed_at": _render_timestamp(
            recorded_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CLOCK_INVALID",
        ),
        "strict_ack_post_effect_bound": False,
        "strict_ack_post_effect_missing_correlation_fields": list(
            PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS
        ),
        "capture_handoff_verified": False,
        "phase_effect_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    canonical = _canonical(payload, code="PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_INVALID") + b"\n"
    return _Derived(
        request=request_facts,
        chain_projection=chain_projection,
        observed_at=recorded_at,
        canonical_provenance=canonical,
        provenance_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _assert_value(
    value: object,
    *,
    derived: _Derived,
) -> VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance:
    if type(value) is not VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CAPABILITY_REQUIRED")
    request = derived.request
    if (
        value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA
        or value.status != _STATUS
        or value.provenance_sha256 != derived.provenance_sha256
        or value.v4_request_correlation_sha256 != request.request_correlation_sha256
        or value.run_id != request.run_id
        or value.plan_sha256 != request.plan_sha256
        or value.phase_name != request.phase_name
        or value.phase_sequence != request.phase_sequence
        or value.oracle != request.oracle
        or value.transport_profile != request.transport_profile
        or value.effect_key != request.effect_key
        or value.phase_request_sha256 != request.phase_request_sha256
        or value.binding != request.binding
        or value.pre_effect_readiness_binding_sha256
        != request.binding.readiness_binding_sha256
        or value.strict_ack_chain != derived.chain_projection
        or value.observed_at != derived.observed_at
        or value.canonical_provenance != derived.canonical_provenance
        or value.strict_ack_post_effect_bound is not False
        or value.strict_ack_post_effect_missing_correlation_fields
        != PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_POST_EFFECT_REQUIRED_CORRELATION_FIELDS
        or value.capture_handoff_verified is not False
        or value.phase_effect_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_TAMPERED")
    return value


def mint_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
    *,
    config: PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    gen2_witnessed_ack_chain: _ack_chain.VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain,
    bound_strict_writer_response: _bound_response.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance:
    """Mint unsequenced provenance from exact existing opaque owners only.

    The verb is deliberately *mint*, not execute/complete: this function has
    no effect boundary and cannot demonstrate that the ACK was generated
    after V4's journaled ``effect-started`` transition.
    """

    derived = _derive(
        config=config,
        request=request,
        gen2_witnessed_ack_chain=gen2_witnessed_ack_chain,
        bound_strict_writer_response=bound_strict_writer_response,
        now=now,
    )
    result = VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance(
        schema=PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_SCHEMA,
        status=_STATUS,
        provenance_sha256=derived.provenance_sha256,
        v4_request_correlation_sha256=derived.request.request_correlation_sha256,
        run_id=derived.request.run_id,
        plan_sha256=derived.request.plan_sha256,
        phase_name=derived.request.phase_name,
        phase_sequence=derived.request.phase_sequence,
        oracle=derived.request.oracle,
        transport_profile=derived.request.transport_profile,
        effect_key=derived.request.effect_key,
        phase_request_sha256=derived.request.phase_request_sha256,
        binding=derived.request.binding,
        pre_effect_readiness_binding_sha256=(
            derived.request.binding.readiness_binding_sha256
        ),
        strict_ack_chain=derived.chain_projection,
        observed_at=derived.observed_at,
        canonical_provenance=derived.canonical_provenance,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(
        config=config,
        chain=gen2_witnessed_ack_chain,
        bound_observation=bound_strict_writer_response,
        pre_effect_readiness=derived.request.pre_effect_readiness,
    )
    return _assert_value(result, derived=derived)


def require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance:
    """Freshly revalidate the same request/evidence pair without any effect."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or state.config != config:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_PROVENANCE_MISSING")
    derived = _derive(
        config=config,
        request=request,
        gen2_witnessed_ack_chain=state.chain,
        bound_strict_writer_response=state.bound_observation,
        now=now,
        provenance_observed_at=value.observed_at,
    )
    if state.pre_effect_readiness is not derived.request.pre_effect_readiness:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_STRICT_ACK_PROVENANCE_PRE_EFFECT_IDENTITY_MISMATCH")
    return _assert_value(value, derived=derived)
