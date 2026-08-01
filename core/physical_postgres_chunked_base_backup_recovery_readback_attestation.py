"""Pure V2 signed PostgreSQL recovery-readback evidence.

This module is a narrow root/host-attestation boundary, not a PostgreSQL
adapter.  A holder of the pinned attester private key signs one bounded,
canonical PostgreSQL readback together with every V2 route, manifest, Witness
handoff, staged-recovery, geometry, and replay-target pin.  Consumers receive
only a revalidated opaque capability; bare readback bytes are never execution
authority here.

The signature proves an attester observed the claimed state.  It does *not*
open a stage, connect to PostgreSQL, fetch an object, restore data, prove WAL
continuity beyond a base-backup endpoint, promote a writer, or affect Full
Matrix readiness.  A future target-recovery boundary must additionally require
its own V2 WAL-continuity evidence before treating a target beyond
``base_backup_end_lsn`` as usable.
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

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_recovery_admission import (
    VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    project_verified_physical_wal_chunked_base_backup_recovery_admission,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupTransferError,
    build_physical_wal_chunked_base_backup_binding,
)


__all__ = (
    "MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_AGE_SECONDS",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA",
    "PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA",
    "PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError",
    "PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope",
    "VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation",
    "build_physical_postgres_chunked_base_backup_recovery_readback_attestation",
    "require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation",
    "verify_physical_postgres_chunked_base_backup_recovery_readback_attestation",
)


PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA = (
    "gold-trade-physical-postgres-chunked-base-backup-recovery-readback-attestation-v2"
)
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA = (
    "gold-trade-physical-postgres-chunked-base-backup-recovery-readback-v2"
)
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_VERSION = 2
PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_ALGORITHM = (
    "ed25519"
)
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_BYTES = 256 * 1024
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BYTES = 64 * 1024
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_AGE_SECONDS = 120
MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_FUTURE_SKEW_SECONDS = 5
REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024

_DOMAIN = b"gold-trade-physical-postgres-chunked-base-backup-recovery-readback-attestation-v2\x00"
_CAPABILITY = object()
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_SITE_RE = re.compile(r"^webapp_(?:fi|ir)$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)
_STAGE_DIRECTORY_RE = re.compile(r"^stage-[0-9a-f]{48}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_ATTESTATION_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00$",
    re.ASCII,
)
_READBACK_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

_BINDING_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "object_storage_namespace",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "destination_age_recipient",
        "writer_term",
        "transport_plane",
        "direct_webapp_transport",
    }
)
_TERM_FIELDS = frozenset(
    {
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "manifest_id",
        "manifest_sha256",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
    }
)
_HANDOFF_FIELDS = frozenset(
    {
        "receipt_id",
        "receipt_nonce",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "legacy_route_binding_sha256",
    }
)
_STAGE_FIELDS = frozenset(
    {
        "recovery_admission_scope_sha256",
        "stage_receipt_sha256",
        "stage_directory_name",
        "receipt_id",
        "receipt_nonce",
        "manifest_id",
        "manifest_sha256",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
        "lineage_sha256",
        "snapshot_sha256",
        "snapshot_bytes",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "chunk_count",
    }
)
_BASELINE_FIELDS = frozenset(
    {
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "completion_attestation_sha256",
        "witness_transition_id",
        "witness_public_key_sha256",
    }
)
_READBACK_FIELDS = frozenset(
    {
        "schema",
        "status",
        "observed_at",
        "receiver_site",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "route",
        "writer_term",
        "stage",
        "baseline",
        "target_replay_lsn",
        "postgresql",
    }
)
_READBACK_ROUTE_FIELDS = frozenset(
    {
        "binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "object_storage_namespace",
        "destination_age_recipient",
        "transport_plane",
        "direct_webapp_transport",
    }
)
_POSTGRES_FIELDS = frozenset(
    {
        "in_recovery",
        "role",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_generation_id",
        "replay_lsn",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "attestation_id",
        "attestation_nonce",
        "issued_at",
        "expires_at",
        "binding",
        "binding_sha256",
        "manifest",
        "handoff",
        "stage",
        "baseline",
        "target_replay_lsn",
        "canonical_readback_base64",
        "readback_sha256",
        "attester_signer",
        "attester_signature",
    }
)


class PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(ValueError):
    """A signed V2 recovery readback is stale, foreign, or malformed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope:
    """Exact V2 route, stage geometry, and observed replay target policy.

    A target beyond ``base_backup_end_lsn`` is permitted as an observation but
    not as WAL-continuity authority.  A future target-recovery preflight must
    pair this attestation with a separately verified V2 continuity capability.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    receiver_site: str
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    completion_attestation_sha256: str
    witness_transition_id: str
    witness_public_key_sha256: str
    expected_target_replay_lsn: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation:
    """Opaque signed host observation, never restore, promotion, or writer authority."""

    schema: str
    canonical_attestation: bytes
    attestation_sha256: str
    attestation_id: str
    attestation_nonce: str
    issued_at: datetime
    expires_at: datetime
    attester_public_key: bytes
    scope_sha256: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    binding_sha256: str
    manifest_id: str
    manifest_sha256: str
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    recovery_admission_scope_sha256: str
    stage_directory_name: str
    stage_receipt_sha256: str
    canonical_readback: bytes
    readback_sha256: str
    observed_at: datetime
    target_replay_lsn: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class _ScopeFacts:
    scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope
    binding: PhysicalWalChunkedBaseBackupBinding
    scope_sha256: str


@dataclass(frozen=True)
class _ContextFacts:
    scope: _ScopeFacts
    admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    manifest_sha256: str
    session_sha256: str
    finalization_permit_sha256: str


@dataclass(frozen=True)
class _ReadbackFacts:
    raw: bytes
    sha256: str
    observed_at: datetime


@dataclass(frozen=True)
class _AttestationFacts:
    raw: bytes
    sha256: str
    attestation_id: str
    attestation_nonce: str
    issued_at: datetime
    expires_at: datetime
    attester_public_key: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    binding_sha256: str
    manifest: dict[str, Any]
    handoff: dict[str, Any]
    stage: dict[str, Any]
    baseline: dict[str, Any]
    target_replay_lsn: str
    readback: _ReadbackFacts
    readback_item: dict[str, Any]


@dataclass(frozen=True)
class _Facts:
    context: _ContextFacts
    attestation: _AttestationFacts


def _fail(code: str) -> None:
    raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    digest = _text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _identifier(value: object, *, code: str) -> str:
    return _text(value, pattern=_ID_RE, code=code)


def _nonce(value: object, *, code: str) -> str:
    return _text(value, pattern=_NONCE_RE, code=code)


def _site(value: object, *, code: str) -> str:
    return _text(value, pattern=_SITE_RE, code=code)


def _generation(value: object, *, code: str) -> str:
    return _text(value, pattern=_GENERATION_RE, code=code)


def _stage_directory_name(value: object, *, code: str) -> str:
    return _text(value, pattern=_STAGE_DIRECTORY_RE, code=code)


def _system_identifier(value: object, *, code: str) -> str:
    return _text(value, pattern=_SYSTEM_IDENTIFIER_RE, code=code)


def _lease_id(value: object, *, code: str) -> str:
    return _text(value, pattern=LEASE_ID_RE, code=code)


def _positive(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) + int(low, 16)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _attestation_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _ATTESTATION_TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc
    if parsed.isoformat() != value:
        _fail(code)
    return parsed


def _attestation_timestamp_text(value: object, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _readback_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _READBACK_TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        _fail(code)
    return parsed


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _base64_bytes(value: object, *, maximum: int, code: str) -> bytes:
    if type(value) is not str or not value or len(value) > maximum * 2:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not decoded or len(decoded) > maximum or base64.b64encode(decoded).decode("ascii") != value:
        _fail(code)
    return decoded


def _signer(value: object, *, code: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if signer["algorithm"] != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_ALGORITHM:
        _fail(code)
    public = _public_key(
        _base64_bytes(signer["public_key_base64"], maximum=32, code=code),
        code=code,
    )
    if len(public) != 32 or _text(signer["key_id"], pattern=_KEY_ID_RE, code=code) != _key_id(public):
        _fail(code)
    return public


def _signature(value: object, *, code: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if signature["algorithm"] != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_ALGORITHM:
        _fail(code)
    encoded = _base64_bytes(signature["signature_base64"], maximum=64, code=code)
    if len(encoded) != 64:
        _fail(code)
    return encoded


def _signer_from_private(value: object, *, code: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    public = value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public = _public_key(public, code=code)
    return value, public, {
        "algorithm": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public).decode("ascii"),
        "key_id": _key_id(public),
    }


def _sign(unsigned: Mapping[str, Any], *, signer: object) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(
        signer,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_INVALID",
    )
    try:
        signature = private.sign(
            _DOMAIN
            + _canonical(
                dict(unsigned),
                code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL",
            )
        )
    except ValueError:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_INVALID")
    if type(signature) is not bytes or len(signature) != 64:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_INVALID")
    return {
        "algorithm": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _verify_signature(payload: Mapping[str, Any], *, expected_attester_public_key: bytes) -> bytes:
    public = _signer(
        payload.get("attester_signer"),
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_INVALID",
    )
    expected = _public_key(
        expected_attester_public_key,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_EXPECTED_SIGNER_INVALID",
    )
    if public != expected:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_MISMATCH")
    signature = _signature(
        payload.get("attester_signature"),
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_INVALID",
    )
    unsigned = {key: value for key, value in payload.items() if key != "attester_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public).verify(
            signature,
            _DOMAIN
            + _canonical(
                unsigned,
                code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL",
            ),
        )
    except (InvalidSignature, ValueError):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNATURE_INVALID")
    return public


def _binding_mapping(value: PhysicalWalChunkedBaseBackupBinding) -> dict[str, object]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "object_storage_namespace": value.object_storage_namespace,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "destination_age_recipient": value.destination_age_recipient,
        "writer_term": {
            "writer_holder_site": value.writer_term.writer_holder_site,
            "writer_epoch": value.writer_term.writer_epoch,
            "writer_lease_id": value.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": value.transport_plane,
        "direct_webapp_transport": value.direct_webapp_transport,
    }


def _normalise_binding(value: object, *, code: str) -> PhysicalWalChunkedBaseBackupBinding:
    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail(code)
    try:
        normalized = build_physical_wal_chunked_base_backup_binding(
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            object_storage_namespace=value.object_storage_namespace,
            route_commitment_sha256=value.route_commitment_sha256,
            four_role_binding_sha256=value.four_role_binding_sha256,
            destination_age_recipient=value.destination_age_recipient,
            writer_holder_site=value.writer_term.writer_holder_site,
            writer_epoch=value.writer_term.writer_epoch,
            writer_lease_id=value.writer_term.writer_lease_id,
            witnessed_term_proof_sha256=value.writer_term.witnessed_term_proof_sha256,
        )
    except (AttributeError, PhysicalWalChunkedBaseBackupTransferError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc
    if normalized != value:
        _fail(code)
    return normalized


def _binding_from_mapping(value: object, *, code: str) -> PhysicalWalChunkedBaseBackupBinding:
    mapping = _exact_mapping(value, fields=_BINDING_FIELDS, code=code)
    term = _exact_mapping(mapping["writer_term"], fields=_TERM_FIELDS, code=code)
    try:
        binding = build_physical_wal_chunked_base_backup_binding(
            source_site=mapping["source_site"],
            destination_site=mapping["destination_site"],
            campaign_id=mapping["campaign_id"],
            release_sha=mapping["release_sha"],
            object_storage_namespace=mapping["object_storage_namespace"],
            route_commitment_sha256=mapping["route_commitment_sha256"],
            four_role_binding_sha256=mapping["four_role_binding_sha256"],
            destination_age_recipient=mapping["destination_age_recipient"],
            writer_holder_site=term["writer_holder_site"],
            writer_epoch=term["writer_epoch"],
            writer_lease_id=term["writer_lease_id"],
            witnessed_term_proof_sha256=term["witnessed_term_proof_sha256"],
        )
    except (PhysicalWalChunkedBaseBackupTransferError, TypeError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(code) from exc
    if mapping != _binding_mapping(binding):
        _fail(code)
    return binding


def _scope_facts(value: object) -> _ScopeFacts:
    if type(value) is not PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_REQUIRED")
    binding = _normalise_binding(
        value.transfer_binding,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    receiver = _site(
        value.receiver_site,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    lineage = _sha256(
        value.lineage_sha256,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    generation = _generation(
        value.baseline_generation_id,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    system_identifier = _system_identifier(
        value.database_system_identifier,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    timeline = _positive(
        value.timeline_id,
        maximum=0xFFFFFFFF,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    wal_size = _positive(
        value.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    baseline, baseline_value = _lsn(
        value.baseline_wal_lsn,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    chain_start, chain_start_value = _lsn(
        value.wal_chain_start_lsn,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    base_end, base_end_value = _lsn(
        value.base_backup_end_lsn,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    target, target_value = _lsn(
        value.expected_target_replay_lsn,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    completion = _sha256(
        value.completion_attestation_sha256,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    transition = _identifier(
        value.witness_transition_id,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    witness = _sha256(
        value.witness_public_key_sha256,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
    )
    if (
        binding.destination_site != receiver
        or binding.source_site == binding.destination_site
        or binding.writer_term.writer_holder_site != binding.source_site
        or wal_size != REQUIRED_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_WAL_SEGMENT_SIZE_BYTES
        or baseline_value > base_end_value
        or chain_start_value > base_end_value
        or target_value < base_end_value
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_MISMATCH")
    normalized = PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope(
        transfer_binding=binding,
        receiver_site=receiver,
        lineage_sha256=lineage,
        baseline_generation_id=generation,
        database_system_identifier=system_identifier,
        timeline_id=timeline,
        wal_segment_size_bytes=wal_size,
        baseline_wal_lsn=baseline,
        wal_chain_start_lsn=chain_start,
        base_backup_end_lsn=base_end,
        completion_attestation_sha256=completion,
        witness_transition_id=transition,
        witness_public_key_sha256=witness,
        expected_target_replay_lsn=target,
    )
    if normalized != value:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID")
    scope_payload = {
        "schema": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA,
        "binding": _binding_mapping(binding),
        "receiver_site": receiver,
        "lineage_sha256": lineage,
        "baseline_generation_id": generation,
        "database_system_identifier": system_identifier,
        "timeline_id": timeline,
        "wal_segment_size_bytes": wal_size,
        "baseline_wal_lsn": baseline,
        "wal_chain_start_lsn": chain_start,
        "base_backup_end_lsn": base_end,
        "completion_attestation_sha256": completion,
        "witness_transition_id": transition,
        "witness_public_key_sha256": witness,
        "expected_target_replay_lsn": target,
    }
    return _ScopeFacts(
        scope=normalized,
        binding=binding,
        scope_sha256=hashlib.sha256(
            _canonical(
                scope_payload,
                code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCOPE_INVALID",
            )
        ).hexdigest(),
    )


def _manifest_pins(context: _ContextFacts) -> dict[str, object]:
    permit = context.manifest.finalization_permit
    return {
        "manifest_id": context.manifest.manifest_id,
        "manifest_sha256": context.manifest_sha256,
        "session_sha256": context.session_sha256,
        "finalization_permit_id": permit.finalization_permit_id,
        "finalization_permit_sha256": context.finalization_permit_sha256,
        "committed_chunk_set_sha256": permit.committed_chunk_set_sha256,
    }


def _handoff_pins(context: _ContextFacts) -> dict[str, object]:
    handoff = context.handoff
    return {
        "receipt_id": handoff.receipt_id,
        "receipt_nonce": handoff.receipt_nonce,
        "lineage_sha256": handoff.lineage_sha256,
        "snapshot_sha256": handoff.snapshot_sha256,
        "snapshot_bytes": handoff.snapshot_bytes,
        "legacy_route_binding_sha256": handoff.legacy_route_binding_sha256,
    }


def _stage_pins(admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission) -> dict[str, object]:
    return {
        "recovery_admission_scope_sha256": admission.scope_sha256,
        "stage_receipt_sha256": admission.stage_receipt_sha256,
        "stage_directory_name": admission.stage_directory_name,
        "receipt_id": admission.receipt_id,
        "receipt_nonce": admission.receipt_nonce,
        "manifest_id": admission.manifest_id,
        "manifest_sha256": admission.manifest_sha256,
        "session_sha256": admission.session_sha256,
        "finalization_permit_id": admission.finalization_permit_id,
        "finalization_permit_sha256": admission.finalization_permit_sha256,
        "committed_chunk_set_sha256": admission.committed_chunk_set_sha256,
        "lineage_sha256": admission.lineage_sha256,
        "snapshot_sha256": admission.snapshot_sha256,
        "snapshot_bytes": admission.snapshot_bytes,
        "total_plaintext_sha256": admission.total_plaintext_sha256,
        "total_plaintext_bytes": admission.total_plaintext_bytes,
        "chunk_count": admission.chunk_count,
    }


def _baseline_pins(scope: _ScopeFacts) -> dict[str, object]:
    policy = scope.scope
    return {
        "baseline_generation_id": policy.baseline_generation_id,
        "database_system_identifier": policy.database_system_identifier,
        "timeline_id": policy.timeline_id,
        "wal_segment_size_bytes": policy.wal_segment_size_bytes,
        "baseline_wal_lsn": policy.baseline_wal_lsn,
        "wal_chain_start_lsn": policy.wal_chain_start_lsn,
        "base_backup_end_lsn": policy.base_backup_end_lsn,
        "completion_attestation_sha256": policy.completion_attestation_sha256,
        "witness_transition_id": policy.witness_transition_id,
        "witness_public_key_sha256": policy.witness_public_key_sha256,
    }


def _context_facts(
    *,
    scope: object,
    recovery_admission: object,
    manifest: object,
    handoff_receipt: object,
    now: datetime,
) -> _ContextFacts:
    scope_facts = _scope_facts(scope)
    current = _utc(
        now,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CLOCK_INVALID",
    )
    try:
        admission = project_verified_physical_wal_chunked_base_backup_recovery_admission(
            recovery_admission
        )
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            manifest,
            now=current,
        )
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=current,
        )
    except Exception as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_V2_CAPABILITY_INVALID"
        ) from exc
    permit = verified_manifest.finalization_permit
    binding = permit.session.binding
    manifest_sha = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    session_sha = hashlib.sha256(permit.session.canonical_session).hexdigest()
    permit_sha = hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
    policy = scope_facts.scope
    if (
        binding != scope_facts.binding
        or handoff.binding_sha256 != admission.binding_sha256
        or handoff.manifest_id != verified_manifest.manifest_id
        or handoff.manifest_sha256 != manifest_sha
        or handoff.session_sha256 != session_sha
        or handoff.finalization_permit_id != permit.finalization_permit_id
        or handoff.finalization_permit_sha256 != permit_sha
        or handoff.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or handoff.lineage_sha256 != policy.lineage_sha256
        or handoff.baseline_generation_id != policy.baseline_generation_id
        or handoff.database_system_identifier != policy.database_system_identifier
        or handoff.timeline_id != policy.timeline_id
        or handoff.wal_segment_size_bytes != policy.wal_segment_size_bytes
        or handoff.baseline_wal_lsn != policy.baseline_wal_lsn
        or handoff.wal_chain_start_lsn != policy.wal_chain_start_lsn
        or handoff.base_backup_end_lsn != policy.base_backup_end_lsn
        or handoff.completion_attestation_sha256 != policy.completion_attestation_sha256
        or handoff.witness_transition_id != policy.witness_transition_id
        or hashlib.sha256(handoff.witness_public_key).hexdigest()
        != policy.witness_public_key_sha256
        or admission.receiver_site != policy.receiver_site
        or admission.receipt_id != handoff.receipt_id
        or admission.receipt_nonce != handoff.receipt_nonce
        or admission.manifest_id != verified_manifest.manifest_id
        or admission.manifest_sha256 != manifest_sha
        or admission.binding_sha256 != handoff.binding_sha256
        or admission.session_sha256 != session_sha
        or admission.finalization_permit_id != permit.finalization_permit_id
        or admission.finalization_permit_sha256 != permit_sha
        or admission.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or admission.lineage_sha256 != handoff.lineage_sha256
        or admission.snapshot_sha256 != handoff.snapshot_sha256
        or admission.snapshot_bytes != handoff.snapshot_bytes
        or admission.total_plaintext_sha256 != verified_manifest.total_plaintext_sha256
        or admission.total_plaintext_bytes != verified_manifest.total_plaintext_bytes
        or admission.chunk_count != len(verified_manifest.chunks)
        or admission.baseline_generation_id != handoff.baseline_generation_id
        or admission.database_system_identifier != handoff.database_system_identifier
        or admission.timeline_id != handoff.timeline_id
        or admission.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or admission.baseline_wal_lsn != handoff.baseline_wal_lsn
        or admission.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or admission.base_backup_end_lsn != handoff.base_backup_end_lsn
        or admission.completion_attestation_sha256 != handoff.completion_attestation_sha256
        or admission.witness_transition_id != handoff.witness_transition_id
        or admission.witness_public_key_sha256
        != hashlib.sha256(handoff.witness_public_key).hexdigest()
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CROSS_PIN_MISMATCH")
    return _ContextFacts(
        scope=scope_facts,
        admission=admission,
        manifest=verified_manifest,
        handoff=handoff,
        manifest_sha256=manifest_sha,
        session_sha256=session_sha,
        finalization_permit_sha256=permit_sha,
    )


def _parse_readback(raw: object, *, now: datetime) -> tuple[dict[str, Any], _ReadbackFacts]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BYTES:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID")
    try:
        item = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(
            "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID"
        ) from exc
    readback = _exact_mapping(
        item,
        fields=_READBACK_FIELDS,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID",
    )
    if _canonical(
        readback,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID",
    ) != raw:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_NONCANONICAL")
    if (
        readback["schema"] != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_SCHEMA
        or readback["status"] != "replay-evidence-observed"
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID")
    observed = _readback_timestamp(
        readback["observed_at"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID",
    )
    current = _utc(
        now,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CLOCK_INVALID",
    )
    if (
        observed
        > current
        + timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_FUTURE_SKEW_SECONDS)
        or current - observed
        > timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_AGE_SECONDS)
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_STALE")
    return readback, _ReadbackFacts(
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        observed_at=observed,
    )


def _checked_stage_mapping(value: object, *, code: str) -> dict[str, Any]:
    stage = _exact_mapping(value, fields=_STAGE_FIELDS, code=code)
    for key, item in stage.items():
        if key == "stage_directory_name":
            _stage_directory_name(item, code=code)
        elif key.endswith("_id"):
            _identifier(item, code=code)
        elif key.endswith("_nonce"):
            _nonce(item, code=code)
        elif key.endswith("_bytes") or key == "chunk_count":
            _positive(item, maximum=2**63 - 1, code=code)
        else:
            _sha256(item, code=code)
    return stage


def _checked_baseline_mapping(value: object, *, code: str) -> dict[str, Any]:
    baseline = _exact_mapping(value, fields=_BASELINE_FIELDS, code=code)
    _generation(baseline["baseline_generation_id"], code=code)
    _system_identifier(baseline["database_system_identifier"], code=code)
    _positive(baseline["timeline_id"], maximum=0xFFFFFFFF, code=code)
    _positive(baseline["wal_segment_size_bytes"], maximum=2**31 - 1, code=code)
    _lsn(baseline["baseline_wal_lsn"], code=code)
    _lsn(baseline["wal_chain_start_lsn"], code=code)
    _lsn(baseline["base_backup_end_lsn"], code=code)
    _sha256(baseline["completion_attestation_sha256"], code=code)
    _identifier(baseline["witness_transition_id"], code=code)
    _sha256(baseline["witness_public_key_sha256"], code=code)
    return baseline


def _assert_readback(
    *,
    item: dict[str, Any],
    context: _ContextFacts,
) -> None:
    scope = context.scope.scope
    binding = context.scope.binding
    code = "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_PIN_MISMATCH"
    if (
        _site(item["receiver_site"], code=code) != scope.receiver_site
        or _site(item["source_site"], code=code) != binding.source_site
        or _site(item["destination_site"], code=code) != binding.destination_site
        or _text(item["campaign_id"], pattern=CAMPAIGN_ID_RE, code=code) != binding.campaign_id
        or _text(item["release_sha"], pattern=RELEASE_SHA_RE, code=code) != binding.release_sha
    ):
        _fail(code)
    route = _exact_mapping(item["route"], fields=_READBACK_ROUTE_FIELDS, code=code)
    if (
        _sha256(route["binding_sha256"], code=code) != context.handoff.binding_sha256
        or _sha256(route["route_commitment_sha256"], code=code)
        != binding.route_commitment_sha256
        or _sha256(route["four_role_binding_sha256"], code=code)
        != binding.four_role_binding_sha256
        or route["object_storage_namespace"] != binding.object_storage_namespace
        or _text(route["destination_age_recipient"], pattern=AGE_RECIPIENT_RE, code=code)
        != binding.destination_age_recipient
        or route["transport_plane"] != binding.transport_plane
        or route["direct_webapp_transport"] != binding.direct_webapp_transport
    ):
        _fail(code)
    term = _exact_mapping(item["writer_term"], fields=_TERM_FIELDS, code=code)
    if (
        _site(term["writer_holder_site"], code=code) != binding.writer_term.writer_holder_site
        or _positive(term["writer_epoch"], maximum=2**63 - 1, code=code)
        != binding.writer_term.writer_epoch
        or _lease_id(term["writer_lease_id"], code=code) != binding.writer_term.writer_lease_id
        or _sha256(term["witnessed_term_proof_sha256"], code=code)
        != binding.writer_term.witnessed_term_proof_sha256
    ):
        _fail(code)
    stage = _checked_stage_mapping(item["stage"], code=code)
    if stage != _stage_pins(context.admission):
        _fail(code)
    baseline = _checked_baseline_mapping(item["baseline"], code=code)
    if baseline != _baseline_pins(context.scope):
        _fail(code)
    target, _target_value = _lsn(item["target_replay_lsn"], code=code)
    if target != scope.expected_target_replay_lsn:
        _fail(code)
    postgres = _exact_mapping(item["postgresql"], fields=_POSTGRES_FIELDS, code=code)
    if (
        postgres["in_recovery"] is not True
        or postgres["role"] != "standby"
        or _system_identifier(postgres["database_system_identifier"], code=code)
        != scope.database_system_identifier
        or _positive(postgres["timeline_id"], maximum=0xFFFFFFFF, code=code)
        != scope.timeline_id
        or _positive(postgres["wal_segment_size_bytes"], maximum=2**31 - 1, code=code)
        != scope.wal_segment_size_bytes
        or _generation(postgres["baseline_generation_id"], code=code)
        != scope.baseline_generation_id
        or _lsn(postgres["replay_lsn"], code=code)[0] != target
    ):
        _fail(code)


def _parse_attestation(
    value: object,
    *,
    expected_attester_public_key: bytes,
    now: datetime,
) -> _AttestationFacts:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(
                payload,
                code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID",
            )
        except (TypeError, ValueError) as exc:
            raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(
                "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID"
            ) from exc
    elif type(value) is bytes:
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_BYTES:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationError(
                "POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL"
            ) from exc
        if type(payload) is not dict or _canonical(
            payload,
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL",
        ) != raw:
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL")
    else:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID")
    if not raw or len(raw) > MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_BYTES:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID")
    attestation = _exact_mapping(
        payload,
        fields=_ATTESTATION_FIELDS,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID",
    )
    if (
        attestation["schema"]
        != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA
        or attestation["version"]
        != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_VERSION
        or attestation["kind"]
        != "physical_postgres_chunked_base_backup_recovery_readback_attestation"
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA_INVALID")
    attester = _verify_signature(
        attestation,
        expected_attester_public_key=expected_attester_public_key,
    )
    issued = _attestation_timestamp(
        attestation["issued_at"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
    )
    expires = _attestation_timestamp(
        attestation["expires_at"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
    )
    current = _utc(
        now,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CLOCK_INVALID",
    )
    if (
        expires <= issued
        or expires - issued
        > timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_AGE_SECONDS)
        or issued
        > current
        + timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_FUTURE_SKEW_SECONDS)
        or expires <= current
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_STALE")
    binding = _binding_from_mapping(
        attestation["binding"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_BINDING_INVALID",
    )
    manifest = _exact_mapping(
        attestation["manifest"],
        fields=_MANIFEST_FIELDS,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    handoff = _exact_mapping(
        attestation["handoff"],
        fields=_HANDOFF_FIELDS,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    stage = _checked_stage_mapping(
        attestation["stage"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    baseline = _checked_baseline_mapping(
        attestation["baseline"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    for key, item in manifest.items():
        if key.endswith("_id"):
            _identifier(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
        else:
            _sha256(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
    for key, item in handoff.items():
        if key.endswith("_id"):
            _identifier(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
        elif key.endswith("_nonce"):
            _nonce(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
        elif key.endswith("_bytes"):
            _positive(item, maximum=2**63 - 1, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
        else:
            _sha256(item, code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID")
    binding_sha = _sha256(
        attestation["binding_sha256"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    target, _target_value = _lsn(
        attestation["target_replay_lsn"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_INVALID",
    )
    readback_raw = _base64_bytes(
        attestation["canonical_readback_base64"],
        maximum=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_BYTES,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID",
    )
    readback_item, readback = _parse_readback(readback_raw, now=current)
    if _sha256(
        attestation["readback_sha256"],
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_INVALID",
    ) != readback.sha256:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_READBACK_HASH_MISMATCH")
    return _AttestationFacts(
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        attestation_id=_identifier(
            attestation["attestation_id"],
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_ID_INVALID",
        ),
        attestation_nonce=_nonce(
            attestation["attestation_nonce"],
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCE_INVALID",
        ),
        issued_at=issued,
        expires_at=expires,
        attester_public_key=attester,
        binding=binding,
        binding_sha256=binding_sha,
        manifest=manifest,
        handoff=handoff,
        stage=stage,
        baseline=baseline,
        target_replay_lsn=target,
        readback=readback,
        readback_item=readback_item,
    )


def _assert_attestation_pins(*, context: _ContextFacts, attestation: _AttestationFacts) -> None:
    if (
        attestation.binding != context.scope.binding
        or attestation.binding_sha256 != context.handoff.binding_sha256
        or attestation.manifest != _manifest_pins(context)
        or attestation.handoff != _handoff_pins(context)
        or attestation.stage != _stage_pins(context.admission)
        or attestation.baseline != _baseline_pins(context.scope)
        or attestation.target_replay_lsn != context.scope.scope.expected_target_replay_lsn
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_PIN_MISMATCH")
    identity_values = {
        attestation.attestation_id,
        attestation.attestation_nonce,
        context.handoff.receipt_id,
        context.handoff.receipt_nonce,
        context.manifest.manifest_id,
        context.manifest.finalization_permit.finalization_permit_id,
    }
    if len(identity_values) != 6:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_IDENTITY_REUSE")
    _assert_readback(item=attestation.readback_item, context=context)


def _derive_facts(
    *,
    attestation: object,
    expected_attester_public_key: bytes,
    scope: object,
    recovery_admission: object,
    manifest: object,
    handoff_receipt: object,
    now: datetime,
) -> _Facts:
    context = _context_facts(
        scope=scope,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=now,
    )
    parsed = _parse_attestation(
        attestation,
        expected_attester_public_key=expected_attester_public_key,
        now=now,
    )
    _assert_attestation_pins(context=context, attestation=parsed)
    return _Facts(context=context, attestation=parsed)


def _result_from_facts(
    facts: _Facts,
) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation:
    parsed = facts.attestation
    context = facts.context
    return VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation(
        schema=PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA,
        canonical_attestation=parsed.raw,
        attestation_sha256=parsed.sha256,
        attestation_id=parsed.attestation_id,
        attestation_nonce=parsed.attestation_nonce,
        issued_at=parsed.issued_at,
        expires_at=parsed.expires_at,
        attester_public_key=parsed.attester_public_key,
        scope_sha256=context.scope.scope_sha256,
        transfer_binding=context.scope.binding,
        binding_sha256=context.handoff.binding_sha256,
        manifest_id=context.manifest.manifest_id,
        manifest_sha256=context.manifest_sha256,
        handoff_receipt_id=context.handoff.receipt_id,
        handoff_receipt_nonce=context.handoff.receipt_nonce,
        recovery_admission_scope_sha256=context.admission.scope_sha256,
        stage_directory_name=context.admission.stage_directory_name,
        stage_receipt_sha256=context.admission.stage_receipt_sha256,
        canonical_readback=parsed.readback.raw,
        readback_sha256=parsed.readback.sha256,
        observed_at=parsed.readback.observed_at,
        target_replay_lsn=parsed.target_replay_lsn,
    )


def _assert_result(
    value: VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation,
    facts: _Facts,
) -> None:
    expected = _result_from_facts(facts)
    for name in (
        "schema",
        "canonical_attestation",
        "attestation_sha256",
        "attestation_id",
        "attestation_nonce",
        "issued_at",
        "expires_at",
        "attester_public_key",
        "scope_sha256",
        "transfer_binding",
        "binding_sha256",
        "manifest_id",
        "manifest_sha256",
        "handoff_receipt_id",
        "handoff_receipt_nonce",
        "recovery_admission_scope_sha256",
        "stage_directory_name",
        "stage_receipt_sha256",
        "canonical_readback",
        "readback_sha256",
        "observed_at",
        "target_replay_lsn",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CAPABILITY_TAMPERED")


def build_physical_postgres_chunked_base_backup_recovery_readback_attestation(
    *,
    scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    canonical_readback: bytes,
    attestation_id: str,
    attestation_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    attester_signer: object,
) -> dict[str, Any]:
    """Sign one exact V2 host observation; this performs no recovery I/O.

    Passing canonical raw readback bytes is intentionally confined to this
    private-key signing boundary.  Verification and future execution
    boundaries accept only the resulting opaque attestation capability.
    """

    issued = _utc(
        issued_at,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
    )
    expires = _utc(
        expires_at,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
    )
    if (
        expires <= issued
        or expires - issued
        > timedelta(seconds=MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_AGE_SECONDS)
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID")
    context = _context_facts(
        scope=scope,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=issued,
    )
    readback_item, readback = _parse_readback(canonical_readback, now=issued)
    _assert_readback(item=readback_item, context=context)
    identifier = _identifier(
        attestation_id,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_ID_INVALID",
    )
    nonce = _nonce(
        attestation_nonce,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCE_INVALID",
    )
    identity_values = {
        identifier,
        nonce,
        context.handoff.receipt_id,
        context.handoff.receipt_nonce,
        context.manifest.manifest_id,
        context.manifest.finalization_permit.finalization_permit_id,
    }
    if len(identity_values) != 6:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_IDENTITY_REUSE")
    _private, _public, signer = _signer_from_private(
        attester_signer,
        code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SIGNER_INVALID",
    )
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA,
        "version": PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_VERSION,
        "kind": "physical_postgres_chunked_base_backup_recovery_readback_attestation",
        "attestation_id": identifier,
        "attestation_nonce": nonce,
        "issued_at": _attestation_timestamp_text(
            issued,
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
        ),
        "expires_at": _attestation_timestamp_text(
            expires,
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_TIME_INVALID",
        ),
        "binding": _binding_mapping(context.scope.binding),
        "binding_sha256": context.handoff.binding_sha256,
        "manifest": _manifest_pins(context),
        "handoff": _handoff_pins(context),
        "stage": _stage_pins(context.admission),
        "baseline": _baseline_pins(context.scope),
        "target_replay_lsn": context.scope.scope.expected_target_replay_lsn,
        "canonical_readback_base64": base64.b64encode(readback.raw).decode("ascii"),
        "readback_sha256": readback.sha256,
        "attester_signer": signer,
    }
    result = {**unsigned, "attester_signature": _sign(unsigned, signer=attester_signer)}
    if len(
        _canonical(
            result,
            code="POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_NONCANONICAL",
        )
    ) > MAX_PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_BYTES:
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_INVALID")
    return result


def verify_physical_postgres_chunked_base_backup_recovery_readback_attestation(
    *,
    attestation: Mapping[str, Any] | bytes,
    expected_attester_public_key: bytes,
    scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation:
    """Verify one signed V2 attestation into a process-local opaque capability."""

    facts = _derive_facts(
        attestation=attestation,
        expected_attester_public_key=expected_attester_public_key,
        scope=scope,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=now,
    )
    result = _result_from_facts(facts)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _assert_result(result, facts)
    return result


def require_verified_physical_postgres_chunked_base_backup_recovery_readback_attestation(
    value: object,
    *,
    expected_attester_public_key: bytes,
    scope: PhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestationScope,
    recovery_admission: VerifiedPhysicalWalChunkedBaseBackupRecoveryAdmission,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    now: datetime,
) -> VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation:
    """Revalidate an opaque signed readback; bare readback bytes are rejected."""

    if (
        type(value) is not VerifiedPhysicalPostgresChunkedBaseBackupRecoveryReadbackAttestation
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_SCHEMA
    ):
        _fail("POSTGRES_CHUNKED_BASE_BACKUP_RECOVERY_READBACK_ATTESTATION_CAPABILITY_REQUIRED")
    facts = _derive_facts(
        attestation=value.canonical_attestation,
        expected_attester_public_key=expected_attester_public_key,
        scope=scope,
        recovery_admission=recovery_admission,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        now=now,
    )
    _assert_result(value, facts)
    return value
