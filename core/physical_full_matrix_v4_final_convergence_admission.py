"""Fail-closed typed admission seam for V4 Phase-8 convergence evidence.

Phase 8 is a read-only observation phase, but a generic successful oracle or
an arbitrary ``evidence_sha256`` cannot establish three-site convergence.
This module therefore reserves the exact evidence grammar that a future
root-owned Phase-8 adapter must satisfy:

* an FI primary readback;
* an IR standby/replay readback;
* Object/blob lineage parity; and
* fresh Witness/route state.

Every one of those independent owner observations must be cross-pinned to the
*live*, journaled V4 Phase-8 effect-start authority and its externally
attested anchor projection.  The driver already owns those two process-local
handles; this module only reads them through its public require functions.

The P8 start proof is not evidence that P7 completed.  The evidence bundle
also requires a separate typed P7-completion anchor provenance.  The shared
root journal derives it from durable records and a fresh Witness-head read;
its completion anchor must be the immediate predecessor of the P8 start
anchor.  This module only projects that already-verified bridge into its P8
claim grammar; it never reads a journal, contacts a Witness, or calls an
adapter itself.

There is deliberately no live collector, adapter, network client, storage
client, signer, trusted-clock callback, or issuer implementation here.  The
four opaque ``Verified...`` classes are reserved capability boundaries for
future owner-specific verifiers.  Consequently even a structurally complete
bundle yields a diagnostic, non-authorizing assessment rather than a final
success or Full-Matrix completion claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Final
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
from core import physical_full_matrix_execution_driver_v4 as _driver


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CLAIM_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_REQUIRED_EVIDENCE_SLOTS",
    "PhysicalFullMatrixV4FinalConvergenceAdmissionAssessment",
    "PhysicalFullMatrixV4FinalConvergenceAdmissionConfig",
    "PhysicalFullMatrixV4FinalConvergenceAdmissionError",
    "PhysicalFullMatrixV4FinalConvergenceEvidenceBundle",
    "PhysicalFullMatrixV4FinalConvergenceEvidenceClaim",
    "PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim",
    "PhysicalFullMatrixV4Phase8EffectStartAnchorBinding",
    "VerifiedPhysicalFullMatrixV4FiPrimaryReadback",
    "VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback",
    "VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity",
    "VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance",
    "VerifiedPhysicalFullMatrixV4WitnessRouteFreshState",
    "assess_physical_full_matrix_v4_final_convergence_admission",
    "project_physical_full_matrix_v4_phase8_effect_start_anchor_binding",
    "project_physical_full_matrix_v4_p7_completion_anchor_provenance",
)


PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-final-convergence-admission-v1"
)
PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-final-convergence-evidence-claim-v1"
)
PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CLAIM_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-p7-completion-anchor-provenance-claim-v1"
)
PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_REQUIRED_EVIDENCE_SLOTS: Final = (
    "fi-primary-readback",
    "ir-standby-replay-readback",
    "object-blob-lineage-parity",
    "witness-route-fresh-state",
)

_ZERO_SHA256: Final = "0" * 64
_FORBIDDEN: Final = "forbidden"
_MAX_DECLARED_EVIDENCE_LIFETIME_SECONDS: Final = 300
_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_EVIDENCE_CAPABILITY = object()


class PhysicalFullMatrixV4FinalConvergenceAdmissionError(ValueError):
    """The Phase-8 convergence admission seam rejected an unsafe input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4FinalConvergenceAdmissionError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4FinalConvergenceAdmissionError(code) from exc


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat().replace("+00:00", "Z")


def _binding_payload(
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding,
    *,
    code: str,
) -> dict[str, object]:
    """Validate and serialize only the redacted V4 binding projection."""

    if type(binding) is not _driver.PhysicalFullMatrixV4ExecutionBinding:
        _fail(code)
    if (
        type(binding.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(binding.campaign_id) is None
        or type(binding.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(binding.release_sha) is None
        or binding.writer_holder_site not in WEBAPP_SITES
        or binding.source_site not in WEBAPP_SITES
        or binding.destination_site not in WEBAPP_SITES
        or binding.source_site == binding.destination_site
        or type(binding.writer_epoch) is not int
        or binding.writer_epoch < 1
        or type(binding.witness_sequence) is not int
        or binding.witness_sequence < 1
        or type(binding.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(binding.writer_lease_id) is None
        or type(binding.witness_transition_id) is not str
        or _IDENTIFIER_RE.fullmatch(binding.witness_transition_id) is None
    ):
        _fail(code)
    for value in (
        binding.readiness_binding_sha256,
        binding.route_commitment_sha256,
        binding.four_role_binding_sha256,
        binding.witnessed_term_proof_sha256,
        binding.roundtrip_attestation_sha256,
        binding.roundtrip_configuration_sha256,
    ):
        _sha256(value, code=code)
    return {
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "readiness_binding_sha256": binding.readiness_binding_sha256,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "writer_holder_site": binding.writer_holder_site,
        "writer_epoch": binding.writer_epoch,
        "writer_lease_id": binding.writer_lease_id,
        "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "roundtrip_attestation_sha256": binding.roundtrip_attestation_sha256,
        "roundtrip_configuration_sha256": binding.roundtrip_configuration_sha256,
        "witness_transition_id": binding.witness_transition_id,
        "witness_sequence": binding.witness_sequence,
    }


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase8EffectStartAnchorBinding:
    """Portable redacted P8 start/anchor projection; never trust by itself.

    This projection is deliberately a typed field-by-field carrier rather
    than a generic tuple hash.  A future owner evidence envelope must repeat
    it exactly and be independently verified into one of the opaque slot
    capabilities below.  It contains no raw host state, secret, credential,
    client, journal handle, or execution authority.
    """

    schema: str
    run_id: UUID
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    final_binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    effect_start_claim_id: str
    effect_start_identity_sha256: str
    anchor_schema: str
    anchor_journal_binding_sha256: str
    anchor_baseline_plan_binding_sha256: str
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
    effect_started_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class _Phase8Context:
    binding: PhysicalFullMatrixV4Phase8EffectStartAnchorBinding


def _phase8_context(
    request: object,
) -> _Phase8Context:
    """Require the exact private Phase-8 start correlation from the driver."""

    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4FinalConvergenceAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EFFECT_START_REQUIRED"
        ) from exc
    expected = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[-1]
    if (
        authority.phase.name != expected.name
        or authority.phase.sequence != expected.sequence
        or authority.phase.oracle != expected.oracle
        or authority.phase.transport_profile != expected.transport_profile
        or authority.phase.destructive is not False
        or authority.run_id != anchor.run_id
        or authority.plan_sha256 != anchor.plan_sha256
        or authority.effect_key != anchor.effect_key
        or authority.phase_request_sha256 != anchor.phase_request_sha256
        or authority.binding != anchor.binding
        or authority.claim_id != anchor.claim_id
        or authority.journaled_effect_start_identity_sha256
        != anchor.journaled_effect_start_identity_sha256
        or authority.writer_authorized is not False
        or authority.promotion_authorized is not False
        or authority.execution_authorized is not False
        or authority.full_matrix_authorized is not False
        or authority.full_matrix_executed is not False
        or anchor.writer_authorized is not False
        or anchor.promotion_authorized is not False
        or anchor.execution_authorized is not False
        or anchor.full_matrix_authorized is not False
        or anchor.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_PHASE8_CONTEXT_INVALID")
    binding = authority.binding
    if (
        binding.writer_holder_site != "webapp_fi"
        or binding.source_site != "webapp_fi"
        or binding.destination_site != "webapp_ir"
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_FINAL_BINDING_INVALID")
    return _Phase8Context(
        binding=PhysicalFullMatrixV4Phase8EffectStartAnchorBinding(
            schema=PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA,
            run_id=authority.run_id,
            plan_sha256=authority.plan_sha256,
            phase_name=authority.phase.name,
            phase_sequence=authority.phase.sequence,
            effect_key=authority.effect_key,
            phase_request_sha256=authority.phase_request_sha256,
            final_binding=binding,
            effect_start_claim_id=authority.claim_id,
            effect_start_identity_sha256=(
                authority.journaled_effect_start_identity_sha256
            ),
            anchor_schema=anchor.schema,
            anchor_journal_binding_sha256=anchor.journal_binding_sha256,
            anchor_baseline_plan_binding_sha256=(
                anchor.baseline_plan_binding_sha256
            ),
            anchor_genesis_sequence=anchor.anchor_genesis_sequence,
            anchor_genesis_head_sha256=anchor.anchor_genesis_head_sha256,
            anchor_previous_sequence=anchor.anchor_previous_sequence,
            anchor_previous_head_sha256=anchor.anchor_previous_head_sha256,
            anchor_sequence=anchor.anchor_sequence,
            anchor_head_sha256=anchor.anchor_head_sha256,
            anchor_commitment_sha256=anchor.anchor_commitment_sha256,
            anchor_attestation_sha256=anchor.anchor_attestation_sha256,
            anchor_local_previous_record_sha256=(
                anchor.anchor_local_previous_record_sha256
            ),
            anchor_local_event_sha256=anchor.anchor_local_event_sha256,
            effect_started_at=_utc(
                anchor.anchor_occurred_at,
                code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_PHASE8_CONTEXT_INVALID",
            ),
        )
    )


def project_physical_full_matrix_v4_phase8_effect_start_anchor_binding(
    *,
    request: object,
) -> PhysicalFullMatrixV4Phase8EffectStartAnchorBinding:
    """Return exact typed Phase-8 start/anchor pins for future owner evidence.

    This function cannot be used before the driver has made the Phase-8
    effect-start durable and supplied the private adapter request copy.
    The typed projection is evidence correlation only; it is never a writer,
    route, promotion, execution, or completion permit.
    """

    return _phase8_context(request).binding


def project_physical_full_matrix_v4_p7_completion_anchor_provenance(
    *,
    request: object,
) -> "VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance":
    """Project the shared durable P7→P8 journal bridge into P8's grammar.

    The returned object remains a nonserializable, non-authorizing evidence
    capability.  It is available only on the exact private Phase-8 adapter
    request after the driver has obtained the root journal's durable
    predecessor-completion proof.  This pure function deliberately has no
    fallback to a raw receipt, a P7 start anchor, or a caller-built claim.
    """

    context = _phase8_context(request)
    try:
        proof = (
            _driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
                request=request
            )
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4FinalConvergenceAdmissionError(
            "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_REQUIRED"
        ) from exc
    expected_p7 = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[-2]
    phase8 = context.binding
    if (
        proof.schema
        != _driver.PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA
        or proof.run_id != phase8.run_id
        or proof.plan_sha256 != phase8.plan_sha256
        or proof.predecessor_phase_name != expected_p7.name
        or proof.predecessor_phase_sequence != expected_p7.sequence
        or proof.successor_phase_name != phase8.phase_name
        or proof.successor_phase_sequence != phase8.phase_sequence
        or proof.successor_effect_key != phase8.effect_key
        or proof.successor_phase_request_sha256 != phase8.phase_request_sha256
        or proof.successor_claim_id != phase8.effect_start_claim_id
        or proof.successor_effect_start_identity_sha256
        != phase8.effect_start_identity_sha256
        or proof.journal_binding_sha256 != phase8.anchor_journal_binding_sha256
        or proof.baseline_plan_binding_sha256
        != phase8.anchor_baseline_plan_binding_sha256
        or proof.anchor_genesis_sequence != phase8.anchor_genesis_sequence
        or proof.anchor_genesis_head_sha256 != phase8.anchor_genesis_head_sha256
        or proof.successor_effect_start_anchor_previous_sequence
        != phase8.anchor_previous_sequence
        or proof.successor_effect_start_anchor_previous_head_sha256
        != phase8.anchor_previous_head_sha256
        or proof.successor_effect_start_anchor_sequence != phase8.anchor_sequence
        or proof.successor_effect_start_anchor_head_sha256 != phase8.anchor_head_sha256
        or proof.predecessor_completion_anchor_sequence
        != phase8.anchor_previous_sequence
        or proof.predecessor_completion_anchor_head_sha256
        != phase8.anchor_previous_head_sha256
        or proof.predecessor_completion_anchor_previous_sequence
        != proof.predecessor_effect_start_anchor_sequence
        or proof.predecessor_completion_anchor_previous_head_sha256
        != proof.predecessor_effect_start_anchor_head_sha256
        or proof.predecessor_completion_anchor_sequence
        != proof.predecessor_completion_anchor_previous_sequence + 1
        or proof.writer_authorized is not False
        or proof.promotion_authorized is not False
        or proof.execution_authorized is not False
        or proof.full_matrix_authorized is not False
        or proof.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_MISMATCH")
    return _mint_verified_physical_full_matrix_v4_p7_completion_anchor_provenance(
        claim=PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim(
            schema=PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CLAIM_SCHEMA,
            run_id=proof.run_id,
            plan_sha256=proof.plan_sha256,
            p7_phase_name=proof.predecessor_phase_name,
            p7_phase_sequence=proof.predecessor_phase_sequence,
            p7_effect_key=proof.predecessor_effect_key,
            p7_phase_request_sha256=proof.predecessor_phase_request_sha256,
            final_binding=phase8,
            p7_completion_receipt_sha256=(
                proof.predecessor_completion_receipt_sha256
            ),
            p7_completion_anchor_sequence=(
                proof.predecessor_completion_anchor_sequence
            ),
            p7_completion_anchor_head_sha256=(
                proof.predecessor_completion_anchor_head_sha256
            ),
            p7_completion_anchor_commitment_sha256=(
                proof.predecessor_completion_anchor_commitment_sha256
            ),
            p7_completion_anchor_attestation_sha256=(
                proof.predecessor_completion_anchor_attestation_sha256
            ),
            p7_completed_at=proof.predecessor_completed_at,
        )
    )


def _phase8_binding_payload(
    value: object,
    *,
    code: str,
) -> dict[str, object]:
    """Validate every portable P8 correlation pin without a tuple digest."""

    if type(value) is not PhysicalFullMatrixV4Phase8EffectStartAnchorBinding:
        _fail(code)
    binding = value
    final_binding_payload = _binding_payload(binding.final_binding, code=code)
    expected = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[-1]
    if (
        binding.schema != PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA
        or type(binding.run_id) is not UUID
        or binding.run_id.int == 0
        or binding.phase_name != expected.name
        or binding.phase_sequence != expected.sequence
        or binding.final_binding.writer_holder_site != "webapp_fi"
        or binding.final_binding.source_site != "webapp_fi"
        or binding.final_binding.destination_site != "webapp_ir"
        or binding.writer_authorized is not False
        or binding.promotion_authorized is not False
        or binding.execution_authorized is not False
        or binding.full_matrix_authorized is not False
        or binding.full_matrix_executed is not False
    ):
        _fail(code)
    for digest, permit_zero in (
        (binding.plan_sha256, False),
        (binding.effect_key, False),
        (binding.phase_request_sha256, False),
        (binding.effect_start_identity_sha256, False),
        (binding.anchor_journal_binding_sha256, False),
        (binding.anchor_baseline_plan_binding_sha256, False),
        (binding.anchor_genesis_head_sha256, True),
        (binding.anchor_previous_head_sha256, True),
        (binding.anchor_head_sha256, False),
        (binding.anchor_commitment_sha256, False),
        (binding.anchor_attestation_sha256, False),
        (binding.anchor_local_previous_record_sha256, True),
        (binding.anchor_local_event_sha256, False),
    ):
        _sha256(digest, code=code, permit_zero=permit_zero)
    if (
        type(binding.anchor_schema) is not str
        or binding.anchor_schema
        != _driver.PHYSICAL_FULL_MATRIX_V4_EFFECT_START_ANCHOR_PROOF_SCHEMA
        or type(binding.anchor_genesis_sequence) is not int
        or binding.anchor_genesis_sequence < 0
        or type(binding.anchor_previous_sequence) is not int
        or binding.anchor_previous_sequence < binding.anchor_genesis_sequence
        or type(binding.anchor_sequence) is not int
        or binding.anchor_sequence != binding.anchor_previous_sequence + 1
    ):
        _fail(code)
    _identifier(binding.effect_start_claim_id, code=code)
    effect_started_at = _utc(binding.effect_started_at, code=code)
    return {
        "schema": binding.schema,
        "run_id": str(binding.run_id),
        "plan_sha256": binding.plan_sha256,
        "phase_name": binding.phase_name,
        "phase_sequence": binding.phase_sequence,
        "effect_key": binding.effect_key,
        "phase_request_sha256": binding.phase_request_sha256,
        "final_binding": final_binding_payload,
        "effect_start_claim_id": binding.effect_start_claim_id,
        "effect_start_identity_sha256": binding.effect_start_identity_sha256,
        "anchor_schema": binding.anchor_schema,
        "anchor_journal_binding_sha256": binding.anchor_journal_binding_sha256,
        "anchor_baseline_plan_binding_sha256": (
            binding.anchor_baseline_plan_binding_sha256
        ),
        "anchor_genesis_sequence": binding.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": binding.anchor_genesis_head_sha256,
        "anchor_previous_sequence": binding.anchor_previous_sequence,
        "anchor_previous_head_sha256": binding.anchor_previous_head_sha256,
        "anchor_sequence": binding.anchor_sequence,
        "anchor_head_sha256": binding.anchor_head_sha256,
        "anchor_commitment_sha256": binding.anchor_commitment_sha256,
        "anchor_attestation_sha256": binding.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": (
            binding.anchor_local_previous_record_sha256
        ),
        "anchor_local_event_sha256": binding.anchor_local_event_sha256,
        "effect_started_at": _render_timestamp(effect_started_at, code=code),
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


@dataclass(frozen=True)
class PhysicalFullMatrixV4FinalConvergenceEvidenceClaim:
    """Public claim grammar; it is not accepted without its opaque verifier.

    ``evidence_sha256`` is intentionally only a content identifier.  The
    admission seam never treats it, a raw claim, or a generic Phase-8 oracle
    as proof of a successful readback.
    """

    schema: str
    slot: str
    observed_site: str
    evidence_kind: str
    assertion: str
    campaign_id: str
    release_sha: str
    run_id: UUID
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    final_binding: PhysicalFullMatrixV4Phase8EffectStartAnchorBinding
    evidence_sha256: str
    observed_at: datetime
    expires_at: datetime
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim:
    """Reserved bridge from P7 completion to the later P8 effect start.

    The current journal exposes a live P8 start anchor, but does not yet
    project the preceding P7 *completion* record/anchor as a typed capability.
    This grammar makes that missing bridge explicit.  A P7 start anchor is
    insufficient: the claimed completion anchor must equal the immediate
    predecessor of the P8 start anchor.
    """

    schema: str
    run_id: UUID
    plan_sha256: str
    p7_phase_name: str
    p7_phase_sequence: int
    p7_effect_key: str
    p7_phase_request_sha256: str
    final_binding: PhysicalFullMatrixV4Phase8EffectStartAnchorBinding
    p7_completion_receipt_sha256: str
    p7_completion_anchor_sequence: int
    p7_completion_anchor_head_sha256: str
    p7_completion_anchor_commitment_sha256: str
    p7_completion_anchor_attestation_sha256: str
    p7_completed_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class _EvidenceClaimSnapshot:
    canonical_claim: bytes
    claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim


@dataclass(frozen=True, eq=False, init=False)
class _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence:
    """Opaque output reserved for one future, owner-specific verifier."""

    claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim,
        capability: object,
    ) -> None:
        if capability is not _EVIDENCE_CAPABILITY:
            raise TypeError(
                "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CONSTRUCTION_FORBIDDEN"
            )
        for name, value in (
            ("claim", claim),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_COPY_FORBIDDEN")


class VerifiedPhysicalFullMatrixV4FiPrimaryReadback(
    _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence
):
    """Reserved capability for the future FI-primary independent verifier."""


class VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback(
    _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence
):
    """Reserved capability for the future IR-standby/replay verifier."""


class VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity(
    _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence
):
    """Reserved capability for the future Object/blob parity verifier."""


class VerifiedPhysicalFullMatrixV4WitnessRouteFreshState(
    _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence
):
    """Reserved capability for the future fresh Witness/route verifier."""


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance:
    """Opaque root-journal projection of the P7 completion anchor."""

    claim: PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        claim: PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim,
        capability: object,
    ) -> None:
        if capability is not _EVIDENCE_CAPABILITY:
            raise TypeError(
                "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CONSTRUCTION_FORBIDDEN"
            )
        for name, value in (
            ("claim", claim),
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
            "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_COPY_FORBIDDEN"
        )

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_COPY_FORBIDDEN"
        )


_EVIDENCE_STATES: WeakKeyDictionary[
    _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence, _EvidenceClaimSnapshot
] = WeakKeyDictionary()
_P7_COMPLETION_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance,
    bytes,
] = WeakKeyDictionary()


@dataclass(frozen=True)
class _SlotSpec:
    slot: str
    observed_site: str
    evidence_kind: str
    assertion: str
    expected_type: type[_VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence]


_SLOT_SPECS: Final = (
    _SlotSpec(
        slot="fi-primary-readback",
        observed_site="webapp_fi",
        evidence_kind="fi-primary-writer-readback-v1",
        assertion="fi-writer-primary-term-and-durable-readback",
        expected_type=VerifiedPhysicalFullMatrixV4FiPrimaryReadback,
    ),
    _SlotSpec(
        slot="ir-standby-replay-readback",
        observed_site="webapp_ir",
        evidence_kind="ir-standby-replay-readback-v1",
        assertion="ir-standby-replay-and-writes-fenced-readback",
        expected_type=VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback,
    ),
    _SlotSpec(
        slot="object-blob-lineage-parity",
        observed_site="object_storage",
        evidence_kind="object-blob-lineage-parity-v1",
        assertion="object-blob-lineage-and-version-parity",
        expected_type=VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity,
    ),
    _SlotSpec(
        slot="witness-route-fresh-state",
        observed_site="witness",
        evidence_kind="witness-route-fresh-state-v1",
        assertion="fresh-witness-term-and-fi-primary-route",
        expected_type=VerifiedPhysicalFullMatrixV4WitnessRouteFreshState,
    ),
)


def _claim_payload(
    value: object,
    *,
    code: str,
) -> dict[str, object]:
    if type(value) is not PhysicalFullMatrixV4FinalConvergenceEvidenceClaim:
        _fail(code)
    claim = value
    if (
        claim.schema != PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_SCHEMA
        or claim.slot not in PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_REQUIRED_EVIDENCE_SLOTS
        # Object Storage is an independent evidence relay.  It must never
        # collapse into the Witness verifier domain, otherwise a single
        # Witness observation could satisfy two separate Phase-8 checks.
        or claim.observed_site
        not in {"webapp_fi", "webapp_ir", "object_storage", "witness"}
        or type(claim.evidence_kind) is not str
        or not claim.evidence_kind
        or type(claim.assertion) is not str
        or not claim.assertion
        or type(claim.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(claim.campaign_id) is None
        or type(claim.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(claim.release_sha) is None
        or type(claim.run_id) is not UUID
        or claim.run_id.int == 0
        or type(claim.phase_name) is not str
        or type(claim.phase_sequence) is not int
        or claim.phase_sequence != 8
        or claim.direct_fi_to_ir_control != _FORBIDDEN
        or claim.direct_ir_to_fi_control != _FORBIDDEN
        or claim.object_storage_authority != _FORBIDDEN
        or claim.writer_authorized is not False
        or claim.promotion_authorized is not False
        or claim.execution_authorized is not False
        or claim.full_matrix_authorized is not False
    ):
        _fail(code)
    for digest in (
        claim.plan_sha256,
        claim.effect_key,
        claim.phase_request_sha256,
        claim.evidence_sha256,
    ):
        _sha256(digest, code=code)
    phase8_binding = _phase8_binding_payload(claim.final_binding, code=code)
    effect_started_at = _utc(claim.final_binding.effect_started_at, code=code)
    observed_at = _utc(claim.observed_at, code=code)
    expires_at = _utc(claim.expires_at, code=code)
    if (
        expires_at <= observed_at
        or (expires_at - observed_at).total_seconds()
        > _MAX_DECLARED_EVIDENCE_LIFETIME_SECONDS
        or observed_at < effect_started_at
        or (observed_at - effect_started_at).total_seconds()
        > _MAX_DECLARED_EVIDENCE_LIFETIME_SECONDS
    ):
        _fail(code)
    return {
        "schema": claim.schema,
        "slot": claim.slot,
        "observed_site": claim.observed_site,
        "evidence_kind": claim.evidence_kind,
        "assertion": claim.assertion,
        "campaign_id": claim.campaign_id,
        "release_sha": claim.release_sha,
        "run_id": str(claim.run_id),
        "plan_sha256": claim.plan_sha256,
        "phase_name": claim.phase_name,
        "phase_sequence": claim.phase_sequence,
        "effect_key": claim.effect_key,
        "phase_request_sha256": claim.phase_request_sha256,
        "final_binding": phase8_binding,
        "evidence_sha256": claim.evidence_sha256,
        "observed_at": _render_timestamp(observed_at, code=code),
        "expires_at": _render_timestamp(expires_at, code=code),
        "direct_fi_to_ir_control": claim.direct_fi_to_ir_control,
        "direct_ir_to_fi_control": claim.direct_ir_to_fi_control,
        "object_storage_authority": claim.object_storage_authority,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def _p7_completion_claim_payload(
    value: object,
    *,
    code: str,
) -> dict[str, object]:
    if type(value) is not PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim:
        _fail(code)
    claim = value
    expected = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[-2]
    if (
        claim.schema
        != PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_CLAIM_SCHEMA
        or type(claim.run_id) is not UUID
        or claim.run_id.int == 0
        or claim.p7_phase_name != expected.name
        or claim.p7_phase_sequence != expected.sequence
        or claim.writer_authorized is not False
        or claim.promotion_authorized is not False
        or claim.execution_authorized is not False
        or claim.full_matrix_authorized is not False
        or claim.full_matrix_executed is not False
    ):
        _fail(code)
    for digest in (
        claim.plan_sha256,
        claim.p7_effect_key,
        claim.p7_phase_request_sha256,
        claim.p7_completion_receipt_sha256,
        claim.p7_completion_anchor_head_sha256,
        claim.p7_completion_anchor_commitment_sha256,
        claim.p7_completion_anchor_attestation_sha256,
    ):
        _sha256(digest, code=code)
    phase8 = _phase8_binding_payload(claim.final_binding, code=code)
    completed_at = _utc(claim.p7_completed_at, code=code)
    effect_started_at = _utc(claim.final_binding.effect_started_at, code=code)
    if (
        type(claim.p7_completion_anchor_sequence) is not int
        or claim.p7_completion_anchor_sequence
        != claim.final_binding.anchor_previous_sequence
        or claim.p7_completion_anchor_head_sha256
        != claim.final_binding.anchor_previous_head_sha256
        or completed_at >= effect_started_at
        or (effect_started_at - completed_at).total_seconds()
        > _MAX_DECLARED_EVIDENCE_LIFETIME_SECONDS
    ):
        _fail(code)
    return {
        "schema": claim.schema,
        "run_id": str(claim.run_id),
        "plan_sha256": claim.plan_sha256,
        "p7_phase_name": claim.p7_phase_name,
        "p7_phase_sequence": claim.p7_phase_sequence,
        "p7_effect_key": claim.p7_effect_key,
        "p7_phase_request_sha256": claim.p7_phase_request_sha256,
        "final_binding": phase8,
        "p7_completion_receipt_sha256": claim.p7_completion_receipt_sha256,
        "p7_completion_anchor_sequence": claim.p7_completion_anchor_sequence,
        "p7_completion_anchor_head_sha256": claim.p7_completion_anchor_head_sha256,
        "p7_completion_anchor_commitment_sha256": (
            claim.p7_completion_anchor_commitment_sha256
        ),
        "p7_completion_anchor_attestation_sha256": (
            claim.p7_completion_anchor_attestation_sha256
        ),
        "p7_completed_at": _render_timestamp(completed_at, code=code),
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _mint_verified_physical_full_matrix_v4_p7_completion_anchor_provenance(
    *,
    claim: PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim,
) -> VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance:
    """Mint only P8's non-authorizing view of a checked journal bridge."""

    canonical_claim = _canonical(
        _p7_completion_claim_payload(
            claim,
            code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_INVALID",
        ),
        code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_INVALID",
    )
    result = VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance(
        claim=claim,
        capability=_EVIDENCE_CAPABILITY,
    )
    _P7_COMPLETION_STATES[result] = canonical_claim
    return result


def _require_verified_p7_completion_anchor_provenance(
    value: object,
) -> PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim:
    if (
        type(value) is not VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance
        or value._capability is not _EVIDENCE_CAPABILITY
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_REQUIRED")
    canonical_claim = _P7_COMPLETION_STATES.get(value)
    if canonical_claim is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_REQUIRED")
    if _canonical(
        _p7_completion_claim_payload(
            value.claim,
            code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_TAMPERED",
        ),
        code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_TAMPERED",
    ) != canonical_claim:
        _fail("PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_TAMPERED")
    return value.claim


def _validate_p7_completion_against_context(
    *,
    claim: PhysicalFullMatrixV4P7CompletionAnchorProvenanceClaim,
    context: _Phase8Context,
) -> None:
    _p7_completion_claim_payload(
        claim,
        code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_INVALID",
    )
    if (
        claim.run_id != context.binding.run_id
        or claim.plan_sha256 != context.binding.plan_sha256
        or _phase8_binding_payload(
            claim.final_binding,
            code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_MISMATCH",
        )
        != _phase8_binding_payload(
            context.binding,
            code="PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_MISMATCH",
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_P7_COMPLETION_ANCHOR_PROVENANCE_MISMATCH")


def _mint_verified_evidence(
    *,
    expected_type: type[_VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence],
    claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim,
) -> _VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence:
    """Private test/future-owner seam; it is not an admission or issuer API."""

    canonical_claim = _canonical(
        _claim_payload(
            claim,
            code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_INVALID",
        ),
        code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_INVALID",
    )
    result = expected_type(claim=claim, capability=_EVIDENCE_CAPABILITY)
    _EVIDENCE_STATES[result] = _EvidenceClaimSnapshot(
        canonical_claim=canonical_claim,
        claim=claim,
    )
    return result


def _mint_verified_physical_full_matrix_v4_fi_primary_readback(
    *, claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim
) -> VerifiedPhysicalFullMatrixV4FiPrimaryReadback:
    return _mint_verified_evidence(
        expected_type=VerifiedPhysicalFullMatrixV4FiPrimaryReadback,
        claim=claim,
    )


def _mint_verified_physical_full_matrix_v4_ir_standby_replay_readback(
    *, claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim
) -> VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback:
    return _mint_verified_evidence(
        expected_type=VerifiedPhysicalFullMatrixV4IrStandbyReplayReadback,
        claim=claim,
    )


def _mint_verified_physical_full_matrix_v4_object_blob_lineage_parity(
    *, claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim
) -> VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity:
    return _mint_verified_evidence(
        expected_type=VerifiedPhysicalFullMatrixV4ObjectBlobLineageParity,
        claim=claim,
    )


def _mint_verified_physical_full_matrix_v4_witness_route_fresh_state(
    *, claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim
) -> VerifiedPhysicalFullMatrixV4WitnessRouteFreshState:
    return _mint_verified_evidence(
        expected_type=VerifiedPhysicalFullMatrixV4WitnessRouteFreshState,
        claim=claim,
    )


def _require_verified_evidence(
    value: object,
    *,
    expected_type: type[_VerifiedPhysicalFullMatrixV4FinalConvergenceEvidence],
) -> PhysicalFullMatrixV4FinalConvergenceEvidenceClaim:
    if (
        type(value) is not expected_type
        or value._capability is not _EVIDENCE_CAPABILITY
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CAPABILITY_REQUIRED")
    state = _EVIDENCE_STATES.get(value)
    if state is None or value.claim is not state.claim:
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CAPABILITY_REQUIRED")
    if _canonical(
        _claim_payload(
            value.claim,
            code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_TAMPERED",
        ),
        code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_TAMPERED",
    ) != state.canonical_claim:
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_TAMPERED")
    return state.claim


@dataclass(frozen=True)
class PhysicalFullMatrixV4FinalConvergenceEvidenceBundle:
    """Four observations plus the predecessor P7-completion bridge for P8."""

    p7_completion_anchor_provenance: object | None = None
    fi_primary_readback: object | None = None
    ir_standby_replay_readback: object | None = None
    object_blob_lineage_parity: object | None = None
    witness_route_fresh_state: object | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixV4FinalConvergenceAdmissionConfig:
    """Default-off typed seam; enabling it still cannot admit convergence."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_DEFAULT_ENABLED

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_CONFIG_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class PhysicalFullMatrixV4FinalConvergenceAdmissionAssessment:
    """Diagnostic only; no result from this module is a convergence permit."""

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    required_evidence_slots: tuple[str, ...]
    final_binding: _driver.PhysicalFullMatrixV4ExecutionBinding | None
    phase8_effect_start_anchor: PhysicalFullMatrixV4Phase8EffectStartAnchorBinding | None
    effect_start_identity_sha256: str | None
    p7_completion_anchor_provenance_present: bool = False
    final_convergence_admitted: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


def _assessment(
    *,
    status: str,
    reason_codes: tuple[str, ...],
    context: _Phase8Context | None,
    p7_completion_anchor_provenance_present: bool = False,
) -> PhysicalFullMatrixV4FinalConvergenceAdmissionAssessment:
    return PhysicalFullMatrixV4FinalConvergenceAdmissionAssessment(
        schema=PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA,
        status=status,
        reason_codes=reason_codes,
        required_evidence_slots=PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_REQUIRED_EVIDENCE_SLOTS,
        final_binding=(None if context is None else context.binding.final_binding),
        phase8_effect_start_anchor=(None if context is None else context.binding),
        effect_start_identity_sha256=(
            None if context is None else context.binding.effect_start_identity_sha256
        ),
        p7_completion_anchor_provenance_present=(
            p7_completion_anchor_provenance_present
        ),
    )


def _validate_claim_against_context(
    *,
    claim: PhysicalFullMatrixV4FinalConvergenceEvidenceClaim,
    spec: _SlotSpec,
    context: _Phase8Context,
) -> None:
    _claim_payload(
        claim,
        code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CLAIM_INVALID",
    )
    if (
        claim.slot != spec.slot
        or claim.observed_site != spec.observed_site
        or claim.evidence_kind != spec.evidence_kind
        or claim.assertion != spec.assertion
        or claim.campaign_id != context.binding.final_binding.campaign_id
        or claim.release_sha != context.binding.final_binding.release_sha
        or claim.run_id != context.binding.run_id
        or claim.plan_sha256 != context.binding.plan_sha256
        or claim.phase_name != context.binding.phase_name
        or claim.phase_sequence != context.binding.phase_sequence
        or claim.effect_key != context.binding.effect_key
        or claim.phase_request_sha256 != context.binding.phase_request_sha256
        or _phase8_binding_payload(
            claim.final_binding,
            code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CROSS_PIN_MISMATCH",
        )
        != _phase8_binding_payload(
            context.binding,
            code="PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CROSS_PIN_MISMATCH",
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_CROSS_PIN_MISMATCH")


def assess_physical_full_matrix_v4_final_convergence_admission(
    *,
    config: PhysicalFullMatrixV4FinalConvergenceAdmissionConfig,
    request: object = None,
    evidence_bundle: object = None,
) -> PhysicalFullMatrixV4FinalConvergenceAdmissionAssessment:
    """Assess P8 inputs while permanently refusing to admit final convergence.

    A disabled config intentionally does not inspect any caller-supplied
    object.  With explicit ``enabled=True``, the live Phase-8 effect-start
    authority plus anchor are mandatory, and every evidence slot must have
    the distinct opaque type reserved for its future owner verifier.  Even
    then the result remains non-authorizing until real independent verifiers,
    the root-journal P7 completion projection, and the root Phase-8 runtime
    are implemented and reviewed elsewhere.
    """

    if (
        type(config) is not PhysicalFullMatrixV4FinalConvergenceAdmissionConfig
        or config.schema != PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_ADMISSION_SCHEMA
        or type(config.enabled) is not bool
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_CONFIG_INVALID")
    if not config.enabled:
        return _assessment(
            status="blocked-default-off",
            reason_codes=("final-convergence-admission-default-disabled",),
            context=None,
        )
    context = _phase8_context(request)
    if type(evidence_bundle) is not PhysicalFullMatrixV4FinalConvergenceEvidenceBundle:
        return _assessment(
            status="blocked-typed-future-evidence-bundle-required",
            reason_codes=("typed-final-convergence-evidence-bundle-required",),
            context=context,
        )
    if (
        type(evidence_bundle.p7_completion_anchor_provenance)
        is not VerifiedPhysicalFullMatrixV4P7CompletionAnchorProvenance
    ):
        return _assessment(
            status="blocked-p7-completion-anchor-provenance-required",
            reason_codes=(
                "p7-completion-anchor-provenance-required",
                "p8-start-anchor-cannot-substitute-for-p7-completion-proof",
            ),
            context=context,
        )
    p7_completion = _require_verified_p7_completion_anchor_provenance(
        evidence_bundle.p7_completion_anchor_provenance
    )
    _validate_p7_completion_against_context(
        claim=p7_completion,
        context=context,
    )
    values = (
        evidence_bundle.fi_primary_readback,
        evidence_bundle.ir_standby_replay_readback,
        evidence_bundle.object_blob_lineage_parity,
        evidence_bundle.witness_route_fresh_state,
    )
    missing = tuple(
        f"typed-owner-evidence-required:{spec.slot}"
        for spec, value in zip(_SLOT_SPECS, values, strict=True)
        if type(value) is not spec.expected_type
    )
    if missing:
        return _assessment(
            status="blocked-typed-future-owner-evidence-required",
            reason_codes=missing,
            context=context,
            p7_completion_anchor_provenance_present=True,
        )
    claims = tuple(
        _require_verified_evidence(value, expected_type=spec.expected_type)
        for spec, value in zip(_SLOT_SPECS, values, strict=True)
    )
    for spec, claim in zip(_SLOT_SPECS, claims, strict=True):
        _validate_claim_against_context(claim=claim, spec=spec, context=context)
    if len({claim.evidence_sha256 for claim in claims}) != len(claims):
        _fail("PHYSICAL_FULL_MATRIX_V4_FINAL_CONVERGENCE_EVIDENCE_REUSED")
    return _assessment(
        status="blocked-typed-evidence-cross-pinned-not-final-admission",
        reason_codes=(
            "future-owner-verifiers-and-phase8-runtime-not-implemented",
            "generic-oracle-evidence-sha256-is-not-convergence-proof",
        ),
        context=context,
        p7_completion_anchor_provenance_present=True,
    )
