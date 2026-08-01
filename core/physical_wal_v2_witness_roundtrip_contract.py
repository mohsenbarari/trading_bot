"""Portable V2 Witness round-trip wire contract.

The V2 recovery bridge, receiver-recovery projection, and durable receiver
ledger deliberately use opaque process-local capabilities.  They must never
be pickled, reconstructed, or sent from WA-IR to WA-FI.  This module defines
the separate portable evidence path instead:

``IR local recovery export -> Witness context certificate -> FI outbox envelope
 -> Witness -> IR durable assertion -> Witness durable ledger -> FI attestation``.

Only signed, canonical, non-secret projections cross a site boundary.  The
IR exporter locally revalidates its opaque recovery/ledger inputs before it
signs an assertion.  The Witness verifies each nested portable signature and
must separately persist one-time mediation state before it emits the final
FI-facing attestation (that durable state lives in the sibling Witness-ledger
adapter, not here).  FI verifies the final nested artifact and receives a
fresh process-local capability plus a non-authorizing projection.

There is no socket, HTTP, SSH, S3 client, filesystem, database, process,
writer response, promotion, or V1 compatibility implementation in this
module.  It is a wire grammar and verifier boundary only.
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
from typing import Any
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

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
from core import physical_wal_v2_remote_ack as _remote_ack
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckError,
    VerifiedPhysicalWalV2RemoteAckContext,
    VerifiedPhysicalWalV2RemoteAckEvidence,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckRequest,
    require_verified_physical_wal_v2_remote_ack_context,
    require_verified_physical_wal_v2_remote_ack_evidence,
    require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    verify_physical_wal_v2_remote_ack_evidence,
    verify_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    PhysicalWalV2RemoteAckReceiverLedgerError,
    VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAXIMUM_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_WAL_V2_WITNESS_CONTEXT_CERTIFICATE_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_IR_DURABLE_ASSERTION_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_RECOVERY_EXPORT_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_SOURCE_ENVELOPE_SCHEMA",
    "PhysicalWalV2WitnessRoundtripConfig",
    "PhysicalWalV2WitnessRoundtripError",
    "PhysicalWalV2WitnessRoundtripProjection",
    "VerifiedPhysicalWalV2WitnessContextCertificate",
    "VerifiedPhysicalWalV2WitnessIrDurableAssertion",
    "VerifiedPhysicalWalV2WitnessRecoveryExport",
    "VerifiedPhysicalWalV2WitnessRoundtripAttestation",
    "VerifiedPhysicalWalV2WitnessSourceEnvelope",
    "build_physical_wal_v2_witness_context_certificate",
    "build_physical_wal_v2_witness_ir_durable_assertion",
    "build_physical_wal_v2_witness_recovery_export",
    "build_physical_wal_v2_witness_roundtrip_attestation",
    "build_physical_wal_v2_witness_source_envelope",
    "build_physical_wal_v2_witness_source_request",
    "project_verified_physical_wal_v2_witness_roundtrip_attestation",
    "require_verified_physical_wal_v2_witness_roundtrip_attestation",
    "verify_physical_wal_v2_witness_context_certificate",
    "verify_physical_wal_v2_witness_ir_durable_assertion",
    "verify_physical_wal_v2_witness_recovery_export",
    "verify_physical_wal_v2_witness_roundtrip_attestation",
    "verify_physical_wal_v2_witness_source_envelope",
)


PHYSICAL_WAL_V2_WITNESS_RECOVERY_EXPORT_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-recovery-export-v1"
)
PHYSICAL_WAL_V2_WITNESS_CONTEXT_CERTIFICATE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-context-certificate-v1"
)
PHYSICAL_WAL_V2_WITNESS_SOURCE_ENVELOPE_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-source-envelope-v1"
)
PHYSICAL_WAL_V2_WITNESS_IR_DURABLE_ASSERTION_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-ir-durable-assertion-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-attestation-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAXIMUM_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAXIMUM_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WIRE_BYTES = 256 * 1024
_WIRE_VERSION = 1

_RECOVERY_EXPORT_DOMAIN = b"gold-trade-physical-wal-v2-witness-recovery-export-v1\x00"
_CONTEXT_CERTIFICATE_DOMAIN = b"gold-trade-physical-wal-v2-witness-context-certificate-v1\x00"
_SOURCE_ENVELOPE_DOMAIN = b"gold-trade-physical-wal-v2-witness-source-envelope-v1\x00"
_IR_ASSERTION_DOMAIN = b"gold-trade-physical-wal-v2-witness-ir-durable-assertion-v1\x00"
_ATTESTATION_DOMAIN = b"gold-trade-physical-wal-v2-witness-roundtrip-attestation-v1\x00"
# The request has to be accepted by the already-reviewed V2 destination
# verifier.  This fixed V2 domain is intentionally identical to that protocol
# generation; every resulting request is parsed by its public verifier below.
_V2_REQUEST_DOMAIN = b"gold-trade-physical-wal-v2-remote-ack-request-v2\x00"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
)
_RECOVERY_EXPORT_FIELDS = frozenset(
    {
        "schema", "version", "kind", "configuration_sha256",
        "canonical_context_base64", "context_sha256",
        "target_recovery_evidence_sha256", "readback_attestation_sha256",
        "readback_attestation_id", "readback_attestation_nonce",
        "stage_receipt_sha256", "witness_transition_id", "target_recovery_observed_at",
        "export_id", "export_nonce", "issued_at", "expires_at",
        "ir_recovery_exporter", "signature_base64",
    }
)
_CONTEXT_CERTIFICATE_FIELDS = frozenset(
    {
        "schema", "version", "kind", "configuration_sha256",
        "recovery_export_base64", "recovery_export_sha256",
        "canonical_context_base64", "context_sha256",
        "activation_mode", "activation_stream_generation_id",
        "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "witness_sequence", "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256", "witness_ledger_binding_sha256",
        "certificate_id", "certificate_nonce", "issued_at", "expires_at",
        "witness_signer", "signature_base64",
    }
)
_SOURCE_ENVELOPE_FIELDS = frozenset(
    {
        "schema", "version", "kind", "configuration_sha256",
        "context_certificate_base64", "context_certificate_sha256",
        "source_request_base64", "source_request_sha256", "context_sha256",
        "request_id", "request_nonce", "request_expires_at",
        "outbox_id", "outbox_nonce", "issued_at", "expires_at",
        "fi_outbox_signer", "signature_base64",
    }
)
_IR_ASSERTION_FIELDS = frozenset(
    {
        "schema", "version", "kind", "configuration_sha256",
        "source_envelope_base64", "source_envelope_sha256",
        "destination_receipt_base64", "destination_receipt_sha256",
        "context_sha256", "source_request_sha256", "request_id", "request_nonce",
        "receipt_id", "receipt_nonce", "durable_ledger_entry_sha256",
        "receiver_recovery_evidence_sha256", "receiver_replay_lsn",
        "target_recovery_evidence_sha256", "readback_attestation_sha256",
        "readback_attestation_id", "readback_attestation_nonce",
        "stage_receipt_sha256", "witness_transition_id", "target_recovery_observed_at",
        "assertion_id", "assertion_nonce", "issued_at", "expires_at",
        "ir_durable_assertion_signer", "signature_base64",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {
        "schema", "version", "kind", "configuration_sha256",
        "ir_durable_assertion_base64", "ir_durable_assertion_sha256",
        "context_certificate_sha256", "context_sha256", "source_envelope_sha256", "source_request_sha256",
        "destination_receipt_sha256", "durable_ledger_entry_sha256",
        "target_recovery_evidence_sha256", "readback_attestation_sha256",
        "stage_receipt_sha256", "writer_term", "witness_transition_id",
        "activation_mode", "activation_stream_generation_id",
        "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256",
        "activation_receiver_permit_sha256",
        "mediation_id", "witness_sequence", "witness_ledger_entry_sha256",
        "witness_ledger_previous_head_sha256", "witness_ledger_binding_sha256",
        "attestation_id", "attestation_nonce", "issued_at", "expires_at",
        "witness_signer", "signature_base64",
    }
)

_RECOVERY_CAPABILITY = object()
_CONTEXT_CAPABILITY = object()
_ENVELOPE_CAPABILITY = object()
_ASSERTION_CAPABILITY = object()
_ATTESTATION_CAPABILITY = object()


class PhysicalWalV2WitnessRoundtripError(ValueError):
    """A portable V2 Witness round-trip input is unsafe or mismatched."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripConfig:
    """Default-off public-key pins for the portable four-hop evidence path."""

    remote_ack_config: PhysicalWalV2RemoteAckConfig | None = None
    ir_recovery_exporter_public_key: bytes = b""
    fi_outbox_public_key: bytes = b""
    ir_durable_assertion_public_key: bytes = b""
    witness_public_key: bytes = b""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAXIMUM_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessRecoveryExport:
    """Opaque local verification of an IR-signed recovery projection."""

    canonical_export: bytes
    export_sha256: str
    canonical_context: bytes
    context_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_transition_id: str
    export_id: str
    export_nonce: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_RECOVERY_EXPORT_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessContextCertificate:
    """Opaque FI-side intake of a Witness-certified V2 canonical context."""

    canonical_certificate: bytes
    certificate_sha256: str
    canonical_recovery_export: bytes
    recovery_export_sha256: str
    canonical_context: bytes
    context_sha256: str
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
    certificate_id: str
    certificate_nonce: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_CONTEXT_CERTIFICATE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessSourceEnvelope:
    """Opaque verified FI outbox message; it contains only signed public bytes."""

    canonical_envelope: bytes
    envelope_sha256: str
    canonical_context_certificate: bytes
    context_certificate_sha256: str
    canonical_source_request: bytes
    source_request_sha256: str
    context_sha256: str
    request_id: str
    request_nonce: str
    request_expires_at: datetime
    outbox_id: str
    outbox_nonce: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_SOURCE_ENVELOPE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessIrDurableAssertion:
    """Opaque verification of IR's signed durable-ledger assertion."""

    canonical_assertion: bytes
    assertion_sha256: str
    canonical_source_envelope: bytes
    source_envelope_sha256: str
    canonical_destination_receipt: bytes
    destination_receipt_sha256: str
    context_sha256: str
    source_request_sha256: str
    request_id: str
    request_nonce: str
    receipt_id: str
    receipt_nonce: str
    durable_ledger_entry_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_transition_id: str
    assertion_id: str
    assertion_nonce: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_IR_DURABLE_ASSERTION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2WitnessRoundtripAttestation:
    """Opaque FI-facing Witness attestation after durable one-time mediation."""

    canonical_attestation: bytes
    attestation_sha256: str
    canonical_ir_durable_assertion: bytes
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
    mediation_id: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    attestation_id: str
    attestation_nonce: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripProjection:
    """Non-authorizing exact FI-visible pins from a verified attestation."""

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
    mediation_id: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    attestation_id: str
    attestation_nonce: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _ConfigFacts:
    remote_ack_config: PhysicalWalV2RemoteAckConfig
    remote_facts: object
    recovery_exporter_public_key: bytes
    fi_outbox_public_key: bytes
    ir_assertion_public_key: bytes
    witness_public_key: bytes
    maximum_age_seconds: int
    configuration_sha256: str


@dataclass(frozen=True)
class _ActivationFacts:
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str
    witness_transition_id: str


_RECOVERY_STATES: WeakKeyDictionary[VerifiedPhysicalWalV2WitnessRecoveryExport, bytes] = WeakKeyDictionary()
_CONTEXT_STATES: WeakKeyDictionary[VerifiedPhysicalWalV2WitnessContextCertificate, bytes] = WeakKeyDictionary()
_ENVELOPE_STATES: WeakKeyDictionary[VerifiedPhysicalWalV2WitnessSourceEnvelope, bytes] = WeakKeyDictionary()
_ASSERTION_STATES: WeakKeyDictionary[VerifiedPhysicalWalV2WitnessIrDurableAssertion, bytes] = WeakKeyDictionary()
_ATTESTATION_STATES: WeakKeyDictionary[VerifiedPhysicalWalV2WitnessRoundtripAttestation, bytes] = WeakKeyDictionary()


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_JSON_CONSTANT_FORBIDDEN")


def _parse_canonical(value: object, *, code: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        raw = _canonical(dict(value), code=code)
    elif type(value) is bytes:
        raw = value
    else:
        _fail(code)
    if not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WIRE_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2WitnessRoundtripError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail(code)
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return dict(parsed), raw


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _witness_ledger_head_sha256(value: object, *, code: str) -> str:
    """Validate a Witness-chain head, including the explicit genesis head.

    The Witness ledger represents the first record's prior head with the all-
    zero SHA-256 value.  Ordinary evidence hashes must never use that sentinel,
    but rejecting it here would make the signed stage-1 certificate impossible
    for a fresh, correctly initialized durable ledger.
    """

    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


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


def _b64(value: object, *, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not 1 <= len(result) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_WIRE_BYTES:
        _fail(code)
    return result


def _b64_text(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _key_id(value: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(value).hexdigest()


def _signer_mapping(value: bytes) -> dict[str, str]:
    return {
        "algorithm": "ed25519",
        "public_key_base64": _b64_text(value),
        "key_id": _key_id(value),
    }


def _signer(value: object, *, expected: bytes, code: str) -> None:
    item = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if item["algorithm"] != "ed25519" or type(item["public_key_base64"]) is not str:
        _fail(code)
    try:
        key = base64.b64decode(item["public_key_base64"].encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if key != expected or item["key_id"] != _key_id(key):
        _fail(code)


def _private_signer(value: object, *, expected: bytes, code: str) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    try:
        actual = value.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except ValueError:
        _fail(code)
    if actual != expected:
        _fail(code)
    return value


def _sign(unsigned: Mapping[str, Any], *, signer: Ed25519PrivateKey, domain: bytes, code: str) -> str:
    try:
        result = signer.sign(domain + _canonical(dict(unsigned), code=code))
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripError(code) from exc
    if len(result) != 64:
        _fail(code)
    return _b64_text(result)


def _verify_signature(
    *, unsigned: Mapping[str, Any], signature: object, public_key: bytes, domain: bytes, code: str
) -> None:
    if type(signature) is not str:
        _fail(code)
    try:
        raw = base64.b64decode(signature.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(raw) != 64:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            raw,
            domain + _canonical(dict(unsigned), code=code),
        )
    except (InvalidSignature, ValueError):
        _fail(code)


def _term(value: object, *, code: str) -> dict[str, Any]:
    item = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    if (
        item["writer_holder_site"] not in {"webapp_fi", "webapp_ir"}
        or type(item["writer_epoch"]) is not int
        or item["writer_epoch"] < 1
        or type(item["writer_lease_id"]) is not str
        or not item["writer_lease_id"]
    ):
        _fail(code)
    _sha256(item["witnessed_term_proof_sha256"], code=code)
    return item


def _lsn(value: object, *, code: str) -> str:
    if type(value) is not str or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalWalV2WitnessRoundtripConfig or value.enabled is not True:
        _fail("V2_WITNESS_ROUNDTRIP_CONFIG_INVALID")
    if type(value.remote_ack_config) is not PhysicalWalV2RemoteAckConfig:
        _fail("V2_WITNESS_ROUNDTRIP_CONFIG_INVALID")
    try:
        remote_facts = _remote_ack._config(value.remote_ack_config)
    except (AttributeError, PhysicalWalV2RemoteAckError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_CONFIG_INVALID") from exc
    keys = (
        _public_key(value.ir_recovery_exporter_public_key, code="V2_WITNESS_ROUNDTRIP_CONFIG_INVALID"),
        _public_key(value.fi_outbox_public_key, code="V2_WITNESS_ROUNDTRIP_CONFIG_INVALID"),
        _public_key(value.ir_durable_assertion_public_key, code="V2_WITNESS_ROUNDTRIP_CONFIG_INVALID"),
        _public_key(value.witness_public_key, code="V2_WITNESS_ROUNDTRIP_CONFIG_INVALID"),
    )
    if len(set(keys)) != len(keys):
        _fail("V2_WITNESS_ROUNDTRIP_CONFIG_ROLE_KEY_REUSE")
    if (
        type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAXIMUM_EVIDENCE_AGE_SECONDS
        or value.maximum_evidence_age_seconds > remote_facts.maximum_age_seconds
    ):
        _fail("V2_WITNESS_ROUNDTRIP_CONFIG_INVALID")
    payload = {
        "schema": "gold-trade-physical-wal-v2-witness-roundtrip-config-v1",
        "context_sha256": remote_facts.context_sha256,
        "source_site": remote_facts.source_site,
        "destination_site": remote_facts.destination_site,
        "source_public_key_base64": _b64_text(remote_facts.source_public_key),
        "destination_public_key_base64": _b64_text(remote_facts.destination_public_key),
        "ir_recovery_exporter_public_key_base64": _b64_text(keys[0]),
        "fi_outbox_public_key_base64": _b64_text(keys[1]),
        "ir_durable_assertion_public_key_base64": _b64_text(keys[2]),
        "witness_public_key_base64": _b64_text(keys[3]),
        "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
    }
    return _ConfigFacts(
        remote_ack_config=value.remote_ack_config,
        remote_facts=remote_facts,
        recovery_exporter_public_key=keys[0],
        fi_outbox_public_key=keys[1],
        ir_assertion_public_key=keys[2],
        witness_public_key=keys[3],
        maximum_age_seconds=value.maximum_evidence_age_seconds,
        configuration_sha256=hashlib.sha256(_canonical(payload, code="V2_WITNESS_ROUNDTRIP_CONFIG_INVALID")).hexdigest(),
    )


def _context(value: object, *, config: _ConfigFacts, code: str) -> tuple[dict[str, Any], bytes, object]:
    try:
        mapping, raw = _remote_ack._parse_canonical_mapping(value, code=code)
        facts = _remote_ack._context_facts(mapping, code=code)
    except (AttributeError, PhysicalWalV2RemoteAckError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripError(code) from exc
    if (
        facts.canonical_context != raw
        or facts.context_sha256 != config.remote_facts.context_sha256
        or facts.source_site != config.remote_facts.source_site
        or facts.destination_site != config.remote_facts.destination_site
    ):
        _fail(code)
    return mapping, raw, facts


def _expiry(value: object, *, issued_at: datetime, upper_bound: datetime, config: _ConfigFacts, now: datetime, code: str) -> datetime:
    result = _utc(value, code=code)
    if (
        result <= issued_at
        or result > upper_bound
        or result <= now
        or result - issued_at > timedelta(seconds=config.maximum_age_seconds)
    ):
        _fail(code)
    return result


def _check_live_activation(
    *, context_mapping: Mapping[str, Any], witnessed_term: object, activation: object, now: datetime
) -> _ActivationFacts:
    """Witness-local term/activation revalidation, never FI portable input."""

    try:
        term = require_live_object_delta_role_matrix_witnessed_term(witnessed_term, now=now)
        live_activation = require_live_object_delta_role_matrix_activation(activation, now=now)
        writer_role = project_active_object_delta_role_matrix_role(live_activation, site=context_mapping["source_site"], now=now)
        standby_role = project_active_object_delta_role_matrix_role(live_activation, site=context_mapping["destination_site"], now=now)
        active_route = active_object_delta_role_matrix_route(live_activation._matrix)
        active_term = live_activation._witnessed_term
        record = live_activation._history[-1]
    except (AttributeError, IndexError, ObjectDeltaRoleMatrixRolloverError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_LIVE_ACTIVATION_INVALID") from exc
    context_term = _term(context_mapping.get("writer_term"), code="V2_WITNESS_ROUNDTRIP_CONTEXT_TERM_INVALID")
    if (
        writer_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or standby_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
        or (term.holder_site, term.writer_epoch, term.writer_lease_id, term.proof_sha256)
        != (
            context_term["writer_holder_site"], context_term["writer_epoch"],
            context_term["writer_lease_id"], context_term["witnessed_term_proof_sha256"],
        )
        or (active_term.holder_site, active_term.writer_epoch, active_term.writer_lease_id, active_term.proof_sha256)
        != (term.holder_site, term.writer_epoch, term.writer_lease_id, term.proof_sha256)
        or (record.holder_site, record.writer_epoch, record.writer_lease_id)
        != (term.holder_site, term.writer_epoch, term.writer_lease_id)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_LIVE_TERM_CROSS_PIN_MISMATCH")
    binding = active_route.source_pin.binding
    permit = active_route.receiver_binding.permit
    if (
        (binding.source_site, binding.destination_site, binding.campaign_id, binding.release_sha, binding.stream_generation_id)
        != (
            context_mapping["source_site"], context_mapping["destination_site"],
            context_mapping["campaign_id"], context_mapping["release_sha"], context_mapping["stream_generation_id"],
        )
        or (permit.source_site, permit.destination_site, permit.campaign_id, permit.release_sha, permit.stream_generation_id, permit.writer_epoch, permit.writer_lease_id)
        != (
            context_mapping["source_site"], context_mapping["destination_site"],
            context_mapping["campaign_id"], context_mapping["release_sha"], context_mapping["stream_generation_id"],
            term.writer_epoch, term.writer_lease_id,
        )
        or record.stream_generation_id != context_mapping["stream_generation_id"]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_LIVE_ACTIVATION_ROUTE_MISMATCH")
    return _ActivationFacts(
        activation_mode=live_activation._matrix.active_mode,
        activation_stream_generation_id=record.stream_generation_id,
        activation_route_artifact_sha256=record.route_artifact_sha256,
        activation_source_cutover_attestation_sha256=record.source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=record.receiver_permit_sha256,
        witness_transition_id=record.witness_transition_id,
    )


def _target_context_cross_pin(
    *, target: VerifiedPhysicalFullMatrixV2RecoveryEvidence, context_mapping: Mapping[str, Any]
) -> None:
    binding = target.transfer_binding
    term = _term(context_mapping.get("writer_term"), code="V2_WITNESS_ROUNDTRIP_CONTEXT_INVALID")
    expected = {
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "object_storage_namespace": binding.object_storage_namespace,
        "route_commitment_sha256": binding.route_commitment_sha256,
        "four_role_binding_sha256": binding.four_role_binding_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "transport_plane": binding.transport_plane,
        "direct_webapp_transport": binding.direct_webapp_transport,
        "stream_generation_id": target.stream_generation_id,
        "canonical_manifest_sha256": target.manifest_sha256,
        "manifest_id": target.manifest_id,
        "handoff_receipt_id": target.handoff_receipt_id,
        "handoff_receipt_nonce": target.handoff_receipt_nonce,
        "lineage_sha256": target.lineage_sha256,
        "baseline_generation_id": target.baseline_generation_id,
        "database_system_identifier": target.database_system_identifier,
        "timeline_id": target.timeline_id,
        "wal_segment_size_bytes": target.wal_segment_size_bytes,
        "baseline_wal_lsn": target.baseline_wal_lsn,
        "wal_chain_start_lsn": target.wal_chain_start_lsn,
        "base_backup_end_lsn": target.base_backup_end_lsn,
        "target_lsn": target.target_replay_lsn,
        "blob_frontier_scope_sha256": target.blob_frontier_scope_sha256,
        "blob_owner_coverage_sha256": target.blob_owner_coverage_sha256,
        "blob_coverage_id": target.blob_coverage_id,
        "blob_coverage_nonce": target.blob_coverage_nonce,
        "wal_continuity_scope_sha256": target.wal_continuity_scope_sha256,
        "wal_continuity_receipt_id": target.wal_continuity_receipt_id,
        "wal_continuity_receipt_nonce": target.wal_continuity_receipt_nonce,
        "wal_continuity_selector_set_sha256": target.wal_continuity_selector_set_sha256,
        "object_version_set_sha256": target.object_version_set_sha256,
        "coverage_scope_sha256": target.coverage_scope_sha256,
    }
    if (
        any(context_mapping.get(name) != expected_value for name, expected_value in expected.items())
        or context_mapping.get("handoff_expires_at") != _render_timestamp(target.handoff_expires_at)
        or (term["writer_holder_site"], term["writer_epoch"], term["writer_lease_id"], term["witnessed_term_proof_sha256"])
        != (
            binding.writer_term.writer_holder_site, binding.writer_term.writer_epoch,
            binding.writer_term.writer_lease_id, binding.writer_term.witnessed_term_proof_sha256,
        )
    ):
        _fail("V2_WITNESS_ROUNDTRIP_TARGET_CONTEXT_CROSS_PIN_MISMATCH")


def _make_message(unsigned: dict[str, object], *, signer: Ed25519PrivateKey, domain: bytes, code: str) -> dict[str, object]:
    return {**unsigned, "signature_base64": _sign(unsigned, signer=signer, domain=domain, code=code)}


def _message(
    value: object, *, fields: frozenset[str], schema: str, kind: str, config: _ConfigFacts, signer_key: bytes, domain: bytes, now: datetime, code: str
) -> tuple[dict[str, Any], bytes, datetime, datetime]:
    item, raw = _parse_canonical(value, code=code)
    item = _exact_mapping(item, fields=fields, code=code)
    if (
        item["schema"] != schema or item["version"] != _WIRE_VERSION or item["kind"] != kind
        or _sha256(item["configuration_sha256"], code=code) != config.configuration_sha256
    ):
        _fail(code)
    issued = _timestamp(item["issued_at"], code=code)
    expires = _timestamp(item["expires_at"], code=code)
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=config.maximum_age_seconds)
        or issued > now + timedelta(seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FUTURE_SKEW_SECONDS)
        or expires <= now
    ):
        _fail("V2_WITNESS_ROUNDTRIP_EVIDENCE_STALE_OR_EXPIRED")
    unsigned = dict(item)
    signature = unsigned.pop("signature_base64")
    _verify_signature(unsigned=unsigned, signature=signature, public_key=signer_key, domain=domain, code=code)
    return item, raw, issued, expires


def build_physical_wal_v2_witness_recovery_export(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    context: VerifiedPhysicalWalV2RemoteAckContext,
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    export_id: str,
    export_nonce: str,
    expires_at: datetime,
    ir_recovery_exporter_signer: object,
    now: datetime,
) -> dict[str, object]:
    """IR-only exporter: locally revalidate opaque V2 recovery, then sign public facts."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    try:
        checked_context = require_verified_physical_wal_v2_remote_ack_context(context, now=observed)
        target = require_verified_physical_full_matrix_v2_recovery_evidence(target_recovery_evidence, now=observed)
    except (PhysicalWalV2RemoteAckError, PhysicalFullMatrixV2RecoveryEvidenceError) as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_IR_RECOVERY_INVALID") from exc
    mapping, canonical_context, facts = _context(checked_context.canonical_context, config=normalized, code="V2_WITNESS_ROUNDTRIP_CONTEXT_INVALID")
    _target_context_cross_pin(target=target, context_mapping=mapping)
    identity = _identifier(export_id, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    nonce = _nonce(export_nonce, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    if identity == nonce:
        _fail("V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=facts.handoff_expires_at, config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    signer = _private_signer(ir_recovery_exporter_signer, expected=normalized.recovery_exporter_public_key, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_RECOVERY_EXPORT_SCHEMA, "version": _WIRE_VERSION,
        "kind": "ir-locally-revalidated-v2-recovery-export", "configuration_sha256": normalized.configuration_sha256,
        "canonical_context_base64": _b64_text(canonical_context), "context_sha256": facts.context_sha256,
        "target_recovery_evidence_sha256": target.evidence_sha256,
        "readback_attestation_sha256": target.readback_attestation_sha256,
        "readback_attestation_id": target.readback_attestation_id,
        "readback_attestation_nonce": target.readback_attestation_nonce,
        "stage_receipt_sha256": target.stage_receipt_sha256,
        "witness_transition_id": target.witness_transition_id,
        "target_recovery_observed_at": _render_timestamp(target.observed_at),
        "export_id": identity, "export_nonce": nonce,
        "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "ir_recovery_exporter": _signer_mapping(normalized.recovery_exporter_public_key),
    }
    result = _make_message(unsigned, signer=signer, domain=_RECOVERY_EXPORT_DOMAIN, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_SIGNER_INVALID")
    verify_physical_wal_v2_witness_recovery_export(result, config=config, now=observed)
    return result


def verify_physical_wal_v2_witness_recovery_export(
    value: Mapping[str, Any] | bytes,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessRecoveryExport:
    """Verify an IR recovery export without accepting any opaque capability bytes."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    item, raw, issued, expires = _message(
        value, fields=_RECOVERY_EXPORT_FIELDS, schema=PHYSICAL_WAL_V2_WITNESS_RECOVERY_EXPORT_SCHEMA,
        kind="ir-locally-revalidated-v2-recovery-export", config=normalized,
        signer_key=normalized.recovery_exporter_public_key, domain=_RECOVERY_EXPORT_DOMAIN,
        now=observed, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID",
    )
    _signer(item["ir_recovery_exporter"], expected=normalized.recovery_exporter_public_key, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    context_raw = _b64(item["canonical_context_base64"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    _mapping, canonical_context, facts = _context(context_raw, config=normalized, code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    if (
        item["context_sha256"] != facts.context_sha256
        or _sha256(item["target_recovery_evidence_sha256"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["target_recovery_evidence_sha256"]
        or _sha256(item["readback_attestation_sha256"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["readback_attestation_sha256"]
        or _sha256(item["stage_receipt_sha256"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["stage_receipt_sha256"]
        or _identifier(item["readback_attestation_id"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["readback_attestation_id"]
        or _nonce(item["readback_attestation_nonce"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["readback_attestation_nonce"]
        or _identifier(item["export_id"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["export_id"]
        or _nonce(item["export_nonce"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") != item["export_nonce"]
        or item["export_id"] == item["export_nonce"]
        or _timestamp(item["target_recovery_observed_at"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID") > observed + timedelta(seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FUTURE_SKEW_SECONDS)
        or expires > facts.handoff_expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID")
    result = VerifiedPhysicalWalV2WitnessRecoveryExport(
        canonical_export=raw, export_sha256=hashlib.sha256(raw).hexdigest(), canonical_context=canonical_context,
        context_sha256=facts.context_sha256, target_recovery_evidence_sha256=item["target_recovery_evidence_sha256"],
        readback_attestation_sha256=item["readback_attestation_sha256"], stage_receipt_sha256=item["stage_receipt_sha256"],
        witness_transition_id=_identifier(item["witness_transition_id"], code="V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_INVALID"),
        export_id=item["export_id"], export_nonce=item["export_nonce"], issued_at=issued, expires_at=expires,
    )
    object.__setattr__(result, "_capability", _RECOVERY_CAPABILITY)
    _RECOVERY_STATES[result] = raw
    return result


def _require_recovery_export(value: object, *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime) -> VerifiedPhysicalWalV2WitnessRecoveryExport:
    if type(value) is not VerifiedPhysicalWalV2WitnessRecoveryExport or value._capability is not _RECOVERY_CAPABILITY or _RECOVERY_STATES.get(value) != value.canonical_export:
        _fail("V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_witness_recovery_export(value.canonical_export, config=config, now=now)
    for name in ("export_sha256", "canonical_context", "context_sha256", "target_recovery_evidence_sha256", "readback_attestation_sha256", "stage_receipt_sha256", "witness_transition_id", "export_id", "export_nonce", "issued_at", "expires_at"):
        if getattr(value, name) != getattr(fresh, name):
            _fail("V2_WITNESS_ROUNDTRIP_RECOVERY_EXPORT_TAMPERED")
    return value


def build_physical_wal_v2_witness_context_certificate(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    recovery_export: VerifiedPhysicalWalV2WitnessRecoveryExport,
    witness_sequence: int,
    witness_ledger_entry_sha256: str,
    witness_ledger_previous_head_sha256: str,
    witness_ledger_binding_sha256: str,
    certificate_id: str,
    certificate_nonce: str,
    expires_at: datetime,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    activation: VerifiedObjectDeltaRoleMatrixActivation,
    witness_signer: object,
    now: datetime,
) -> dict[str, object]:
    """Witness-certify a current IR recovery export and live role binding."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    export = _require_recovery_export(recovery_export, config=config, now=observed)
    mapping, _context_raw, _facts = _context(export.canonical_context, config=normalized, code="V2_WITNESS_ROUNDTRIP_CONTEXT_INVALID")
    live = _check_live_activation(context_mapping=mapping, witnessed_term=witnessed_term, activation=activation, now=observed)
    if export.witness_transition_id != live.witness_transition_id:
        _fail("V2_WITNESS_ROUNDTRIP_LIVE_TERM_CROSS_PIN_MISMATCH")
    if type(witness_sequence) is not int or witness_sequence < 1:
        _fail("V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    ledger_entry_sha = _sha256(
        witness_ledger_entry_sha256,
        code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID",
    )
    previous_head_sha = _witness_ledger_head_sha256(
        witness_ledger_previous_head_sha256,
        code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID",
    )
    ledger_binding_sha = _sha256(
        witness_ledger_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID",
    )
    identity = _identifier(certificate_id, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    nonce = _nonce(certificate_nonce, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    if identity == nonce:
        _fail("V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=export.expires_at, config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    signer = _private_signer(witness_signer, expected=normalized.witness_public_key, code="V2_WITNESS_ROUNDTRIP_WITNESS_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_CONTEXT_CERTIFICATE_SCHEMA, "version": _WIRE_VERSION,
        "kind": "witness-certified-v2-canonical-context", "configuration_sha256": normalized.configuration_sha256,
        "recovery_export_base64": _b64_text(export.canonical_export), "recovery_export_sha256": export.export_sha256,
        "canonical_context_base64": _b64_text(export.canonical_context), "context_sha256": export.context_sha256,
        "activation_mode": live.activation_mode, "activation_stream_generation_id": live.activation_stream_generation_id,
        "activation_route_artifact_sha256": live.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": live.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": live.activation_receiver_permit_sha256,
        "witness_sequence": witness_sequence,
        "witness_ledger_entry_sha256": ledger_entry_sha,
        "witness_ledger_previous_head_sha256": previous_head_sha,
        "witness_ledger_binding_sha256": ledger_binding_sha,
        "certificate_id": identity, "certificate_nonce": nonce,
        "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "witness_signer": _signer_mapping(normalized.witness_public_key),
    }
    result = _make_message(unsigned, signer=signer, domain=_CONTEXT_CERTIFICATE_DOMAIN, code="V2_WITNESS_ROUNDTRIP_WITNESS_SIGNER_INVALID")
    verify_physical_wal_v2_witness_context_certificate(result, config=config, now=observed)
    return result


def verify_physical_wal_v2_witness_context_certificate(
    value: Mapping[str, Any] | bytes,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessContextCertificate:
    """Verify FI-safe Witness context intake; raw context alone is rejected elsewhere."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    item, raw, issued, expires = _message(
        value, fields=_CONTEXT_CERTIFICATE_FIELDS, schema=PHYSICAL_WAL_V2_WITNESS_CONTEXT_CERTIFICATE_SCHEMA,
        kind="witness-certified-v2-canonical-context", config=normalized, signer_key=normalized.witness_public_key,
        domain=_CONTEXT_CERTIFICATE_DOMAIN, now=observed, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID",
    )
    _signer(item["witness_signer"], expected=normalized.witness_public_key, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    export_raw = _b64(item["recovery_export_base64"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    export = verify_physical_wal_v2_witness_recovery_export(export_raw, config=config, now=observed)
    context_raw = _b64(item["canonical_context_base64"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    _mapping, canonical_context, facts = _context(context_raw, config=normalized, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    for field_name in (
        "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256", "activation_receiver_permit_sha256",
        "witness_ledger_entry_sha256", "witness_ledger_binding_sha256",
    ):
        _sha256(item[field_name], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    if (
        item["recovery_export_sha256"] != export.export_sha256
        or canonical_context != export.canonical_context
        or item["context_sha256"] != facts.context_sha256
        or facts.context_sha256 != export.context_sha256
        or type(item["activation_mode"]) is not str or not item["activation_mode"]
        or type(item["activation_stream_generation_id"]) is not str or not item["activation_stream_generation_id"]
        or type(item["witness_sequence"]) is not int or item["witness_sequence"] < 1
        or _witness_ledger_head_sha256(item["witness_ledger_previous_head_sha256"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID") != item["witness_ledger_previous_head_sha256"]
        or _identifier(item["certificate_id"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID") != item["certificate_id"]
        or _nonce(item["certificate_nonce"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID") != item["certificate_nonce"]
        or item["certificate_id"] == item["certificate_nonce"]
        or expires > export.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    result = VerifiedPhysicalWalV2WitnessContextCertificate(
        canonical_certificate=raw, certificate_sha256=hashlib.sha256(raw).hexdigest(), canonical_recovery_export=export.canonical_export,
        recovery_export_sha256=export.export_sha256, canonical_context=canonical_context, context_sha256=facts.context_sha256,
        target_recovery_evidence_sha256=export.target_recovery_evidence_sha256, readback_attestation_sha256=export.readback_attestation_sha256,
        stage_receipt_sha256=export.stage_receipt_sha256, witness_transition_id=export.witness_transition_id,
        activation_mode=item["activation_mode"], activation_stream_generation_id=item["activation_stream_generation_id"],
        activation_route_artifact_sha256=item["activation_route_artifact_sha256"],
        activation_source_cutover_attestation_sha256=item["activation_source_cutover_attestation_sha256"],
        activation_receiver_permit_sha256=item["activation_receiver_permit_sha256"],
        witness_sequence=item["witness_sequence"],
        witness_ledger_entry_sha256=item["witness_ledger_entry_sha256"],
        witness_ledger_previous_head_sha256=item["witness_ledger_previous_head_sha256"],
        witness_ledger_binding_sha256=item["witness_ledger_binding_sha256"],
        certificate_id=item["certificate_id"], certificate_nonce=item["certificate_nonce"], issued_at=issued, expires_at=expires,
    )
    object.__setattr__(result, "_capability", _CONTEXT_CAPABILITY)
    _CONTEXT_STATES[result] = raw
    return result


def _require_context_certificate(value: object, *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime) -> VerifiedPhysicalWalV2WitnessContextCertificate:
    if type(value) is not VerifiedPhysicalWalV2WitnessContextCertificate or value._capability is not _CONTEXT_CAPABILITY or _CONTEXT_STATES.get(value) != value.canonical_certificate:
        _fail("V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_witness_context_certificate(value.canonical_certificate, config=config, now=now)
    for name in ("certificate_sha256", "canonical_recovery_export", "recovery_export_sha256", "canonical_context", "context_sha256", "target_recovery_evidence_sha256", "readback_attestation_sha256", "stage_receipt_sha256", "witness_transition_id", "activation_mode", "activation_stream_generation_id", "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256", "activation_receiver_permit_sha256", "witness_sequence", "witness_ledger_entry_sha256", "witness_ledger_previous_head_sha256", "witness_ledger_binding_sha256", "certificate_id", "certificate_nonce", "issued_at", "expires_at"):
        if getattr(value, name) != getattr(fresh, name):
            _fail("V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_TAMPERED")
    return value


def build_physical_wal_v2_witness_source_request(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    context_certificate: VerifiedPhysicalWalV2WitnessContextCertificate,
    request_id: str,
    request_nonce: str,
    expires_at: datetime,
    source_signer: object,
    now: datetime,
) -> dict[str, object]:
    """FI-only V2 request builder that accepts *only* verified Witness context."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    certificate = _require_context_certificate(context_certificate, config=config, now=observed)
    mapping, _raw, facts = _context(certificate.canonical_context, config=normalized, code="V2_WITNESS_ROUNDTRIP_CONTEXT_CERTIFICATE_INVALID")
    identity = _identifier(request_id, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    nonce = _nonce(request_nonce, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    if identity == nonce:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=min(certificate.expires_at, facts.handoff_expires_at), config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    signer = _private_signer(source_signer, expected=normalized.remote_facts.source_public_key, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": _remote_ack.PHYSICAL_WAL_V2_REMOTE_ACK_REQUEST_SCHEMA,
        "version": 2, "kind": "physical-wal-v2-replay-ack-request",
        "context": mapping, "context_sha256": facts.context_sha256,
        "request_id": identity, "request_nonce": nonce,
        "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "source_signer": _signer_mapping(normalized.remote_facts.source_public_key),
    }
    result = {**unsigned, "source_signature": {"algorithm": "ed25519", "signature_base64": _sign(unsigned, signer=signer, domain=_V2_REQUEST_DOMAIN, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_SIGNER_INVALID")}}
    try:
        verify_physical_wal_v2_remote_ack_request(source_request=result, config=normalized.remote_ack_config, now=observed)
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID") from exc
    return result


def build_physical_wal_v2_witness_source_envelope(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    context_certificate: VerifiedPhysicalWalV2WitnessContextCertificate,
    source_request: Mapping[str, Any] | bytes,
    outbox_id: str,
    outbox_nonce: str,
    expires_at: datetime,
    fi_outbox_signer: object,
    now: datetime,
) -> dict[str, object]:
    """Wrap one verified FI request for Witness-only forwarding to WA-IR."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    certificate = _require_context_certificate(context_certificate, config=config, now=observed)
    try:
        request = verify_physical_wal_v2_remote_ack_request(source_request=source_request, config=normalized.remote_ack_config, now=observed)
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_REQUEST_INVALID") from exc
    if request.context_sha256 != certificate.context_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_CONTEXT_MISMATCH")
    identity = _identifier(outbox_id, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    nonce = _nonce(outbox_nonce, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    if len({identity, nonce, request.request_id, request.request_nonce}) != 4:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=min(certificate.expires_at, request.expires_at), config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    signer = _private_signer(fi_outbox_signer, expected=normalized.fi_outbox_public_key, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_SOURCE_ENVELOPE_SCHEMA, "version": _WIRE_VERSION,
        "kind": "fi-outbox-v2-request-for-witness-forwarding", "configuration_sha256": normalized.configuration_sha256,
        "context_certificate_base64": _b64_text(certificate.canonical_certificate), "context_certificate_sha256": certificate.certificate_sha256,
        "source_request_base64": _b64_text(request.canonical_request), "source_request_sha256": hashlib.sha256(request.canonical_request).hexdigest(),
        "context_sha256": request.context_sha256, "request_id": request.request_id, "request_nonce": request.request_nonce,
        "request_expires_at": _render_timestamp(request.expires_at), "outbox_id": identity, "outbox_nonce": nonce,
        "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "fi_outbox_signer": _signer_mapping(normalized.fi_outbox_public_key),
    }
    result = _make_message(unsigned, signer=signer, domain=_SOURCE_ENVELOPE_DOMAIN, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_SIGNER_INVALID")
    verify_physical_wal_v2_witness_source_envelope(result, config=config, now=observed)
    return result


def verify_physical_wal_v2_witness_source_envelope(
    value: Mapping[str, Any] | bytes,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessSourceEnvelope:
    """Verify the FI request only if its certified context is nested exactly."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    item, raw, issued, expires = _message(
        value, fields=_SOURCE_ENVELOPE_FIELDS, schema=PHYSICAL_WAL_V2_WITNESS_SOURCE_ENVELOPE_SCHEMA,
        kind="fi-outbox-v2-request-for-witness-forwarding", config=normalized, signer_key=normalized.fi_outbox_public_key,
        domain=_SOURCE_ENVELOPE_DOMAIN, now=observed, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID",
    )
    _signer(item["fi_outbox_signer"], expected=normalized.fi_outbox_public_key, code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    certificate_raw = _b64(item["context_certificate_base64"], code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    certificate = verify_physical_wal_v2_witness_context_certificate(certificate_raw, config=config, now=observed)
    request_raw = _b64(item["source_request_base64"], code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    try:
        request = verify_physical_wal_v2_remote_ack_request(source_request=request_raw, config=normalized.remote_ack_config, now=observed)
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_REQUEST_INVALID") from exc
    if (
        item["context_certificate_sha256"] != certificate.certificate_sha256
        or item["source_request_sha256"] != hashlib.sha256(request.canonical_request).hexdigest()
        or item["context_sha256"] != request.context_sha256
        or request.context_sha256 != certificate.context_sha256
        or item["request_id"] != request.request_id or item["request_nonce"] != request.request_nonce
        or _timestamp(item["request_expires_at"], code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID") != request.expires_at
        or _identifier(item["outbox_id"], code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID") != item["outbox_id"]
        or _nonce(item["outbox_nonce"], code="V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID") != item["outbox_nonce"]
        or len({item["outbox_id"], item["outbox_nonce"], request.request_id, request.request_nonce}) != 4
        or expires > certificate.expires_at or expires > request.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_INVALID")
    result = VerifiedPhysicalWalV2WitnessSourceEnvelope(
        canonical_envelope=raw, envelope_sha256=hashlib.sha256(raw).hexdigest(), canonical_context_certificate=certificate.canonical_certificate,
        context_certificate_sha256=certificate.certificate_sha256, canonical_source_request=request.canonical_request,
        source_request_sha256=hashlib.sha256(request.canonical_request).hexdigest(), context_sha256=request.context_sha256,
        request_id=request.request_id, request_nonce=request.request_nonce, request_expires_at=request.expires_at,
        outbox_id=item["outbox_id"], outbox_nonce=item["outbox_nonce"], issued_at=issued, expires_at=expires,
    )
    object.__setattr__(result, "_capability", _ENVELOPE_CAPABILITY)
    _ENVELOPE_STATES[result] = raw
    return result


def _require_source_envelope(value: object, *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime) -> VerifiedPhysicalWalV2WitnessSourceEnvelope:
    if type(value) is not VerifiedPhysicalWalV2WitnessSourceEnvelope or value._capability is not _ENVELOPE_CAPABILITY or _ENVELOPE_STATES.get(value) != value.canonical_envelope:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_witness_source_envelope(value.canonical_envelope, config=config, now=now)
    for name in ("envelope_sha256", "canonical_context_certificate", "context_certificate_sha256", "canonical_source_request", "source_request_sha256", "context_sha256", "request_id", "request_nonce", "request_expires_at", "outbox_id", "outbox_nonce", "issued_at", "expires_at"):
        if getattr(value, name) != getattr(fresh, name):
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_ENVELOPE_TAMPERED")
    return value


def build_physical_wal_v2_witness_ir_durable_assertion(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    source_envelope: VerifiedPhysicalWalV2WitnessSourceEnvelope,
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence,
    receiver_recovery_evidence: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    target_recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    receiver_ledger_receipt: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    receiver_ledger_config: PhysicalWalV2RemoteAckReceiverLedgerConfig,
    assertion_id: str,
    assertion_nonce: str,
    expires_at: datetime,
    ir_durable_assertion_signer: object,
    now: datetime,
) -> dict[str, object]:
    """IR-only export after revalidating all opaque local V2 receipt inputs."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    # This root-owned configuration is deliberately an explicit *local-only*
    # input.  It is used solely to revalidate the opaque IR ledger receipt and
    # is never serialized into the portable assertion or any FI/Witness wire.
    if type(receiver_ledger_config) is not PhysicalWalV2RemoteAckReceiverLedgerConfig:
        _fail("V2_WITNESS_ROUNDTRIP_IR_LEDGER_CONFIG_REQUIRED")
    envelope = _require_source_envelope(source_envelope, config=config, now=observed)
    try:
        request = verify_physical_wal_v2_remote_ack_request(source_request=envelope.canonical_source_request, config=normalized.remote_ack_config, now=observed)
        pair = require_verified_physical_wal_v2_remote_ack_evidence(remote_ack_evidence, config=normalized.remote_ack_config, now=observed)
        recovery = require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(receiver_recovery_evidence, source_request=request, config=normalized.remote_ack_config, now=observed)
        target = require_verified_physical_full_matrix_v2_recovery_evidence(target_recovery_evidence, now=observed)
        ledger = require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
            receiver_ledger_receipt, config=receiver_ledger_config, source_request=request,
            receiver_recovery_evidence=recovery, target_recovery_evidence=target, remote_ack_evidence=pair, now=observed,
        )
    except (PhysicalWalV2RemoteAckError, PhysicalFullMatrixV2RecoveryEvidenceError, PhysicalWalV2RemoteAckReceiverLedgerError) as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_IR_LOCAL_REVALIDATION_FAILED") from exc
    if pair.canonical_request != envelope.canonical_source_request:
        _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_REQUEST_MISMATCH")
    mapping, _context_raw, _facts = _context_from_source_request(request.canonical_request, config=normalized)
    _target_context_cross_pin(target=target, context_mapping=mapping)
    if (
        ledger.canonical_source_request != pair.canonical_request or ledger.canonical_destination_receipt != pair.canonical_receipt
        or ledger.source_request_sha256 != hashlib.sha256(pair.canonical_request).hexdigest()
        or ledger.destination_receipt_sha256 != hashlib.sha256(pair.canonical_receipt).hexdigest()
        or ledger.context_sha256 != pair.context_sha256 or ledger.request_id != pair.request_id or ledger.request_nonce != pair.request_nonce
        or ledger.receipt_id != pair.receipt_id or ledger.receipt_nonce != pair.receipt_nonce
        or ledger.receiver_recovery_evidence_sha256 != recovery.evidence.receiver_recovery_evidence_sha256
        or ledger.receiver_replay_lsn != recovery.evidence.replay_lsn
        or ledger.target_recovery_evidence_sha256 != target.evidence_sha256
        or ledger.readback_attestation_sha256 != target.readback_attestation_sha256
        or ledger.stage_receipt_sha256 != target.stage_receipt_sha256
        or ledger.witness_transition_id != target.witness_transition_id
    ):
        _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_CROSS_PIN_MISMATCH")
    identity = _identifier(assertion_id, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    nonce = _nonce(assertion_nonce, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    if len({identity, nonce, pair.request_id, pair.request_nonce, pair.receipt_id, pair.receipt_nonce}) != 6:
        _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=min(envelope.expires_at, request.expires_at), config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    signer = _private_signer(ir_durable_assertion_signer, expected=normalized.ir_assertion_public_key, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_IR_DURABLE_ASSERTION_SCHEMA, "version": _WIRE_VERSION,
        "kind": "ir-locally-fsynced-v2-receiver-ledger-assertion", "configuration_sha256": normalized.configuration_sha256,
        "source_envelope_base64": _b64_text(envelope.canonical_envelope), "source_envelope_sha256": envelope.envelope_sha256,
        "destination_receipt_base64": _b64_text(pair.canonical_receipt), "destination_receipt_sha256": hashlib.sha256(pair.canonical_receipt).hexdigest(),
        "context_sha256": pair.context_sha256, "source_request_sha256": hashlib.sha256(pair.canonical_request).hexdigest(),
        "request_id": pair.request_id, "request_nonce": pair.request_nonce, "receipt_id": pair.receipt_id, "receipt_nonce": pair.receipt_nonce,
        "durable_ledger_entry_sha256": ledger.durable_ledger_entry_sha256,
        "receiver_recovery_evidence_sha256": recovery.evidence.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": recovery.evidence.replay_lsn, "target_recovery_evidence_sha256": target.evidence_sha256,
        "readback_attestation_sha256": target.readback_attestation_sha256, "readback_attestation_id": target.readback_attestation_id,
        "readback_attestation_nonce": target.readback_attestation_nonce, "stage_receipt_sha256": target.stage_receipt_sha256,
        "witness_transition_id": target.witness_transition_id, "target_recovery_observed_at": _render_timestamp(target.observed_at),
        "assertion_id": identity, "assertion_nonce": nonce, "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "ir_durable_assertion_signer": _signer_mapping(normalized.ir_assertion_public_key),
    }
    result = _make_message(unsigned, signer=signer, domain=_IR_ASSERTION_DOMAIN, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_SIGNER_INVALID")
    verify_physical_wal_v2_witness_ir_durable_assertion(result, config=config, now=observed)
    return result


def _context_from_source_request(raw: bytes, *, config: _ConfigFacts) -> tuple[dict[str, Any], bytes, object]:
    try:
        outer = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=_strict_object, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    if type(outer) is not dict:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")
    return _context(outer.get("context"), config=config, code="V2_WITNESS_ROUNDTRIP_SOURCE_REQUEST_INVALID")


def verify_physical_wal_v2_witness_ir_durable_assertion(
    value: Mapping[str, Any] | bytes,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessIrDurableAssertion:
    """Verify nested FI/Witness/IR signatures; ledger durability is IR-signed fact."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    item, raw, issued, expires = _message(
        value, fields=_IR_ASSERTION_FIELDS, schema=PHYSICAL_WAL_V2_WITNESS_IR_DURABLE_ASSERTION_SCHEMA,
        kind="ir-locally-fsynced-v2-receiver-ledger-assertion", config=normalized, signer_key=normalized.ir_assertion_public_key,
        domain=_IR_ASSERTION_DOMAIN, now=observed, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID",
    )
    _signer(item["ir_durable_assertion_signer"], expected=normalized.ir_assertion_public_key, code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    envelope_raw = _b64(item["source_envelope_base64"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    envelope = verify_physical_wal_v2_witness_source_envelope(envelope_raw, config=config, now=observed)
    receipt_raw = _b64(item["destination_receipt_base64"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    try:
        pair = verify_physical_wal_v2_remote_ack_evidence(source_request=envelope.canonical_source_request, destination_receipt=receipt_raw, config=normalized.remote_ack_config, now=observed)
    except PhysicalWalV2RemoteAckError as exc:
        raise PhysicalWalV2WitnessRoundtripError("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_PAIR_INVALID") from exc
    certificate = verify_physical_wal_v2_witness_context_certificate(envelope.canonical_context_certificate, config=config, now=observed)
    for field_name in (
        "durable_ledger_entry_sha256", "receiver_recovery_evidence_sha256", "target_recovery_evidence_sha256",
        "readback_attestation_sha256", "stage_receipt_sha256",
    ):
        _sha256(item[field_name], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    if (
        item["source_envelope_sha256"] != envelope.envelope_sha256
        or item["destination_receipt_sha256"] != hashlib.sha256(pair.canonical_receipt).hexdigest()
        or item["context_sha256"] != pair.context_sha256 or pair.context_sha256 != certificate.context_sha256
        or item["source_request_sha256"] != hashlib.sha256(pair.canonical_request).hexdigest()
        or item["request_id"] != pair.request_id or item["request_nonce"] != pair.request_nonce
        or item["receipt_id"] != pair.receipt_id or item["receipt_nonce"] != pair.receipt_nonce
        or item["receiver_recovery_evidence_sha256"] != pair.receiver_recovery_evidence_sha256
        or _lsn(item["receiver_replay_lsn"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") != pair.receiver_replay_lsn
        or item["target_recovery_evidence_sha256"] != certificate.target_recovery_evidence_sha256
        or item["readback_attestation_sha256"] != certificate.readback_attestation_sha256
        or item["stage_receipt_sha256"] != certificate.stage_receipt_sha256
        or item["witness_transition_id"] != certificate.witness_transition_id
        or _identifier(item["readback_attestation_id"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") != item["readback_attestation_id"]
        or _nonce(item["readback_attestation_nonce"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") != item["readback_attestation_nonce"]
        or _timestamp(item["target_recovery_observed_at"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") > observed + timedelta(seconds=MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FUTURE_SKEW_SECONDS)
        or _identifier(item["assertion_id"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") != item["assertion_id"]
        or _nonce(item["assertion_nonce"], code="V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID") != item["assertion_nonce"]
        or len({item["assertion_id"], item["assertion_nonce"], pair.request_id, pair.request_nonce, pair.receipt_id, pair.receipt_nonce}) != 6
        or expires > envelope.expires_at or expires > pair.acknowledged_at + timedelta(seconds=normalized.maximum_age_seconds)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_INVALID")
    result = VerifiedPhysicalWalV2WitnessIrDurableAssertion(
        canonical_assertion=raw, assertion_sha256=hashlib.sha256(raw).hexdigest(), canonical_source_envelope=envelope.canonical_envelope,
        source_envelope_sha256=envelope.envelope_sha256, canonical_destination_receipt=pair.canonical_receipt,
        destination_receipt_sha256=hashlib.sha256(pair.canonical_receipt).hexdigest(), context_sha256=pair.context_sha256,
        source_request_sha256=hashlib.sha256(pair.canonical_request).hexdigest(), request_id=pair.request_id, request_nonce=pair.request_nonce,
        receipt_id=pair.receipt_id, receipt_nonce=pair.receipt_nonce, durable_ledger_entry_sha256=item["durable_ledger_entry_sha256"],
        receiver_recovery_evidence_sha256=pair.receiver_recovery_evidence_sha256, receiver_replay_lsn=pair.receiver_replay_lsn,
        target_recovery_evidence_sha256=item["target_recovery_evidence_sha256"], readback_attestation_sha256=item["readback_attestation_sha256"],
        stage_receipt_sha256=item["stage_receipt_sha256"], witness_transition_id=item["witness_transition_id"],
        assertion_id=item["assertion_id"], assertion_nonce=item["assertion_nonce"], issued_at=issued, expires_at=expires,
    )
    object.__setattr__(result, "_capability", _ASSERTION_CAPABILITY)
    _ASSERTION_STATES[result] = raw
    return result


def _require_ir_assertion(value: object, *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime) -> VerifiedPhysicalWalV2WitnessIrDurableAssertion:
    if type(value) is not VerifiedPhysicalWalV2WitnessIrDurableAssertion or value._capability is not _ASSERTION_CAPABILITY or _ASSERTION_STATES.get(value) != value.canonical_assertion:
        _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_witness_ir_durable_assertion(value.canonical_assertion, config=config, now=now)
    for name in ("assertion_sha256", "canonical_source_envelope", "source_envelope_sha256", "canonical_destination_receipt", "destination_receipt_sha256", "context_sha256", "source_request_sha256", "request_id", "request_nonce", "receipt_id", "receipt_nonce", "durable_ledger_entry_sha256", "receiver_recovery_evidence_sha256", "receiver_replay_lsn", "target_recovery_evidence_sha256", "readback_attestation_sha256", "stage_receipt_sha256", "witness_transition_id", "assertion_id", "assertion_nonce", "issued_at", "expires_at"):
        if getattr(value, name) != getattr(fresh, name):
            _fail("V2_WITNESS_ROUNDTRIP_IR_ASSERTION_TAMPERED")
    return value


def build_physical_wal_v2_witness_roundtrip_attestation(
    *,
    config: PhysicalWalV2WitnessRoundtripConfig,
    ir_durable_assertion: VerifiedPhysicalWalV2WitnessIrDurableAssertion,
    mediation_id: str,
    witness_sequence: int,
    witness_ledger_entry_sha256: str,
    witness_ledger_previous_head_sha256: str,
    witness_ledger_binding_sha256: str,
    attestation_id: str,
    attestation_nonce: str,
    expires_at: datetime,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    activation: VerifiedObjectDeltaRoleMatrixActivation,
    witness_signer: object,
    now: datetime,
) -> dict[str, object]:
    """Witness signs the FI-facing receipt only after its durable ledger records mediation."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    assertion = _require_ir_assertion(ir_durable_assertion, config=config, now=observed)
    envelope = verify_physical_wal_v2_witness_source_envelope(assertion.canonical_source_envelope, config=config, now=observed)
    certificate = verify_physical_wal_v2_witness_context_certificate(envelope.canonical_context_certificate, config=config, now=observed)
    mapping, _context_raw, _facts = _context(certificate.canonical_context, config=normalized, code="V2_WITNESS_ROUNDTRIP_CONTEXT_INVALID")
    live = _check_live_activation(context_mapping=mapping, witnessed_term=witnessed_term, activation=activation, now=observed)
    term = _term(mapping["writer_term"], code="V2_WITNESS_ROUNDTRIP_CONTEXT_TERM_INVALID")
    if (
        certificate.activation_mode != live.activation_mode or certificate.activation_stream_generation_id != live.activation_stream_generation_id
        or certificate.activation_route_artifact_sha256 != live.activation_route_artifact_sha256
        or certificate.activation_source_cutover_attestation_sha256 != live.activation_source_cutover_attestation_sha256
        or certificate.activation_receiver_permit_sha256 != live.activation_receiver_permit_sha256
        or certificate.witness_transition_id != live.witness_transition_id
    ):
        _fail("V2_WITNESS_ROUNDTRIP_LIVE_ACTIVATION_CHANGED")
    mediated = _identifier(mediation_id, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    if type(witness_sequence) is not int or witness_sequence < 1:
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    if witness_sequence <= certificate.witness_sequence:
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_SEQUENCE_INVALID")
    ledger_sha = _sha256(witness_ledger_entry_sha256, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    previous_head_sha = _witness_ledger_head_sha256(
        witness_ledger_previous_head_sha256,
        code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID",
    )
    ledger_binding_sha = _sha256(
        witness_ledger_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID",
    )
    if ledger_binding_sha != certificate.witness_ledger_binding_sha256:
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_LEDGER_BINDING_MISMATCH")
    identity = _identifier(attestation_id, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    nonce = _nonce(attestation_nonce, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    if len({mediated, identity, nonce, assertion.assertion_id, assertion.assertion_nonce}) != 5:
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    expiry = _expiry(expires_at, issued_at=observed, upper_bound=assertion.expires_at, config=normalized, now=observed, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    signer = _private_signer(witness_signer, expected=normalized.witness_public_key, code="V2_WITNESS_ROUNDTRIP_WITNESS_SIGNER_INVALID")
    unsigned: dict[str, object] = {
        "schema": PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_SCHEMA, "version": _WIRE_VERSION,
        "kind": "witness-durably-mediated-v2-roundtrip-attestation", "configuration_sha256": normalized.configuration_sha256,
        "ir_durable_assertion_base64": _b64_text(assertion.canonical_assertion), "ir_durable_assertion_sha256": assertion.assertion_sha256,
        "context_certificate_sha256": certificate.certificate_sha256,
        "context_sha256": assertion.context_sha256, "source_envelope_sha256": assertion.source_envelope_sha256,
        "source_request_sha256": assertion.source_request_sha256, "destination_receipt_sha256": assertion.destination_receipt_sha256,
        "durable_ledger_entry_sha256": assertion.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": assertion.target_recovery_evidence_sha256,
        "readback_attestation_sha256": assertion.readback_attestation_sha256, "stage_receipt_sha256": assertion.stage_receipt_sha256,
        "writer_term": term, "witness_transition_id": assertion.witness_transition_id,
        "activation_mode": live.activation_mode, "activation_stream_generation_id": live.activation_stream_generation_id,
        "activation_route_artifact_sha256": live.activation_route_artifact_sha256,
        "activation_source_cutover_attestation_sha256": live.activation_source_cutover_attestation_sha256,
        "activation_receiver_permit_sha256": live.activation_receiver_permit_sha256,
        "mediation_id": mediated, "witness_sequence": witness_sequence,
        "witness_ledger_entry_sha256": ledger_sha,
        "witness_ledger_previous_head_sha256": previous_head_sha,
        "witness_ledger_binding_sha256": ledger_binding_sha,
        "attestation_id": identity, "attestation_nonce": nonce, "issued_at": _render_timestamp(observed), "expires_at": _render_timestamp(expiry),
        "witness_signer": _signer_mapping(normalized.witness_public_key),
    }
    result = _make_message(unsigned, signer=signer, domain=_ATTESTATION_DOMAIN, code="V2_WITNESS_ROUNDTRIP_WITNESS_SIGNER_INVALID")
    verify_physical_wal_v2_witness_roundtrip_attestation(result, config=config, now=observed)
    return result


def verify_physical_wal_v2_witness_roundtrip_attestation(
    value: Mapping[str, Any] | bytes,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessRoundtripAttestation:
    """FI verifies the full nested portable chain and obtains only opaque evidence."""

    normalized = _config(config)
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_CLOCK_INVALID")
    item, raw, issued, expires = _message(
        value, fields=_ATTESTATION_FIELDS, schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_SCHEMA,
        kind="witness-durably-mediated-v2-roundtrip-attestation", config=normalized, signer_key=normalized.witness_public_key,
        domain=_ATTESTATION_DOMAIN, now=observed, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID",
    )
    _signer(item["witness_signer"], expected=normalized.witness_public_key, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    assertion_raw = _b64(item["ir_durable_assertion_base64"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    assertion = verify_physical_wal_v2_witness_ir_durable_assertion(assertion_raw, config=config, now=observed)
    term = _term(item["writer_term"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    for field_name in (
        "durable_ledger_entry_sha256", "target_recovery_evidence_sha256", "readback_attestation_sha256", "stage_receipt_sha256",
        "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256", "activation_receiver_permit_sha256",
        "context_certificate_sha256", "witness_ledger_entry_sha256", "witness_ledger_binding_sha256",
    ):
        _sha256(item[field_name], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    if (
        item["ir_durable_assertion_sha256"] != assertion.assertion_sha256
        or item["context_sha256"] != assertion.context_sha256 or item["source_envelope_sha256"] != assertion.source_envelope_sha256
        or item["source_request_sha256"] != assertion.source_request_sha256 or item["destination_receipt_sha256"] != assertion.destination_receipt_sha256
        or item["durable_ledger_entry_sha256"] != assertion.durable_ledger_entry_sha256
        or item["target_recovery_evidence_sha256"] != assertion.target_recovery_evidence_sha256
        or item["readback_attestation_sha256"] != assertion.readback_attestation_sha256 or item["stage_receipt_sha256"] != assertion.stage_receipt_sha256
        or item["witness_transition_id"] != assertion.witness_transition_id
        or type(item["activation_mode"]) is not str or not item["activation_mode"]
        or type(item["activation_stream_generation_id"]) is not str or not item["activation_stream_generation_id"]
        or _identifier(item["mediation_id"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID") != item["mediation_id"]
        or type(item["witness_sequence"]) is not int or item["witness_sequence"] < 1
        or _witness_ledger_head_sha256(item["witness_ledger_previous_head_sha256"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID") != item["witness_ledger_previous_head_sha256"]
        or _identifier(item["attestation_id"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID") != item["attestation_id"]
        or _nonce(item["attestation_nonce"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID") != item["attestation_nonce"]
        or len({item["mediation_id"], item["attestation_id"], item["attestation_nonce"], assertion.assertion_id, assertion.assertion_nonce}) != 5
        or expires > assertion.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    # The nested assertion's context certificate carries the actual term hash;
    # do not let a Witness response relabel it with an unrelated writer term.
    envelope = verify_physical_wal_v2_witness_source_envelope(assertion.canonical_source_envelope, config=config, now=observed)
    certificate = verify_physical_wal_v2_witness_context_certificate(envelope.canonical_context_certificate, config=config, now=observed)
    context_mapping, _context_raw, _facts = _context(certificate.canonical_context, config=normalized, code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    context_term = _term(context_mapping["writer_term"], code="V2_WITNESS_ROUNDTRIP_ATTESTATION_INVALID")
    if (
        term != context_term
        or item["context_certificate_sha256"] != certificate.certificate_sha256
        or item["activation_mode"] != certificate.activation_mode
        or item["activation_stream_generation_id"] != certificate.activation_stream_generation_id
        or item["activation_route_artifact_sha256"] != certificate.activation_route_artifact_sha256
        or item["activation_source_cutover_attestation_sha256"] != certificate.activation_source_cutover_attestation_sha256
        or item["activation_receiver_permit_sha256"] != certificate.activation_receiver_permit_sha256
        or item["witness_sequence"] <= certificate.witness_sequence
        or item["witness_ledger_binding_sha256"] != certificate.witness_ledger_binding_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_CROSS_PIN_MISMATCH")
    result = VerifiedPhysicalWalV2WitnessRoundtripAttestation(
        canonical_attestation=raw, attestation_sha256=hashlib.sha256(raw).hexdigest(), canonical_ir_durable_assertion=assertion.canonical_assertion,
        ir_durable_assertion_sha256=assertion.assertion_sha256, context_sha256=assertion.context_sha256,
        context_certificate_sha256=item["context_certificate_sha256"],
        source_envelope_sha256=assertion.source_envelope_sha256, source_request_sha256=assertion.source_request_sha256,
        destination_receipt_sha256=assertion.destination_receipt_sha256, durable_ledger_entry_sha256=assertion.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=assertion.target_recovery_evidence_sha256, readback_attestation_sha256=assertion.readback_attestation_sha256,
        stage_receipt_sha256=assertion.stage_receipt_sha256, writer_holder_site=term["writer_holder_site"], writer_epoch=term["writer_epoch"],
        writer_lease_id=term["writer_lease_id"], witnessed_term_proof_sha256=term["witnessed_term_proof_sha256"],
        witness_transition_id=assertion.witness_transition_id, activation_mode=item["activation_mode"],
        activation_stream_generation_id=item["activation_stream_generation_id"], activation_route_artifact_sha256=item["activation_route_artifact_sha256"],
        activation_source_cutover_attestation_sha256=item["activation_source_cutover_attestation_sha256"], activation_receiver_permit_sha256=item["activation_receiver_permit_sha256"],
        mediation_id=item["mediation_id"], witness_sequence=item["witness_sequence"], witness_ledger_entry_sha256=item["witness_ledger_entry_sha256"],
        witness_ledger_previous_head_sha256=item["witness_ledger_previous_head_sha256"],
        witness_ledger_binding_sha256=item["witness_ledger_binding_sha256"],
        attestation_id=item["attestation_id"], attestation_nonce=item["attestation_nonce"], issued_at=issued, expires_at=expires,
    )
    object.__setattr__(result, "_capability", _ATTESTATION_CAPABILITY)
    _ATTESTATION_STATES[result] = raw
    return result


def require_verified_physical_wal_v2_witness_roundtrip_attestation(
    value: object,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> VerifiedPhysicalWalV2WitnessRoundtripAttestation:
    """Reverify the final Witness artifact at FI's current trusted-clock call site."""

    if type(value) is not VerifiedPhysicalWalV2WitnessRoundtripAttestation or value._capability is not _ATTESTATION_CAPABILITY or _ATTESTATION_STATES.get(value) != value.canonical_attestation:
        _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_CAPABILITY_REQUIRED")
    fresh = verify_physical_wal_v2_witness_roundtrip_attestation(value.canonical_attestation, config=config, now=now)
    for name in ("attestation_sha256", "canonical_ir_durable_assertion", "ir_durable_assertion_sha256", "context_certificate_sha256", "context_sha256", "source_envelope_sha256", "source_request_sha256", "destination_receipt_sha256", "durable_ledger_entry_sha256", "target_recovery_evidence_sha256", "readback_attestation_sha256", "stage_receipt_sha256", "writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256", "witness_transition_id", "activation_mode", "activation_stream_generation_id", "activation_route_artifact_sha256", "activation_source_cutover_attestation_sha256", "activation_receiver_permit_sha256", "mediation_id", "witness_sequence", "witness_ledger_entry_sha256", "witness_ledger_previous_head_sha256", "witness_ledger_binding_sha256", "attestation_id", "attestation_nonce", "issued_at", "expires_at"):
        if getattr(value, name) != getattr(fresh, name):
            _fail("V2_WITNESS_ROUNDTRIP_ATTESTATION_TAMPERED")
    return value


def project_verified_physical_wal_v2_witness_roundtrip_attestation(
    value: object,
    *, config: PhysicalWalV2WitnessRoundtripConfig, now: datetime
) -> PhysicalWalV2WitnessRoundtripProjection:
    """Project exact, non-authorizing pins for the separate FI strict adapter."""

    verified = require_verified_physical_wal_v2_witness_roundtrip_attestation(value, config=config, now=now)
    return PhysicalWalV2WitnessRoundtripProjection(
        attestation_sha256=verified.attestation_sha256, ir_durable_assertion_sha256=verified.ir_durable_assertion_sha256,
        context_certificate_sha256=verified.context_certificate_sha256,
        context_sha256=verified.context_sha256, source_envelope_sha256=verified.source_envelope_sha256,
        source_request_sha256=verified.source_request_sha256, destination_receipt_sha256=verified.destination_receipt_sha256,
        durable_ledger_entry_sha256=verified.durable_ledger_entry_sha256, target_recovery_evidence_sha256=verified.target_recovery_evidence_sha256,
        readback_attestation_sha256=verified.readback_attestation_sha256, stage_receipt_sha256=verified.stage_receipt_sha256,
        writer_holder_site=verified.writer_holder_site, writer_epoch=verified.writer_epoch, writer_lease_id=verified.writer_lease_id,
        witnessed_term_proof_sha256=verified.witnessed_term_proof_sha256, witness_transition_id=verified.witness_transition_id,
        activation_mode=verified.activation_mode, activation_stream_generation_id=verified.activation_stream_generation_id,
        activation_route_artifact_sha256=verified.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=verified.activation_source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=verified.activation_receiver_permit_sha256, mediation_id=verified.mediation_id,
        witness_sequence=verified.witness_sequence, witness_ledger_entry_sha256=verified.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=verified.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=verified.witness_ledger_binding_sha256,
        attestation_id=verified.attestation_id, attestation_nonce=verified.attestation_nonce, issued_at=verified.issued_at, expires_at=verified.expires_at,
    )
