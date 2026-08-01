"""Fail-closed Phase-1 boundary for a V4-correlated Strict-ACK checkpoint.

The legacy Gen2 Strict-ACK chain is intentionally insufficient to complete
V4 Phase 1: its wire and commit records have no V4 effect identity.  This
module makes that gap explicit.  It can correlate a previously verified
Strict-ACK provenance object with the *post-journal-start* private V4 adapter
request, but it cannot fabricate the missing local capture checkpoint.

Accordingly the only result currently constructible is an opaque diagnostic
that says a V4-correlated post-effect checkpoint is required and unavailable.
It is not a phase result, a writer/promotion permit, or an execution permit.
There is deliberately no callback, runner, client, transport, storage, or
legacy ACK adapter here.  A future effectful coordinator must add a separately
owned capture/checkpoint verifier before Phase 1 may report success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from typing import Final
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_phase1_strict_ack_provenance as _provenance


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable",
    "record_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable",
    "require_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-boundary-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS: Final = (
    "v4-correlated-post-effect-strict-ack-checkpoint-required-unavailable"
)

_PHASE: Final = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
_CAPABILITY = object()


class PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError(ValueError):
    """One typed refusal from the non-executing Phase-1 boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig:
    """Default-off policy for the exact existing provenance verifier only."""

    strict_ack_provenance_config: (
        _provenance.PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig | None
    ) = None
    enabled: bool = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_DEFAULT_ENABLED
    )
    writer_authorized: bool = False
    promotion_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable:
    """Opaque proof that legacy ACK reuse was refused after V4 effect start.

    The contained correlation is deliberately public/redacted only.  The
    process-local state retains the exact driver and provenance capabilities,
    so a lookalike object or a pre-effect request cannot be replayed as this
    diagnostic.  The object records an absence; it is never evidence of a
    successful Phase-1 ACK or completion.
    """

    schema: str
    status: str
    boundary_sha256: str
    strict_ack_provenance_sha256: str
    run_id: object
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    claim_id: str
    journaled_effect_start_identity_sha256: str
    effect_start_anchor_sequence: int
    effect_start_anchor_head_sha256: str
    strict_ack_post_effect_bound: bool = False
    post_effect_checkpoint_required: bool = True
    post_effect_checkpoint_available: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    provenance: _provenance.VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance
    authority: _driver.PhysicalFullMatrixV4EffectStartAuthority
    anchor: _driver.PhysicalFullMatrixV4EffectStartAnchorProof
    canonical: bytes
    boundary_sha256: str


_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable, _Facts
] = WeakKeyDictionary()


def _config(value: object) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig:
    if (
        type(value) is not PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig
        or value.enabled is not True
        or type(value.strict_ack_provenance_config)
        is not _provenance.PhysicalFullMatrixV4Phase1StrictAckProvenanceConfig
        or value.strict_ack_provenance_config.enabled is not True
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.phase_completion_evidenced is not False
        or value.next_phase_start_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CONFIG_INVALID")
    return value


def _facts(
    *,
    config: object,
    request: object,
    strict_ack_provenance: object,
    now: datetime,
) -> _Facts:
    checked_config = _config(config)
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_REQUEST_INVALID")
    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CORRELATION_REQUIRED"
        ) from exc
    try:
        provenance = (
            _provenance.require_verified_physical_full_matrix_v4_phase1_strict_ack_provenance(
                strict_ack_provenance,
                config=checked_config.strict_ack_provenance_config,
                request=request,
                now=now,
            )
        )
    except _provenance.PhysicalFullMatrixV4Phase1StrictAckProvenanceError as exc:
        # A forged or detached provenance object must not be conflated with a
        # request that never received a V4 post-effect correlation.  Both
        # outcomes remain fail-closed, but callers need a stable mismatch
        # signal for the former so it cannot be retried as a missing capture.
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CORRELATION_MISMATCH"
        ) from exc
    if (
        authority.phase != _PHASE
        or anchor.phase != _PHASE
        or provenance.phase_name != _PHASE.name
        or provenance.phase_sequence != _PHASE.sequence
        or provenance.run_id != authority.run_id
        or provenance.plan_sha256 != authority.plan_sha256
        or provenance.effect_key != authority.effect_key
        or provenance.phase_request_sha256 != authority.phase_request_sha256
        or provenance.binding != authority.binding
        or anchor.run_id != authority.run_id
        or anchor.plan_sha256 != authority.plan_sha256
        or anchor.effect_key != authority.effect_key
        or anchor.phase_request_sha256 != authority.phase_request_sha256
        or anchor.binding != authority.binding
        or anchor.claim_id != authority.claim_id
        or anchor.journaled_effect_start_identity_sha256
        != authority.journaled_effect_start_identity_sha256
        or provenance.strict_ack_post_effect_bound is not False
        or provenance.capture_handoff_verified is not False
        or provenance.phase_effect_authorized is not False
        or provenance.execution_authorized is not False
        or provenance.full_matrix_authorized is not False
        or authority.writer_authorized is not False
        or authority.promotion_authorized is not False
        or authority.execution_authorized is not False
        or authority.full_matrix_authorized is not False
        or anchor.writer_authorized is not False
        or anchor.promotion_authorized is not False
        or anchor.execution_authorized is not False
        or anchor.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_BINDING_MISMATCH")
    try:
        canonical = canonical_json_bytes(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SCHEMA,
                "status": PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS,
                "strict_ack_provenance_sha256": provenance.provenance_sha256,
                "run_id": str(authority.run_id),
                "plan_sha256": authority.plan_sha256,
                "phase_name": authority.phase.name,
                "phase_sequence": authority.phase.sequence,
                "effect_key": authority.effect_key,
                "phase_request_sha256": authority.phase_request_sha256,
                "claim_id": authority.claim_id,
                "journaled_effect_start_identity_sha256": authority.journaled_effect_start_identity_sha256,
                "effect_start_anchor_sequence": anchor.anchor_sequence,
                "effect_start_anchor_head_sha256": anchor.anchor_head_sha256,
                "strict_ack_post_effect_bound": False,
                "post_effect_checkpoint_required": True,
                "post_effect_checkpoint_available": False,
                "writer_authorized": False,
                "promotion_authorized": False,
                "phase_completion_evidenced": False,
                "next_phase_start_authorized": False,
                "execution_authorized": False,
                "full_matrix_authorized": False,
            }
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CANONICAL_INVALID"
        ) from exc
    return _Facts(
        config=checked_config,
        request=request,
        provenance=provenance,
        authority=authority,
        anchor=anchor,
        canonical=canonical,
        boundary_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _assert_value(
    value: object, *, facts: _Facts
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable:
    if (
        type(value)
        is not PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable
        or value._capability is not _CAPABILITY
        or value.schema
        != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SCHEMA
        or value.status
        != PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS
        or value.boundary_sha256 != facts.boundary_sha256
        or value.strict_ack_provenance_sha256 != facts.provenance.provenance_sha256
        or value.run_id != facts.authority.run_id
        or value.plan_sha256 != facts.authority.plan_sha256
        or value.phase_name != facts.authority.phase.name
        or value.phase_sequence != facts.authority.phase.sequence
        or value.effect_key != facts.authority.effect_key
        or value.phase_request_sha256 != facts.authority.phase_request_sha256
        or value.claim_id != facts.authority.claim_id
        or value.journaled_effect_start_identity_sha256
        != facts.authority.journaled_effect_start_identity_sha256
        or value.effect_start_anchor_sequence != facts.anchor.anchor_sequence
        or value.effect_start_anchor_head_sha256 != facts.anchor.anchor_head_sha256
        or value.strict_ack_post_effect_bound is not False
        or value.post_effect_checkpoint_required is not True
        or value.post_effect_checkpoint_available is not False
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.phase_completion_evidenced is not False
        or value.next_phase_start_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_TAMPERED")
    return value


def record_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    strict_ack_provenance: _provenance.VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance,
    now: datetime,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable:
    """Record the current safe outcome: no V4-correlated checkpoint exists.

    It first requires a journaled V4 effect-start correlation and externally
    attested start anchor.  It then revalidates the older ACK provenance
    against that exact adapter request.  This still cannot establish capture
    causality, so it must return only this non-authorizing unavailability.
    """

    facts = _facts(
        config=config,
        request=request,
        strict_ack_provenance=strict_ack_provenance,
        now=now,
    )
    result = PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable(
        schema=PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_SCHEMA,
        status=PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATUS,
        boundary_sha256=facts.boundary_sha256,
        strict_ack_provenance_sha256=facts.provenance.provenance_sha256,
        run_id=facts.authority.run_id,
        plan_sha256=facts.authority.plan_sha256,
        phase_name=facts.authority.phase.name,
        phase_sequence=facts.authority.phase.sequence,
        effect_key=facts.authority.effect_key,
        phase_request_sha256=facts.authority.phase_request_sha256,
        claim_id=facts.authority.claim_id,
        journaled_effect_start_identity_sha256=(
            facts.authority.journaled_effect_start_identity_sha256
        ),
        effect_start_anchor_sequence=facts.anchor.anchor_sequence,
        effect_start_anchor_head_sha256=facts.anchor.anchor_head_sha256,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = facts
    return _assert_value(result, facts=facts)


def require_physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint_unavailable(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckBoundaryConfig,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    strict_ack_provenance: _provenance.VerifiedPhysicalFullMatrixV4Phase1StrictAckProvenance,
    now: datetime,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable:
    """Revalidate the opaque diagnostic without converting it to success."""

    if (
        type(value)
        is not PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointUnavailable
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_STATE_MISSING")
    facts = _facts(
        config=config,
        request=request,
        strict_ack_provenance=strict_ack_provenance,
        now=now,
    )
    if (
        state.config != facts.config
        or state.request is not request
        or state.provenance is not strict_ack_provenance
        or state.authority is not facts.authority
        or state.anchor is not facts.anchor
        or state.canonical != facts.canonical
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_BOUNDARY_CORRELATION_MISMATCH")
    return _assert_value(value, facts=facts)
