"""Fail-closed V2 strict-writer-response boundary.

This is deliberately a new protocol generation.  A V2 wire acknowledgement
or receiver-local replay projection is not enough to release a writer
response.  Before it calls the injected *local* writer transaction boundary,
this module revalidates at a root-owned clock:

* the signed V2 request/receipt pair and request-bound recovery projection;
* the full V2 target-recovery/readback bridge;
* the receiver's fsync'd V2 ledger receipt; and
* a live Writer-Witness term and live role-matrix activation for the exact
  current writer/standby direction.

The injected runtime must atomically persist both the local response record
and the unique consumption of the exact remote receipt before it returns its
pinned-key signed receipt.  The runtime is not allowed to release the
application response before that durable transaction commits.  This module
then repeats all liveness checks before minting an opaque observation.

There is intentionally no V1 import, compatibility conversion, networking,
filesystem, Object Storage, database, promotion, or writer-start operation
here.  In particular this contract does not make a process-local receiver
ledger capability transferable between hosts; a separately reviewed,
Witness-mediated durable-evidence delivery boundary is required for that live
deployment step.
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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
from core.physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceError,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckError,
    VerifiedPhysicalWalV2RemoteAckEvidence,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckRequest,
    require_verified_physical_wal_v2_remote_ack_evidence,
    require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    verify_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    PhysicalWalV2RemoteAckReceiverLedgerError,
    VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY",
    "PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA",
    "PhysicalWalV2StrictRemoteAckWriterCommitInstruction",
    "PhysicalWalV2StrictRemoteAckWriterResponseConfig",
    "PhysicalWalV2StrictRemoteAckWriterResponseError",
    "PhysicalWalV2StrictRemoteAckWriterResponseProjection",
    "PhysicalWalV2StrictRemoteAckWriterRuntime",
    "VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation",
    "commit_physical_wal_v2_strict_remote_ack_writer_response",
    "project_verified_physical_wal_v2_strict_remote_ack_writer_response_observation",
    "require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation",
)


PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA = (
    "gold-trade-physical-wal-v2-strict-remote-ack-writer-response-v2"
)
PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-v2-strict-remote-ack-writer-commit-receipt-v2"
)
PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED = False
PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY = (
    "root-owned-atomic-local-response-and-v2-receipt-consumption-v1"
)

DEFAULT_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_BYTES = 64 * 1024

_COMMIT_DOMAIN = b"gold-trade-physical-wal-v2-strict-remote-ack-writer-commit-receipt-v2\x00"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_COMMIT_ID_RE = re.compile(r"^v2-strict-writer-[0-9a-f]{64}$", re.ASCII)
_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
)
_COMMIT_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "configuration_sha256",
        "atomic_commit_boundary",
        "commit_id",
        "context_sha256",
        "source_request_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "request_id",
        "request_nonce",
        "receipt_id",
        "receipt_nonce",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
        "witness_transition_id",
        "writer_term",
        "activation_mode",
        "activation_stream_generation_id",
        "activation_route_artifact_sha256",
        "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "local_commit_record_id",
        "local_response_id",
        "receipt_consumption_id",
        "committed_at",
        "signature_base64",
    }
)
_CAPABILITY = object()


class PhysicalWalV2StrictRemoteAckWriterResponseError(ValueError):
    """A V2 strict writer-response admission cannot safely proceed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2StrictRemoteAckWriterResponseConfig:
    """Default-off V2-only policy for one exact writer direction.

    ``local_commit_signer_public_key`` is the pin for the root-owned local
    transaction adapter.  It is intentionally a public key only; loading and
    protecting its corresponding private key is outside this pure boundary.
    """

    remote_ack_config: PhysicalWalV2RemoteAckConfig | None = None
    receiver_ledger_config: PhysicalWalV2RemoteAckReceiverLedgerConfig | None = None
    local_commit_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalWalV2StrictRemoteAckWriterCommitInstruction:
    """Exact non-secret inputs to the injected atomic writer transaction.

    ``commit_id`` is deterministic for the exact remote receipt and live
    activation binding.  A crash/retry therefore asks the runtime for the
    same durable transaction rather than inventing a second consumption.
    """

    schema: str
    configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    context_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_transition_id: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    issued_at: datetime


class PhysicalWalV2StrictRemoteAckWriterRuntime(Protocol):
    """Injected root-owned local transaction boundary.

    The implementation **must** atomically persist a local response record
    and a unique consumption record for ``destination_receipt_sha256`` before
    it signs and returns the canonical receipt.  It must withhold the
    application response until that transaction commits.  Repeated identical
    instructions must return the same durable receipt; any conflicting reuse
    must fail instead of creating another local response.
    """

    def commit_after_verified_v2_remote_ack(
        self,
        *,
        instruction: PhysicalWalV2StrictRemoteAckWriterCommitInstruction,
    ) -> bytes: ...


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation:
    """Opaque V2-only result after all inputs are revalidated post-commit."""

    schema: str
    context_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    local_commit_record_id: str
    local_response_id: str
    committed_at: datetime
    # The initial reservation shell exposed the preceding minimal fields.
    # Defaults retain its harmless direct-construction behavior; capability
    # and process-local state still prevent it from becoming authority.
    observation_sha256: str = ""
    source_site: str = ""
    destination_site: str = ""
    route_commitment_sha256: str = ""
    four_role_binding_sha256: str = ""
    stream_generation_id: str = ""
    object_version_set_sha256: str = ""
    target_replay_lsn: str = ""
    target_recovery_evidence_sha256: str = ""
    readback_attestation_sha256: str = ""
    stage_receipt_sha256: str = ""
    writer_holder_site: str = ""
    writer_epoch: int = 0
    writer_lease_id: str = ""
    witnessed_term_proof_sha256: str = ""
    witness_transition_id: str = ""
    activation_mode: str = ""
    activation_stream_generation_id: str = ""
    activation_route_artifact_sha256: str = ""
    activation_source_cutover_attestation_sha256: str = ""
    activation_receiver_permit_sha256: str = ""
    commit_id: str = ""
    runtime_commit_receipt_sha256: str = ""
    receipt_consumption_id: str = ""
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2StrictRemoteAckWriterResponseProjection:
    """Non-authorizing exact pins for the separate V2 readiness generation."""

    schema: str
    observation_sha256: str
    context_sha256: str
    source_site: str
    destination_site: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    stream_generation_id: str
    object_version_set_sha256: str
    target_replay_lsn: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
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
    receipt_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ConfigFacts:
    remote_ack_config: PhysicalWalV2RemoteAckConfig
    receiver_ledger_config: PhysicalWalV2RemoteAckReceiverLedgerConfig
    local_commit_signer_public_key: bytes
    maximum_evidence_age_seconds: int
    configuration_sha256: str


@dataclass(frozen=True)
class _ActivationFacts:
    mode: str
    stream_generation_id: str
    route_artifact_sha256: str
    source_cutover_attestation_sha256: str
    receiver_permit_sha256: str


@dataclass(frozen=True)
class _Admission:
    config: _ConfigFacts
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence
    request: VerifiedPhysicalWalV2RemoteAckRequest
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence
    receiver_ledger_receipt: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    activation: VerifiedObjectDeltaRoleMatrixActivation
    activation_facts: _ActivationFacts


@dataclass(frozen=True)
class _RuntimeReceiptFacts:
    canonical_receipt: bytes
    receipt_sha256: str
    local_commit_record_id: str
    local_response_id: str
    receipt_consumption_id: str
    committed_at: datetime


@dataclass(frozen=True)
class _ObservationState:
    config: PhysicalWalV2StrictRemoteAckWriterResponseConfig
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence
    receiver_ledger_receipt: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    activation: VerifiedObjectDeltaRoleMatrixActivation
    canonical_runtime_receipt: bytes


_STATES: WeakKeyDictionary[
    VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation, _ObservationState
] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalV2StrictRemoteAckWriterResponseError(code)


def _trusted_now() -> datetime:
    """Read the local boundary clock; public ``now`` arguments are ignored."""

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


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
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
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_JSON_CONSTANT_FORBIDDEN")


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


def _configuration_sha256(
    *,
    remote_ack_config: PhysicalWalV2RemoteAckConfig,
    receiver_ledger_config: PhysicalWalV2RemoteAckReceiverLedgerConfig,
    local_commit_signer_public_key: bytes,
    maximum_evidence_age_seconds: int,
) -> str:
    """Commit all V2 verifier pins, never a secret, into runtime receipts."""

    try:
        payload = {
            "schema": PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
            "remote_ack_context_sha256": remote_ack_config.expected_context_sha256,
            "remote_ack_source_site": remote_ack_config.expected_source_site,
            "remote_ack_destination_site": remote_ack_config.expected_destination_site,
            "remote_ack_source_public_key_base64": base64.b64encode(
                remote_ack_config.expected_source_public_key
            ).decode("ascii"),
            "remote_ack_destination_public_key_base64": base64.b64encode(
                remote_ack_config.expected_destination_public_key
            ).decode("ascii"),
            "remote_ack_maximum_evidence_age_seconds": remote_ack_config.maximum_evidence_age_seconds,
            "receiver_ledger_state_root": str(receiver_ledger_config.state_root),
            "receiver_ledger_maximum_entries": receiver_ledger_config.maximum_entries,
            "local_commit_signer_public_key_base64": base64.b64encode(
                local_commit_signer_public_key
            ).decode("ascii"),
            "maximum_evidence_age_seconds": maximum_evidence_age_seconds,
        }
        return hashlib.sha256(_canonical(payload, code="V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")).hexdigest()
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID"
        ) from exc


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2StrictRemoteAckWriterResponseConfig:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_REQUIRED")
    if value.enabled is not True:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_DISABLED")
    if type(value.remote_ack_config) is not PhysicalWalV2RemoteAckConfig:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")
    if type(value.receiver_ledger_config) is not PhysicalWalV2RemoteAckReceiverLedgerConfig:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")
    if value.remote_ack_config.enabled is not True or value.receiver_ledger_config.enabled is not True:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")
    if value.receiver_ledger_config.remote_ack_config != value.remote_ack_config:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_MISMATCH")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1
        <= value.maximum_evidence_age_seconds
        <= MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_MAXIMUM_EVIDENCE_AGE_SECONDS
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")
    signer = value.local_commit_signer_public_key
    # Keep an incomplete signer pin distinguishable from a bad receiver
    # capability.  This preserves a useful fail-closed diagnostic before a
    # caller has supplied the live receiver proof, while a runtime is never
    # invoked without the exact valid pin below.
    if type(signer) is not bytes:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_CONFIG_INVALID")
    return _ConfigFacts(
        remote_ack_config=value.remote_ack_config,
        receiver_ledger_config=value.receiver_ledger_config,
        local_commit_signer_public_key=signer,
        maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        configuration_sha256=_configuration_sha256(
            remote_ack_config=value.remote_ack_config,
            receiver_ledger_config=value.receiver_ledger_config,
            local_commit_signer_public_key=signer,
            maximum_evidence_age_seconds=value.maximum_evidence_age_seconds,
        ),
    )


def _commit_signer_public_key(value: _ConfigFacts) -> bytes:
    key = value.local_commit_signer_public_key
    if len(key) != 32 or key == b"\x00" * 32:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_LOCAL_COMMIT_SIGNER_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(key)
    except ValueError:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_LOCAL_COMMIT_SIGNER_INVALID")
    return key


def _request_context(request: VerifiedPhysicalWalV2RemoteAckRequest) -> dict[str, Any]:
    try:
        outer = json.loads(
            request.canonical_request.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2StrictRemoteAckWriterResponseError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_REQUEST_CONTEXT_INVALID")
    if type(outer) is not dict or type(outer.get("context")) is not dict:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_REQUEST_CONTEXT_INVALID")
    # The V2 request verifier has already required canonical exact context
    # fields and signature validity.  Preserve the raw signed mapping only
    # for explicit cross-pins below.
    return dict(outer["context"])


def _activation_facts(
    *,
    activation: VerifiedObjectDeltaRoleMatrixActivation,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    source_site: str,
    destination_site: str,
    target: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    now: datetime,
) -> _ActivationFacts:
    """Revalidate the Witness activation and bind it to physical V2 pins."""

    try:
        live_activation = require_live_object_delta_role_matrix_activation(activation, now=now)
        writer_role = project_active_object_delta_role_matrix_role(
            live_activation,
            site=source_site,
            now=now,
        )
        standby_role = project_active_object_delta_role_matrix_role(
            live_activation,
            site=destination_site,
            now=now,
        )
        # Activation has already fully verified its matrix, route generations,
        # and term.  This narrow active-route projection is used only to
        # compare its non-secret current route pins to the physical route.
        active_route = active_object_delta_role_matrix_route(live_activation._matrix)
        active_term = live_activation._witnessed_term
        record = live_activation._history[-1]
    except (AttributeError, IndexError, ObjectDeltaRoleMatrixRolloverError, ValueError) as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_ACTIVATION_INVALID"
        ) from exc
    try:
        require_live_object_delta_role_matrix_witnessed_term(active_term, now=now)
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_ACTIVATION_INVALID"
        ) from exc
    if (
        writer_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or standby_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
        or (active_term.holder_site, active_term.writer_epoch, active_term.writer_lease_id, active_term.proof_sha256)
        != (
            witnessed_term.holder_site,
            witnessed_term.writer_epoch,
            witnessed_term.writer_lease_id,
            witnessed_term.proof_sha256,
        )
        or (
            record.holder_site,
            record.writer_epoch,
            record.writer_lease_id,
            record.witness_transition_id,
        )
        != (
            witnessed_term.holder_site,
            witnessed_term.writer_epoch,
            witnessed_term.writer_lease_id,
            witnessed_term.witness_transition_id,
        )
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_ACTIVATION_TERM_CROSS_PIN_MISMATCH")
    binding = active_route.source_pin.binding
    permit = active_route.receiver_binding.permit
    physical = target.transfer_binding
    if (
        (
            binding.source_site,
            binding.destination_site,
            binding.campaign_id,
            binding.release_sha,
            binding.stream_generation_id,
        )
        != (
            physical.source_site,
            physical.destination_site,
            physical.campaign_id,
            physical.release_sha,
            target.stream_generation_id,
        )
        or (
            permit.source_site,
            permit.destination_site,
            permit.campaign_id,
            permit.release_sha,
            permit.stream_generation_id,
            permit.writer_epoch,
            permit.writer_lease_id,
        )
        != (
            physical.source_site,
            physical.destination_site,
            physical.campaign_id,
            physical.release_sha,
            target.stream_generation_id,
            witnessed_term.writer_epoch,
            witnessed_term.writer_lease_id,
        )
        or record.stream_generation_id != target.stream_generation_id
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_ACTIVATION_ROUTE_CROSS_PIN_MISMATCH")
    return _ActivationFacts(
        mode=live_activation._matrix.active_mode,
        stream_generation_id=record.stream_generation_id,
        route_artifact_sha256=record.route_artifact_sha256,
        source_cutover_attestation_sha256=record.source_cutover_attestation_sha256,
        receiver_permit_sha256=record.receiver_permit_sha256,
    )


def _admit(
    *,
    normalized: _ConfigFacts,
    remote_ack_evidence: object,
    receiver_ledger_receipt: object,
    receiver_recovery_evidence: object,
    target_recovery_evidence: object,
    witnessed_term: object,
    activation: object,
    now: datetime,
) -> _Admission:
    """Revalidate every independent V2 input at one trusted boundary clock."""

    if (
        type(receiver_ledger_receipt)
        is not VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
        or receiver_ledger_receipt._capability is None
    ):
        _fail("V2_STRICT_REMOTE_ACK_DURABLE_LEDGER_REQUIRED")
    try:
        pair = require_verified_physical_wal_v2_remote_ack_evidence(
            remote_ack_evidence,
            config=normalized.remote_ack_config,
            now=now,
        )
        request = verify_physical_wal_v2_remote_ack_request(
            source_request=pair.canonical_request,
            config=normalized.remote_ack_config,
            now=now,
        )
        recovery = require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
            receiver_recovery_evidence,
            source_request=request,
            config=normalized.remote_ack_config,
            now=now,
        )
        target = require_verified_physical_full_matrix_v2_recovery_evidence(
            target_recovery_evidence,
            now=now,
        )
        ledger = require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
            receiver_ledger_receipt,
            config=normalized.receiver_ledger_config,
            source_request=request,
            receiver_recovery_evidence=recovery,
            target_recovery_evidence=target,
            remote_ack_evidence=pair,
            now=now,
        )
        term = require_live_object_delta_role_matrix_witnessed_term(witnessed_term, now=now)
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_REMOTE_ACK_INVALID"
        ) from exc
    except PhysicalWalV2RemoteAckReceiverLedgerError as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_DURABLE_LEDGER_REQUIRED"
        ) from exc
    except PhysicalFullMatrixV2RecoveryEvidenceError as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_RECOVERY_EVIDENCE_INVALID"
        ) from exc
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_WITNESS_TERM_INVALID"
        ) from exc

    context = _request_context(request)
    term_mapping = _term_mapping(
        context.get("writer_term"),
        code="V2_STRICT_REMOTE_ACK_WRITER_REQUEST_CONTEXT_INVALID",
    )
    physical = target.transfer_binding
    if (
        pair.context_sha256 != request.context_sha256
        or pair.request_id != request.request_id
        or pair.request_nonce != request.request_nonce
        or pair.receiver_recovery_evidence_sha256
        != recovery.evidence.receiver_recovery_evidence_sha256
        or pair.receiver_replay_lsn != recovery.evidence.replay_lsn
        or ledger.canonical_source_request != pair.canonical_request
        or ledger.canonical_destination_receipt != pair.canonical_receipt
        or ledger.context_sha256 != request.context_sha256
        or ledger.source_request_sha256 != hashlib.sha256(pair.canonical_request).hexdigest()
        or ledger.destination_receipt_sha256 != hashlib.sha256(pair.canonical_receipt).hexdigest()
        or ledger.request_id != pair.request_id
        or ledger.request_nonce != pair.request_nonce
        or ledger.receipt_id != pair.receipt_id
        or ledger.receipt_nonce != pair.receipt_nonce
        or ledger.receiver_recovery_evidence_sha256
        != recovery.evidence.receiver_recovery_evidence_sha256
        or ledger.receiver_replay_lsn != recovery.evidence.replay_lsn
        or ledger.target_recovery_evidence_sha256 != target.evidence_sha256
        or ledger.readback_attestation_sha256 != target.readback_attestation_sha256
        or ledger.stage_receipt_sha256 != target.stage_receipt_sha256
        or ledger.witness_transition_id != target.witness_transition_id
        or ledger.target_recovery_observed_at != target.observed_at
        or pair.acknowledged_at > ledger.committed_at
        or ledger.committed_at > now
        or request.source_site != physical.source_site
        or request.destination_site != physical.destination_site
        or request.target_lsn != target.target_replay_lsn
        or request.object_version_set_sha256 != target.object_version_set_sha256
        or context.get("route_commitment_sha256") != target.route_commitment_sha256
        or context.get("four_role_binding_sha256") != target.four_role_binding_sha256
        or context.get("stream_generation_id") != target.stream_generation_id
        or context.get("object_version_set_sha256") != target.object_version_set_sha256
        or context.get("target_lsn") != target.target_replay_lsn
        or (
            term_mapping["writer_holder_site"],
            term_mapping["writer_epoch"],
            term_mapping["writer_lease_id"],
            term_mapping["witnessed_term_proof_sha256"],
        )
        != (
            physical.writer_term.writer_holder_site,
            physical.writer_term.writer_epoch,
            physical.writer_term.writer_lease_id,
            physical.writer_term.witnessed_term_proof_sha256,
        )
        or (
            term.holder_site,
            term.writer_epoch,
            term.writer_lease_id,
            term.proof_sha256,
            term.witness_transition_id,
        )
        != (
            physical.writer_term.writer_holder_site,
            physical.writer_term.writer_epoch,
            physical.writer_term.writer_lease_id,
            physical.writer_term.witnessed_term_proof_sha256,
            target.witness_transition_id,
        )
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_V2_CROSS_PIN_MISMATCH")
    if now - ledger.committed_at > timedelta(seconds=normalized.maximum_evidence_age_seconds):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_DURABLE_LEDGER_STALE")
    activation_facts = _activation_facts(
        activation=activation,
        witnessed_term=term,
        source_site=request.source_site,
        destination_site=request.destination_site,
        target=target,
        now=now,
    )
    return _Admission(
        config=normalized,
        remote_ack_evidence=pair,
        request=request,
        receiver_recovery_evidence=recovery,
        target_recovery_evidence=target,
        receiver_ledger_receipt=ledger,
        witnessed_term=term,
        activation=activation,
        activation_facts=activation_facts,
    )


def _commit_id(value: _Admission) -> str:
    payload = {
        "schema": PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        "configuration_sha256": value.config.configuration_sha256,
        "context_sha256": value.request.context_sha256,
        "source_request_sha256": hashlib.sha256(value.remote_ack_evidence.canonical_request).hexdigest(),
        "destination_receipt_sha256": hashlib.sha256(value.remote_ack_evidence.canonical_receipt).hexdigest(),
        "durable_ledger_entry_sha256": value.receiver_ledger_receipt.durable_ledger_entry_sha256,
        "writer_term": {
            "writer_holder_site": value.witnessed_term.holder_site,
            "writer_epoch": value.witnessed_term.writer_epoch,
            "writer_lease_id": value.witnessed_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.witnessed_term.proof_sha256,
        },
        "witness_transition_id": value.witnessed_term.witness_transition_id,
        "activation_route_artifact_sha256": value.activation_facts.route_artifact_sha256,
    }
    return "v2-strict-writer-" + hashlib.sha256(
        _canonical(payload, code="V2_STRICT_REMOTE_ACK_WRITER_COMMIT_ID_INVALID")
    ).hexdigest()


def _instruction(value: _Admission, *, issued_at: datetime) -> PhysicalWalV2StrictRemoteAckWriterCommitInstruction:
    pair = value.remote_ack_evidence
    ledger = value.receiver_ledger_receipt
    target = value.target_recovery_evidence
    term = value.witnessed_term
    activation = value.activation_facts
    return PhysicalWalV2StrictRemoteAckWriterCommitInstruction(
        schema=PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        configuration_sha256=value.config.configuration_sha256,
        atomic_commit_boundary=PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_ATOMIC_COMMIT_BOUNDARY,
        commit_id=_commit_id(value),
        context_sha256=pair.context_sha256,
        source_request_sha256=hashlib.sha256(pair.canonical_request).hexdigest(),
        destination_receipt_sha256=hashlib.sha256(pair.canonical_receipt).hexdigest(),
        durable_ledger_entry_sha256=ledger.durable_ledger_entry_sha256,
        request_id=pair.request_id,
        request_nonce=pair.request_nonce,
        receipt_id=pair.receipt_id,
        receipt_nonce=pair.receipt_nonce,
        target_recovery_evidence_sha256=target.evidence_sha256,
        readback_attestation_sha256=target.readback_attestation_sha256,
        stage_receipt_sha256=target.stage_receipt_sha256,
        witness_transition_id=term.witness_transition_id,
        writer_holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        activation_mode=activation.mode,
        activation_stream_generation_id=activation.stream_generation_id,
        activation_route_artifact_sha256=activation.route_artifact_sha256,
        activation_source_cutover_attestation_sha256=activation.source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=activation.receiver_permit_sha256,
        issued_at=issued_at,
    )


def _runtime_unsigned(
    instruction: PhysicalWalV2StrictRemoteAckWriterCommitInstruction,
    *,
    local_commit_record_id: str,
    local_response_id: str,
    receipt_consumption_id: str,
    committed_at: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_SCHEMA,
        "version": 2,
        "kind": "durable-local-writer-response-and-v2-receipt-consumption",
        "configuration_sha256": instruction.configuration_sha256,
        "atomic_commit_boundary": instruction.atomic_commit_boundary,
        "commit_id": instruction.commit_id,
        "context_sha256": instruction.context_sha256,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "durable_ledger_entry_sha256": instruction.durable_ledger_entry_sha256,
        "request_id": instruction.request_id,
        "request_nonce": instruction.request_nonce,
        "receipt_id": instruction.receipt_id,
        "receipt_nonce": instruction.receipt_nonce,
        "target_recovery_evidence_sha256": instruction.target_recovery_evidence_sha256,
        "readback_attestation_sha256": instruction.readback_attestation_sha256,
        "stage_receipt_sha256": instruction.stage_receipt_sha256,
        "witness_transition_id": instruction.witness_transition_id,
        "writer_term": {
            "writer_holder_site": instruction.writer_holder_site,
            "writer_epoch": instruction.writer_epoch,
            "writer_lease_id": instruction.writer_lease_id,
            "witnessed_term_proof_sha256": instruction.witnessed_term_proof_sha256,
        },
        "activation_mode": instruction.activation_mode,
        "activation_stream_generation_id": instruction.activation_stream_generation_id,
        "activation_route_artifact_sha256": instruction.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": instruction.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": instruction.activation_receiver_permit_sha256,
        "local_commit_record_id": local_commit_record_id,
        "local_response_id": local_response_id,
        "receipt_consumption_id": receipt_consumption_id,
        "committed_at": _render_timestamp(committed_at),
    }


def _runtime_receipt(
    value: object,
    *,
    instruction: PhysicalWalV2StrictRemoteAckWriterCommitInstruction,
    config: _ConfigFacts,
    now: datetime,
) -> _RuntimeReceiptFacts:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_COMMIT_RECEIPT_BYTES:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2StrictRemoteAckWriterResponseError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID")
    receipt = _exact_mapping(
        parsed,
        fields=_COMMIT_RECEIPT_FIELDS,
        code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_FIELDS_INVALID",
    )
    if _canonical(receipt, code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID") != value:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_NONCANONICAL")
    local_commit_record_id = _identifier(
        receipt["local_commit_record_id"],
        code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    local_response_id = _identifier(
        receipt["local_response_id"],
        code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    receipt_consumption_id = _identifier(
        receipt["receipt_consumption_id"],
        code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    if len({local_commit_record_id, local_response_id, receipt_consumption_id}) != 3:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_IDENTITY_REUSED")
    committed_at = _timestamp(
        receipt["committed_at"],
        code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID",
    )
    expected = _runtime_unsigned(
        instruction,
        local_commit_record_id=local_commit_record_id,
        local_response_id=local_response_id,
        receipt_consumption_id=receipt_consumption_id,
        committed_at=committed_at,
    )
    if receipt["signature_base64"] is None or type(receipt["signature_base64"]) is not str:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    actual_unsigned = {key: item for key, item in receipt.items() if key != "signature_base64"}
    if actual_unsigned != expected:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_BINDING_MISMATCH")
    try:
        signature = base64.b64decode(receipt["signature_base64"].encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if len(signature) != 64:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    try:
        Ed25519PublicKey.from_public_bytes(_commit_signer_public_key(config)).verify(
            signature,
            _COMMIT_DOMAIN + _canonical(expected, code="V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_INVALID"),
        )
    except (InvalidSignature, ValueError):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_SIGNATURE_INVALID")
    if committed_at > now + timedelta(seconds=MAX_PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_FUTURE_SKEW_SECONDS):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_FUTURE")
    if now - committed_at > timedelta(seconds=config.maximum_evidence_age_seconds):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_RECEIPT_STALE")
    return _RuntimeReceiptFacts(
        canonical_receipt=value,
        receipt_sha256=hashlib.sha256(value).hexdigest(),
        local_commit_record_id=local_commit_record_id,
        local_response_id=local_response_id,
        receipt_consumption_id=receipt_consumption_id,
        committed_at=committed_at,
    )


def _observation_payload(
    *,
    admission: _Admission,
    instruction: PhysicalWalV2StrictRemoteAckWriterCommitInstruction,
    receipt: _RuntimeReceiptFacts,
) -> dict[str, object]:
    target = admission.target_recovery_evidence
    pair = admission.remote_ack_evidence
    term = admission.witnessed_term
    activation = admission.activation_facts
    return {
        "schema": PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        "context_sha256": pair.context_sha256,
        "source_site": admission.request.source_site,
        "destination_site": admission.request.destination_site,
        "route_commitment_sha256": target.route_commitment_sha256,
        "four_role_binding_sha256": target.four_role_binding_sha256,
        "stream_generation_id": target.stream_generation_id,
        "object_version_set_sha256": target.object_version_set_sha256,
        "target_replay_lsn": target.target_replay_lsn,
        "source_request_sha256": instruction.source_request_sha256,
        "destination_receipt_sha256": instruction.destination_receipt_sha256,
        "durable_ledger_entry_sha256": instruction.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": instruction.target_recovery_evidence_sha256,
        "readback_attestation_sha256": instruction.readback_attestation_sha256,
        "stage_receipt_sha256": instruction.stage_receipt_sha256,
        "writer_holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witnessed_term_proof_sha256": term.proof_sha256,
        "witness_transition_id": term.witness_transition_id,
        "activation_mode": activation.mode,
        "activation_stream_generation_id": activation.stream_generation_id,
        "activation_route_artifact_sha256": activation.route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": activation.source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": activation.receiver_permit_sha256,
        "commit_id": instruction.commit_id,
        "runtime_commit_receipt_sha256": receipt.receipt_sha256,
        "local_commit_record_id": receipt.local_commit_record_id,
        "local_response_id": receipt.local_response_id,
        "receipt_consumption_id": receipt.receipt_consumption_id,
        "committed_at": _render_timestamp(receipt.committed_at),
    }


def _observation_from(
    *,
    admission: _Admission,
    instruction: PhysicalWalV2StrictRemoteAckWriterCommitInstruction,
    receipt: _RuntimeReceiptFacts,
) -> VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation:
    payload = _observation_payload(admission=admission, instruction=instruction, receipt=receipt)
    digest = hashlib.sha256(
        _canonical(payload, code="V2_STRICT_REMOTE_ACK_WRITER_OBSERVATION_INVALID")
    ).hexdigest()
    result = VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation(
        schema=PHYSICAL_WAL_V2_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        observation_sha256=digest,
        context_sha256=payload["context_sha256"],
        source_site=payload["source_site"],
        destination_site=payload["destination_site"],
        route_commitment_sha256=payload["route_commitment_sha256"],
        four_role_binding_sha256=payload["four_role_binding_sha256"],
        stream_generation_id=payload["stream_generation_id"],
        object_version_set_sha256=payload["object_version_set_sha256"],
        target_replay_lsn=payload["target_replay_lsn"],
        source_request_sha256=payload["source_request_sha256"],
        destination_receipt_sha256=payload["destination_receipt_sha256"],
        durable_ledger_entry_sha256=payload["durable_ledger_entry_sha256"],
        target_recovery_evidence_sha256=payload["target_recovery_evidence_sha256"],
        readback_attestation_sha256=payload["readback_attestation_sha256"],
        stage_receipt_sha256=payload["stage_receipt_sha256"],
        writer_holder_site=payload["writer_holder_site"],
        writer_epoch=payload["writer_epoch"],
        writer_lease_id=payload["writer_lease_id"],
        witnessed_term_proof_sha256=payload["witnessed_term_proof_sha256"],
        witness_transition_id=payload["witness_transition_id"],
        activation_mode=payload["activation_mode"],
        activation_stream_generation_id=payload["activation_stream_generation_id"],
        activation_route_artifact_sha256=payload["activation_route_artifact_sha256"],
        activation_source_cutover_attestation_sha256=payload[
            "activation_source_cutover_attestation_sha256"
        ],
        activation_receiver_permit_sha256=payload["activation_receiver_permit_sha256"],
        commit_id=payload["commit_id"],
        runtime_commit_receipt_sha256=payload["runtime_commit_receipt_sha256"],
        local_commit_record_id=payload["local_commit_record_id"],
        local_response_id=payload["local_response_id"],
        receipt_consumption_id=payload["receipt_consumption_id"],
        committed_at=receipt.committed_at,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _validate_observation(
    value: object,
    *,
    config: PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    now: datetime,
) -> tuple[VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation, _Admission, _RuntimeReceiptFacts]:
    if (
        type(value) is not VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation
        or value._capability is not _CAPABILITY
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_OBSERVATION_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_OBSERVATION_CAPABILITY_REQUIRED")
    normalized = _config(config)
    state_normalized = _config(state.config)
    if normalized.configuration_sha256 != state_normalized.configuration_sha256:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_OBSERVATION_CONFIG_MISMATCH")
    admission = _admit(
        normalized=normalized,
        remote_ack_evidence=state.remote_ack_evidence,
        receiver_ledger_receipt=state.receiver_ledger_receipt,
        receiver_recovery_evidence=state.receiver_recovery_evidence,
        target_recovery_evidence=state.target_recovery_evidence,
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
    expected = _observation_from(admission=admission, instruction=instruction, receipt=receipt)
    for name in (
        "schema",
        "observation_sha256",
        "context_sha256",
        "source_site",
        "destination_site",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "stream_generation_id",
        "object_version_set_sha256",
        "target_replay_lsn",
        "source_request_sha256",
        "destination_receipt_sha256",
        "durable_ledger_entry_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "stage_receipt_sha256",
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
        "receipt_consumption_id",
        "committed_at",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("V2_STRICT_REMOTE_ACK_WRITER_OBSERVATION_TAMPERED")
    return value, admission, receipt


def commit_physical_wal_v2_strict_remote_ack_writer_response(
    *,
    config: PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence,
    receiver_ledger_receipt: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence | None = None,
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence | None = None,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm | None = None,
    activation: VerifiedObjectDeltaRoleMatrixActivation | None = None,
    runtime: PhysicalWalV2StrictRemoteAckWriterRuntime | None = None,
    # Kept only so an old caller cannot turn a raw legacy observation into a
    # V2 authority.  It is never read as term evidence and is rejected after
    # the durable-ledger capability check.
    writer_term_observation: object | None = None,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation:
    """Commit only after full V2 + live-Witness validation, otherwise fence.

    ``now`` is intentionally non-authoritative and retained only to avoid an
    accidental caller-controlled clock migration.  Tests patch
    :func:`_trusted_now` explicitly.
    """

    normalized = _config(config)
    # Retain the most precise and safest diagnostic for a raw/non-durable
    # receipt.  No runtime or writer term is touched before this check.
    if (
        type(receiver_ledger_receipt)
        is not VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt
        or receiver_ledger_receipt._capability is None
    ):
        _fail("V2_STRICT_REMOTE_ACK_DURABLE_LEDGER_REQUIRED")
    del now
    if writer_term_observation is not None:
        _fail("V2_STRICT_REMOTE_ACK_WRITER_LEGACY_TERM_ARGUMENT_FORBIDDEN")
    before = _utc(_trusted_now(), code="V2_STRICT_REMOTE_ACK_WRITER_CLOCK_INVALID")
    admission = _admit(
        normalized=normalized,
        remote_ack_evidence=remote_ack_evidence,
        receiver_ledger_receipt=receiver_ledger_receipt,
        receiver_recovery_evidence=receiver_recovery_evidence,
        target_recovery_evidence=target_recovery_evidence,
        witnessed_term=witnessed_term,
        activation=activation,
        now=before,
    )
    _commit_signer_public_key(normalized)
    instruction = _instruction(admission, issued_at=before)
    callback = getattr(runtime, "commit_after_verified_v2_remote_ack", None)
    if not callable(callback):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_REQUIRED")
    try:
        raw_receipt = callback(instruction=instruction)
    except PhysicalWalV2StrictRemoteAckWriterResponseError:
        raise
    except Exception as exc:
        raise PhysicalWalV2StrictRemoteAckWriterResponseError(
            "V2_STRICT_REMOTE_ACK_WRITER_RUNTIME_COMMIT_FAILED"
        ) from exc
    after = _utc(_trusted_now(), code="V2_STRICT_REMOTE_ACK_WRITER_CLOCK_INVALID")
    # A valid pre-commit proof cannot credit an answer once a term, role,
    # ledger, replay evidence, or recovery bridge has expired/changed.
    post = _admit(
        normalized=normalized,
        remote_ack_evidence=remote_ack_evidence,
        receiver_ledger_receipt=receiver_ledger_receipt,
        receiver_recovery_evidence=receiver_recovery_evidence,
        target_recovery_evidence=target_recovery_evidence,
        witnessed_term=witnessed_term,
        activation=activation,
        now=after,
    )
    post_instruction = _instruction(post, issued_at=after)
    # ``issued_at`` is a local diagnostic timestamp, not a remote-receipt
    # identity.  A normal callback takes measurable time, so compare every
    # security-relevant instruction field while allowing the post-commit
    # trusted-clock revalidation to have a later issue time.
    if any(
        getattr(instruction, name) != getattr(post_instruction, name)
        for name in PhysicalWalV2StrictRemoteAckWriterCommitInstruction.__dataclass_fields__
        if name != "issued_at"
    ):
        _fail("V2_STRICT_REMOTE_ACK_WRITER_INPUT_CHANGED_DURING_COMMIT")
    receipt = _runtime_receipt(
        raw_receipt,
        instruction=post_instruction,
        config=normalized,
        now=after,
    )
    result = _observation_from(admission=post, instruction=post_instruction, receipt=receipt)
    _STATES[result] = _ObservationState(
        config=config,
        remote_ack_evidence=remote_ack_evidence,
        receiver_ledger_receipt=receiver_ledger_receipt,
        receiver_recovery_evidence=receiver_recovery_evidence,
        target_recovery_evidence=target_recovery_evidence,
        witnessed_term=witnessed_term,
        activation=activation,
        canonical_runtime_receipt=receipt.canonical_receipt,
    )
    _validate_observation(result, config=config, now=after)
    return result


def require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation(
    value: object,
    *,
    config: PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    now: datetime | None = None,
) -> VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation:
    """Revalidate the full V2 strict response at the local trusted clock."""

    del now
    observed = _utc(_trusted_now(), code="V2_STRICT_REMOTE_ACK_WRITER_CLOCK_INVALID")
    result, _admission, _receipt = _validate_observation(value, config=config, now=observed)
    return result


def project_verified_physical_wal_v2_strict_remote_ack_writer_response_observation(
    value: object,
    *,
    config: PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    now: datetime | None = None,
) -> PhysicalWalV2StrictRemoteAckWriterResponseProjection:
    """Return exact non-authorizing pins only after a fresh V2 revalidation."""

    verified = require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation(
        value,
        config=config,
        now=now,
    )
    return PhysicalWalV2StrictRemoteAckWriterResponseProjection(
        schema=verified.schema,
        observation_sha256=verified.observation_sha256,
        context_sha256=verified.context_sha256,
        source_site=verified.source_site,
        destination_site=verified.destination_site,
        route_commitment_sha256=verified.route_commitment_sha256,
        four_role_binding_sha256=verified.four_role_binding_sha256,
        stream_generation_id=verified.stream_generation_id,
        object_version_set_sha256=verified.object_version_set_sha256,
        target_replay_lsn=verified.target_replay_lsn,
        source_request_sha256=verified.source_request_sha256,
        destination_receipt_sha256=verified.destination_receipt_sha256,
        durable_ledger_entry_sha256=verified.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=verified.target_recovery_evidence_sha256,
        readback_attestation_sha256=verified.readback_attestation_sha256,
        stage_receipt_sha256=verified.stage_receipt_sha256,
        writer_holder_site=verified.writer_holder_site,
        writer_epoch=verified.writer_epoch,
        writer_lease_id=verified.writer_lease_id,
        witnessed_term_proof_sha256=verified.witnessed_term_proof_sha256,
        witness_transition_id=verified.witness_transition_id,
        activation_mode=verified.activation_mode,
        activation_stream_generation_id=verified.activation_stream_generation_id,
        activation_route_artifact_sha256=verified.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=(
            verified.activation_source_cutover_attestation_sha256
        ),
        activation_receiver_permit_sha256=verified.activation_receiver_permit_sha256,
        commit_id=verified.commit_id,
        runtime_commit_receipt_sha256=verified.runtime_commit_receipt_sha256,
        local_commit_record_id=verified.local_commit_record_id,
        local_response_id=verified.local_response_id,
        receipt_consumption_id=verified.receipt_consumption_id,
        committed_at=verified.committed_at,
    )
