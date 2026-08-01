"""Fail-closed FI writer response after a portable V2 Witness round trip.

This boundary deliberately consumes *only* an already verified
``VerifiedPhysicalWalV2WitnessRoundtripAttestation``.  It does not accept a
raw WA-IR receipt, a receiver-ledger capability, a target-recovery capability,
or the older direct remote-ack strict-writer API.  Those values are local to
their owning process and are revalidated by the signed Witness chain before
they reach this module.

Before and after the local transaction, the boundary checks the attestation at
a root-owned clock and cross-pins a live Writer-Witness term and active role
matrix.  The legacy synchronous helper injects that transaction directly;
the default-off prepare/finalize companion lets a root-owned transaction own
the interval without exposing an authority that can be serialized or forged.
The runtime must atomically create the local response and consume the exact
attestation SHA-256 once.  Its pinned-key receipt is then checked against the
complete non-secret instruction.  The returned observation is opaque and
non-serializable; a separate readiness layer can only use its exact
non-authorizing projection.

There is intentionally no transport, filesystem, database, Object Storage,
process management, V1 compatibility, promotion, or writer-start operation in
this module.  Durable transaction implementation belongs to the injected
root-owned runtime.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
    active_object_delta_role_matrix_route,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixActivation,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    project_active_object_delta_role_matrix_role,
    require_live_object_delta_role_matrix_activation,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.physical_wal_v2_witness_roundtrip_contract import (
    PhysicalWalV2WitnessRoundtripConfig,
    PhysicalWalV2WitnessRoundtripError,
    VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    require_verified_physical_wal_v2_witness_roundtrip_attestation,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA",
    "PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction",
    "PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection",
    "PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse",
    "PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig",
    "PhysicalWalV2WitnessRoundtripStrictWriterResponseError",
    "PhysicalWalV2WitnessRoundtripStrictWriterResponseProjection",
    "PhysicalWalV2WitnessRoundtripStrictWriterRuntime",
    "VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation",
    "commit_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "prepare_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "project_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bridge_intent",
    "project_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation",
    "require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response",
    "require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-response-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-commit-receipt-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-and-witness-attestation-consumption-v1"
)

DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_MAXIMUM_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_BYTES = 64 * 1024

_COMMIT_DOMAIN = (
    b"gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-commit-receipt-v1\x00"
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_COMMIT_ID_RE = re.compile(r"^v2-witness-strict-writer-[0-9a-f]{64}$", re.ASCII)
_TERM_FIELDS = frozenset(
    {
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)
_COMMIT_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "configuration_sha256",
        "atomic_commit_boundary",
        "commit_id",
        "attestation_sha256",
        "ir_durable_assertion_sha256",
        "context_certificate_sha256",
        "context_sha256",
        "source_envelope_sha256",
        "source_request_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "witness_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "writer_term",
        "witness_transition_id",
        "activation_mode",
        "activation_stream_generation_id",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "local_commit_record_id",
        "local_response_id",
        "attestation_consumption_id",
        "committed_at",
        "signature_base64",
    }
)
_CAPABILITY = object()
_PREPARED_CAPABILITY = object()


class PhysicalWalV2WitnessRoundtripStrictWriterResponseError(ValueError):
    """A portable Witness-attestation writer response cannot safely proceed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig:
    """Default-off local policy for one V2 Witness-certified FI writer.

    The nested round-trip config supplies all remote/public-key pins.  The
    local commit signer pin belongs solely to the atomic transaction runtime;
    this pure boundary never receives its private key.
    """

    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig | None = None
    local_commit_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
    """Exact public pins for one atomic local response/attestation consume.

    ``commit_id`` is deterministic.  A retry therefore requests the same
    transaction and the runtime must return its preexisting durable receipt,
    not produce a second response or a second consumption record.
    """

    schema: str
    configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    issued_at: datetime


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection:
    """Fresh scalar V2 pins for the opaque V1/V2 bridge issuer only.

    The normal prepared instruction intentionally does not disclose the
    signed-attestation or Witness-term validity windows.  A pre-transaction
    bridge certificate must be bounded by both windows, however.  This
    projection is minted only after revalidating the exact opaque prepared
    capability against its retained verified chain; callers cannot supply
    a raw instruction, attestation, term, or activation to this function.
    """

    strict_schema: str
    configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    attestation_sha256: str
    context_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    attestation_issued_at: datetime
    attestation_expires_at: datetime
    term_issued_at: datetime
    term_expires_at: datetime


class PhysicalWalV2WitnessRoundtripStrictWriterRuntime(Protocol):
    """Injected root-owned atomic local transaction boundary.

    The implementation must atomically persist (1) the local writer response
    and (2) exactly one durable consumption keyed by
    ``instruction.attestation_sha256`` before it signs and returns its receipt.
    It must not reveal the application response before that transaction
    commits.  Repeated identical instructions must return the same receipt;
    conflicting reuse of an attestation SHA must fail closed.
    """

    def commit_after_verified_witness_roundtrip_attestation(
        self,
        *,
        instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    ) -> bytes: ...


@dataclass(frozen=True, eq=False, init=False)
class PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
    """Opaque provenance for one non-secret V2 writer transaction instruction.

    The instruction is deliberately public only as a non-secret diagnostic
    projection.  A root-owned durable transaction must obtain it through
    :func:`require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response`,
    which rechecks this in-process capability, its private
    :class:`WeakKeyDictionary` state, the configuration, and the Witness chain
    at the current trusted clock.  The field is *not* independently
    authorizing.
    """

    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
        capability: object,
    ) -> None:
        if capability is not _PREPARED_CAPABILITY:
            raise TypeError(
                "V2_WITNESS_STRICT_WRITER_PREPARED_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "V2_WITNESS_STRICT_WRITER_PREPARED_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation:
    """Opaque local result after pre/post attestation and liveness checks."""

    schema: str
    observation_sha256: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    commit_id: str
    runtime_commit_receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_OBSERVATION_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterResponseProjection:
    """Exact non-authorizing pins from a freshly revalidated observation."""

    schema: str
    observation_sha256: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    commit_id: str
    runtime_commit_receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ConfigFacts:
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig
    local_commit_signer_public_key: bytes
    maximum_evidence_age_seconds: int
    configuration_sha256: str
    source_site: str
    destination_site: str


@dataclass(frozen=True)
class _LiveActivationFacts:
    mode: str
    stream_generation_id: str
    route_artifact_sha256: str
    source_cutover_attestation_sha256: str
    receiver_permit_sha256: str
    witness_transition_id: str


@dataclass(frozen=True)
class _Admission:
    config: _ConfigFacts
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    activation: VerifiedObjectDeltaRoleMatrixActivation
    live: _LiveActivationFacts


@dataclass(frozen=True)
class _RuntimeReceiptFacts:
    canonical_receipt: bytes
    receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    attestation_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ObservationState:
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    activation: VerifiedObjectDeltaRoleMatrixActivation
    canonical_runtime_receipt: bytes


@dataclass(frozen=True)
class _PreparedState:
    """Private provenance retained only while a prepared capability is live."""

    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm | None
    activation: VerifiedObjectDeltaRoleMatrixActivation | None
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction


_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation,
    _ObservationState,
] = WeakKeyDictionary()
_PREPARED_STATES: WeakKeyDictionary[
    PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse,
    _PreparedState,
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(code)


def _trusted_now() -> datetime:
    """Read the authoritative local boundary clock; caller clocks are ignored."""

    return datetime.now(timezone.utc)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
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
        or (not permit_zero and value == "0" * 64)
    ):
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_STRICT_WRITER_COMMIT_RECEIPT_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_STRICT_WRITER_COMMIT_RECEIPT_JSON_CONSTANT_FORBIDDEN")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _term_mapping(value: object, *, code: str) -> dict[str, Any]:
    term = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    if (
        term["writer_holder_site"] not in {"webapp_fi", "webapp_ir"}
        or type(term["writer_epoch"]) is not int
        or term["writer_epoch"] < 1
        or type(term["writer_lease_id"]) is not str
        or not term["writer_lease_id"]
    ):
        _fail(code)
    _sha256(term["witnessed_term_proof_sha256"], code=code)
    return term


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _configuration_sha256(
    *,
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig,
    local_commit_signer_public_key: bytes,
    maximum_evidence_age_seconds: int,
) -> str:
    """Commit public policy pins into each local runtime receipt."""

    try:
        remote = roundtrip_config.remote_ack_config
        payload = {
            "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
            "remote_ack_context_sha256": remote.expected_context_sha256,
            "remote_ack_source_site": remote.expected_source_site,
            "remote_ack_destination_site": remote.expected_destination_site,
            "remote_ack_source_public_key_base64": base64.b64encode(
                remote.expected_source_public_key
            ).decode("ascii"),
            "remote_ack_destination_public_key_base64": base64.b64encode(
                remote.expected_destination_public_key
            ).decode("ascii"),
            "ir_recovery_exporter_public_key_base64": base64.b64encode(
                roundtrip_config.ir_recovery_exporter_public_key
            ).decode("ascii"),
            "fi_outbox_public_key_base64": base64.b64encode(
                roundtrip_config.fi_outbox_public_key
            ).decode("ascii"),
            "ir_durable_assertion_public_key_base64": base64.b64encode(
                roundtrip_config.ir_durable_assertion_public_key
            ).decode("ascii"),
            "witness_public_key_base64": base64.b64encode(
                roundtrip_config.witness_public_key
            ).decode("ascii"),
            "roundtrip_maximum_evidence_age_seconds": roundtrip_config.maximum_evidence_age_seconds,
            "local_commit_signer_public_key_base64": base64.b64encode(
                local_commit_signer_public_key
            ).decode("ascii"),
            "maximum_evidence_age_seconds": maximum_evidence_age_seconds,
        }
        return hashlib.sha256(
            _canonical(payload, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID")
        ).hexdigest()
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(
            "V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"
        ) from exc


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig:
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_DISABLED")
    if type(value.roundtrip_config) is not PhysicalWalV2WitnessRoundtripConfig:
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_INVALID")
    roundtrip_config = value.roundtrip_config
    remote = roundtrip_config.remote_ack_config
    if (
        roundtrip_config.enabled is not True
        or remote is None
        or getattr(remote, "enabled", None) is not True
    ):
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_INVALID")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1
        <= value.maximum_evidence_age_seconds
        <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_MAXIMUM_EVIDENCE_AGE_SECONDS
        or value.maximum_evidence_age_seconds
        > roundtrip_config.maximum_evidence_age_seconds
    ):
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_INVALID")
    local_signer = _public_key(
        value.local_commit_signer_public_key,
        code="V2_WITNESS_STRICT_WRITER_LOCAL_COMMIT_SIGNER_INVALID",
    )
    try:
        source_site = remote.expected_source_site
        destination_site = remote.expected_destination_site
        role_keys = (
            _public_key(remote.expected_source_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
            _public_key(remote.expected_destination_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
            _public_key(roundtrip_config.ir_recovery_exporter_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
            _public_key(roundtrip_config.fi_outbox_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
            _public_key(roundtrip_config.ir_durable_assertion_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
            _public_key(roundtrip_config.witness_public_key, code="V2_WITNESS_STRICT_WRITER_CONFIG_INVALID"),
        )
    except AttributeError:
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_INVALID")
    if (
        type(source_site) is not str
        or not source_site
        or type(destination_site) is not str
        or not destination_site
        or source_site == destination_site
        or local_signer in role_keys
        or len(set(role_keys)) != len(role_keys)
    ):
        _fail("V2_WITNESS_STRICT_WRITER_CONFIG_ROLE_KEY_REUSE")
    return _ConfigFacts(
        roundtrip_config=roundtrip_config,
        local_commit_signer_public_key=local_signer,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        configuration_sha256=_configuration_sha256(
            roundtrip_config=roundtrip_config,
            local_commit_signer_public_key=local_signer,
            maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        ),
        source_site=source_site,
        destination_site=destination_site,
    )


def _live_activation_facts(
    *,
    normalized: _ConfigFacts,
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    witnessed_term: object,
    activation: object,
    now: datetime,
) -> tuple[VerifiedObjectDeltaRoleMatrixWitnessedTerm, VerifiedObjectDeltaRoleMatrixActivation, _LiveActivationFacts]:
    """Cross-pin current local term/role state to the signed Witness result."""

    try:
        term = require_live_object_delta_role_matrix_witnessed_term(
            witnessed_term,
            now=now,
        )
        live_activation = require_live_object_delta_role_matrix_activation(
            activation,
            now=now,
        )
        writer_role = project_active_object_delta_role_matrix_role(
            live_activation,
            site=normalized.source_site,
            now=now,
        )
        standby_role = project_active_object_delta_role_matrix_role(
            live_activation,
            site=normalized.destination_site,
            now=now,
        )
        active_route = active_object_delta_role_matrix_route(live_activation._matrix)
        active_term = live_activation._witnessed_term
        record = live_activation._history[-1]
    except (AttributeError, IndexError, ObjectDeltaRoleMatrixRolloverError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(
            "V2_WITNESS_STRICT_WRITER_LIVE_ACTIVATION_INVALID"
        ) from exc
    if (
        writer_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or standby_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
        or (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.proof_sha256,
            term.witness_transition_id,
        )
        != (
            attestation.writer_holder_site,
            attestation.writer_epoch,
            attestation.writer_lease_id,
            attestation.witnessed_term_proof_sha256,
            attestation.witness_transition_id,
        )
        or (
            active_term.holder_site,
            active_term.writer_epoch,
            active_term.writer_lease_id,
            active_term.proof_sha256,
            active_term.witness_transition_id,
        )
        != (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.proof_sha256,
            term.witness_transition_id,
        )
        or (
            record.holder_site,
            record.writer_epoch,
            record.writer_lease_id,
            record.witness_transition_id,
        )
        != (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.witness_transition_id,
        )
    ):
        _fail("V2_WITNESS_STRICT_WRITER_LIVE_TERM_CROSS_PIN_MISMATCH")
    binding = active_route.source_pin.binding
    permit = active_route.receiver_binding.permit
    if (
        (
            binding.source_site,
            binding.destination_site,
            binding.stream_generation_id,
        )
        != (
            normalized.source_site,
            normalized.destination_site,
            attestation.activation_stream_generation_id,
        )
        or (
            permit.source_site,
            permit.destination_site,
            permit.stream_generation_id,
            permit.writer_epoch,
            permit.writer_lease_id,
        )
        != (
            normalized.source_site,
            normalized.destination_site,
            attestation.activation_stream_generation_id,
            term.writer_epoch,
            term.writer_lease_id,
        )
        or record.stream_generation_id != attestation.activation_stream_generation_id
        or live_activation._matrix.active_mode != attestation.activation_mode
        or record.route_artifact_sha256 != attestation.activation_route_artifact_sha256
        or (
            record.source_cutover_attestation_sha256
            != attestation.activation_source_cutover_attestation_sha256
        )
        or record.receiver_permit_sha256 != attestation.activation_receiver_permit_sha256
    ):
        _fail("V2_WITNESS_STRICT_WRITER_LIVE_ACTIVATION_CROSS_PIN_MISMATCH")
    return (
        term,
        live_activation,
        _LiveActivationFacts(
            mode=live_activation._matrix.active_mode,
            stream_generation_id=record.stream_generation_id,
            route_artifact_sha256=record.route_artifact_sha256,
            source_cutover_attestation_sha256=record.source_cutover_attestation_sha256,
            receiver_permit_sha256=record.receiver_permit_sha256,
            witness_transition_id=record.witness_transition_id,
        ),
    )


def _admit(
    *,
    normalized: _ConfigFacts,
    attestation: object,
    witnessed_term: object,
    activation: object,
    now: datetime,
) -> _Admission:
    """Revalidate the portable chain and all live local pins at one clock."""

    try:
        verified = require_verified_physical_wal_v2_witness_roundtrip_attestation(
            attestation,
            config=normalized.roundtrip_config,
            now=now,
        )
    except PhysicalWalV2WitnessRoundtripError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(
            "V2_WITNESS_STRICT_WRITER_ATTESTATION_INVALID"
        ) from exc
    term, live_activation, live = _live_activation_facts(
        normalized=normalized,
        attestation=verified,
        witnessed_term=witnessed_term,
        activation=activation,
        now=now,
    )
    if (
        verified.writer_holder_site != normalized.source_site
        or live.witness_transition_id != verified.witness_transition_id
        or now - verified.issued_at
        > timedelta(seconds=normalized.maximum_evidence_age_seconds)
    ):
        _fail("V2_WITNESS_STRICT_WRITER_ATTESTATION_CROSS_PIN_MISMATCH")
    return _Admission(
        config=normalized,
        attestation=verified,
        witnessed_term=term,
        activation=live_activation,
        live=live,
    )


def _commit_id(value: _Admission) -> str:
    attestation = value.attestation
    payload = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
        "configuration_sha256": value.config.configuration_sha256,
        "attestation_sha256": attestation.attestation_sha256,
        "ir_durable_assertion_sha256": attestation.ir_durable_assertion_sha256,
        "context_certificate_sha256": attestation.context_certificate_sha256,
        "witness_sequence": attestation.witness_sequence,
        "witness_ledger_entry_sha256": attestation.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": attestation.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": attestation.witness_ledger_binding_sha256,
        "writer_term": {
            "writer_holder_site": value.witnessed_term.holder_site,
            "writer_epoch": value.witnessed_term.writer_epoch,
            "writer_lease_id": value.witnessed_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.witnessed_term.proof_sha256,
        },
        "witness_transition_id": value.witnessed_term.witness_transition_id,
        "activation_route_artifact_sha256": value.live.route_artifact_sha256,
    }
    return "v2-witness-strict-writer-" + hashlib.sha256(
        _canonical(payload, code="V2_WITNESS_STRICT_WRITER_COMMIT_ID_INVALID")
    ).hexdigest()


def _instruction(
    value: _Admission,
    *,
    issued_at: datetime,
) -> PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
    attestation = value.attestation
    term = value.witnessed_term
    live = value.live
    return PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
        configuration_sha256=value.config.configuration_sha256,
        atomic_commit_boundary=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY,
        commit_id=_commit_id(value),
        attestation_sha256=attestation.attestation_sha256,
        ir_durable_assertion_sha256=attestation.ir_durable_assertion_sha256,
        context_certificate_sha256=attestation.context_certificate_sha256,
        context_sha256=attestation.context_sha256,
        source_envelope_sha256=attestation.source_envelope_sha256,
        source_request_sha256=attestation.source_request_sha256,
        destination_receipt_sha256=attestation.destination_receipt_sha256,
        durable_ledger_entry_sha256=attestation.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=attestation.target_recovery_evidence_sha256,
        readback_attestation_sha256=attestation.readback_attestation_sha256,
        stage_receipt_sha256=attestation.stage_receipt_sha256,
        witness_sequence=attestation.witness_sequence,
        witness_ledger_entry_sha256=attestation.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=attestation.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=attestation.witness_ledger_binding_sha256,
        writer_holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        witness_transition_id=term.witness_transition_id,
        activation_mode=live.mode,
        activation_stream_generation_id=live.stream_generation_id,
        activation_route_artifact_sha256=live.route_artifact_sha256,
        activation_source_cutover_attestation_sha256=live.source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=live.receiver_permit_sha256,
        issued_at=issued_at,
    )


def _prepared_state(
    value: object,
) -> _PreparedState:
    """Require the exact in-process prepare result and its private state."""

    if (
        type(value)
        is not PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
        or value._capability is not _PREPARED_CAPABILITY
    ):
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_CAPABILITY_REQUIRED")
    state = _PREPARED_STATES.get(value)
    if state is None:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_CAPABILITY_REQUIRED")
    # The publicly visible instruction is deliberately usable by the
    # root-owned transaction, but it must remain the exact object minted by
    # prepare.  Replacing it (even with an equal-looking value) is a boundary
    # violation rather than a retry.
    if value.instruction is not state.instruction:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_TAMPERED")
    return state


def prepare_physical_wal_v2_witness_roundtrip_strict_writer_response(
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm | None = None,
    activation: VerifiedObjectDeltaRoleMatrixActivation | None = None,
    now: datetime | None = None,
) -> PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
    """Mint one local-only V2 transaction instruction after current admission.

    This performs no durable write and does not authorize a response by
    itself.  The owner of a fresh local transaction may use the public,
    non-secret ``prepared.instruction`` as the exact persistence bind, then
    call :func:`finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response`
    only after that transaction has committed and signed its canonical
    receipt.

    ``now`` is intentionally non-authoritative.  Production reads the trusted
    local boundary clock; tests patch :func:`_trusted_now` explicitly.
    """

    normalized = _config(config)
    del now
    observed = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    admission = _admit(
        normalized=normalized,
        attestation=attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        now=observed,
    )
    instruction = _instruction(admission, issued_at=observed)
    result = PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse(
        instruction=instruction,
        capability=_PREPARED_CAPABILITY,
    )
    _PREPARED_STATES[result] = _PreparedState(
        config=config,
        attestation=attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        instruction=instruction,
    )
    return result


def require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction:
    """Return the exact prepared instruction after fresh local revalidation.

    This is the only intended handoff from the opaque prepare capability to a
    root-owned durable transaction.  It deliberately uses the verified inputs
    retained by :func:`prepare_physical_wal_v2_witness_roundtrip_strict_writer_response`;
    a caller cannot substitute a different Witness attestation, term, or
    activation while retaining the same prepared identity.  Finalization
    performs a second check with the then-current inputs after the transaction
    returns its receipt.
    """

    state = _prepared_state(value)
    normalized = _config(config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_CONFIG_MISMATCH")
    del now
    observed = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    admitted = _admit(
        normalized=normalized,
        attestation=state.attestation,
        witnessed_term=state.witnessed_term,
        activation=state.activation,
        now=observed,
    )
    revalidated_instruction = _instruction(admitted, issued_at=observed)
    if any(
        getattr(state.instruction, name) != getattr(revalidated_instruction, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    ):
        _fail("V2_WITNESS_STRICT_WRITER_INPUT_CHANGED_DURING_PREPARED_USE")
    return state.instruction


def project_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bridge_intent(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection:
    """Project bridge validity pins from one freshly revalidated V2 prepare.

    This is deliberately narrower than exposing the retained verified V2
    objects to another module.  It performs the same current-chain and live
    activation revalidation as ``require_prepared...`` and then releases only
    scalar fields that the V1/V2 certificate signer must commit to.  The
    caller-provided ``now`` remains non-authoritative, matching the existing
    prepare/require boundary.
    """

    state = _prepared_state(value)
    normalized = _config(config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_CONFIG_MISMATCH")
    del now
    observed = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    admitted = _admit(
        normalized=normalized,
        attestation=state.attestation,
        witnessed_term=state.witnessed_term,
        activation=state.activation,
        now=observed,
    )
    revalidated_instruction = _instruction(admitted, issued_at=observed)
    if any(
        getattr(state.instruction, name) != getattr(revalidated_instruction, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    ):
        _fail("V2_WITNESS_STRICT_WRITER_INPUT_CHANGED_DURING_PREPARED_USE")
    try:
        attestation_issued_at = _utc(
            admitted.attestation.issued_at,
            code="V2_WITNESS_STRICT_WRITER_PREPARED_BRIDGE_INTENT_INVALID",
        )
        attestation_expires_at = _utc(
            admitted.attestation.expires_at,
            code="V2_WITNESS_STRICT_WRITER_PREPARED_BRIDGE_INTENT_INVALID",
        )
        term_issued_at = _utc(
            admitted.witnessed_term.issued_at,
            code="V2_WITNESS_STRICT_WRITER_PREPARED_BRIDGE_INTENT_INVALID",
        )
        term_expires_at = _utc(
            admitted.witnessed_term.expires_at,
            code="V2_WITNESS_STRICT_WRITER_PREPARED_BRIDGE_INTENT_INVALID",
        )
    except AttributeError:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_BRIDGE_INTENT_INVALID")
    instruction = state.instruction
    return PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection(
        strict_schema=instruction.schema,
        configuration_sha256=instruction.configuration_sha256,
        atomic_commit_boundary=instruction.atomic_commit_boundary,
        commit_id=instruction.commit_id,
        attestation_sha256=instruction.attestation_sha256,
        context_sha256=instruction.context_sha256,
        writer_holder_site=instruction.writer_holder_site,
        writer_epoch=instruction.writer_epoch,
        writer_lease_id=instruction.writer_lease_id,
        witnessed_term_proof_sha256=instruction.witnessed_term_proof_sha256,
        witness_transition_id=instruction.witness_transition_id,
        activation_mode=instruction.activation_mode,
        activation_stream_generation_id=instruction.activation_stream_generation_id,
        activation_route_artifact_sha256=instruction.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=(
            instruction.activation_source_cutover_attestation_sha256
        ),
        activation_receiver_permit_sha256=instruction.activation_receiver_permit_sha256,
        attestation_issued_at=attestation_issued_at,
        attestation_expires_at=attestation_expires_at,
        term_issued_at=term_issued_at,
        term_expires_at=term_expires_at,
    )


def _attestation_consumption_id(
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
) -> str:
    """Canonical one-time key the runtime must make unique transactionally."""

    return "v2-witness-consume-" + instruction.attestation_sha256


def _runtime_unsigned(
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    *,
    local_commit_record_id: str,
    local_response_id: str,
    attestation_consumption_id: str,
    committed_at: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_SCHEMA,
        "version": 1,
        "kind": "durable-local-writer-response-and-witness-attestation-consumption",
        "configuration_sha256": instruction.configuration_sha256,
        "atomic_commit_boundary": instruction.atomic_commit_boundary,
        "commit_id": instruction.commit_id,
        "attestation_sha256": instruction.attestation_sha256,
        "ir_durable_assertion_sha256": instruction.ir_durable_assertion_sha256,
        "context_certificate_sha256": instruction.context_certificate_sha256,
        "context_sha256": instruction.context_sha256,
        "source_envelope_sha256": instruction.source_envelope_sha256,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "durable_ledger_entry_sha256": instruction.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": instruction.target_recovery_evidence_sha256,
        "readback_attestation_sha256": instruction.readback_attestation_sha256,
        "stage_receipt_sha256": instruction.stage_receipt_sha256,
        "witness_sequence": instruction.witness_sequence,
        "witness_ledger_entry_sha256": instruction.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": instruction.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": instruction.witness_ledger_binding_sha256,
        "writer_term": {
            "writer_holder_site": instruction.writer_holder_site,
            "writer_epoch": instruction.writer_epoch,
            "writer_lease_id": instruction.writer_lease_id,
            "witnessed_term_proof_sha256": instruction.witnessed_term_proof_sha256,
        },
        "witness_transition_id": instruction.witness_transition_id,
        "activation_mode": instruction.activation_mode,
        "activation_stream_generation_id": instruction.activation_stream_generation_id,
        "activation_route_artifact_sha256": instruction.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": instruction.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": instruction.activation_receiver_permit_sha256,
        "local_commit_record_id": local_commit_record_id,
        "local_response_id": local_response_id,
        "attestation_consumption_id": attestation_consumption_id,
        "committed_at": _render_timestamp(committed_at),
    }


def _runtime_receipt(
    value: object,
    *,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    config: _ConfigFacts,
    now: datetime,
) -> _RuntimeReceiptFacts:
    if (
        type(value) is not bytes
        or not 1
        <= len(value)
        <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_COMMIT_RECEIPT_BYTES
    ):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2WitnessRoundtripStrictWriterResponseError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID")
    receipt = _exact_mapping(
        parsed,
        fields=_COMMIT_RECEIPT_FIELDS,
        code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_FIELDS_INVALID",
    )
    if _canonical(receipt, code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID") != value:
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_NONCANONICAL")
    local_commit_record_id = _identifier(
        receipt["local_commit_record_id"],
        code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    local_response_id = _identifier(
        receipt["local_response_id"],
        code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    consumption_id = _identifier(
        receipt["attestation_consumption_id"],
        code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    if len({local_commit_record_id, local_response_id, consumption_id}) != 3:
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_IDENTITY_REUSED")
    if consumption_id != _attestation_consumption_id(instruction):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_CONSUMPTION_MISMATCH")
    committed_at = _timestamp(
        receipt["committed_at"],
        code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    expected = _runtime_unsigned(
        instruction,
        local_commit_record_id=local_commit_record_id,
        local_response_id=local_response_id,
        attestation_consumption_id=consumption_id,
        committed_at=committed_at,
    )
    actual_unsigned = {key: item for key, item in receipt.items() if key != "signature_base64"}
    if actual_unsigned != expected:
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_BINDING_MISMATCH")
    if type(receipt["signature_base64"]) is not str:
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    try:
        signature = base64.b64decode(
            receipt["signature_base64"].encode("ascii", "strict"),
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(config.local_commit_signer_public_key).verify(
            signature,
            _COMMIT_DOMAIN
            + _canonical(
                expected,
                code="V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_INVALID",
            ),
        )
    except (InvalidSignature, ValueError):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if committed_at > now + timedelta(
        seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_FUTURE_SKEW_SECONDS
    ):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_FUTURE")
    if now - committed_at > timedelta(seconds=config.maximum_evidence_age_seconds):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_RECEIPT_STALE")
    return _RuntimeReceiptFacts(
        canonical_receipt=value,
        receipt_sha256=hashlib.sha256(value).hexdigest(),
        local_commit_record_id=local_commit_record_id,
        local_response_id=local_response_id,
        attestation_consumption_id=consumption_id,
        committed_at=committed_at,
    )


def _observation_payload(
    *,
    admission: _Admission,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    receipt: _RuntimeReceiptFacts,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
        "attestation_sha256": instruction.attestation_sha256,
        "ir_durable_assertion_sha256": instruction.ir_durable_assertion_sha256,
        "context_certificate_sha256": instruction.context_certificate_sha256,
        "context_sha256": instruction.context_sha256,
        "source_envelope_sha256": instruction.source_envelope_sha256,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "durable_ledger_entry_sha256": instruction.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": instruction.target_recovery_evidence_sha256,
        "readback_attestation_sha256": instruction.readback_attestation_sha256,
        "stage_receipt_sha256": instruction.stage_receipt_sha256,
        "witness_sequence": instruction.witness_sequence,
        "witness_ledger_entry_sha256": instruction.witness_ledger_entry_sha256,
        "witness_ledger_previous_head_sha256": instruction.witness_ledger_previous_head_sha256,
        "witness_ledger_binding_sha256": instruction.witness_ledger_binding_sha256,
        "writer_holder_site": instruction.writer_holder_site,
        "writer_epoch": instruction.writer_epoch,
        "writer_lease_id": instruction.writer_lease_id,
        "witnessed_term_proof_sha256": instruction.witnessed_term_proof_sha256,
        "witness_transition_id": instruction.witness_transition_id,
        "activation_mode": instruction.activation_mode,
        "activation_stream_generation_id": instruction.activation_stream_generation_id,
        "activation_route_artifact_sha256": instruction.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": instruction.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": instruction.activation_receiver_permit_sha256,
        "commit_id": instruction.commit_id,
        "runtime_commit_receipt_sha256": receipt.receipt_sha256,
        "local_commit_record_id": receipt.local_commit_record_id,
        "local_response_id": receipt.local_response_id,
        "attestation_consumption_id": receipt.attestation_consumption_id,
        "committed_at": _render_timestamp(receipt.committed_at),
    }


def _observation_from(
    *,
    admission: _Admission,
    instruction: PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction,
    receipt: _RuntimeReceiptFacts,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation:
    payload = _observation_payload(
        admission=admission,
        instruction=instruction,
        receipt=receipt,
    )
    digest = hashlib.sha256(
        _canonical(payload, code="V2_WITNESS_STRICT_WRITER_OBSERVATION_INVALID")
    ).hexdigest()
    result = VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
        observation_sha256=digest,
        attestation_sha256=payload["attestation_sha256"],
        ir_durable_assertion_sha256=payload["ir_durable_assertion_sha256"],
        context_certificate_sha256=payload["context_certificate_sha256"],
        context_sha256=payload["context_sha256"],
        source_envelope_sha256=payload["source_envelope_sha256"],
        source_request_sha256=payload["source_request_sha256"],
        destination_receipt_sha256=payload["destination_receipt_sha256"],
        durable_ledger_entry_sha256=payload["durable_ledger_entry_sha256"],
        target_recovery_evidence_sha256=payload["target_recovery_evidence_sha256"],
        readback_attestation_sha256=payload["readback_attestation_sha256"],
        stage_receipt_sha256=payload["stage_receipt_sha256"],
        witness_sequence=payload["witness_sequence"],
        witness_ledger_entry_sha256=payload["witness_ledger_entry_sha256"],
        witness_ledger_previous_head_sha256=payload[
            "witness_ledger_previous_head_sha256"
        ],
        witness_ledger_binding_sha256=payload["witness_ledger_binding_sha256"],
        writer_holder_site=payload["writer_holder_site"],
        writer_epoch=payload["writer_epoch"],
        writer_lease_id=payload["writer_lease_id"],
        witnessed_term_proof_sha256=payload["witnessed_term_proof_sha256"],
        witness_transition_id=payload["witness_transition_id"],
        activation_mode=payload["activation_mode"],
        activation_stream_generation_id=payload["activation_stream_generation_id"],
        activation_route_artifact_sha256=payload[
            "activation_route_artifact_sha256"
        ],
        activation_source_cutover_attestation_sha256=payload[
            "activation_source_cutover_attestation_sha256"
        ],
        activation_receiver_permit_sha256=payload["activation_receiver_permit_sha256"],
        commit_id=payload["commit_id"],
        runtime_commit_receipt_sha256=payload["runtime_commit_receipt_sha256"],
        local_commit_record_id=payload["local_commit_record_id"],
        local_response_id=payload["local_response_id"],
        attestation_consumption_id=payload["attestation_consumption_id"],
        committed_at=receipt.committed_at,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _validate_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    now: datetime,
) -> tuple[
    VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation,
    _Admission,
    _RuntimeReceiptFacts,
]:
    if (
        type(value)
        is not VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation
        or value._capability is not _CAPABILITY
    ):
        _fail("V2_WITNESS_STRICT_WRITER_OBSERVATION_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("V2_WITNESS_STRICT_WRITER_OBSERVATION_CAPABILITY_REQUIRED")
    normalized = _config(config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_OBSERVATION_CONFIG_MISMATCH")
    admission = _admit(
        normalized=normalized,
        attestation=state.attestation,
        witnessed_term=state.witnessed_term,
        activation=state.activation,
        now=now,
    )
    instruction = _instruction(admission, issued_at=now)
    receipt = _runtime_receipt(
        state.canonical_runtime_receipt,
        instruction=instruction,
        config=normalized,
        now=now,
    )
    expected = _observation_from(
        admission=admission,
        instruction=instruction,
        receipt=receipt,
    )
    for name in (
        "schema",
        "observation_sha256",
        "attestation_sha256",
        "ir_durable_assertion_sha256",
        "context_certificate_sha256",
        "context_sha256",
        "source_envelope_sha256",
        "source_request_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "witness_sequence",
        "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256",
        "witness_ledger_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "witness_transition_id",
        "activation_mode",
        "activation_stream_generation_id",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "commit_id",
        "runtime_commit_receipt_sha256",
        "local_commit_record_id",
        "local_response_id",
        "attestation_consumption_id",
        "committed_at",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("V2_WITNESS_STRICT_WRITER_OBSERVATION_TAMPERED")
    return value, admission, receipt


def finalize_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
    prepared: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    runtime_receipt: bytes,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm | None = None,
    activation: VerifiedObjectDeltaRoleMatrixActivation | None = None,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation:
    """Accept a committed receipt only after fresh V2/Witness revalidation.

    The caller is responsible for its own atomic local transaction between
    :func:`prepare_physical_wal_v2_witness_roundtrip_strict_writer_response`
    and this function.  This pure boundary neither opens nor commits that
    transaction.  It merely proves that the committed canonical receipt still
    belongs to the exact prepared instruction while its Witness term and role
    activation remain live.

    ``now`` is deliberately ignored so callers cannot extend any evidence
    lifetime.  Tests patch :func:`_trusted_now` explicitly.
    """

    state = _prepared_state(prepared)
    normalized = _config(config)
    saved = _config(state.config)
    if normalized.configuration_sha256 != saved.configuration_sha256:
        _fail("V2_WITNESS_STRICT_WRITER_PREPARED_CONFIG_MISMATCH")
    del now
    observed = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    post = _admit(
        normalized=normalized,
        attestation=state.attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        now=observed,
    )
    post_instruction = _instruction(post, issued_at=observed)
    if any(
        getattr(state.instruction, name) != getattr(post_instruction, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    ):
        _fail("V2_WITNESS_STRICT_WRITER_INPUT_CHANGED_DURING_FINALIZE")
    receipt = _runtime_receipt(
        runtime_receipt,
        instruction=post_instruction,
        config=normalized,
        now=observed,
    )
    result = _observation_from(
        admission=post,
        instruction=post_instruction,
        receipt=receipt,
    )
    _STATES[result] = _ObservationState(
        config=config,
        attestation=state.attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        canonical_runtime_receipt=receipt.canonical_receipt,
    )
    _validate_observation(result, config=config, now=observed)
    return result


def commit_physical_wal_v2_witness_roundtrip_strict_writer_response(
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    attestation: VerifiedPhysicalWalV2WitnessRoundtripAttestation,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm | None = None,
    activation: VerifiedObjectDeltaRoleMatrixActivation | None = None,
    runtime: PhysicalWalV2WitnessRoundtripStrictWriterRuntime | None = None,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation:
    """Atomically consume one verified Witness attestation at the FI writer.

    ``now`` is intentionally non-authoritative.  Callers cannot extend an
    attestation's validity by supplying a clock; production reads the local
    root-owned clock and tests patch :func:`_trusted_now` explicitly.
    """

    normalized = _config(config)
    del now
    before = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    admission = _admit(
        normalized=normalized,
        attestation=attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        now=before,
    )
    instruction = _instruction(admission, issued_at=before)
    callback = getattr(runtime, "commit_after_verified_witness_roundtrip_attestation", None)
    if not callable(callback):
        _fail("V2_WITNESS_STRICT_WRITER_RUNTIME_REQUIRED")
    try:
        raw_receipt = callback(instruction=instruction)
    except PhysicalWalV2WitnessRoundtripStrictWriterResponseError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterResponseError(
            "V2_WITNESS_STRICT_WRITER_RUNTIME_COMMIT_FAILED"
        ) from exc
    after = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    post = _admit(
        normalized=normalized,
        attestation=attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        now=after,
    )
    post_instruction = _instruction(post, issued_at=after)
    if any(
        getattr(instruction, name) != getattr(post_instruction, name)
        for name in PhysicalWalV2WitnessRoundtripStrictWriterCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    ):
        _fail("V2_WITNESS_STRICT_WRITER_INPUT_CHANGED_DURING_COMMIT")
    receipt = _runtime_receipt(
        raw_receipt,
        instruction=post_instruction,
        config=normalized,
        now=after,
    )
    result = _observation_from(
        admission=post,
        instruction=post_instruction,
        receipt=receipt,
    )
    _STATES[result] = _ObservationState(
        config=config,
        attestation=attestation,
        witnessed_term=witnessed_term,
        activation=activation,
        canonical_runtime_receipt=receipt.canonical_receipt,
    )
    _validate_observation(result, config=config, now=after)
    return result


def require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2WitnessRoundtripStrictWriterResponseObservation:
    """Revalidate an opaque observation at the current local trusted clock."""

    del now
    observed = _utc(_trusted_now(), code="V2_WITNESS_STRICT_WRITER_CLOCK_INVALID")
    result, _admission, _receipt = _validate_observation(
        value,
        config=config,
        now=observed,
    )
    return result


def project_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
    value: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2WitnessRoundtripStrictWriterResponseProjection:
    """Return exact non-authorizing pins after fresh local revalidation."""

    verified = (
        require_verified_physical_wal_v2_witness_roundtrip_strict_writer_response_observation(
            value,
            config=config,
            now=now,
        )
    )
    return PhysicalWalV2WitnessRoundtripStrictWriterResponseProjection(
        schema=verified.schema,
        observation_sha256=verified.observation_sha256,
        attestation_sha256=verified.attestation_sha256,
        ir_durable_assertion_sha256=verified.ir_durable_assertion_sha256,
        context_certificate_sha256=verified.context_certificate_sha256,
        context_sha256=verified.context_sha256,
        source_envelope_sha256=verified.source_envelope_sha256,
        source_request_sha256=verified.source_request_sha256,
        destination_receipt_sha256=verified.destination_receipt_sha256,
        durable_ledger_entry_sha256=verified.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=verified.target_recovery_evidence_sha256,
        readback_attestation_sha256=verified.readback_attestation_sha256,
        stage_receipt_sha256=verified.stage_receipt_sha256,
        witness_sequence=verified.witness_sequence,
        witness_ledger_entry_sha256=verified.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=(
            verified.witness_ledger_previous_head_sha256
        ),
        witness_ledger_binding_sha256=verified.witness_ledger_binding_sha256,
        writer_holder_site=verified.writer_holder_site,
        writer_epoch=verified.writer_epoch,
        writer_lease_id=verified.writer_lease_id,
        witnessed_term_proof_sha256=verified.witnessed_term_proof_sha256,
        witness_transition_id=verified.witness_transition_id,
        activation_mode=verified.activation_mode,
        activation_stream_generation_id=verified.activation_stream_generation_id,
        activation_route_artifact_sha256=(
            verified.activation_route_artifact_sha256
        ),
        activation_source_cutover_attestation_sha256=(
            verified.activation_source_cutover_attestation_sha256
        ),
        activation_receiver_permit_sha256=verified.activation_receiver_permit_sha256,
        commit_id=verified.commit_id,
        runtime_commit_receipt_sha256=verified.runtime_commit_receipt_sha256,
        local_commit_record_id=verified.local_commit_record_id,
        local_response_id=verified.local_response_id,
        attestation_consumption_id=verified.attestation_consumption_id,
        committed_at=verified.committed_at,
    )
