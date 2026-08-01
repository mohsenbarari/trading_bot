"""Pure V2 Full-Matrix join for the Witness-mediated acknowledgement path.

This is intentionally separate from the legacy ACK-chain boundary.  It
accepts exactly three owner-verified capabilities: current V2 recovery
evidence, the portable Witness round-trip attestation, and the FI local strict
writer response that consumed that attestation.  It revalidates all three at a
single supplied clock and exposes only an opaque, non-authorizing join.

The receiver replay position and receipt facts are read only by re-verifying
the signed portable IR assertion nested in the Witness attestation.  No raw
IR recovery capability, receiver-ledger capability, older strict response, or
transport/local-state interface can enter this boundary.  It performs no I/O,
does not persist anything, and never grants recovery, promotion, execution, or
writer authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
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
from core.physical_wal_v2_witness_roundtrip_strict_writer_response import (
    PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation,
    require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation,
)


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA",
    "PhysicalFullMatrixV2WitnessedAckChainConfig",
    "PhysicalFullMatrixV2WitnessedAckChainError",
    "PhysicalFullMatrixV2WitnessedAckChainInputs",
    "PhysicalFullMatrixV2WitnessedAckChainProjection",
    "VerifiedPhysicalFullMatrixV2WitnessedAckChain",
    "mint_verified_physical_full_matrix_v2_witnessed_ack_chain",
    "project_verified_physical_full_matrix_v2_witnessed_ack_chain",
    "require_verified_physical_full_matrix_v2_witnessed_ack_chain",
)


PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-witnessed-ack-chain-v1"
)
PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES = 256 * 1024

_TERM_FIELDS = frozenset(
    {
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)
_CAPABILITY = object()


class PhysicalFullMatrixV2WitnessedAckChainError(ValueError):
    """A V2 Witness ACK-chain input is unsafe, stale, or cross-pinned wrongly."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedAckChainConfig:
    """Default-off policy using only the V2 Witness and strict-writer owners."""

    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig | None = None
    strict_writer_config: (
        PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig | None
    ) = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedAckChainInputs:
    """The only admissible evidence surfaces for this final V2 join."""

    recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence | None = None
    witness_roundtrip_attestation: (
        VerifiedPhysicalWalV2WitnessRoundtripAttestation | None
    ) = None
    strict_writer_response: (
        VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation | None
    ) = None


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2WitnessedAckChain:
    """Opaque, revalidatable V2 join; it is explicitly non-authorizing."""

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
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    roundtrip_attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
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
    recovery_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalFullMatrixV2WitnessedAckChainProjection:
    """Exact public pins after fresh revalidation; never a release permit."""

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
    destination_receipt_sha256: str
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
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    roundtrip_attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
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
    recovery_authorized: bool
    promotion_authorized: bool
    execution_authorized: bool


@dataclass(frozen=True)
class _ConfigFacts:
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig
    strict_writer_config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
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
    config: PhysicalFullMatrixV2WitnessedAckChainConfig
    inputs: PhysicalFullMatrixV2WitnessedAckChainInputs


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV2WitnessedAckChain, _State] = (
    WeakKeyDictionary()
)


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2WitnessedAckChainError(code)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == "0" * 64)
    ):
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
            _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_CONSTANT_FORBIDDEN")


def _canonical_context(value: object) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalFullMatrixV2WitnessedAckChainError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    if type(parsed) is not dict:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    try:
        canonical = canonical_json_bytes(parsed)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID"
        ) from exc
    if canonical != value:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    return dict(parsed)


def _roundtrip_configuration_sha256(value: object) -> str:
    """Read the configuration hash only from a reverified signed artifact."""

    if (
        type(value) is not bytes
        or not 1
        <= len(value)
        <= MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_BYTES
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalFullMatrixV2WitnessedAckChainError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    if type(parsed) is not dict or canonical_json_bytes(parsed) != value:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID")
    return _sha256(
        parsed.get("configuration_sha256"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_ATTESTATION_INVALID",
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
    return holder, epoch, lease, _sha256(value["witnessed_term_proof_sha256"], code=code)


def _config(value: object) -> _ConfigFacts:
    if (
        type(value) is not PhysicalFullMatrixV2WitnessedAckChainConfig
        or value.enabled is not True
        or type(value.roundtrip_config) is not PhysicalWalV2WitnessRoundtripConfig
        or type(value.strict_writer_config)
        is not PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
    roundtrip_config = value.roundtrip_config
    strict_config = value.strict_writer_config
    remote = roundtrip_config.remote_ack_config
    if (
        roundtrip_config.enabled is not True
        or strict_config.enabled is not True
        or strict_config.roundtrip_config != roundtrip_config
        or remote is None
        or getattr(remote, "enabled", None) is not True
        or type(value.maximum_evidence_age_seconds) is not int
        or not 1
        <= value.maximum_evidence_age_seconds
        <= MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_MAXIMUM_EVIDENCE_AGE_SECONDS
        or value.maximum_evidence_age_seconds
        > roundtrip_config.maximum_evidence_age_seconds
        or value.maximum_evidence_age_seconds
        > strict_config.maximum_evidence_age_seconds
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_MISMATCH")
    try:
        source_site = _site(remote.expected_source_site, code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
        destination_site = _site(remote.expected_destination_site, code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
    except AttributeError:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_INVALID")
    if source_site == destination_site:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONFIG_MISMATCH")
    return _ConfigFacts(
        roundtrip_config=roundtrip_config,
        strict_writer_config=strict_config,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        source_site=source_site,
        destination_site=destination_site,
    )


def _inputs(value: object) -> PhysicalFullMatrixV2WitnessedAckChainInputs:
    if type(value) is not PhysicalFullMatrixV2WitnessedAckChainInputs:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_INPUTS_INVALID")
    required = (
        (value.recovery_evidence, VerifiedPhysicalFullMatrixV2RecoveryEvidence),
        (
            value.witness_roundtrip_attestation,
            VerifiedPhysicalWalV2WitnessRoundtripAttestation,
        ),
        (
            value.strict_writer_response,
            VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation,
        ),
    )
    if any(type(item) is not expected for item, expected in required):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_INPUTS_INVALID")
    return value


def _portable_facts(
    *,
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    config: _ConfigFacts,
    now: datetime,
) -> _PortableFacts:
    """Use only portable contract verifiers to reach signed assertion facts."""

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
        raise PhysicalFullMatrixV2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_PORTABLE_ASSERTION_INVALID"
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
        or attestation.durable_ledger_entry_sha256
        != assertion.durable_ledger_entry_sha256
        or attestation.target_recovery_evidence_sha256
        != assertion.target_recovery_evidence_sha256
        or attestation.readback_attestation_sha256
        != assertion.readback_attestation_sha256
        or attestation.stage_receipt_sha256 != assertion.stage_receipt_sha256
        or attestation.witness_transition_id != assertion.witness_transition_id
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_PORTABLE_CROSS_PIN_MISMATCH")
    return _PortableFacts(
        assertion=assertion,
        envelope=envelope,
        certificate=certificate,
        context=context,
    )


def _context_facts(
    context: Mapping[str, Any],
) -> tuple[str, str, str, str, str, str, str, tuple[str, int, str, str], str, str]:
    """Extract only canonical V2 request context facts already contract-verified."""

    campaign = context.get("campaign_id")
    release = context.get("release_sha")
    if (
        type(campaign) is not str
        or CAMPAIGN_ID_RE.fullmatch(campaign) is None
        or type(release) is not str
        or RELEASE_SHA_RE.fullmatch(release) is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID")
    source = _site(
        context.get("source_site"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    destination = _site(
        context.get("destination_site"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    route = _sha256(
        context.get("route_commitment_sha256"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    four_role = _sha256(
        context.get("four_role_binding_sha256"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    stream = _identifier(
        context.get("stream_generation_id"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    term = _term(
        context.get("writer_term"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    target_lsn = _lsn(
        context.get("target_lsn"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    object_versions = _sha256(
        context.get("object_version_set_sha256"),
        code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CONTEXT_INVALID",
    )
    return (
        campaign,
        release,
        source,
        destination,
        route,
        four_role,
        stream,
        term,
        target_lsn,
        object_versions,
    )


def _derive(
    *,
    config: object,
    inputs: object,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2WitnessedAckChain:
    normalized = _config(config)
    supplied = _inputs(inputs)
    observed = _utc(now, code="PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CLOCK_INVALID")
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
        strict = (
            require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
                supplied.strict_writer_response,
                config=normalized.strict_writer_config,
                now=observed,
            )
        )
    except (
        PhysicalFullMatrixV2RecoveryEvidenceError,
        PhysicalWalV2WitnessRoundtripError,
        PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
        TypeError,
        ValueError,
    ) as exc:
        raise PhysicalFullMatrixV2WitnessedAckChainError(
            "PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_UPSTREAM_INVALID"
        ) from exc
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
        or attestation.readback_attestation_sha256
        != recovery.readback_attestation_sha256
        or attestation.stage_receipt_sha256 != recovery.stage_receipt_sha256
        or assertion.target_recovery_evidence_sha256 != recovery.evidence_sha256
        or assertion.readback_attestation_sha256
        != recovery.readback_attestation_sha256
        or assertion.stage_receipt_sha256 != recovery.stage_receipt_sha256
        or assertion.witness_transition_id != recovery.witness_transition_id
        or strict.attestation_sha256 != attestation.attestation_sha256
        or strict.ir_durable_assertion_sha256 != attestation.ir_durable_assertion_sha256
        or strict.context_certificate_sha256 != attestation.context_certificate_sha256
        or strict.context_sha256 != attestation.context_sha256
        or strict.source_envelope_sha256 != attestation.source_envelope_sha256
        or strict.source_request_sha256 != attestation.source_request_sha256
        or strict.destination_receipt_sha256 != attestation.destination_receipt_sha256
        or strict.durable_ledger_entry_sha256
        != attestation.durable_ledger_entry_sha256
        or strict.target_recovery_evidence_sha256
        != attestation.target_recovery_evidence_sha256
        or strict.readback_attestation_sha256 != attestation.readback_attestation_sha256
        or strict.stage_receipt_sha256 != attestation.stage_receipt_sha256
        or strict.witness_transition_id != attestation.witness_transition_id
        or strict.witness_sequence != attestation.witness_sequence
        or strict.witness_ledger_entry_sha256
        != attestation.witness_ledger_entry_sha256
        or strict.witness_ledger_previous_head_sha256
        != attestation.witness_ledger_previous_head_sha256
        or strict.witness_ledger_binding_sha256
        != attestation.witness_ledger_binding_sha256
        or (
            strict.writer_holder_site,
            strict.writer_epoch,
            strict.writer_lease_id,
            strict.witnessed_term_proof_sha256,
        )
        != context_term
        or strict.activation_mode != attestation.activation_mode
        or strict.activation_stream_generation_id
        != attestation.activation_stream_generation_id
        or strict.activation_route_artifact_sha256
        != attestation.activation_route_artifact_sha256
        or strict.activation_source_cutover_attestation_sha256
        != attestation.activation_source_cutover_attestation_sha256
        or strict.activation_receiver_permit_sha256
        != attestation.activation_receiver_permit_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CROSS_PIN_MISMATCH")
    if (
        observed - recovery.observed_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or recovery.observed_at
        > observed
        + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or observed - attestation.issued_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or attestation.issued_at
        > observed
        + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
        or observed - strict.committed_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
        or strict.committed_at
        > observed
        + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_FUTURE_SKEW_SECONDS)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_EVIDENCE_STALE_OR_FUTURE")
    roundtrip_configuration_sha = _roundtrip_configuration_sha256(
        attestation.canonical_attestation
    )
    payload: dict[str, object] = {
        "schema": PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA,
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
        "activation_source_cutover_attestation_sha256": (
            attestation.activation_source_cutover_attestation_sha256
        ),
        "activation_receiver_permit_sha256": attestation.activation_receiver_permit_sha256,
        "witness_sequence": attestation.witness_sequence,
        "witness_ledger_entry_sha256": attestation.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": attestation.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": attestation.witness_ledger_binding_sha256,
        "roundtrip_attestation_sha256": attestation.attestation_sha256,
        "ir_durable_assertion_sha256": attestation.ir_durable_assertion_sha256,
        "context_certificate_sha256": attestation.context_certificate_sha256,
        "roundtrip_configuration_sha256": roundtrip_configuration_sha,
        "strict_observation_sha256": strict.observation_sha256,
        "strict_runtime_commit_receipt_sha256": strict.runtime_commit_receipt_sha256,
        "strict_commit_id": strict.commit_id,
        "strict_local_commit_record_id": strict.local_commit_record_id,
        "strict_local_response_id": strict.local_response_id,
        "strict_attestation_consumption_id": strict.attestation_consumption_id,
        "strict_committed_at": strict.committed_at.isoformat(),
        "target_lsn": recovery.target_replay_lsn,
        "object_version_set_sha256": recovery.object_version_set_sha256,
        "recovery_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    chain_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return VerifiedPhysicalFullMatrixV2WitnessedAckChain(
        schema=PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA,
        chain_sha256=chain_sha,
        campaign_id=campaign,
        release_sha=release,
        source_site=source,
        destination_site=destination,
        route_commitment_sha256=route,
        four_role_binding_sha256=four_role,
        writer_holder_site=context_term[0],
        writer_epoch=context_term[1],
        writer_lease_id=context_term[2],
        witnessed_term_proof_sha256=context_term[3],
        context_sha256=attestation.context_sha256,
        source_envelope_sha256=attestation.source_envelope_sha256,
        source_request_sha256=attestation.source_request_sha256,
        request_id=assertion.request_id,
        request_nonce=assertion.request_nonce,
        destination_receipt_sha256=attestation.destination_receipt_sha256,
        receipt_id=assertion.receipt_id,
        receipt_nonce=assertion.receipt_nonce,
        durable_ledger_entry_sha256=attestation.durable_ledger_entry_sha256,
        receiver_recovery_evidence_sha256=assertion.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=assertion.receiver_replay_lsn,
        target_recovery_evidence_sha256=attestation.target_recovery_evidence_sha256,
        readback_attestation_sha256=attestation.readback_attestation_sha256,
        stage_receipt_sha256=attestation.stage_receipt_sha256,
        witness_transition_id=attestation.witness_transition_id,
        activation_mode=attestation.activation_mode,
        activation_stream_generation_id=attestation.activation_stream_generation_id,
        activation_route_artifact_sha256=attestation.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=(
            attestation.activation_source_cutover_attestation_sha256
        ),
        activation_receiver_permit_sha256=attestation.activation_receiver_permit_sha256,
        witness_sequence=attestation.witness_sequence,
        witness_ledger_entry_sha256=attestation.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=attestation.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=attestation.witness_ledger_binding_sha256,
        roundtrip_attestation_sha256=attestation.attestation_sha256,
        ir_durable_assertion_sha256=attestation.ir_durable_assertion_sha256,
        context_certificate_sha256=attestation.context_certificate_sha256,
        roundtrip_configuration_sha256=roundtrip_configuration_sha,
        strict_observation_sha256=strict.observation_sha256,
        strict_runtime_commit_receipt_sha256=strict.runtime_commit_receipt_sha256,
        strict_commit_id=strict.commit_id,
        strict_local_commit_record_id=strict.local_commit_record_id,
        strict_local_response_id=strict.local_response_id,
        strict_attestation_consumption_id=strict.attestation_consumption_id,
        strict_committed_at=strict.committed_at,
        target_lsn=recovery.target_replay_lsn,
        object_version_set_sha256=recovery.object_version_set_sha256,
    )


def _assert_result(
    value: object,
    *,
    expected: VerifiedPhysicalFullMatrixV2WitnessedAckChain,
) -> VerifiedPhysicalFullMatrixV2WitnessedAckChain:
    if type(value) is not VerifiedPhysicalFullMatrixV2WitnessedAckChain:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CAPABILITY_REQUIRED")
    for name in (
        "schema",
        "chain_sha256",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "context_sha256",
        "source_envelope_sha256",
        "source_request_sha256",
        "request_id",
        "request_nonce",
        "destination_receipt_sha256",
        "receipt_id",
        "receipt_nonce",
        "durable_ledger_entry_sha256",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "witness_transition_id",
        "activation_mode",
        "activation_stream_generation_id",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "witness_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "roundtrip_attestation_sha256",
        "ir_durable_assertion_sha256",
        "context_certificate_sha256",
        "roundtrip_configuration_sha256",
        "strict_observation_sha256",
        "strict_runtime_commit_receipt_sha256",
        "strict_commit_id",
        "strict_local_commit_record_id",
        "strict_local_response_id",
        "strict_attestation_consumption_id",
        "strict_committed_at",
        "target_lsn",
        "object_version_set_sha256",
        "recovery_authorized",
        "promotion_authorized",
        "execution_authorized",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CAPABILITY_TAMPERED")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_SCHEMA
        or value.recovery_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CAPABILITY_TAMPERED")
    return value


def mint_verified_physical_full_matrix_v2_witnessed_ack_chain(
    *,
    config: PhysicalFullMatrixV2WitnessedAckChainConfig,
    inputs: PhysicalFullMatrixV2WitnessedAckChainInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2WitnessedAckChain:
    """Mint a local non-authorizing V2 join after all owner checks succeed."""

    expected = _derive(config=config, inputs=inputs, now=now)
    object.__setattr__(expected, "_capability", _CAPABILITY)
    _STATES[expected] = _State(config=config, inputs=inputs)
    return _assert_result(expected, expected=expected)


def require_verified_physical_full_matrix_v2_witnessed_ack_chain(
    value: object,
    *,
    config: PhysicalFullMatrixV2WitnessedAckChainConfig,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2WitnessedAckChain:
    """Revalidate the three owners and every cross-pin at one supplied clock."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2WitnessedAckChain
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or config != state.config:
        _fail("PHYSICAL_FULL_MATRIX_V2_WITNESSED_ACK_CHAIN_PROVENANCE_MISSING")
    return _assert_result(value, expected=_derive(config=config, inputs=state.inputs, now=now))


def project_verified_physical_full_matrix_v2_witnessed_ack_chain(
    value: object,
    *,
    config: PhysicalFullMatrixV2WitnessedAckChainConfig,
    now: datetime,
) -> PhysicalFullMatrixV2WitnessedAckChainProjection:
    """Project only exact, non-authorizing pins from a fresh V2 join."""

    verified = require_verified_physical_full_matrix_v2_witnessed_ack_chain(
        value,
        config=config,
        now=now,
    )
    return PhysicalFullMatrixV2WitnessedAckChainProjection(
        schema=verified.schema,
        chain_sha256=verified.chain_sha256,
        campaign_id=verified.campaign_id,
        release_sha=verified.release_sha,
        source_site=verified.source_site,
        destination_site=verified.destination_site,
        route_commitment_sha256=verified.route_commitment_sha256,
        four_role_binding_sha256=verified.four_role_binding_sha256,
        writer_holder_site=verified.writer_holder_site,
        writer_epoch=verified.writer_epoch,
        writer_lease_id=verified.writer_lease_id,
        witnessed_term_proof_sha256=verified.witnessed_term_proof_sha256,
        context_sha256=verified.context_sha256,
        source_envelope_sha256=verified.source_envelope_sha256,
        source_request_sha256=verified.source_request_sha256,
        destination_receipt_sha256=verified.destination_receipt_sha256,
        durable_ledger_entry_sha256=verified.durable_ledger_entry_sha256,
        receiver_recovery_evidence_sha256=verified.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=verified.receiver_replay_lsn,
        target_recovery_evidence_sha256=verified.target_recovery_evidence_sha256,
        readback_attestation_sha256=verified.readback_attestation_sha256,
        stage_receipt_sha256=verified.stage_receipt_sha256,
        witness_transition_id=verified.witness_transition_id,
        activation_mode=verified.activation_mode,
        activation_stream_generation_id=verified.activation_stream_generation_id,
        activation_route_artifact_sha256=verified.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=(
            verified.activation_source_cutover_attestation_sha256
        ),
        activation_receiver_permit_sha256=verified.activation_receiver_permit_sha256,
        witness_sequence=verified.witness_sequence,
        witness_ledger_entry_sha256=verified.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=(
            verified.witness_ledger_previous_head_sha256
        ),
        witness_ledger_binding_sha256=verified.witness_ledger_binding_sha256,
        roundtrip_attestation_sha256=verified.roundtrip_attestation_sha256,
        ir_durable_assertion_sha256=verified.ir_durable_assertion_sha256,
        context_certificate_sha256=verified.context_certificate_sha256,
        roundtrip_configuration_sha256=verified.roundtrip_configuration_sha256,
        strict_observation_sha256=verified.strict_observation_sha256,
        strict_runtime_commit_receipt_sha256=(
            verified.strict_runtime_commit_receipt_sha256
        ),
        strict_commit_id=verified.strict_commit_id,
        strict_local_commit_record_id=verified.strict_local_commit_record_id,
        strict_local_response_id=verified.strict_local_response_id,
        strict_attestation_consumption_id=(
            verified.strict_attestation_consumption_id
        ),
        strict_committed_at=verified.strict_committed_at,
        target_lsn=verified.target_lsn,
        object_version_set_sha256=verified.object_version_set_sha256,
        recovery_authorized=verified.recovery_authorized,
        promotion_authorized=verified.promotion_authorized,
        execution_authorized=verified.execution_authorized,
    )
