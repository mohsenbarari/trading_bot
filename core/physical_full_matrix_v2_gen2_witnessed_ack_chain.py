"""Pure Gen2-only Full-Matrix Witnessed ACK-chain join.

This module intentionally does not import, accept, adapt, or fall back to the
historical Gen1 strict-writer ACK chain.  Its exact positive inputs are:

* one owner-verified V2 recovery-evidence capability;
* one owner-verified portable V2 Witness roundtrip attestation; and
* one owner-verified **Gen2 bridge-bound** strict-writer observation.

The Gen2 response already proves the V1 parent and V1--V2 bridge at its own
owner boundary.  This join revalidates that owner again, cross-pins every V2
attestation field, both persisted V2-base identities, the complete flat V1
parent projection, and all bridge certificate/intent/binding pins into an
opaque diagnostic chain.  It is default-off, performs no I/O, and is never a
writer, recovery, promotion, or execution permit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceError,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_wal_v2_witness_roundtrip_contract import (
    PhysicalWalV2WitnessRoundtripConfig,
    PhysicalWalV2WitnessRoundtripError,
    VerifiedPhysicalWalV2WitnessContextCertificate,
    VerifiedPhysicalWalV2WitnessIrDurableAssertion,
    VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    VerifiedPhysicalWalV2WitnessSourceEnvelope,
    require_verified_physical_wal_v2_witness_roundtrip_attestation,
    verify_physical_wal_v2_witness_context_certificate,
    verify_physical_wal_v2_witness_ir_durable_assertion,
    verify_physical_wal_v2_witness_source_envelope,
)
from core.physical_wal_v2_witness_roundtrip_strict_writer_bound_response import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation,
)
from core.physical_wal_v2_witness_roundtrip_strict_writer_response import (
    PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
)


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA",
    "PhysicalFullMatrixV2Gen2WitnessedAckChainConfig",
    "PhysicalFullMatrixV2Gen2WitnessedAckChainError",
    "PhysicalFullMatrixV2Gen2WitnessedAckChainInputs",
    "PhysicalFullMatrixV2Gen2WitnessedAckChainPins",
    "PhysicalFullMatrixV2Gen2WitnessedAckChainProjection",
    "VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain",
    "mint_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain",
    "project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain",
    "require_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain",
)


PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-gen2-witnessed-ack-chain-v1"
)
PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES = 256 * 1024
# The bridge owner accepts a canonical certificate no larger than 64 KiB.  The
# ACK join does not parse it (the bridge-bound Gen2 owner already did), but it
# must still bound and type-check bytes before hashing them defensively.
MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_BRIDGE_CERTIFICATE_BYTES = 64 * 1024

_GEN1_BASE_COMMIT_RE = re.compile(
    r"^v2-witness-strict-writer-[0-9a-f]{64}$", re.ASCII
)
_GEN2_BOUND_COMMIT_RE = re.compile(
    r"^v2-witness-strict-writer-g2-[0-9a-f]{64}$", re.ASCII
)

_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64
_TERM_FIELDS = frozenset(
    {
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)


class PhysicalFullMatrixV2Gen2WitnessedAckChainError(ValueError):
    """A Gen2 Witnessed ACK-chain input is invalid, stale, or cross-pinned."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedAckChainConfig:
    """Default-off policy cross-pinning the V2 roundtrip and Gen2 owner."""

    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig | None = None
    bound_response_config: (
        PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig | None
    ) = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedAckChainInputs:
    """The only three Gen2 admissible opaque owner capabilities."""

    recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence | None = None
    witness_roundtrip_attestation: (
        VerifiedPhysicalWalV2WitnessRoundtripAttestation | None
    ) = None
    bound_strict_writer_response: (
        VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation
        | None
    ) = None


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedAckChainPins:
    """Complete non-authorizing Gen2 chain payload and readiness bind surface.

    Fields are intentionally flat.  A later readiness or V4 layer must not
    collapse a valid V2 witness observation into only a commit id: every V2
    pin, both base identities, every V1 parent scalar, and every bridge pin
    remains independently visible in the hashed chain/binding.
    """

    schema: str
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
    source_envelope_sha256: str
    source_request_sha256: str
    request_id: str
    request_nonce: str
    destination_receipt_sha256: str
    receipt_id: str
    receipt_nonce: str
    durable_ledger_entry_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    witness_mediation_id: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    roundtrip_attestation_sha256: str
    roundtrip_attestation_id: str
    roundtrip_attestation_nonce: str
    roundtrip_attestation_issued_at: datetime
    roundtrip_attestation_expires_at: datetime
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    roundtrip_configuration_sha256: str
    strict_observation_schema: str
    strict_observation_sha256: str
    strict_runtime_commit_receipt_sha256: str
    strict_instruction_schema: str
    strict_configuration_sha256: str
    strict_v2_base_configuration_sha256: str
    strict_atomic_commit_boundary: str
    strict_commit_id: str
    strict_v2_base_commit_id: str
    strict_local_commit_record_id: str
    strict_local_response_id: str
    strict_attestation_consumption_id: str
    strict_committed_at: datetime
    strict_issued_at: datetime
    strict_v1_parent_cluster_id: str
    strict_v1_parent_local_site: str
    strict_v1_parent_release_sha: str
    strict_v1_parent_generation_id: str
    strict_v1_writer_admission_commit_id: str
    strict_v1_writer_admission_commit_sha256: str
    strict_v1_writer_admission_receipt_sha256: str
    strict_v1_parent_prior_revision: int
    strict_v1_parent_next_revision: int
    strict_v1_parent_fence_generation: int
    strict_v1_parent_holder_site: str
    strict_v1_parent_evidence_id: str
    strict_v1_parent_revalidation_id: str
    strict_v1_parent_writer_epoch: int
    strict_v1_parent_writer_lease_id: str
    strict_v1_parent_term_issued_at: datetime
    strict_v1_parent_term_expires_at: datetime
    strict_v1_parent_admitted_at: datetime
    strict_v1_v2_writer_term_bridge_certificate_id: str
    strict_v1_v2_writer_term_bridge_intent_sha256: str
    strict_v1_v2_writer_term_bridge_certificate_sha256: str
    strict_v1_v2_writer_term_bridge_parent_binding_sha256: str
    target_lsn: str
    object_version_set_sha256: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain(
    PhysicalFullMatrixV2Gen2WitnessedAckChainPins
):
    """Opaque process-local Gen2 ACK join, explicitly non-authorizing."""

    recovery_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalFullMatrixV2Gen2WitnessedAckChainProjection(
    PhysicalFullMatrixV2Gen2WitnessedAckChainPins
):
    """Exact public Gen2 pins after fresh owner/chain revalidation."""

    recovery_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True)
class _ConfigFacts:
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig
    bound_response_config: PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    maximum_evidence_age_seconds: int
    source_site: str
    destination_site: str


@dataclass(frozen=True)
class _PortableFacts:
    assertion: VerifiedPhysicalWalV2WitnessIrDurableAssertion
    envelope: VerifiedPhysicalWalV2WitnessSourceEnvelope
    certificate: VerifiedPhysicalWalV2WitnessContextCertificate
    context: dict[str, Any]


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2Gen2WitnessedAckChainConfig
    inputs: PhysicalFullMatrixV2Gen2WitnessedAckChainInputs


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain, _State] = WeakKeyDictionary()


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        result = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)
    if result.microsecond:
        _fail(code)
    return result


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or (not permit_zero and value == _ZERO_SHA256):
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if type(value) is not str or value not in WEBAPP_SITES:
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


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_CONSTANT_FORBIDDEN")


def _canonical_context(value: object) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    try:
        parsed = json.loads(value.decode("ascii", "strict"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except PhysicalFullMatrixV2Gen2WitnessedAckChainError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    if type(parsed) is not dict:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    try:
        if canonical_json_bytes(parsed) != value:
            _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID"
        ) from exc
    return dict(parsed)


def _roundtrip_configuration_sha256(value: object) -> str:
    """Read the roundtrip config digest only from a canonical signed artifact."""

    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    try:
        parsed = json.loads(value.decode("ascii", "strict"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except PhysicalFullMatrixV2Gen2WitnessedAckChainError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    if type(parsed) is not dict:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    try:
        if canonical_json_bytes(parsed) != value:
            _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID"
        ) from exc
    return _sha(
        parsed.get("configuration_sha256"),
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID",
    )


def _term(value: object, *, code: str) -> tuple[str, int, str, str]:
    if type(value) is not dict or set(value) != _TERM_FIELDS:
        _fail(code)
    holder = _site(value["writer_holder_site"], code=code)
    epoch = value["writer_epoch"]
    lease = value["writer_lease_id"]
    if (
        type(epoch) is not int
        or not 1 <= epoch <= 2**31 - 1
        or type(lease) is not str
        or LEASE_ID_RE.fullmatch(lease) is None
    ):
        _fail(code)
    return holder, epoch, lease, _sha(value["witnessed_term_proof_sha256"], code=code)


def _config(value: object) -> _ConfigFacts:
    if (
        type(value) is not PhysicalFullMatrixV2Gen2WitnessedAckChainConfig
        or value.enabled is not True
        or type(value.roundtrip_config) is not PhysicalWalV2WitnessRoundtripConfig
        or type(value.bound_response_config)
        is not PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
    roundtrip = value.roundtrip_config
    bound = value.bound_response_config
    legacy_config = bound.legacy_response_config
    remote = roundtrip.remote_ack_config
    if (
        roundtrip.enabled is not True
        or bound.enabled is not True
        or type(legacy_config)
        is not PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
        or legacy_config.enabled is not True
        or legacy_config.roundtrip_config != roundtrip
        or remote is None
        or getattr(remote, "enabled", None) is not True
        or type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS
        or value.maximum_evidence_age_seconds > roundtrip.maximum_evidence_age_seconds
        or value.maximum_evidence_age_seconds > bound.maximum_evidence_age_seconds
        or value.maximum_evidence_age_seconds > legacy_config.maximum_evidence_age_seconds
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_MISMATCH")
    try:
        source = _site(
            remote.expected_source_site,
            code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_INVALID",
        )
        destination = _site(
            remote.expected_destination_site,
            code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_INVALID",
        )
    except AttributeError:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
    if source == destination:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONFIG_MISMATCH")
    return _ConfigFacts(
        roundtrip_config=roundtrip,
        bound_response_config=bound,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        source_site=source,
        destination_site=destination,
    )


def _inputs(value: object) -> PhysicalFullMatrixV2Gen2WitnessedAckChainInputs:
    if type(value) is not PhysicalFullMatrixV2Gen2WitnessedAckChainInputs:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_INPUTS_INVALID")
    if (
        type(value.recovery_evidence) is not VerifiedPhysicalFullMatrixV2RecoveryEvidence
        or type(value.witness_roundtrip_attestation)
        is not VerifiedPhysicalWalV2WitnessRoundtripAttestation
        or type(value.bound_strict_writer_response)
        is not VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_INPUTS_INVALID")
    return value


def _portable_facts(
    *,
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    config: _ConfigFacts,
    now: datetime,
) -> _PortableFacts:
    try:
        assertion = verify_physical_wal_v2_witness_ir_durable_assertion(
            attestation.canonical_ir_durable_assertion,
            config=config.roundtrip_config,
            now=now,
        )
        envelope = verify_physical_wal_v2_witness_source_envelope(
            assertion.canonical_source_envelope,
            config=config.roundtrip_config,
            now=now,
        )
        certificate = verify_physical_wal_v2_witness_context_certificate(
            envelope.canonical_context_certificate,
            config=config.roundtrip_config,
            now=now,
        )
    except PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_PORTABLE_ASSERTION_INVALID"
        ) from exc
    context = _canonical_context(certificate.canonical_context)
    if (
        attestation.canonical_ir_durable_assertion != assertion.canonical_assertion
        or attestation.ir_durable_assertion_sha256 != assertion.assertion_sha256
        or attestation.context_certificate_sha256 != certificate.certificate_sha256
        or attestation.context_sha256 != assertion.context_sha256
        or attestation.context_sha256 != envelope.context_sha256
        or attestation.context_sha256 != certificate.context_sha256
        or attestation.source_envelope_sha256 != assertion.source_envelope_sha256
        or attestation.source_envelope_sha256 != envelope.envelope_sha256
        or attestation.source_request_sha256 != assertion.source_request_sha256
        or attestation.source_request_sha256 != envelope.source_request_sha256
        or attestation.destination_receipt_sha256 != assertion.destination_receipt_sha256
        or attestation.durable_ledger_entry_sha256 != assertion.durable_ledger_entry_sha256
        or attestation.target_recovery_evidence_sha256 != assertion.target_recovery_evidence_sha256
        or attestation.readback_attestation_sha256 != assertion.readback_attestation_sha256
        or attestation.stage_receipt_sha256 != assertion.stage_receipt_sha256
        or attestation.witness_transition_id != assertion.witness_transition_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_PORTABLE_CROSS_PIN_MISMATCH")
    return _PortableFacts(
        assertion=assertion,
        envelope=envelope,
        certificate=certificate,
        context=context,
    )


def _context_facts(
    context: dict[str, Any],
) -> tuple[str, str, str, str, str, str, str, tuple[str, int, str, str], str, str]:
    campaign = context.get("campaign_id")
    release = context.get("release_sha")
    if (
        type(campaign) is not str
        or CAMPAIGN_ID_RE.fullmatch(campaign) is None
        or type(release) is not str
        or RELEASE_SHA_RE.fullmatch(release) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    source = _site(context.get("source_site"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    destination = _site(context.get("destination_site"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    route = _sha(context.get("route_commitment_sha256"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    four_role = _sha(context.get("four_role_binding_sha256"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    stream = _identifier(context.get("stream_generation_id"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    term = _term(context.get("writer_term"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    target_lsn = _lsn(context.get("target_lsn"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    objects = _sha(context.get("object_version_set_sha256"), code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    return campaign, release, source, destination, route, four_role, stream, term, target_lsn, objects


def _strict_instruction(
    value: object,
    *,
    normalized: _ConfigFacts,
) -> tuple[
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
]:
    try:
        observation = require_verified_physical_wal_v2_witness_roundtrip_strict_writer_bound_response_observation(
            value,
            config=normalized.bound_response_config,
        )
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
        raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_BOUND_RESPONSE_INVALID"
        ) from exc
    if (
        type(observation)
        is not VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation
        or type(observation.instruction)
        is not PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
        or observation.schema
        != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_BOUND_RESPONSE_INVALID")
    return observation, observation.instruction


def _validate_strict_shape(
    value: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
) -> None:
    """Reject a malformed owner projection before it enters the Gen2 hash."""

    if (
        value.schema != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA
        or value.atomic_commit_boundary
        != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY
        or type(value.commit_id) is not str
        or _GEN2_BOUND_COMMIT_RE.fullmatch(value.commit_id) is None
        or type(value.v2_base_commit_id) is not str
        or _GEN1_BASE_COMMIT_RE.fullmatch(value.v2_base_commit_id) is None
        or type(value.writer_holder_site) is not str
        or value.writer_holder_site not in WEBAPP_SITES
        or type(value.v1_parent_local_site) is not str
        or value.v1_parent_local_site not in WEBAPP_SITES
        or type(value.v1_parent_holder_site) is not str
        or value.v1_parent_holder_site not in WEBAPP_SITES
        or value.v1_parent_holder_site != value.writer_holder_site
        or value.v1_parent_local_site != value.v1_parent_holder_site
        or type(value.writer_epoch) is not int
        or value.writer_epoch < 1
        or type(value.v1_parent_writer_epoch) is not int
        or value.v1_parent_writer_epoch < 1
        or value.v1_parent_writer_epoch != value.writer_epoch
        or type(value.v1_parent_prior_revision) is not int
        or value.v1_parent_prior_revision < 0
        or type(value.v1_parent_next_revision) is not int
        or type(value.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None
        or type(value.v1_parent_writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(value.v1_parent_writer_lease_id) is None
        or value.v1_parent_writer_lease_id != value.writer_lease_id
        or value.v1_parent_next_revision != value.v1_parent_prior_revision + 1
        or type(value.v1_parent_fence_generation) is not int
        or value.v1_parent_fence_generation < 0
        or type(value.witness_sequence) is not int
        or value.witness_sequence < 1
        or type(value.activation_mode) is not str
        or value.activation_mode not in {"normal_fi_writer", "promoted_ir_writer"}
        or type(value.v1_parent_release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(value.v1_parent_release_sha) is None
        or type(value.canonical_v1_v2_writer_term_bridge_certificate) is not bytes
        or not 1
        <= len(value.canonical_v1_v2_writer_term_bridge_certificate)
        <= MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_BRIDGE_CERTIFICATE_BYTES
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID")
    for item in (
        value.configuration_sha256,
        value.v2_base_configuration_sha256,
        value.attestation_sha256,
        value.ir_durable_assertion_sha256,
        value.context_certificate_sha256,
        value.context_sha256,
        value.source_envelope_sha256,
        value.source_request_sha256,
        value.destination_receipt_sha256,
        value.durable_ledger_entry_sha256,
        value.target_recovery_evidence_sha256,
        value.readback_attestation_sha256,
        value.stage_receipt_sha256,
        value.witness_ledger_entry_sha256,
        value.witness_ledger_binding_sha256,
        value.witnessed_term_proof_sha256,
        value.activation_route_artifact_sha256,
        value.activation_source_cutover_attestation_sha256,
        value.activation_receiver_permit_sha256,
        value.v1_writer_admission_commit_sha256,
        value.v1_writer_admission_receipt_sha256,
        value.v1_v2_writer_term_bridge_intent_sha256,
        value.v1_v2_writer_term_bridge_certificate_sha256,
        value.v1_v2_writer_term_bridge_parent_binding_sha256,
    ):
        _sha(item, code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID")
    _sha(
        value.witness_ledger_previous_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID",
        permit_zero=True,
    )
    for item in (
        value.writer_lease_id,
        value.witness_transition_id,
        value.activation_stream_generation_id,
        value.v1_parent_cluster_id,
        value.v1_parent_generation_id,
        value.v1_writer_admission_commit_id,
        value.v1_parent_evidence_id,
        value.v1_parent_revalidation_id,
        value.v1_v2_writer_term_bridge_certificate_id,
    ):
        _identifier(item, code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID")
    for item in (
        value.v1_parent_term_issued_at,
        value.v1_parent_term_expires_at,
        value.v1_parent_admitted_at,
    ):
        _utc(item, code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID")
    if (
        value.v1_parent_term_expires_at <= value.v1_parent_term_issued_at
        or value.v1_parent_admitted_at < value.v1_parent_term_issued_at
        or value.v1_parent_admitted_at >= value.v1_parent_term_expires_at
        or hashlib.sha256(value.canonical_v1_v2_writer_term_bridge_certificate).hexdigest()
        != value.v1_v2_writer_term_bridge_certificate_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID")


def _strict_payload(
    value: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    *,
    observation: VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation,
) -> dict[str, object]:
    """Flat exact Gen2 response pins, deliberately including the V1 bridge."""

    return {
        "strict_observation_schema": observation.schema,
        "strict_observation_sha256": observation.observation_sha256,
        "strict_runtime_commit_receipt_sha256": observation.runtime_commit_receipt_sha256,
        "strict_instruction_schema": value.schema,
        "strict_configuration_sha256": value.configuration_sha256,
        "strict_v2_base_configuration_sha256": value.v2_base_configuration_sha256,
        "strict_atomic_commit_boundary": value.atomic_commit_boundary,
        "strict_commit_id": value.commit_id,
        "strict_v2_base_commit_id": value.v2_base_commit_id,
        "strict_local_commit_record_id": observation.local_commit_record_id,
        "strict_local_response_id": observation.local_response_id,
        "strict_attestation_consumption_id": observation.attestation_consumption_id,
        "strict_committed_at": observation.committed_at,
        "strict_issued_at": value.issued_at,
        "strict_v1_parent_cluster_id": value.v1_parent_cluster_id,
        "strict_v1_parent_local_site": value.v1_parent_local_site,
        "strict_v1_parent_release_sha": value.v1_parent_release_sha,
        "strict_v1_parent_generation_id": value.v1_parent_generation_id,
        "strict_v1_writer_admission_commit_id": value.v1_writer_admission_commit_id,
        "strict_v1_writer_admission_commit_sha256": value.v1_writer_admission_commit_sha256,
        "strict_v1_writer_admission_receipt_sha256": value.v1_writer_admission_receipt_sha256,
        "strict_v1_parent_prior_revision": value.v1_parent_prior_revision,
        "strict_v1_parent_next_revision": value.v1_parent_next_revision,
        "strict_v1_parent_fence_generation": value.v1_parent_fence_generation,
        "strict_v1_parent_holder_site": value.v1_parent_holder_site,
        "strict_v1_parent_evidence_id": value.v1_parent_evidence_id,
        "strict_v1_parent_revalidation_id": value.v1_parent_revalidation_id,
        "strict_v1_parent_writer_epoch": value.v1_parent_writer_epoch,
        "strict_v1_parent_writer_lease_id": value.v1_parent_writer_lease_id,
        "strict_v1_parent_term_issued_at": value.v1_parent_term_issued_at,
        "strict_v1_parent_term_expires_at": value.v1_parent_term_expires_at,
        "strict_v1_parent_admitted_at": value.v1_parent_admitted_at,
        "strict_v1_v2_writer_term_bridge_certificate_id": value.v1_v2_writer_term_bridge_certificate_id,
        "strict_v1_v2_writer_term_bridge_intent_sha256": value.v1_v2_writer_term_bridge_intent_sha256,
        "strict_v1_v2_writer_term_bridge_certificate_sha256": value.v1_v2_writer_term_bridge_certificate_sha256,
        "strict_v1_v2_writer_term_bridge_parent_binding_sha256": value.v1_v2_writer_term_bridge_parent_binding_sha256,
    }


def _hashable(value: object) -> object:
    if type(value) is datetime:
        return _utc(value, code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_PAYLOAD_INVALID").isoformat()
    return value


def _derive(
    *,
    config: object,
    inputs: object,
    now: datetime,
) -> PhysicalFullMatrixV2Gen2WitnessedAckChainPins:
    normalized = _config(config)
    supplied = _inputs(inputs)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CLOCK_INVALID")
    try:
        recovery = require_verified_physical_full_matrix_v2_recovery_evidence(
            supplied.recovery_evidence,
            now=observed,
        )
        attestation = require_verified_physical_wal_v2_witness_roundtrip_attestation(
            supplied.witness_roundtrip_attestation,
            config=normalized.roundtrip_config,
            now=observed,
        )
        strict_observation, strict = _strict_instruction(
            supplied.bound_strict_writer_response,
            normalized=normalized,
        )
    except (
        PhysicalFullMatrixV2RecoveryEvidenceError,
        PhysicalWalV2WitnessRoundtripError,
        PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
        PhysicalFullMatrixV2Gen2WitnessedAckChainError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, PhysicalFullMatrixV2Gen2WitnessedAckChainError):
            raise
        raise PhysicalFullMatrixV2Gen2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID"
        ) from exc
    _validate_strict_shape(strict)
    # Re-check every time-bearing fact against one caller-supplied UTC clock.
    # The strict-response owner intentionally has its own trusted clock; this
    # join does not override it, but it does ensure that the already-verified
    # observation can still participate in *this* campaign assessment.
    recovery_observed_at = _utc(
        recovery.observed_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID",
    )
    attestation_issued_at = _utc(
        attestation.issued_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID",
    )
    attestation_expires_at = _utc(
        attestation.expires_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID",
    )
    strict_committed_at = _utc(
        strict_observation.committed_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID",
    )
    strict_issued_at = _utc(
        strict.issued_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID",
    )
    parent_term_issued_at = _utc(
        strict.v1_parent_term_issued_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID",
    )
    parent_term_expires_at = _utc(
        strict.v1_parent_term_expires_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID",
    )
    parent_admitted_at = _utc(
        strict.v1_parent_admitted_at,
        code="PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_STRICT_SHAPE_INVALID",
    )
    portable = _portable_facts(attestation=attestation, config=normalized, now=observed)
    (
        campaign,
        release,
        source,
        destination,
        route,
        four_role,
        stream,
        context_term,
        target_lsn,
        object_versions,
    ) = _context_facts(portable.context)
    binding = recovery.transfer_binding
    recovery_term = binding.writer_term
    assertion = portable.assertion
    if (
        source != normalized.source_site
        or destination != normalized.destination_site
        or source != binding.source_site
        or destination != binding.destination_site
        or campaign != binding.campaign_id
        or release != binding.release_sha
        or route != recovery.route_commitment_sha256
        or four_role != recovery.four_role_binding_sha256
        or stream != recovery.stream_generation_id
        or target_lsn != recovery.target_replay_lsn
        or object_versions != recovery.object_version_set_sha256
        or context_term
        != (
            recovery_term.writer_holder_site,
            recovery_term.writer_epoch,
            recovery_term.writer_lease_id,
            recovery_term.witnessed_term_proof_sha256,
        )
        or (
            attestation.writer_holder_site,
            attestation.writer_epoch,
            attestation.writer_lease_id,
            attestation.witnessed_term_proof_sha256,
        )
        != context_term
        or attestation.witness_transition_id != recovery.witness_transition_id
        or attestation.target_recovery_evidence_sha256 != recovery.evidence_sha256
        or attestation.readback_attestation_sha256 != recovery.readback_attestation_sha256
        or attestation.stage_receipt_sha256 != recovery.stage_receipt_sha256
        or assertion.target_recovery_evidence_sha256 != recovery.evidence_sha256
        or assertion.readback_attestation_sha256 != recovery.readback_attestation_sha256
        or assertion.stage_receipt_sha256 != recovery.stage_receipt_sha256
        or assertion.witness_transition_id != recovery.witness_transition_id
        or strict.attestation_sha256 != attestation.attestation_sha256
        or strict.ir_durable_assertion_sha256 != attestation.ir_durable_assertion_sha256
        or strict.context_certificate_sha256 != attestation.context_certificate_sha256
        or strict.context_sha256 != attestation.context_sha256
        or strict.source_envelope_sha256 != attestation.source_envelope_sha256
        or strict.source_request_sha256 != attestation.source_request_sha256
        or strict.destination_receipt_sha256 != attestation.destination_receipt_sha256
        or strict.durable_ledger_entry_sha256 != attestation.durable_ledger_entry_sha256
        or strict.target_recovery_evidence_sha256 != attestation.target_recovery_evidence_sha256
        or strict.readback_attestation_sha256 != attestation.readback_attestation_sha256
        or strict.stage_receipt_sha256 != attestation.stage_receipt_sha256
        or strict.witness_transition_id != attestation.witness_transition_id
        or strict.witness_sequence != attestation.witness_sequence
        or strict.witness_ledger_entry_sha256 != attestation.witness_ledger_entry_sha256
        or strict.witness_ledger_previous_head_sha256 != attestation.witness_ledger_previous_head_sha256
        or strict.witness_ledger_binding_sha256 != attestation.witness_ledger_binding_sha256
        or (
            strict.writer_holder_site,
            strict.writer_epoch,
            strict.writer_lease_id,
            strict.witnessed_term_proof_sha256,
        )
        != context_term
        or strict.activation_mode != attestation.activation_mode
        or strict.activation_stream_generation_id != attestation.activation_stream_generation_id
        or strict.activation_route_artifact_sha256 != attestation.activation_route_artifact_sha256
        or strict.activation_source_cutover_attestation_sha256
        != attestation.activation_source_cutover_attestation_sha256
        or strict.activation_receiver_permit_sha256 != attestation.activation_receiver_permit_sha256
        or strict_observation.attestation_consumption_id
        != "v2-witness-consume-g2-" + attestation.attestation_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CROSS_PIN_MISMATCH")
    if (
        observed - recovery_observed_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or recovery_observed_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or observed - attestation_issued_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or attestation_issued_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or attestation_expires_at <= attestation_issued_at
        or attestation_expires_at <= observed
        or observed - strict_committed_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or strict_committed_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or observed - strict_issued_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or strict_issued_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or parent_term_issued_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or parent_admitted_at
        > observed + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or parent_term_expires_at <= observed
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_EVIDENCE_STALE_OR_FUTURE")
    strict_pins = _strict_payload(strict, observation=strict_observation)
    strict_pins.update(
        {
            "strict_committed_at": strict_committed_at,
            "strict_issued_at": strict_issued_at,
            "strict_v1_parent_term_issued_at": parent_term_issued_at,
            "strict_v1_parent_term_expires_at": parent_term_expires_at,
            "strict_v1_parent_admitted_at": parent_admitted_at,
        }
    )
    payload: dict[str, object] = {
        "campaign_id": campaign,
        "release_sha": release,
        "source_site": source,
        "destination_site": destination,
        "route_commitment_sha256": route,
        "four_role_binding_sha256": four_role,
        "writer_holder_site": context_term[0],
        "writer_epoch": context_term[1],
        "writer_lease_id": context_term[2],
        "witnessed_term_proof_sha256": context_term[3],
        "context_sha256": attestation.context_sha256,
        "source_envelope_sha256": attestation.source_envelope_sha256,
        "source_request_sha256": attestation.source_request_sha256,
        "request_id": assertion.request_id,
        "request_nonce": assertion.request_nonce,
        "destination_receipt_sha256": attestation.destination_receipt_sha256,
        "receipt_id": assertion.receipt_id,
        "receipt_nonce": assertion.receipt_nonce,
        "durable_ledger_entry_sha256": attestation.durable_ledger_entry_sha256,
        "receiver_recovery_evidence_sha256": assertion.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": assertion.receiver_replay_lsn,
        "target_recovery_evidence_sha256": attestation.target_recovery_evidence_sha256,
        "readback_attestation_sha256": attestation.readback_attestation_sha256,
        "stage_receipt_sha256": attestation.stage_receipt_sha256,
        "witness_transition_id": attestation.witness_transition_id,
        "activation_mode": attestation.activation_mode,
        "activation_stream_generation_id": attestation.activation_stream_generation_id,
        "activation_route_artifact_sha256": attestation.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": attestation.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": attestation.activation_receiver_permit_sha256,
        "witness_mediation_id": attestation.mediation_id,
        "witness_sequence": attestation.witness_sequence,
        "witness_ledger_entry_sha256": attestation.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": attestation.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": attestation.witness_ledger_binding_sha256,
        "roundtrip_attestation_sha256": attestation.attestation_sha256,
        "roundtrip_attestation_id": attestation.attestation_id,
        "roundtrip_attestation_nonce": attestation.attestation_nonce,
        "roundtrip_attestation_issued_at": attestation_issued_at,
        "roundtrip_attestation_expires_at": attestation_expires_at,
        "ir_durable_assertion_sha256": attestation.ir_durable_assertion_sha256,
        "context_certificate_sha256": attestation.context_certificate_sha256,
        "roundtrip_configuration_sha256": _roundtrip_configuration_sha256(attestation.canonical_attestation),
        "target_lsn": recovery.target_replay_lsn,
        "object_version_set_sha256": recovery.object_version_set_sha256,
        **strict_pins,
    }
    hash_payload = {
        "schema": PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA,
        **{name: _hashable(item) for name, item in payload.items()},
        "recovery_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    chain_sha = hashlib.sha256(canonical_json_bytes(hash_payload)).hexdigest()
    return PhysicalFullMatrixV2Gen2WitnessedAckChainPins(
        schema=PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA,
        chain_sha256=chain_sha,
        **payload,
    )


def _assert_result(
    value: object,
    *,
    expected: PhysicalFullMatrixV2Gen2WitnessedAckChainPins,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain:
    if type(value) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CAPABILITY_REQUIRED")
    for name in PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__:
        if getattr(value, name) != getattr(expected, name):
            _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CAPABILITY_TAMPERED")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_SCHEMA
        or value.recovery_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CAPABILITY_TAMPERED")
    return value


def mint_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
    *,
    config: PhysicalFullMatrixV2Gen2WitnessedAckChainConfig,
    inputs: PhysicalFullMatrixV2Gen2WitnessedAckChainInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain:
    """Mint an opaque, non-authorizing Gen2 join after all owner checks."""

    pins = _derive(config=config, inputs=inputs, now=now)
    result = VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain(**pins.__dict__)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(config=config, inputs=inputs)
    return _assert_result(result, expected=pins)


def require_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
    value: object,
    *,
    config: PhysicalFullMatrixV2Gen2WitnessedAckChainConfig,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain:
    """Revalidate all three opaque owners and every Gen2 cross-pin."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedAckChain
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or config != state.config:
        _fail("PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_ACK_CHAIN_PROVENANCE_MISSING")
    return _assert_result(value, expected=_derive(config=config, inputs=state.inputs, now=now))


def project_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
    value: object,
    *,
    config: PhysicalFullMatrixV2Gen2WitnessedAckChainConfig,
    now: datetime,
) -> PhysicalFullMatrixV2Gen2WitnessedAckChainProjection:
    """Project every pinned Gen2 field after a fresh opaque revalidation."""

    verified = require_verified_physical_full_matrix_v2_gen2_witnessed_ack_chain(
        value,
        config=config,
        now=now,
    )
    payload = {
        name: getattr(verified, name)
        for name in PhysicalFullMatrixV2Gen2WitnessedAckChainPins.__dataclass_fields__
    }
    return PhysicalFullMatrixV2Gen2WitnessedAckChainProjection(**payload)
