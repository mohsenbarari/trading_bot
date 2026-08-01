"""Gen2-only campaign-readiness projection for the V1-bound V2 ACK chain.

This readiness generation is deliberately isolated from every historical
Gen1 witnessed ACK/readiness type.  It accepts one opaque Gen2-only ACK chain,
repeats every chain pin in a flat immutable binding, and reports only local
diagnostic evidence.  It never authorizes execution, promotion, recovery,
storage, transport, or a writer.  In particular, a raw durable database row
cannot stand in for the process-local Gen2 strict-observation capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_gen2_witnessed_ack_chain import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA,
    PhysicalFullMatrixV2Gen2WitnessedAckChainConfig,
    PhysicalFullMatrixV2Gen2WitnessedAckChainError,
    PhysicalFullMatrixV2Gen2WitnessedAckChainPins,
    PhysicalFullMatrixV2Gen2WitnessedAckChainProjection,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain,
    project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain,
)
from core.physical_wal_v2_witness_roundtrip_strict_writer_bound_response import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS",
    "PhysicalFullMatrixV2Gen2WitnessedCampaignBinding",
    "PhysicalFullMatrixV2Gen2WitnessedCampaignInputs",
    "PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness",
    "PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig",
    "PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError",
    "VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness",
    "assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
    "mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
    "require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness",
)


PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-gen2-witnessed-campaign-readiness-v1"
)
PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED = "blocked"
PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED = (
    "v2-gen2-witnessed-ack-chain-observed"
)
PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS = (
    "v2-gen2-witness-mediated-ack-chain",
)

_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64
_GEN1_BASE_COMMIT_RE = re.compile(
    r"^v2-witness-strict-writer-[0-9a-f]{64}$", re.ASCII
)
_GEN2_BOUND_COMMIT_RE = re.compile(
    r"^v2-witness-strict-writer-g2-[0-9a-f]{64}$", re.ASCII
)
_ACTIVATION_MODES = frozenset({"normal_fi_writer", "promoted_ir_writer"})


class PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError(ValueError):
    """Gen2 readiness evidence is malformed, legacy, foreign, or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedCampaignBinding(
    PhysicalFullMatrixV2Gen2WitnessedAckChainPins
):
    """Complete flat Gen2 ACK pin surface for exactly one readiness report.

    Inheriting the pin record is intentional: introducing a new readiness
    generation must not accidentally omit a future V2 base, V1-parent, or
    bridge pin.  The comparison below iterates that exact inherited field
    surface rather than maintaining a lossy hand-written subset.
    """


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig:
    """Default-off policy accepting only the separate Gen2 ACK generation."""

    binding: PhysicalFullMatrixV2Gen2WitnessedCampaignBinding | None = None
    gen2_witnessed_ack_chain_config: (
        PhysicalFullMatrixV2Gen2WitnessedAckChainConfig | None
    ) = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedCampaignInputs:
    """One typed Gen2 chain plus an explicit historical-artifact fence."""

    gen2_witnessed_ack_chain: VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain | None = None
    legacy_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    """Public non-authorizing diagnostic report for a local Gen2 observation."""

    schema: str
    status: str
    campaign_id: str | None
    release_sha: str | None
    binding_sha256: str | None
    observed_slots: tuple[str, ...]
    reason_codes: tuple[str, ...]
    external_execution_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    """Opaque process-local provenance for a positive Gen2 readiness report."""

    report: PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig
    inputs: PhysicalFullMatrixV2Gen2WitnessedCampaignInputs
    report: PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
    report_sha256: str


_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness, _State
] = WeakKeyDictionary()

_PIN_FIELD_NAMES = tuple(PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__)
_SHA_PIN_NAMES = (
    "chain_sha256",
    "route_commitment_sha256",
    "four_role_binding_sha256",
    "witnessed_term_proof_sha256",
    "context_sha256",
    "source_envelope_sha256",
    "source_request_sha256",
    "destination_receipt_sha256",
    "durable_ledger_entry_sha256",
    "receiver_recovery_evidence_sha256",
    "target_recovery_evidence_sha256",
    "readback_attestation_sha256",
    "stage_receipt_sha256",
    "activation_route_artifact_sha256",
    "activation_source_cutover_attestation_sha256",
    "activation_receiver_permit_sha256",
    "witness_ledger_entry_sha256",
    "witness_ledger_binding_sha256",
    "roundtrip_attestation_sha256",
    "ir_durable_assertion_sha256",
    "context_certificate_sha256",
    "roundtrip_configuration_sha256",
    "strict_observation_sha256",
    "strict_runtime_commit_receipt_sha256",
    "strict_configuration_sha256",
    "strict_v2_base_configuration_sha256",
    "strict_v1_writer_admission_commit_sha256",
    "strict_v1_writer_admission_receipt_sha256",
    "strict_v1_v2_writer_term_bridge_intent_sha256",
    "strict_v1_v2_writer_term_bridge_certificate_sha256",
    "strict_v1_v2_writer_term_bridge_parent_binding_sha256",
    "object_version_set_sha256",
)
_IDENTIFIER_PIN_NAMES = (
    "writer_lease_id",
    "witness_transition_id",
    "witness_mediation_id",
    "activation_stream_generation_id",
    "request_id",
    "request_nonce",
    "receipt_id",
    "receipt_nonce",
    "strict_local_commit_record_id",
    "strict_local_response_id",
    "strict_v1_parent_cluster_id",
    "strict_v1_parent_generation_id",
    "strict_v1_writer_admission_commit_id",
    "strict_v1_parent_evidence_id",
    "strict_v1_parent_revalidation_id",
    "strict_v1_v2_writer_term_bridge_certificate_id",
    "roundtrip_attestation_id",
    "roundtrip_attestation_nonce",
)
_TIME_PIN_NAMES = (
    "roundtrip_attestation_issued_at",
    "roundtrip_attestation_expires_at",
    "strict_committed_at",
    "strict_issued_at",
    "strict_v1_parent_term_issued_at",
    "strict_v1_parent_term_expires_at",
    "strict_v1_parent_admitted_at",
)


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CLOCK_INVALID")
    try:
        result = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CLOCK_INVALID")
    if result.microsecond:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CLOCK_INVALID")
    return result


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or not value or len(value) > 255:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    if any(character.isspace() for character in value):
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> str:
    if type(value) is not str or "/" not in value or len(value) > 32:
        _fail(code)
    prefix, suffix = value.split("/", 1)
    if not prefix or not suffix:
        _fail(code)
    try:
        int(prefix, 16)
        int(suffix, 16)
    except ValueError:
        _fail(code)
    return value


def _binding(value: object) -> PhysicalFullMatrixV2Gen2WitnessedCampaignBinding:
    """Validate the full inherited pin surface before it becomes a policy pin."""

    code = "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_BINDING_INVALID"
    if type(value) is not PhysicalFullMatrixV2Gen2WitnessedCampaignBinding:
        _fail(code)
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA
        or type(value.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None
        or type(value.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(value.release_sha) is None
        or type(value.source_site) is not str
        or value.source_site not in WEBAPP_SITES
        or type(value.destination_site) is not str
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
        or type(value.writer_holder_site) is not str
        or value.writer_holder_site != value.source_site
        or type(value.writer_epoch) is not int
        or not 1 <= value.writer_epoch <= 2**31 - 1
        or type(value.witness_sequence) is not int
        or value.witness_sequence < 1
        or value.activation_mode not in _ACTIVATION_MODES
        or value.strict_observation_schema
        != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA
        or value.strict_instruction_schema
        != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA
        or value.strict_atomic_commit_boundary
        != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY
        or type(value.strict_commit_id) is not str
        or _GEN2_BOUND_COMMIT_RE.fullmatch(value.strict_commit_id) is None
        or type(value.strict_v2_base_commit_id) is not str
        or _GEN1_BASE_COMMIT_RE.fullmatch(value.strict_v2_base_commit_id) is None
        or type(value.strict_v1_parent_prior_revision) is not int
        or value.strict_v1_parent_prior_revision < 0
        or type(value.strict_v1_parent_next_revision) is not int
        or type(value.strict_v1_parent_fence_generation) is not int
        or value.strict_v1_parent_fence_generation < 0
        or type(value.strict_v1_parent_writer_epoch) is not int
        or value.strict_v1_parent_writer_epoch != value.writer_epoch
    ):
        _fail(code)
    for name in _SHA_PIN_NAMES:
        _sha(getattr(value, name), code=code)
    _sha(value.witness_ledger_previous_head_sha256, code=code, permit_zero=True)
    for name in _IDENTIFIER_PIN_NAMES:
        _identifier(getattr(value, name), code=code)
    for name in _TIME_PIN_NAMES:
        _utc(getattr(value, name))
    _lsn(value.receiver_replay_lsn, code=code)
    _lsn(value.target_lsn, code=code)
    if (
        type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
        or type(value.strict_v1_parent_writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.strict_v1_parent_writer_lease_id) is None
        or value.strict_v1_parent_local_site != value.writer_holder_site
        or value.strict_v1_parent_holder_site != value.writer_holder_site
        or value.strict_v1_parent_writer_lease_id != value.writer_lease_id
        or value.strict_v1_parent_next_revision
        != value.strict_v1_parent_prior_revision + 1
        or value.strict_attestation_consumption_id
        != "v2-witness-consume-g2-" + value.roundtrip_attestation_sha256
        or value.strict_v1_parent_term_expires_at
        <= value.strict_v1_parent_term_issued_at
        or value.strict_v1_parent_admitted_at < value.strict_v1_parent_term_issued_at
        or value.strict_v1_parent_admitted_at >= value.strict_v1_parent_term_expires_at
        or value.roundtrip_attestation_expires_at
        <= value.roundtrip_attestation_issued_at
    ):
        _fail(code)
    return value


def _binding_mapping(value: PhysicalFullMatrixV2Gen2WitnessedCampaignBinding) -> dict[str, object]:
    """Canonical map containing every inherited ACK pin, without omission."""

    result: dict[str, object] = {
        "readiness_schema": PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    }
    for name in _PIN_FIELD_NAMES:
        item = getattr(value, name)
        result[name] = _utc(item).isoformat() if type(item) is datetime else item
    return result


def _binding_sha256(value: PhysicalFullMatrixV2Gen2WitnessedCampaignBinding) -> str:
    return hashlib.sha256(canonical_json_bytes(_binding_mapping(value))).hexdigest()


def _config(
    value: object,
) -> tuple[
    PhysicalFullMatrixV2Gen2WitnessedCampaignBinding,
    PhysicalFullMatrixV2Gen2WitnessedAckChainConfig,
]:
    if (
        type(value) is not PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig
        or value.enabled is not True
        or type(value.gen2_witnessed_ack_chain_config)
        is not PhysicalFullMatrixV2Gen2WitnessedAckChainConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CONFIG_INVALID")
    return _binding(value.binding), value.gen2_witnessed_ack_chain_config


def _legacy_artifacts_present(value: object) -> bool:
    """Fence legacy capability objects without parsing, adapting, or importing them."""

    if value is None:
        return False
    if type(value) in (tuple, list, str):
        return len(value) != 0
    return True


def _report(
    *,
    binding: PhysicalFullMatrixV2Gen2WitnessedCampaignBinding | None,
    slots: set[str],
    reasons: set[str],
) -> PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    positive = (
        not reasons
        and tuple(sorted(slots))
        == PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS
    )
    return PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
        status=(
            PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
            if positive
            else PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED
        ),
        campaign_id=None if binding is None else binding.campaign_id,
        release_sha=None if binding is None else binding.release_sha,
        binding_sha256=None if binding is None else _binding_sha256(binding),
        observed_slots=tuple(sorted(slots)),
        reason_codes=tuple(sorted(reasons)),
    )


def _report_sha256(value: PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness) -> str:
    """Pin every public diagnostic field against in-process object mutation."""

    try:
        encoded = canonical_json_bytes(
            {
                "schema": value.schema,
                "status": value.status,
                "campaign_id": value.campaign_id,
                "release_sha": value.release_sha,
                "binding_sha256": value.binding_sha256,
                "observed_slots": value.observed_slots,
                "reason_codes": value.reason_codes,
                "external_execution_authorized": value.external_execution_authorized,
                "promotion_authorized": value.promotion_authorized,
                "execution_authorized": value.execution_authorized,
            }
        )
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CAPABILITY_TAMPERED")
    return hashlib.sha256(encoded).hexdigest()


def _chain_matches_binding(
    chain: PhysicalFullMatrixV2Gen2WitnessedAckChainProjection,
    *,
    binding: PhysicalFullMatrixV2Gen2WitnessedCampaignBinding,
) -> bool:
    """Require equality of every inherited Gen2 ACK field, never a subset."""

    if type(chain) is not PhysicalFullMatrixV2Gen2WitnessedAckChainProjection:
        return False
    if any(
        getattr(chain, name, object()) != getattr(binding, name)
        for name in _PIN_FIELD_NAMES
    ):
        return False
    return (
        chain.recovery_authorized is False
        and chain.promotion_authorized is False
        and chain.execution_authorized is False
    )


def assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
    config: object,
    inputs: object,
    *,
    now: datetime,
) -> PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    """Assess one fresh opaque Gen2 chain without I/O or side effects."""

    slots: set[str] = set()
    reasons: set[str] = set()
    try:
        observed = _utc(now)
    except PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError:
        return _report(binding=None, slots=slots, reasons={"invalid-assessment-clock"})
    try:
        binding, chain_config = _config(config)
    except PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError as exc:
        return _report(
            binding=None,
            slots=slots,
            reasons={
                "driver-disabled"
                if exc.code == "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CONFIG_INVALID"
                else "invalid-v2-gen2-witnessed-campaign-binding"
            },
        )
    if type(inputs) is not PhysicalFullMatrixV2Gen2WitnessedCampaignInputs:
        return _report(
            binding=binding,
            slots=slots,
            reasons={"invalid-v2-gen2-witnessed-campaign-inputs"},
        )
    if _legacy_artifacts_present(inputs.legacy_artifacts):
        return _report(
            binding=binding,
            slots=slots,
            reasons={"legacy-gen1-artifact-rejected"},
        )
    if inputs.gen2_witnessed_ack_chain is None:
        reasons.add("missing-v2-gen2-witness-mediated-ack-chain")
    elif type(inputs.gen2_witnessed_ack_chain) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain:
        # This deliberately includes legacy Gen1 ACK chains/observations and
        # raw reconstructed records.  No adapter or fallback exists here.
        reasons.add("v2-gen2-witness-mediated-ack-chain-mismatch")
    else:
        try:
            chain = project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
                inputs.gen2_witnessed_ack_chain,
                config=chain_config,
                now=observed,
            )
            if not _chain_matches_binding(chain, binding=binding):
                _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_ACK_CHAIN_MISMATCH")
            slots.add("v2-gen2-witness-mediated-ack-chain")
        except (
            PhysicalFullMatrixV2Gen2WitnessedAckChainError,
            PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError,
            TypeError,
            ValueError,
        ):
            reasons.add("v2-gen2-witness-mediated-ack-chain-mismatch")
    return _report(binding=binding, slots=slots, reasons=reasons)


def _positive(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    if (
        type(value) is not PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
        or value.schema != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA
        or value.status
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or value.reason_codes != ()
        or value.observed_slots
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS
        or type(value.campaign_id) is not str
        or type(value.release_sha) is not str
        or type(value.binding_sha256) is not str
        or SHA256_RE.fullmatch(value.binding_sha256) is None
        or value.external_execution_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail(code)
    return value


def mint_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
    *,
    config: PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessConfig,
    inputs: PhysicalFullMatrixV2Gen2WitnessedCampaignInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    """Mint only process-local provenance for a complete Gen2 readiness report."""

    report = _positive(
        assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
            config,
            inputs,
            now=now,
        ),
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_POSITIVE_REQUIRED",
    )
    result = VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness(report=report)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(
        config=config,
        inputs=inputs,
        report=report,
        report_sha256=_report_sha256(report),
    )
    return result


def require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
    value: object,
    *,
    now: datetime | None = None,
) -> PhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    """Return an untampered, optionally fresh-revalidated local report only."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or value.report is not state.report:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CAPABILITY_REQUIRED")
    report = _positive(
        state.report,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CAPABILITY_TAMPERED",
    )
    if _report_sha256(report) != state.report_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_CAPABILITY_TAMPERED")
    if now is None:
        return report
    observed = _utc(now)
    rechecked = _positive(
        assess_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
            state.config,
            state.inputs,
            now=observed,
        ),
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_REVALIDATION_BLOCKED",
    )
    if rechecked != report:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_READINESS_REVALIDATION_MISMATCH")
    return report
