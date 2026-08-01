"""Witnessed V2-only local readiness for the physical Full Matrix.

This generation is intentionally separate from the earlier V2 readiness
aggregate.  Its sole positive input is the opaque, revalidatable
Witness-mediated ACK-chain bridge.  A positive result is diagnostic local
evidence only: it never authorizes a writer, promotion, deployment, storage
mutation, transport, or Full-Matrix phase.

The bridge is the owning boundary for the portable four-hop roundtrip.  This
module neither parses mailbox artifacts nor combines preflight, V1, raw V2
context, recovery, or receiver-ledger objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_witnessed_ack_chain import (
    PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA,
    PhysicalFullMatrixV2WitnessedAckChainConfig,
    PhysicalFullMatrixV2WitnessedAckChainError,
    PhysicalFullMatrixV2WitnessedAckChainProjection,
    VerifiedPhysicalFullMatrixV2WitnessedAckChain,
    project_verified_physical_full_matrix_v2_witnessed_ack_chain,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_REQUIRED_READINESS_SLOTS",
    "PhysicalFullMatrixV2WitnessedCampaignBinding",
    "PhysicalFullMatrixV2WitnessedCampaignInputs",
    "PhysicalFullMatrixV2WitnessedCampaignReadiness",
    "PhysicalFullMatrixV2WitnessedCampaignReadinessConfig",
    "PhysicalFullMatrixV2WitnessedCampaignReadinessError",
    "VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness",
    "assess_physical_full_matrix_v2_witnessed_campaign_readiness",
    "mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness",
    "require_verified_physical_full_matrix_v2_witnessed_campaign_readiness",
)


PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-witnessed-campaign-readiness-v1"
)
PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED = "blocked"
PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED = (
    "v2-witnessed-ack-chain-observed"
)
PHYSICAL_FULL_MATRIX_V2_WITNESSED_REQUIRED_READINESS_SLOTS = (
    "v2-witness-mediated-ack-chain",
)

_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64


class PhysicalFullMatrixV2WitnessedCampaignReadinessError(ValueError):
    """Witnessed V2 evidence is malformed, stale, foreign, or non-local."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedCampaignBinding:
    """Exact redacted pins for one witnessed V2 writer direction.

    These values repeat the bridge projection deliberately.  The readiness
    layer must not treat a valid roundtrip from a different campaign, writer
    term, activation, Witness ledger chain, or recovery lineage as evidence
    for this campaign.
    """

    chain_sha256: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    context_sha256: str
    context_certificate_sha256: str
    source_request_sha256: str
    source_envelope_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    ir_durable_assertion_sha256: str
    roundtrip_attestation_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    roundtrip_configuration_sha256: str
    strict_observation_sha256: str
    strict_runtime_commit_receipt_sha256: str
    strict_commit_id: str
    strict_local_commit_record_id: str
    strict_local_response_id: str
    strict_attestation_consumption_id: str
    strict_committed_at: datetime
    target_lsn: str
    object_version_set_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedCampaignReadinessConfig:
    """Default-off policy bound to the sole witnessed-ACK bridge generation."""

    binding: PhysicalFullMatrixV2WitnessedCampaignBinding | None = None
    witnessed_ack_chain_config: PhysicalFullMatrixV2WitnessedAckChainConfig | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedCampaignInputs:
    """One typed Witnessed ACK chain and an explicit legacy-artifact fence."""

    witnessed_ack_chain: VerifiedPhysicalFullMatrixV2WitnessedAckChain | None = None
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedCampaignReadiness:
    """Public non-authorizing diagnostic report for one local observation."""

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
class VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness:
    """Opaque process-local provenance for a positive witnessed observation."""

    report: PhysicalFullMatrixV2WitnessedCampaignReadiness
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2WitnessedCampaignReadinessConfig
    inputs: PhysicalFullMatrixV2WitnessedCampaignInputs
    report: PhysicalFullMatrixV2WitnessedCampaignReadiness
    report_sha256: str


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness, _State] = (
    WeakKeyDictionary()
)


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2WitnessedCampaignReadinessError(code)


def _utc(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CLOCK_INVALID")
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
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


def _binding(value: object) -> PhysicalFullMatrixV2WitnessedCampaignBinding:
    if type(value) is not PhysicalFullMatrixV2WitnessedCampaignBinding:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    if type(value.campaign_id) is not str or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    if type(value.release_sha) is not str or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    if (
        type(value.source_site) is not str
        or type(value.destination_site) is not str
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
        or type(value.writer_holder_site) is not str
        or value.writer_holder_site != value.source_site
        or type(value.writer_epoch) is not int
        or not 1 <= value.writer_epoch <= 2**31 - 1
        or type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
        or type(value.activation_mode) is not str
        or not value.activation_mode
        or type(value.activation_stream_generation_id) is not str
        or not value.activation_stream_generation_id
        or type(value.witness_transition_id) is not str
        or not value.witness_transition_id
        or type(value.witness_sequence) is not int
        or value.witness_sequence < 1
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    for field_name in (
        "chain_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "witnessed_term_proof_sha256",
        "context_sha256",
        "context_certificate_sha256",
        "source_request_sha256",
        "source_envelope_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "receiver_recovery_evidence_sha256",
        "ir_durable_assertion_sha256",
        "roundtrip_attestation_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "witness_ledger_entry_sha256",
        "witness_ledger_binding_sha256",
        "roundtrip_configuration_sha256",
        "strict_observation_sha256",
        "strict_runtime_commit_receipt_sha256",
        "object_version_set_sha256",
    ):
        _sha256(
            getattr(value, field_name),
            code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID",
        )
    previous_head = value.witness_ledger_previous_head_sha256
    if (
        type(previous_head) is not str
        or SHA256_RE.fullmatch(previous_head) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    for field_name in (
        "strict_commit_id",
        "strict_local_commit_record_id",
        "strict_local_response_id",
        "strict_attestation_consumption_id",
    ):
        _identifier(
            getattr(value, field_name),
            code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID",
        )
    _lsn(
        value.receiver_replay_lsn,
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID",
    )
    _lsn(
        value.target_lsn,
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID",
    )
    if (
        type(value.strict_committed_at) is not datetime
        or value.strict_committed_at.tzinfo is None
        or value.strict_committed_at.utcoffset() is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_BINDING_INVALID")
    return value


def _binding_mapping(value: PhysicalFullMatrixV2WitnessedCampaignBinding) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
        "chain_sha256": value.chain_sha256,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "writer_holder_site": value.writer_holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
        "context_sha256": value.context_sha256,
        "context_certificate_sha256": value.context_certificate_sha256,
        "source_request_sha256": value.source_request_sha256,
        "source_envelope_sha256": value.source_envelope_sha256,
        "destination_receipt_sha256": value.destination_receipt_sha256,
        "durable_ledger_entry_sha256": value.durable_ledger_entry_sha256,
        "receiver_recovery_evidence_sha256": value.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": value.receiver_replay_lsn,
        "ir_durable_assertion_sha256": value.ir_durable_assertion_sha256,
        "roundtrip_attestation_sha256": value.roundtrip_attestation_sha256,
        "target_recovery_evidence_sha256": value.target_recovery_evidence_sha256,
        "readback_attestation_sha256": value.readback_attestation_sha256,
        "stage_receipt_sha256": value.stage_receipt_sha256,
        "witness_transition_id": value.witness_transition_id,
        "activation_mode": value.activation_mode,
        "activation_stream_generation_id": value.activation_stream_generation_id,
        "activation_route_artifact_sha256": value.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": value.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": value.activation_receiver_permit_sha256,
        "witness_sequence": value.witness_sequence,
        "witness_ledger_entry_sha256": value.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": value.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": value.witness_ledger_binding_sha256,
        "roundtrip_configuration_sha256": value.roundtrip_configuration_sha256,
        "strict_observation_sha256": value.strict_observation_sha256,
        "strict_runtime_commit_receipt_sha256": value.strict_runtime_commit_receipt_sha256,
        "strict_commit_id": value.strict_commit_id,
        "strict_local_commit_record_id": value.strict_local_commit_record_id,
        "strict_local_response_id": value.strict_local_response_id,
        "strict_attestation_consumption_id": value.strict_attestation_consumption_id,
        "strict_committed_at": value.strict_committed_at.isoformat(),
        "target_lsn": value.target_lsn,
        "object_version_set_sha256": value.object_version_set_sha256,
    }


def _binding_sha256(value: PhysicalFullMatrixV2WitnessedCampaignBinding) -> str:
    return hashlib.sha256(canonical_json_bytes(_binding_mapping(value))).hexdigest()


def _config(
    value: object,
) -> tuple[
    PhysicalFullMatrixV2WitnessedCampaignBinding,
    PhysicalFullMatrixV2WitnessedAckChainConfig,
]:
    if (
        type(value) is not PhysicalFullMatrixV2WitnessedCampaignReadinessConfig
        or value.enabled is not True
        or type(value.witnessed_ack_chain_config)
        is not PhysicalFullMatrixV2WitnessedAckChainConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CONFIG_INVALID")
    return _binding(value.binding), value.witnessed_ack_chain_config


def _legacy_artifacts_present(value: object) -> bool:
    """Reject historical artifacts without parsing or adapting them."""

    if value is None:
        return False
    if type(value) in (tuple, list, str):
        return len(value) != 0
    return True


def _report(
    *,
    binding: PhysicalFullMatrixV2WitnessedCampaignBinding | None,
    slots: set[str],
    reasons: set[str],
) -> PhysicalFullMatrixV2WitnessedCampaignReadiness:
    positive = not reasons and tuple(sorted(slots)) == PHYSICAL_FULL_MATRIX_V2_WITNESSED_REQUIRED_READINESS_SLOTS
    return PhysicalFullMatrixV2WitnessedCampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
        status=(
            PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
            if positive
            else PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_BLOCKED
        ),
        campaign_id=None if binding is None else binding.campaign_id,
        release_sha=None if binding is None else binding.release_sha,
        binding_sha256=None if binding is None else _binding_sha256(binding),
        observed_slots=tuple(sorted(slots)),
        reason_codes=tuple(sorted(reasons)),
    )


def _report_sha256(value: PhysicalFullMatrixV2WitnessedCampaignReadiness) -> str:
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
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CAPABILITY_TAMPERED")
    return hashlib.sha256(encoded).hexdigest()


def _chain_matches_binding(
    chain: PhysicalFullMatrixV2WitnessedAckChainProjection,
    *,
    binding: PhysicalFullMatrixV2WitnessedCampaignBinding,
) -> bool:
    """Compare every public bridge pin explicitly; no boolean authority exists."""

    expected = {
        "chain_sha256": binding.chain_sha256,
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
        "context_sha256": binding.context_sha256,
        "context_certificate_sha256": binding.context_certificate_sha256,
        "source_request_sha256": binding.source_request_sha256,
        "source_envelope_sha256": binding.source_envelope_sha256,
        "destination_receipt_sha256": binding.destination_receipt_sha256,
        "durable_ledger_entry_sha256": binding.durable_ledger_entry_sha256,
        "receiver_recovery_evidence_sha256": binding.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": binding.receiver_replay_lsn,
        "ir_durable_assertion_sha256": binding.ir_durable_assertion_sha256,
        "roundtrip_attestation_sha256": binding.roundtrip_attestation_sha256,
        "target_recovery_evidence_sha256": binding.target_recovery_evidence_sha256,
        "readback_attestation_sha256": binding.readback_attestation_sha256,
        "stage_receipt_sha256": binding.stage_receipt_sha256,
        "witness_transition_id": binding.witness_transition_id,
        "activation_mode": binding.activation_mode,
        "activation_stream_generation_id": binding.activation_stream_generation_id,
        "activation_route_artifact_sha256": binding.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": binding.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": binding.activation_receiver_permit_sha256,
        "witness_sequence": binding.witness_sequence,
        "witness_ledger_entry_sha256": binding.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": binding.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": binding.witness_ledger_binding_sha256,
        "roundtrip_configuration_sha256": binding.roundtrip_configuration_sha256,
        "strict_observation_sha256": binding.strict_observation_sha256,
        "strict_runtime_commit_receipt_sha256": binding.strict_runtime_commit_receipt_sha256,
        "strict_commit_id": binding.strict_commit_id,
        "strict_local_commit_record_id": binding.strict_local_commit_record_id,
        "strict_local_response_id": binding.strict_local_response_id,
        "strict_attestation_consumption_id": binding.strict_attestation_consumption_id,
        "strict_committed_at": binding.strict_committed_at,
        "target_lsn": binding.target_lsn,
        "object_version_set_sha256": binding.object_version_set_sha256,
    }
    if getattr(chain, "schema", None) != PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA:
        return False
    if any(getattr(chain, name, object()) != expected_value for name, expected_value in expected.items()):
        return False
    return (
        getattr(chain, "recovery_authorized", None) is False
        and getattr(chain, "promotion_authorized", None) is False
        and getattr(chain, "execution_authorized", None) is False
    )


def assess_physical_full_matrix_v2_witnessed_campaign_readiness(
    config: object,
    inputs: object,
    *,
    now: datetime,
) -> PhysicalFullMatrixV2WitnessedCampaignReadiness:
    """Assess one fresh opaque Witnessed ACK chain without performing I/O."""

    slots: set[str] = set()
    reasons: set[str] = set()
    try:
        _utc(now)
    except PhysicalFullMatrixV2WitnessedCampaignReadinessError:
        return _report(binding=None, slots=slots, reasons={"invalid-assessment-clock"})
    try:
        binding, witnessed_config = _config(config)
    except PhysicalFullMatrixV2WitnessedCampaignReadinessError as exc:
        return _report(
            binding=None,
            slots=slots,
            reasons={
                "driver-disabled"
                if exc.code == "PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CONFIG_INVALID"
                else "invalid-v2-witnessed-campaign-binding"
            },
        )
    if type(inputs) is not PhysicalFullMatrixV2WitnessedCampaignInputs:
        return _report(binding=binding, slots=slots, reasons={"invalid-v2-witnessed-campaign-inputs"})
    if _legacy_artifacts_present(inputs.legacy_runner_artifacts):
        return _report(binding=binding, slots=slots, reasons={"legacy-v1-artifact-rejected"})
    if inputs.witnessed_ack_chain is None:
        reasons.add("missing-v2-witness-mediated-ack-chain")
    elif type(inputs.witnessed_ack_chain) is not VerifiedPhysicalFullMatrixV2WitnessedAckChain:
        reasons.add("v2-witness-mediated-ack-chain-mismatch")
    else:
        try:
            chain = project_verified_physical_full_matrix_v2_witnessed_ack_chain(
                inputs.witnessed_ack_chain,
                config=witnessed_config,
                now=now,
            )
            if (
                type(chain) is not PhysicalFullMatrixV2WitnessedAckChainProjection
                or not _chain_matches_binding(chain, binding=binding)
            ):
                _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_ACK_CHAIN_MISMATCH")
            slots.add("v2-witness-mediated-ack-chain")
        except (
            PhysicalFullMatrixV2WitnessedAckChainError,
            PhysicalFullMatrixV2WitnessedCampaignReadinessError,
            TypeError,
            ValueError,
        ):
            reasons.add("v2-witness-mediated-ack-chain-mismatch")
    return _report(binding=binding, slots=slots, reasons=reasons)


def _positive(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixV2WitnessedCampaignReadiness:
    if (
        type(value) is not PhysicalFullMatrixV2WitnessedCampaignReadiness
        or value.schema != PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_SCHEMA
        or value.status
        != PHYSICAL_FULL_MATRIX_V2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or value.reason_codes != ()
        or value.observed_slots != PHYSICAL_FULL_MATRIX_V2_WITNESSED_REQUIRED_READINESS_SLOTS
        or type(value.campaign_id) is not str
        or type(value.release_sha) is not str
        or type(value.binding_sha256) is not str
        or value.external_execution_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail(code)
    return value


def mint_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
    *,
    config: PhysicalFullMatrixV2WitnessedCampaignReadinessConfig,
    inputs: PhysicalFullMatrixV2WitnessedCampaignInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness:
    """Mint only process-local provenance for a complete witnessed report."""

    report = _positive(
        assess_physical_full_matrix_v2_witnessed_campaign_readiness(config, inputs, now=now),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_POSITIVE_REQUIRED",
    )
    result = VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness(report=report)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(
        config=config,
        inputs=inputs,
        report=report,
        report_sha256=_report_sha256(report),
    )
    return result


def require_verified_physical_full_matrix_v2_witnessed_campaign_readiness(
    value: object,
    *,
    now: datetime | None = None,
) -> PhysicalFullMatrixV2WitnessedCampaignReadiness:
    """Return only an untampered, optionally revalidated local report."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2WitnessedCampaignReadiness
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or value.report is not state.report:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CAPABILITY_REQUIRED")
    report = _positive(
        state.report,
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CAPABILITY_TAMPERED",
    )
    if _report_sha256(report) != state.report_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_CAPABILITY_TAMPERED")
    if now is None:
        return report
    rechecked = _positive(
        assess_physical_full_matrix_v2_witnessed_campaign_readiness(
            state.config,
            state.inputs,
            now=now,
        ),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_REVALIDATION_BLOCKED",
    )
    if rechecked != report:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_READINESS_REVALIDATION_MISMATCH")
    return report
