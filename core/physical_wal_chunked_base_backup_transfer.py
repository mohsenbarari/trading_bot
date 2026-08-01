"""Pure Witness-pinned contracts for a chunked physical base-backup transfer.

This is deliberately a *control-plane* contract, not an uploader.  It makes
the unsafe shape of a single very large base-backup object impossible to
describe: every encrypted object is one independently permitted, immutable
chunk.  The Witness signs a short-lived permit for each exact object key and
may sign a durable acceptance only while that permit is still live.  A late
object can still exist in Object Storage, but it has no signed commitment and
therefore remains an unreferenced orphan which cannot appear in a final
manifest.

There is no filesystem, Object Storage, age, database, SSH, socket, subprocess
or clock access here.  A production Witness runtime must atomically append and
fsync its nonce/index ledger *before* it emits a signed commitment or
finalization permit.  Callers supply the clock and ledger observations to the
pure verifiers below.  Nothing in this module authorizes a writer, promotion,
restore, direct WebApp-to-WebApp transfer, or a network call.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PLAINTEXT_BYTES",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_METADATA_BYTES",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS",
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SESSION_SECONDS",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMMITMENT_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMPLETION_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PERMIT_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FINALIZATION_PERMIT_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSFER_SESSION_SCHEMA",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION",
    "PhysicalWalChunkedBaseBackupBinding",
    "PhysicalWalChunkedBaseBackupChunk",
    "PhysicalWalChunkedBaseBackupTransferError",
    "PhysicalWalChunkedBaseBackupWriterTerm",
    "VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet",
    "VerifiedPhysicalWalChunkedBaseBackupChunkCommitment",
    "VerifiedPhysicalWalChunkedBaseBackupChunkCompletion",
    "VerifiedPhysicalWalChunkedBaseBackupChunkPermit",
    "VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit",
    "VerifiedPhysicalWalChunkedBaseBackupTransferSession",
    "build_physical_wal_chunked_base_backup_binding",
    "build_physical_wal_chunked_base_backup_chunk_completion",
    "build_physical_wal_chunked_base_backup_chunk_permit",
    "build_physical_wal_chunked_base_backup_finalization_permit",
    "build_physical_wal_chunked_base_backup_transfer_session",
    "build_physical_wal_chunked_base_backup_witness_chunk_commitment",
    "begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set",
    "canonical_physical_wal_chunked_base_backup_chunk_commitment_bytes",
    "canonical_physical_wal_chunked_base_backup_chunk_completion_bytes",
    "canonical_physical_wal_chunked_base_backup_chunk_permit_bytes",
    "canonical_physical_wal_chunked_base_backup_finalization_permit_bytes",
    "canonical_physical_wal_chunked_base_backup_transfer_session_bytes",
    "derive_physical_wal_chunked_base_backup_chunk_key",
    "derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256",
    "append_physical_wal_chunked_base_backup_witness_accepted_chunk",
    "require_verified_physical_wal_chunked_base_backup_chunk_commitment",
    "require_verified_physical_wal_chunked_base_backup_chunk_completion",
    "require_verified_physical_wal_chunked_base_backup_chunk_permit",
    "require_verified_physical_wal_chunked_base_backup_finalization_permit",
    "require_verified_physical_wal_chunked_base_backup_transfer_session",
    "require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set",
    "verify_physical_wal_chunked_base_backup_chunk_commitment",
    "verify_physical_wal_chunked_base_backup_chunk_completion",
    "verify_physical_wal_chunked_base_backup_chunk_permit",
    "verify_physical_wal_chunked_base_backup_finalization_permit",
    "verify_physical_wal_chunked_base_backup_transfer_session",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION = 2
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM = "ed25519"
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSFER_SESSION_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-transfer-session-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PERMIT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-chunk-permit-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMPLETION_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-chunk-completion-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMMITMENT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-chunk-commitment-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FINALIZATION_PERMIT_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-finalization-permit-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE = (
    "private-versioned-object-storage-witness-mediated-v1"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_DIRECT_WEBAPP_TRANSPORT = "forbidden"

# The limits intentionally describe small, renewable work units.  A runtime
# requests another permit for the next chunk; it must never stretch a permit
# or reuse a timed-out object.  The maximum is not an availability lease.
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_METADATA_BYTES = 512 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SESSION_SECONDS = 12 * 60 * 60
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS = 120
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES = 128 * 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PLAINTEXT_BYTES = 128 * 1024 * 1024
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS = 262_144
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FUTURE_SKEW_SECONDS = 5

_SESSION_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-transfer-session-v2\x00"
_PERMIT_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-chunk-permit-v2\x00"
_COMPLETION_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-chunk-completion-v2\x00"
_COMMITMENT_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-chunk-commitment-v2\x00"
_FINALIZATION_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-finalization-permit-v2\x00"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_MUTABLE_ALIAS_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})

_TERM_FIELDS = frozenset(
    {"writer_holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"}
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
_CHUNK_FIELDS = frozenset(
    {
        "index",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
        "age_recipient",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_SESSION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_nonce",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)
_PERMIT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_sha256",
        "permit_id",
        "permit_nonce",
        "chunk_index",
        "object_key",
        "max_ciphertext_bytes",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_sha256",
        "permit_id",
        "permit_nonce",
        "permit_sha256",
        "completion_id",
        "completion_nonce",
        "completed_at",
        "chunk",
        "source_signer",
        "source_signature",
    }
)
_COMMITMENT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_sha256",
        "permit_id",
        "permit_sha256",
        "completion_id",
        "completion_sha256",
        "commitment_id",
        "commitment_nonce",
        "durable_ledger_entry_id",
        "committed_at",
        "chunk",
        "witness_signer",
        "witness_signature",
    }
)
_FINALIZATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_nonce",
        "committed_chunk_set_sha256",
        "committed_chunk_count",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)

_VERIFIED_SESSION_CAPABILITY = object()
_VERIFIED_PERMIT_CAPABILITY = object()
_VERIFIED_COMPLETION_CAPABILITY = object()
_VERIFIED_COMMITMENT_CAPABILITY = object()
_VERIFIED_ACCEPTED_CHUNK_SET_CAPABILITY = object()
_VERIFIED_FINALIZATION_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupTransferError(ValueError):
    """A chunked base-backup evidence item is malformed, stale, or unbound."""


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupWriterTerm:
    """Exact active Writer/Witness identity, not a writer authorization."""

    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupBinding:
    """The exact mediated Object Storage route for one backup session."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    object_storage_namespace: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    destination_age_recipient: str
    writer_term: PhysicalWalChunkedBaseBackupWriterTerm
    transport_plane: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE
    direct_webapp_transport: str = PHYSICAL_WAL_CHUNKED_BASE_BACKUP_DIRECT_WEBAPP_TRANSPORT


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupChunk:
    """One immutable, encrypted object-version selector and plaintext claim."""

    index: int
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupTransferSession:
    """Opaque verified Witness session; not upload, restore, or writer authority."""

    canonical_session: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupChunkPermit:
    """Opaque short-lived Witness permit for one exact immutable chunk key."""

    canonical_permit: bytes
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    permit_id: str
    permit_nonce: str
    chunk_index: int
    object_key: str
    max_ciphertext_bytes: int
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupChunkCompletion:
    """Opaque source-signed completion that still needs Witness acceptance."""

    canonical_completion: bytes
    permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit
    completion_id: str
    completion_nonce: str
    completed_at: datetime
    chunk: PhysicalWalChunkedBaseBackupChunk
    source_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupChunkCommitment:
    """Opaque Witness durable-acceptance evidence for exactly one chunk.

    The signature is meaningful only when the Witness runtime emitted it after
    atomically recording ``durable_ledger_entry_id``.  The pure verifier cannot
    persist that ledger; it can ensure a late completion was never accepted.
    """

    canonical_commitment: bytes
    completion: VerifiedPhysicalWalChunkedBaseBackupChunkCompletion
    commitment_id: str
    commitment_nonce: str
    durable_ledger_entry_id: str
    committed_at: datetime
    chunk: PhysicalWalChunkedBaseBackupChunk
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet:
    """Opaque Witness-owned contiguous accepted-commit state.

    This capability can be constructed only by the pure ``begin``/``append``
    transition functions below, each of which accepts only an already verified
    Witness commitment at exactly the next index.  A finalizer deliberately
    receives this capability instead of a caller-supplied list.  A future
    Witness runtime must make the same transition atomic with its durable
    ledger update before it exposes the successor state.
    """

    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    committed_chunks: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, ...]
    committed_chunk_set_sha256: str
    next_chunk_index: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit:
    """Opaque fresh Witness pin for one exact, contiguous accepted chunk set."""

    canonical_finalization_permit: bytes
    session: VerifiedPhysicalWalChunkedBaseBackupTransferSession
    finalization_permit_id: str
    finalization_permit_nonce: str
    committed_chunk_set_sha256: str
    committed_chunk_count: int
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _SessionFacts:
    raw: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


@dataclass(frozen=True)
class _PermitFacts:
    raw: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_sha256: str
    permit_id: str
    permit_nonce: str
    chunk_index: int
    object_key: str
    max_ciphertext_bytes: int
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


@dataclass(frozen=True)
class _CompletionFacts:
    raw: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_sha256: str
    permit_id: str
    permit_nonce: str
    permit_sha256: str
    completion_id: str
    completion_nonce: str
    completed_at: datetime
    chunk: PhysicalWalChunkedBaseBackupChunk
    source_public_key: bytes


@dataclass(frozen=True)
class _CommitmentFacts:
    raw: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_sha256: str
    permit_id: str
    permit_sha256: str
    completion_id: str
    completion_sha256: str
    commitment_id: str
    commitment_nonce: str
    durable_ledger_entry_id: str
    committed_at: datetime
    chunk: PhysicalWalChunkedBaseBackupChunk
    witness_public_key: bytes


@dataclass(frozen=True)
class _FinalizationFacts:
    raw: bytes
    binding: PhysicalWalChunkedBaseBackupBinding
    session_id: str
    session_sha256: str
    finalization_permit_id: str
    finalization_permit_nonce: str
    committed_chunk_set_sha256: str
    committed_chunk_count: int
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


def _fail(message: str) -> None:
    raise PhysicalWalChunkedBaseBackupTransferError(message)


def _canonical(value: object, *, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupTransferError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("chunked base-backup JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("chunked base-backup JSON has a forbidden constant")


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(f"{label} is invalid")
    return value


def _id(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_ID_RE)


def _nonce(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=_NONCE_RE)


def _sha256(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=SHA256_RE)


def _nonzero_sha256(value: object, *, label: str) -> str:
    digest = _sha256(value, label=label)
    if digest == "0" * 64:
        _fail(f"{label} is invalid")
    return digest


def _site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _fail(f"{label} is invalid")
    return value


def _chunk_index(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0 or value >= MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail(f"{label} is invalid")
    return value


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{label} is invalid")
    if parsed.tzinfo is None:
        _fail(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(f"{label} is not canonical UTC")
    return normalized


def _timestamp_text(value: object, *, label: str) -> str:
    return _utc(value, label=label).isoformat()


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(f"{label} is invalid")
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        _fail(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(f"{label} is invalid")
    if len(decoded) != expected_bytes:
        _fail(f"{label} is invalid")
    return decoded


def _signer(value: object, *, label: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label=f"{label} signer")
    if signer["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM:
        _fail(f"{label} signer algorithm is invalid")
    public_key = _public_key(
        _decode_base64(signer["public_key_base64"], label=f"{label} signer public key", expected_bytes=32),
        label=f"{label} signer public key",
    )
    if _text(signer["key_id"], label=f"{label} signer key ID", pattern=_KEY_ID_RE) != _key_id(public_key):
        _fail(f"{label} signer key ID does not match public key")
    return public_key


def _signature(value: object, *, label: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label=f"{label} signature")
    if signature["algorithm"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM:
        _fail(f"{label} signature algorithm is invalid")
    return _decode_base64(signature["signature_base64"], label=f"{label} signature", expected_bytes=64)


def _signer_from_private(value: object, *, label: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalWalChunkedBaseBackupTransferError(f"{label} signing is unavailable") from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(f"{label} signer is invalid")
    try:
        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail(f"{label} signer is invalid")
    public_key = _public_key(public_key, label=f"{label} signer public key")
    return (
        value,
        public_key,
        {
            "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _key_id(public_key),
        },
    )


def _sign(unsigned: Mapping[str, Any], *, domain: bytes, signer: object, label: str) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(signer, label=label)
    try:
        signature = private.sign(domain + _canonical(dict(unsigned), label=label))
    except ValueError:
        _fail(f"{label} signer failed")
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail(f"{label} signer produced an invalid signature")
    return {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _verify_signature(
    payload: Mapping[str, Any],
    *,
    signature_field: str,
    signer_field: str,
    domain: bytes,
    label: str,
    expected_key: bytes | None,
) -> bytes:
    public_key = _signer(payload.get(signer_field), label=label)
    if expected_key is not None and public_key != expected_key:
        _fail(f"{label} signer does not match expected route key")
    signature = _signature(payload.get(signature_field), label=label)
    unsigned = {key: item for key, item in payload.items() if key != signature_field}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, domain + _canonical(unsigned, label=label)
        )
    except (InvalidSignature, ValueError):
        _fail(f"{label} signature is invalid")
    return public_key


def _parse_canonical(value: object, *, label: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(payload, label=label)
        except (TypeError, ValueError):
            _fail(f"{label} is invalid")
    elif isinstance(value, bytes):
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_METADATA_BYTES:
            _fail(f"{label} byte size is invalid")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail(f"{label} is invalid JSON")
        if not isinstance(payload, dict) or _canonical(payload, label=label) != raw:
            _fail(f"{label} is not canonical JSON")
    else:
        _fail(f"{label} is invalid")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_METADATA_BYTES:
        _fail(f"{label} byte size is invalid")
    return payload, raw


def _term_from_mapping(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupWriterTerm:
    term = _exact_mapping(value, fields=_TERM_FIELDS, label=f"{label} writer term")
    return PhysicalWalChunkedBaseBackupWriterTerm(
        writer_holder_site=_site(term["writer_holder_site"], label=f"{label} writer holder"),
        writer_epoch=_positive_int(term["writer_epoch"], label=f"{label} writer epoch", maximum=(2**63 - 1)),
        writer_lease_id=_text(term["writer_lease_id"], label=f"{label} writer lease", pattern=LEASE_ID_RE),
        witnessed_term_proof_sha256=_nonzero_sha256(
            term["witnessed_term_proof_sha256"], label=f"{label} writer term proof"
        ),
    )


def _term_mapping(value: PhysicalWalChunkedBaseBackupWriterTerm) -> dict[str, Any]:
    return {
        "writer_holder_site": value.writer_holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "witnessed_term_proof_sha256": value.witnessed_term_proof_sha256,
    }


def _binding_from_mapping(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupBinding:
    item = _exact_mapping(value, fields=_BINDING_FIELDS, label=f"{label} binding")
    return _normalise_binding(
        PhysicalWalChunkedBaseBackupBinding(
            source_site=item["source_site"],
            destination_site=item["destination_site"],
            campaign_id=item["campaign_id"],
            release_sha=item["release_sha"],
            object_storage_namespace=item["object_storage_namespace"],
            route_commitment_sha256=item["route_commitment_sha256"],
            four_role_binding_sha256=item["four_role_binding_sha256"],
            destination_age_recipient=item["destination_age_recipient"],
            writer_term=_term_from_mapping(item["writer_term"], label=label),
            transport_plane=item["transport_plane"],
            direct_webapp_transport=item["direct_webapp_transport"],
        ),
        label=label,
    )


def _binding_mapping(value: PhysicalWalChunkedBaseBackupBinding) -> dict[str, Any]:
    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "object_storage_namespace": value.object_storage_namespace,
        "route_commitment_sha256": value.route_commitment_sha256,
        "four_role_binding_sha256": value.four_role_binding_sha256,
        "destination_age_recipient": value.destination_age_recipient,
        "writer_term": _term_mapping(value.writer_term),
        "transport_plane": value.transport_plane,
        "direct_webapp_transport": value.direct_webapp_transport,
    }


def _normalise_binding(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupBinding:
    if type(value) is not PhysicalWalChunkedBaseBackupBinding:
        _fail(f"{label} is invalid")
    source = _site(value.source_site, label=f"{label} source site")
    destination = _site(value.destination_site, label=f"{label} destination site")
    if source == destination:
        _fail(f"{label} source and destination overlap")
    campaign = _text(value.campaign_id, label=f"{label} campaign", pattern=CAMPAIGN_ID_RE)
    release = _text(value.release_sha, label=f"{label} release", pattern=RELEASE_SHA_RE)
    namespace = value.object_storage_namespace
    if type(namespace) is not str or namespace not in PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES:
        _fail(f"{label} Object Storage namespace is invalid")
    route = _nonzero_sha256(value.route_commitment_sha256, label=f"{label} route commitment")
    four_role = _nonzero_sha256(value.four_role_binding_sha256, label=f"{label} four-role binding")
    if route == four_role:
        _fail(f"{label} route and four-role bindings overlap")
    recipient = _text(value.destination_age_recipient, label=f"{label} destination recipient", pattern=AGE_RECIPIENT_RE)
    if type(value.writer_term) is not PhysicalWalChunkedBaseBackupWriterTerm:
        _fail(f"{label} writer term is invalid")
    term = _term_from_mapping(_term_mapping(value.writer_term), label=label)
    if term.writer_holder_site != source:
        _fail(f"{label} writer holder does not match source site")
    if value.transport_plane != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSPORT_PLANE:
        _fail(f"{label} transport plane is invalid")
    if value.direct_webapp_transport != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_DIRECT_WEBAPP_TRANSPORT:
        _fail(f"{label} direct WebApp transport is not forbidden")
    return PhysicalWalChunkedBaseBackupBinding(
        source_site=source,
        destination_site=destination,
        campaign_id=campaign,
        release_sha=release,
        object_storage_namespace=namespace,
        route_commitment_sha256=route,
        four_role_binding_sha256=four_role,
        destination_age_recipient=recipient,
        writer_term=term,
    )


def build_physical_wal_chunked_base_backup_binding(
    *,
    source_site: str,
    destination_site: str,
    campaign_id: str,
    release_sha: str,
    object_storage_namespace: str,
    route_commitment_sha256: str,
    four_role_binding_sha256: str,
    destination_age_recipient: str,
    writer_holder_site: str,
    writer_epoch: int,
    writer_lease_id: str,
    witnessed_term_proof_sha256: str,
) -> PhysicalWalChunkedBaseBackupBinding:
    """Build one fully normalized mediated route binding without I/O."""

    return _normalise_binding(
        PhysicalWalChunkedBaseBackupBinding(
            source_site=source_site,
            destination_site=destination_site,
            campaign_id=campaign_id,
            release_sha=release_sha,
            object_storage_namespace=object_storage_namespace,
            route_commitment_sha256=route_commitment_sha256,
            four_role_binding_sha256=four_role_binding_sha256,
            destination_age_recipient=destination_age_recipient,
            writer_term=PhysicalWalChunkedBaseBackupWriterTerm(
                writer_holder_site=writer_holder_site,
                writer_epoch=writer_epoch,
                writer_lease_id=writer_lease_id,
                witnessed_term_proof_sha256=witnessed_term_proof_sha256,
            ),
        ),
        label="chunked base-backup binding",
    )


def _object_key(value: object, *, label: str) -> str:
    key = _text(value, label=label, pattern=OBJECT_KEY_RE)
    components = key.split("/")
    if (
        not key.endswith(".age")
        or ".." in components
        or any(
            component.casefold() in _MUTABLE_ALIAS_COMPONENTS
            or component.split(".", 1)[0].casefold() in _MUTABLE_ALIAS_COMPONENTS
            for component in components
        )
    ):
        _fail(f"{label} is a mutable alias")
    return key


def _version_id(value: object, *, label: str) -> str:
    version = _text(value, label=label, pattern=VERSION_ID_RE)
    if version.casefold() in _MUTABLE_ALIAS_COMPONENTS | {"null", "none"}:
        _fail(f"{label} is a mutable alias")
    return version


def derive_physical_wal_chunked_base_backup_chunk_key(
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    session_id: str,
    chunk_index: int,
    permit_nonce: str,
) -> str:
    """Derive the only legal immutable object key for one permit.

    A session-id digest avoids leaking an operational session identifier into
    an Object Storage listing while retaining a deterministic, route-bound
    unique path.  A nonce is mandatory even though an index is monotonic, so a
    retired/late permit can never be repurposed for a newly issued object.
    """

    normalized = _normalise_binding(binding, label="chunked base-backup binding")
    session = _id(session_id, label="chunked base-backup session ID")
    index = _chunk_index(chunk_index, label="chunked base-backup chunk index")
    nonce = _nonce(permit_nonce, label="chunked base-backup permit nonce")
    session_component = hashlib.sha256(session.encode("ascii")).hexdigest()
    key = (
        f"{normalized.object_storage_namespace}/{normalized.campaign_id}/{normalized.release_sha}/"
        f"base-backup-v2/{session_component}/chunks/{index:020d}-{nonce}.age"
    )
    return _object_key(key, label="chunked base-backup derived object key")


def _chunk_from_mapping(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupChunk:
    item = _exact_mapping(value, fields=_CHUNK_FIELDS, label=label)
    return _normalise_chunk(
        PhysicalWalChunkedBaseBackupChunk(
            index=item["index"],
            object_key=item["object_key"],
            version_id=item["version_id"],
            ciphertext_sha256=item["ciphertext_sha256"],
            ciphertext_bytes=item["ciphertext_bytes"],
            plaintext_sha256=item["plaintext_sha256"],
            plaintext_bytes=item["plaintext_bytes"],
            age_recipient=item["age_recipient"],
        ),
        label=label,
    )


def _chunk_mapping(value: PhysicalWalChunkedBaseBackupChunk) -> dict[str, Any]:
    return {
        "index": value.index,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "plaintext_sha256": value.plaintext_sha256,
        "plaintext_bytes": value.plaintext_bytes,
        "age_recipient": value.age_recipient,
    }


def _normalise_chunk(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupChunk:
    if type(value) is not PhysicalWalChunkedBaseBackupChunk:
        _fail(f"{label} is invalid")
    return PhysicalWalChunkedBaseBackupChunk(
        index=_chunk_index(value.index, label=f"{label} index"),
        object_key=_object_key(value.object_key, label=f"{label} object key"),
        version_id=_version_id(value.version_id, label=f"{label} version ID"),
        ciphertext_sha256=_nonzero_sha256(value.ciphertext_sha256, label=f"{label} ciphertext hash"),
        ciphertext_bytes=_positive_int(
            value.ciphertext_bytes,
            label=f"{label} ciphertext bytes",
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
        ),
        plaintext_sha256=_nonzero_sha256(value.plaintext_sha256, label=f"{label} plaintext hash"),
        plaintext_bytes=_positive_int(
            value.plaintext_bytes,
            label=f"{label} plaintext bytes",
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PLAINTEXT_BYTES,
        ),
        age_recipient=_text(value.age_recipient, label=f"{label} age recipient", pattern=AGE_RECIPIENT_RE),
    )


def _parse_session(value: object, *, expected_witness_public_key: bytes | None = None) -> _SessionFacts:
    payload, raw = _parse_canonical(value, label="chunked base-backup transfer session")
    session = _exact_mapping(payload, fields=_SESSION_FIELDS, label="chunked base-backup transfer session")
    if (
        session["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSFER_SESSION_SCHEMA
        or session["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or session["kind"] != "physical_wal_chunked_base_backup_transfer_session"
    ):
        _fail("chunked base-backup transfer session schema is invalid")
    binding = _binding_from_mapping(session["binding"], label="chunked base-backup transfer session")
    witness = _verify_signature(
        session,
        signature_field="witness_signature",
        signer_field="witness_signer",
        domain=_SESSION_DOMAIN,
        label="chunked base-backup transfer session",
        expected_key=expected_witness_public_key,
    )
    issued = _timestamp(session["issued_at"], label="chunked base-backup transfer session issued_at")
    expires = _timestamp(session["expires_at"], label="chunked base-backup transfer session expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SESSION_SECONDS):
        _fail("chunked base-backup transfer session lifetime is invalid")
    session_id = _id(session["session_id"], label="chunked base-backup transfer session ID")
    nonce = _nonce(session["session_nonce"], label="chunked base-backup transfer session nonce")
    if session_id == nonce:
        _fail("chunked base-backup transfer session identity reuses nonce")
    return _SessionFacts(raw, binding, session_id, nonce, issued, expires, witness)


def _parse_permit(value: object, *, expected_witness_public_key: bytes | None = None) -> _PermitFacts:
    payload, raw = _parse_canonical(value, label="chunked base-backup chunk permit")
    permit = _exact_mapping(payload, fields=_PERMIT_FIELDS, label="chunked base-backup chunk permit")
    if (
        permit["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PERMIT_SCHEMA
        or permit["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or permit["kind"] != "physical_wal_chunked_base_backup_chunk_permit"
    ):
        _fail("chunked base-backup chunk permit schema is invalid")
    binding = _binding_from_mapping(permit["binding"], label="chunked base-backup chunk permit")
    witness = _verify_signature(
        permit,
        signature_field="witness_signature",
        signer_field="witness_signer",
        domain=_PERMIT_DOMAIN,
        label="chunked base-backup chunk permit",
        expected_key=expected_witness_public_key,
    )
    issued = _timestamp(permit["issued_at"], label="chunked base-backup chunk permit issued_at")
    expires = _timestamp(permit["expires_at"], label="chunked base-backup chunk permit expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS):
        _fail("chunked base-backup chunk permit lifetime is invalid")
    session_id = _id(permit["session_id"], label="chunked base-backup chunk permit session ID")
    permit_id = _id(permit["permit_id"], label="chunked base-backup chunk permit ID")
    nonce = _nonce(permit["permit_nonce"], label="chunked base-backup chunk permit nonce")
    if len({session_id, permit_id, nonce}) != 3:
        _fail("chunked base-backup chunk permit identity reuses a session value")
    index = _chunk_index(permit["chunk_index"], label="chunked base-backup chunk permit index")
    object_key = _object_key(permit["object_key"], label="chunked base-backup chunk permit object key")
    expected_key = derive_physical_wal_chunked_base_backup_chunk_key(
        binding=binding, session_id=session_id, chunk_index=index, permit_nonce=nonce
    )
    if object_key != expected_key:
        _fail("chunked base-backup chunk permit object key is not its exact unique selector")
    return _PermitFacts(
        raw=raw,
        binding=binding,
        session_id=session_id,
        session_sha256=_nonzero_sha256(permit["session_sha256"], label="chunked base-backup chunk permit session hash"),
        permit_id=permit_id,
        permit_nonce=nonce,
        chunk_index=index,
        object_key=object_key,
        max_ciphertext_bytes=_positive_int(
            permit["max_ciphertext_bytes"],
            label="chunked base-backup chunk permit ciphertext cap",
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
        ),
        issued_at=issued,
        expires_at=expires,
        witness_public_key=witness,
    )


def _parse_completion(value: object, *, expected_source_public_key: bytes | None = None) -> _CompletionFacts:
    payload, raw = _parse_canonical(value, label="chunked base-backup chunk completion")
    completion = _exact_mapping(payload, fields=_COMPLETION_FIELDS, label="chunked base-backup chunk completion")
    if (
        completion["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMPLETION_SCHEMA
        or completion["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or completion["kind"] != "physical_wal_chunked_base_backup_chunk_completion"
    ):
        _fail("chunked base-backup chunk completion schema is invalid")
    binding = _binding_from_mapping(completion["binding"], label="chunked base-backup chunk completion")
    source = _verify_signature(
        completion,
        signature_field="source_signature",
        signer_field="source_signer",
        domain=_COMPLETION_DOMAIN,
        label="chunked base-backup chunk completion",
        expected_key=expected_source_public_key,
    )
    session_id = _id(completion["session_id"], label="chunked base-backup chunk completion session ID")
    permit_id = _id(completion["permit_id"], label="chunked base-backup chunk completion permit ID")
    completion_id = _id(completion["completion_id"], label="chunked base-backup chunk completion ID")
    nonce = _nonce(completion["completion_nonce"], label="chunked base-backup chunk completion nonce")
    if len({session_id, permit_id, completion_id, nonce}) != 4:
        _fail("chunked base-backup chunk completion identity reuses a prior value")
    return _CompletionFacts(
        raw=raw,
        binding=binding,
        session_id=session_id,
        session_sha256=_nonzero_sha256(completion["session_sha256"], label="chunked base-backup chunk completion session hash"),
        permit_id=permit_id,
        permit_nonce=_nonce(completion["permit_nonce"], label="chunked base-backup chunk completion permit nonce"),
        permit_sha256=_nonzero_sha256(completion["permit_sha256"], label="chunked base-backup chunk completion permit hash"),
        completion_id=completion_id,
        completion_nonce=nonce,
        completed_at=_timestamp(completion["completed_at"], label="chunked base-backup chunk completion completed_at"),
        chunk=_chunk_from_mapping(completion["chunk"], label="chunked base-backup chunk completion chunk"),
        source_public_key=source,
    )


def _parse_commitment(value: object, *, expected_witness_public_key: bytes | None = None) -> _CommitmentFacts:
    payload, raw = _parse_canonical(value, label="chunked base-backup chunk commitment")
    commitment = _exact_mapping(payload, fields=_COMMITMENT_FIELDS, label="chunked base-backup chunk commitment")
    if (
        commitment["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMMITMENT_SCHEMA
        or commitment["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or commitment["kind"] != "physical_wal_chunked_base_backup_witness_durable_chunk_commitment"
    ):
        _fail("chunked base-backup chunk commitment schema is invalid")
    binding = _binding_from_mapping(commitment["binding"], label="chunked base-backup chunk commitment")
    witness = _verify_signature(
        commitment,
        signature_field="witness_signature",
        signer_field="witness_signer",
        domain=_COMMITMENT_DOMAIN,
        label="chunked base-backup chunk commitment",
        expected_key=expected_witness_public_key,
    )
    session_id = _id(commitment["session_id"], label="chunked base-backup chunk commitment session ID")
    permit_id = _id(commitment["permit_id"], label="chunked base-backup chunk commitment permit ID")
    completion_id = _id(commitment["completion_id"], label="chunked base-backup chunk commitment completion ID")
    commitment_id = _id(commitment["commitment_id"], label="chunked base-backup chunk commitment ID")
    nonce = _nonce(commitment["commitment_nonce"], label="chunked base-backup chunk commitment nonce")
    ledger = _id(commitment["durable_ledger_entry_id"], label="chunked base-backup durable ledger entry ID")
    if len({session_id, permit_id, completion_id, commitment_id, nonce, ledger}) != 6:
        _fail("chunked base-backup chunk commitment identity reuses a prior value")
    return _CommitmentFacts(
        raw=raw,
        binding=binding,
        session_id=session_id,
        session_sha256=_nonzero_sha256(commitment["session_sha256"], label="chunked base-backup chunk commitment session hash"),
        permit_id=permit_id,
        permit_sha256=_nonzero_sha256(commitment["permit_sha256"], label="chunked base-backup chunk commitment permit hash"),
        completion_id=completion_id,
        completion_sha256=_nonzero_sha256(commitment["completion_sha256"], label="chunked base-backup chunk commitment completion hash"),
        commitment_id=commitment_id,
        commitment_nonce=nonce,
        durable_ledger_entry_id=ledger,
        committed_at=_timestamp(commitment["committed_at"], label="chunked base-backup chunk commitment committed_at"),
        chunk=_chunk_from_mapping(commitment["chunk"], label="chunked base-backup chunk commitment chunk"),
        witness_public_key=witness,
    )


def _parse_finalization(value: object, *, expected_witness_public_key: bytes | None = None) -> _FinalizationFacts:
    payload, raw = _parse_canonical(value, label="chunked base-backup finalization permit")
    permit = _exact_mapping(payload, fields=_FINALIZATION_FIELDS, label="chunked base-backup finalization permit")
    if (
        permit["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FINALIZATION_PERMIT_SCHEMA
        or permit["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or permit["kind"] != "physical_wal_chunked_base_backup_finalization_permit"
    ):
        _fail("chunked base-backup finalization permit schema is invalid")
    binding = _binding_from_mapping(permit["binding"], label="chunked base-backup finalization permit")
    witness = _verify_signature(
        permit,
        signature_field="witness_signature",
        signer_field="witness_signer",
        domain=_FINALIZATION_DOMAIN,
        label="chunked base-backup finalization permit",
        expected_key=expected_witness_public_key,
    )
    issued = _timestamp(permit["issued_at"], label="chunked base-backup finalization permit issued_at")
    expires = _timestamp(permit["expires_at"], label="chunked base-backup finalization permit expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS):
        _fail("chunked base-backup finalization permit lifetime is invalid")
    session_id = _id(permit["session_id"], label="chunked base-backup finalization permit session ID")
    permit_id = _id(permit["finalization_permit_id"], label="chunked base-backup finalization permit ID")
    nonce = _nonce(permit["finalization_permit_nonce"], label="chunked base-backup finalization permit nonce")
    if len({session_id, permit_id, nonce}) != 3:
        _fail("chunked base-backup finalization permit identity reuses a session value")
    return _FinalizationFacts(
        raw=raw,
        binding=binding,
        session_id=session_id,
        session_sha256=_nonzero_sha256(permit["session_sha256"], label="chunked base-backup finalization permit session hash"),
        finalization_permit_id=permit_id,
        finalization_permit_nonce=nonce,
        committed_chunk_set_sha256=_nonzero_sha256(
            permit["committed_chunk_set_sha256"], label="chunked base-backup finalization permit committed set hash"
        ),
        committed_chunk_count=_positive_int(
            permit["committed_chunk_count"],
            label="chunked base-backup finalization permit chunk count",
            maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
        ),
        total_plaintext_sha256=_nonzero_sha256(
            permit["total_plaintext_sha256"], label="chunked base-backup finalization permit total plaintext hash"
        ),
        total_plaintext_bytes=_positive_int(
            permit["total_plaintext_bytes"],
            label="chunked base-backup finalization permit total plaintext bytes",
            maximum=2**63 - 1,
        ),
        issued_at=issued,
        expires_at=expires,
        witness_public_key=witness,
    )


def _assert_not_future(value: datetime, *, now: datetime, label: str) -> None:
    if value > now + timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FUTURE_SKEW_SECONDS):
        _fail(f"{label} is from the future")


def _assert_live(issued: datetime, expires: datetime, *, now: datetime, label: str) -> None:
    _assert_not_future(issued, now=now, label=label)
    if now > expires:
        _fail(f"{label} is expired")


def _values(value: object, *, label: str, validator) -> frozenset[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        _fail(f"{label} replay set is invalid")
    return frozenset(validator(item, label=f"{label} value") for item in value)


def _index_values(value: object, *, label: str) -> frozenset[int]:
    return _values(value, label=label, validator=_chunk_index)


def _assert_distinct_observed(
    *,
    value: str,
    observed: object,
    label: str,
    validator,
) -> None:
    if value in _values(observed, label=label, validator=validator):
        _fail(f"{label} was replayed")


def _session_from_verified(
    value: object,
    *,
    now: datetime,
    require_live: bool,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupTransferSession, _SessionFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupTransferSession
        or value._capability is not _VERIFIED_SESSION_CAPABILITY
    ):
        _fail("verified chunked base-backup transfer session capability is required")
    facts = _parse_session(value.canonical_session, expected_witness_public_key=value.witness_public_key)
    if (
        facts.binding != value.binding
        or facts.session_id != value.session_id
        or facts.session_nonce != value.session_nonce
        or facts.issued_at != value.issued_at
        or facts.expires_at != value.expires_at
    ):
        _fail("verified chunked base-backup transfer session was tampered")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    if require_live:
        _assert_live(facts.issued_at, facts.expires_at, now=observed_now, label="chunked base-backup transfer session")
    return value, facts


def _permit_from_verified(
    value: object,
    *,
    now: datetime,
    require_live: bool,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupChunkPermit, _PermitFacts, _SessionFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupChunkPermit
        or value._capability is not _VERIFIED_PERMIT_CAPABILITY
    ):
        _fail("verified chunked base-backup chunk permit capability is required")
    session, session_facts = _session_from_verified(value.session, now=now, require_live=require_live)
    facts = _parse_permit(value.canonical_permit, expected_witness_public_key=session_facts.witness_public_key)
    if (
        facts.binding != session_facts.binding
        or facts.session_id != session_facts.session_id
        or facts.session_sha256 != hashlib.sha256(session_facts.raw).hexdigest()
        or facts.permit_id != value.permit_id
        or facts.permit_nonce != value.permit_nonce
        or facts.chunk_index != value.chunk_index
        or facts.object_key != value.object_key
        or facts.max_ciphertext_bytes != value.max_ciphertext_bytes
        or facts.issued_at != value.issued_at
        or facts.expires_at != value.expires_at
        or facts.witness_public_key != value.witness_public_key
    ):
        _fail("verified chunked base-backup chunk permit was tampered")
    if facts.issued_at < session_facts.issued_at or facts.expires_at > session_facts.expires_at:
        _fail("verified chunked base-backup chunk permit falls outside its session")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    if require_live:
        _assert_live(facts.issued_at, facts.expires_at, now=observed_now, label="chunked base-backup chunk permit")
    return value, facts, session_facts


def _completion_from_verified(
    value: object,
    *,
    now: datetime,
    require_permit_live: bool,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCompletion, _CompletionFacts, _PermitFacts, _SessionFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupChunkCompletion
        or value._capability is not _VERIFIED_COMPLETION_CAPABILITY
    ):
        _fail("verified chunked base-backup chunk completion capability is required")
    permit, permit_facts, session_facts = _permit_from_verified(
        value.permit, now=now, require_live=require_permit_live
    )
    facts = _parse_completion(value.canonical_completion, expected_source_public_key=value.source_public_key)
    _assert_completion_matches_permit(facts, permit_facts, session_facts)
    if (
        facts.completion_id != value.completion_id
        or facts.completion_nonce != value.completion_nonce
        or facts.completed_at != value.completed_at
        or facts.chunk != value.chunk
        or facts.source_public_key != value.source_public_key
    ):
        _fail("verified chunked base-backup chunk completion was tampered")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    _assert_not_future(facts.completed_at, now=observed_now, label="chunked base-backup chunk completion")
    return value, facts, permit_facts, session_facts


def _assert_completion_matches_permit(
    completion: _CompletionFacts,
    permit: _PermitFacts,
    session: _SessionFacts,
) -> None:
    if (
        completion.binding != session.binding
        or completion.session_id != session.session_id
        or completion.session_sha256 != hashlib.sha256(session.raw).hexdigest()
        or completion.permit_id != permit.permit_id
        or completion.permit_nonce != permit.permit_nonce
        or completion.permit_sha256 != hashlib.sha256(permit.raw).hexdigest()
    ):
        _fail("chunked base-backup completion does not bind its exact permit and session")
    if completion.completed_at < permit.issued_at or completion.completed_at > permit.expires_at:
        _fail("chunked base-backup completion is outside its permit deadline")
    if (
        completion.chunk.index != permit.chunk_index
        or completion.chunk.object_key != permit.object_key
        or completion.chunk.ciphertext_bytes > permit.max_ciphertext_bytes
        or completion.chunk.age_recipient != session.binding.destination_age_recipient
    ):
        _fail("chunked base-backup completion chunk is foreign to its permit")


def _commitment_from_verified(
    value: object,
    *,
    now: datetime,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, _CommitmentFacts, _CompletionFacts, _PermitFacts, _SessionFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupChunkCommitment
        or value._capability is not _VERIFIED_COMMITMENT_CAPABILITY
    ):
        _fail("verified chunked base-backup chunk commitment capability is required")
    # A durable commitment remains useful after its per-chunk permit expires.
    # Revalidate the completion at the recorded commitment time, never at a
    # later finalization time; this proves the Witness accepted it while live.
    commitment_payload, _raw = _parse_canonical(value.canonical_commitment, label="chunked base-backup chunk commitment")
    committed_at = _timestamp(commitment_payload.get("committed_at"), label="chunked base-backup chunk commitment committed_at")
    completion, completion_facts, permit_facts, session_facts = _completion_from_verified(
        value.completion, now=committed_at, require_permit_live=True
    )
    facts = _parse_commitment(
        value.canonical_commitment, expected_witness_public_key=permit_facts.witness_public_key
    )
    _assert_commitment_matches_completion(facts, completion_facts, permit_facts, session_facts)
    if (
        facts.commitment_id != value.commitment_id
        or facts.commitment_nonce != value.commitment_nonce
        or facts.durable_ledger_entry_id != value.durable_ledger_entry_id
        or facts.committed_at != value.committed_at
        or facts.chunk != value.chunk
        or facts.witness_public_key != value.witness_public_key
    ):
        _fail("verified chunked base-backup chunk commitment was tampered")
    _assert_not_future(facts.committed_at, now=_utc(now, label="chunked base-backup verification clock"), label="chunked base-backup chunk commitment")
    return value, facts, completion_facts, permit_facts, session_facts


def _assert_commitment_matches_completion(
    commitment: _CommitmentFacts,
    completion: _CompletionFacts,
    permit: _PermitFacts,
    session: _SessionFacts,
) -> None:
    if (
        commitment.binding != session.binding
        or commitment.session_id != session.session_id
        or commitment.session_sha256 != hashlib.sha256(session.raw).hexdigest()
        or commitment.permit_id != permit.permit_id
        or commitment.permit_sha256 != hashlib.sha256(permit.raw).hexdigest()
        or commitment.completion_id != completion.completion_id
        or commitment.completion_sha256 != hashlib.sha256(completion.raw).hexdigest()
        or commitment.chunk != completion.chunk
    ):
        _fail("chunked base-backup Witness commitment does not bind exact accepted completion")
    if commitment.committed_at < completion.completed_at or commitment.committed_at > permit.expires_at:
        _fail("chunked base-backup Witness commitment is outside permit deadline")


def build_physical_wal_chunked_base_backup_transfer_session(
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    session_id: str,
    session_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a Witness-signed opaque session; it does not allocate storage."""

    normalized = _normalise_binding(binding, label="chunked base-backup binding")
    session = _id(session_id, label="chunked base-backup transfer session ID")
    nonce = _nonce(session_nonce, label="chunked base-backup transfer session nonce")
    if session == nonce:
        _fail("chunked base-backup transfer session identity reuses nonce")
    issued_text = _timestamp_text(issued_at, label="chunked base-backup transfer session issued_at")
    expires_text = _timestamp_text(expires_at, label="chunked base-backup transfer session expires_at")
    issued = _timestamp(issued_text, label="chunked base-backup transfer session issued_at")
    expires = _timestamp(expires_text, label="chunked base-backup transfer session expires_at")
    if expires <= issued or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SESSION_SECONDS):
        _fail("chunked base-backup transfer session lifetime is invalid")
    _private, _public, signer = _signer_from_private(witness_signer, label="chunked base-backup transfer session")
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_TRANSFER_SESSION_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_transfer_session",
        "binding": _binding_mapping(normalized),
        "session_id": session,
        "session_nonce": nonce,
        "issued_at": issued_text,
        "expires_at": expires_text,
        "witness_signer": signer,
    }
    return {**unsigned, "witness_signature": _sign(unsigned, domain=_SESSION_DOMAIN, signer=witness_signer, label="chunked base-backup transfer session")}


def canonical_physical_wal_chunked_base_backup_transfer_session_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    """Return canonical verified session bytes without I/O."""

    return _parse_session(value).raw


def verify_physical_wal_chunked_base_backup_transfer_session(
    *,
    transfer_session: Mapping[str, Any] | bytes,
    expected_binding: PhysicalWalChunkedBaseBackupBinding,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_session_ids: Collection[str] = (),
    consumed_session_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupTransferSession:
    """Verify a live exact session.  Ledger observations remain caller-owned."""

    binding = _normalise_binding(expected_binding, label="expected chunked base-backup binding")
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    facts = _parse_session(transfer_session, expected_witness_public_key=witness)
    if facts.binding != binding:
        _fail("chunked base-backup transfer session route, four-role binding, recipient, or term is foreign")
    _assert_live(facts.issued_at, facts.expires_at, now=observed_now, label="chunked base-backup transfer session")
    _assert_distinct_observed(value=facts.session_id, observed=consumed_session_ids, label="chunked base-backup transfer session ID", validator=_id)
    _assert_distinct_observed(value=facts.session_nonce, observed=consumed_session_nonces, label="chunked base-backup transfer session nonce", validator=_nonce)
    result = VerifiedPhysicalWalChunkedBaseBackupTransferSession(
        canonical_session=facts.raw,
        binding=binding,
        session_id=facts.session_id,
        session_nonce=facts.session_nonce,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_SESSION_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_transfer_session(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupTransferSession:
    """Revalidate a live opaque session, without consuming it."""

    session, _facts = _session_from_verified(value, now=now, require_live=True)
    return session


def build_physical_wal_chunked_base_backup_chunk_permit(
    *,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    permit_id: str,
    permit_nonce: str,
    chunk_index: int,
    max_ciphertext_bytes: int,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a single short-lived Witness permit with an exact object key."""

    session, facts = _session_from_verified(transfer_session, now=issued_at, require_live=True)
    permit = _id(permit_id, label="chunked base-backup chunk permit ID")
    nonce = _nonce(permit_nonce, label="chunked base-backup chunk permit nonce")
    index = _chunk_index(chunk_index, label="chunked base-backup chunk permit index")
    if len({facts.session_id, permit, nonce}) != 3:
        _fail("chunked base-backup chunk permit identity reuses a session value")
    cap = _positive_int(
        max_ciphertext_bytes,
        label="chunked base-backup chunk permit ciphertext cap",
        maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_CIPHERTEXT_BYTES,
    )
    issued_text = _timestamp_text(issued_at, label="chunked base-backup chunk permit issued_at")
    expires_text = _timestamp_text(expires_at, label="chunked base-backup chunk permit expires_at")
    issued = _timestamp(issued_text, label="chunked base-backup chunk permit issued_at")
    expires = _timestamp(expires_text, label="chunked base-backup chunk permit expires_at")
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS)
        or issued < facts.issued_at
        or expires > facts.expires_at
    ):
        _fail("chunked base-backup chunk permit lifetime is invalid")
    _private, public, signer = _signer_from_private(witness_signer, label="chunked base-backup chunk permit")
    if public != facts.witness_public_key:
        _fail("chunked base-backup chunk permit signer does not match transfer session Witness")
    object_key = derive_physical_wal_chunked_base_backup_chunk_key(
        binding=facts.binding, session_id=facts.session_id, chunk_index=index, permit_nonce=nonce
    )
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_PERMIT_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_chunk_permit",
        "binding": _binding_mapping(facts.binding),
        "session_id": facts.session_id,
        "session_sha256": hashlib.sha256(facts.raw).hexdigest(),
        "permit_id": permit,
        "permit_nonce": nonce,
        "chunk_index": index,
        "object_key": object_key,
        "max_ciphertext_bytes": cap,
        "issued_at": issued_text,
        "expires_at": expires_text,
        "witness_signer": signer,
    }
    return {**unsigned, "witness_signature": _sign(unsigned, domain=_PERMIT_DOMAIN, signer=witness_signer, label="chunked base-backup chunk permit")}


def canonical_physical_wal_chunked_base_backup_chunk_permit_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    return _parse_permit(value).raw


def verify_physical_wal_chunked_base_backup_chunk_permit(
    *,
    chunk_permit: Mapping[str, Any] | bytes,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_permit_ids: Collection[str] = (),
    consumed_permit_nonces: Collection[str] = (),
    reserved_chunk_indexes: Collection[int] = (),
    expected_next_chunk_index: int | None = None,
) -> VerifiedPhysicalWalChunkedBaseBackupChunkPermit:
    """Verify a live exact chunk permit before any side effect starts.

    ``reserved_chunk_indexes`` and ``expected_next_chunk_index`` are views of
    the Witness durable issuance ledger.  The pure function deliberately does
    not pretend to persist them.
    """

    session, session_facts = _session_from_verified(transfer_session, now=now, require_live=True)
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    if witness != session_facts.witness_public_key:
        _fail("expected Witness key does not match transfer session")
    facts = _parse_permit(chunk_permit, expected_witness_public_key=witness)
    if (
        facts.binding != session_facts.binding
        or facts.session_id != session_facts.session_id
        or facts.session_sha256 != hashlib.sha256(session_facts.raw).hexdigest()
        or facts.issued_at < session_facts.issued_at
        or facts.expires_at > session_facts.expires_at
    ):
        _fail("chunked base-backup chunk permit route, session, term, or recipient is foreign")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    _assert_live(facts.issued_at, facts.expires_at, now=observed_now, label="chunked base-backup chunk permit")
    _assert_distinct_observed(value=facts.permit_id, observed=consumed_permit_ids, label="chunked base-backup chunk permit ID", validator=_id)
    _assert_distinct_observed(value=facts.permit_nonce, observed=consumed_permit_nonces, label="chunked base-backup chunk permit nonce", validator=_nonce)
    if facts.chunk_index in _index_values(reserved_chunk_indexes, label="chunked base-backup chunk index"):
        _fail("chunked base-backup chunk index is already reserved")
    if expected_next_chunk_index is not None and facts.chunk_index != _chunk_index(
        expected_next_chunk_index, label="expected chunked base-backup next chunk index"
    ):
        _fail("chunked base-backup chunk index is not monotonic")
    result = VerifiedPhysicalWalChunkedBaseBackupChunkPermit(
        canonical_permit=facts.raw,
        session=session,
        permit_id=facts.permit_id,
        permit_nonce=facts.permit_nonce,
        chunk_index=facts.chunk_index,
        object_key=facts.object_key,
        max_ciphertext_bytes=facts.max_ciphertext_bytes,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_PERMIT_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_chunk_permit(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupChunkPermit:
    permit, _facts, _session = _permit_from_verified(value, now=now, require_live=True)
    return permit


def build_physical_wal_chunked_base_backup_chunk_completion(
    *,
    chunk_permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    completion_id: str,
    completion_nonce: str,
    completed_at: datetime,
    chunk: PhysicalWalChunkedBaseBackupChunk,
    source_signer: object,
) -> dict[str, Any]:
    """Build a source completion claim before Witness durable acceptance."""

    permit, permit_facts, session_facts = _permit_from_verified(
        chunk_permit, now=completed_at, require_live=True
    )
    completion = _id(completion_id, label="chunked base-backup chunk completion ID")
    nonce = _nonce(completion_nonce, label="chunked base-backup chunk completion nonce")
    if len({session_facts.session_id, permit_facts.permit_id, completion, nonce}) != 4:
        _fail("chunked base-backup chunk completion identity reuses a prior value")
    completed_text = _timestamp_text(completed_at, label="chunked base-backup chunk completion completed_at")
    normalized_chunk = _normalise_chunk(chunk, label="chunked base-backup completion chunk")
    draft = _CompletionFacts(
        raw=b"",
        binding=session_facts.binding,
        session_id=session_facts.session_id,
        session_sha256=hashlib.sha256(session_facts.raw).hexdigest(),
        permit_id=permit_facts.permit_id,
        permit_nonce=permit_facts.permit_nonce,
        permit_sha256=hashlib.sha256(permit_facts.raw).hexdigest(),
        completion_id=completion,
        completion_nonce=nonce,
        completed_at=_timestamp(completed_text, label="chunked base-backup chunk completion completed_at"),
        chunk=normalized_chunk,
        source_public_key=b"",
    )
    _assert_completion_matches_permit(draft, permit_facts, session_facts)
    _private, _public, signer = _signer_from_private(source_signer, label="chunked base-backup chunk completion")
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMPLETION_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_chunk_completion",
        "binding": _binding_mapping(session_facts.binding),
        "session_id": session_facts.session_id,
        "session_sha256": hashlib.sha256(session_facts.raw).hexdigest(),
        "permit_id": permit_facts.permit_id,
        "permit_nonce": permit_facts.permit_nonce,
        "permit_sha256": hashlib.sha256(permit_facts.raw).hexdigest(),
        "completion_id": completion,
        "completion_nonce": nonce,
        "completed_at": completed_text,
        "chunk": _chunk_mapping(normalized_chunk),
        "source_signer": signer,
    }
    return {**unsigned, "source_signature": _sign(unsigned, domain=_COMPLETION_DOMAIN, signer=source_signer, label="chunked base-backup chunk completion")}


def canonical_physical_wal_chunked_base_backup_chunk_completion_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    return _parse_completion(value).raw


def verify_physical_wal_chunked_base_backup_chunk_completion(
    *,
    chunk_completion: Mapping[str, Any] | bytes,
    chunk_permit: VerifiedPhysicalWalChunkedBaseBackupChunkPermit,
    expected_source_public_key: bytes,
    now: datetime,
    consumed_completion_ids: Collection[str] = (),
    consumed_completion_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupChunkCompletion:
    """Verify a source completion while its exact permit remains live."""

    permit, permit_facts, session_facts = _permit_from_verified(chunk_permit, now=now, require_live=True)
    source = _public_key(expected_source_public_key, label="expected source public key")
    facts = _parse_completion(chunk_completion, expected_source_public_key=source)
    _assert_completion_matches_permit(facts, permit_facts, session_facts)
    observed_now = _utc(now, label="chunked base-backup verification clock")
    _assert_not_future(facts.completed_at, now=observed_now, label="chunked base-backup chunk completion")
    _assert_distinct_observed(value=facts.completion_id, observed=consumed_completion_ids, label="chunked base-backup chunk completion ID", validator=_id)
    _assert_distinct_observed(value=facts.completion_nonce, observed=consumed_completion_nonces, label="chunked base-backup chunk completion nonce", validator=_nonce)
    result = VerifiedPhysicalWalChunkedBaseBackupChunkCompletion(
        canonical_completion=facts.raw,
        permit=permit,
        completion_id=facts.completion_id,
        completion_nonce=facts.completion_nonce,
        completed_at=facts.completed_at,
        chunk=facts.chunk,
        source_public_key=source,
    )
    object.__setattr__(result, "_capability", _VERIFIED_COMPLETION_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_chunk_completion(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupChunkCompletion:
    completion, _facts, _permit, _session = _completion_from_verified(
        value, now=now, require_permit_live=True
    )
    return completion


def build_physical_wal_chunked_base_backup_witness_chunk_commitment(
    *,
    chunk_completion: VerifiedPhysicalWalChunkedBaseBackupChunkCompletion,
    commitment_id: str,
    commitment_nonce: str,
    durable_ledger_entry_id: str,
    committed_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a Witness acceptance only at the durable-ledger boundary.

    The caller is the future Witness runtime and MUST call this only after the
    matching ledger row is durably appended.  This pure function binds the row
    identifier and rejects signing if the per-chunk permit has already ended.
    """

    completion, completion_facts, permit_facts, session_facts = _completion_from_verified(
        chunk_completion, now=committed_at, require_permit_live=True
    )
    commitment = _id(commitment_id, label="chunked base-backup chunk commitment ID")
    nonce = _nonce(commitment_nonce, label="chunked base-backup chunk commitment nonce")
    ledger = _id(durable_ledger_entry_id, label="chunked base-backup durable ledger entry ID")
    if len({session_facts.session_id, permit_facts.permit_id, completion_facts.completion_id, commitment, nonce, ledger}) != 6:
        _fail("chunked base-backup chunk commitment identity reuses a prior value")
    committed_text = _timestamp_text(committed_at, label="chunked base-backup chunk commitment committed_at")
    committed = _timestamp(committed_text, label="chunked base-backup chunk commitment committed_at")
    if committed < completion_facts.completed_at or committed > permit_facts.expires_at:
        _fail("chunked base-backup Witness commitment is outside permit deadline")
    _private, public, signer = _signer_from_private(witness_signer, label="chunked base-backup chunk commitment")
    if public != permit_facts.witness_public_key:
        _fail("chunked base-backup Witness commitment signer does not match permit Witness")
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNK_COMMITMENT_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_witness_durable_chunk_commitment",
        "binding": _binding_mapping(session_facts.binding),
        "session_id": session_facts.session_id,
        "session_sha256": hashlib.sha256(session_facts.raw).hexdigest(),
        "permit_id": permit_facts.permit_id,
        "permit_sha256": hashlib.sha256(permit_facts.raw).hexdigest(),
        "completion_id": completion_facts.completion_id,
        "completion_sha256": hashlib.sha256(completion_facts.raw).hexdigest(),
        "commitment_id": commitment,
        "commitment_nonce": nonce,
        "durable_ledger_entry_id": ledger,
        "committed_at": committed_text,
        "chunk": _chunk_mapping(completion_facts.chunk),
        "witness_signer": signer,
    }
    return {**unsigned, "witness_signature": _sign(unsigned, domain=_COMMITMENT_DOMAIN, signer=witness_signer, label="chunked base-backup chunk commitment")}


def canonical_physical_wal_chunked_base_backup_chunk_commitment_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    return _parse_commitment(value).raw


def verify_physical_wal_chunked_base_backup_chunk_commitment(
    *,
    chunk_commitment: Mapping[str, Any] | bytes,
    chunk_completion: VerifiedPhysicalWalChunkedBaseBackupChunkCompletion,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_commitment_ids: Collection[str] = (),
    consumed_commitment_nonces: Collection[str] = (),
    committed_chunk_indexes: Collection[int] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupChunkCommitment:
    """Verify signed durable Witness acceptance; late completions fail closed."""

    # Parse commitment first to obtain its historic acceptance time.  We then
    # revalidate the completion at that exact time, so accepted work remains
    # usable after a permit expires without accepting a late object.
    payload, _raw = _parse_canonical(chunk_commitment, label="chunked base-backup chunk commitment")
    committed_at = _timestamp(payload.get("committed_at"), label="chunked base-backup chunk commitment committed_at")
    completion, completion_facts, permit_facts, session_facts = _completion_from_verified(
        chunk_completion, now=committed_at, require_permit_live=True
    )
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    if witness != permit_facts.witness_public_key:
        _fail("expected Witness key does not match chunk permit")
    facts = _parse_commitment(chunk_commitment, expected_witness_public_key=witness)
    _assert_commitment_matches_completion(facts, completion_facts, permit_facts, session_facts)
    _assert_not_future(facts.committed_at, now=_utc(now, label="chunked base-backup verification clock"), label="chunked base-backup chunk commitment")
    _assert_distinct_observed(value=facts.commitment_id, observed=consumed_commitment_ids, label="chunked base-backup chunk commitment ID", validator=_id)
    _assert_distinct_observed(value=facts.commitment_nonce, observed=consumed_commitment_nonces, label="chunked base-backup chunk commitment nonce", validator=_nonce)
    if facts.chunk.index in _index_values(committed_chunk_indexes, label="chunked base-backup committed chunk index"):
        _fail("chunked base-backup chunk index already has durable Witness acceptance")
    result = VerifiedPhysicalWalChunkedBaseBackupChunkCommitment(
        canonical_commitment=facts.raw,
        completion=completion,
        commitment_id=facts.commitment_id,
        commitment_nonce=facts.commitment_nonce,
        durable_ledger_entry_id=facts.durable_ledger_entry_id,
        committed_at=facts.committed_at,
        chunk=facts.chunk,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_COMMITMENT_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_chunk_commitment(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupChunkCommitment:
    commitment, _facts, _completion, _permit, _session = _commitment_from_verified(value, now=now)
    return commitment


def _normalise_commitment_sequence(
    value: object,
    *,
    now: datetime,
    label: str,
) -> tuple[tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, ...], _SessionFacts]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(f"{label} is invalid")
    if not value or len(value) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail(f"{label} is invalid")
    observed: list[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment] = []
    session_facts: _SessionFacts | None = None
    prior_index = -1
    commitments: set[str] = set()
    selectors: set[tuple[str, str]] = set()
    for expected_index, item in enumerate(value):
        commitment, facts, _completion, _permit, session = _commitment_from_verified(item, now=now)
        if session_facts is None:
            session_facts = session
        elif (
            session.raw != session_facts.raw
            or session.binding != session_facts.binding
            or session.witness_public_key != session_facts.witness_public_key
        ):
            _fail(f"{label} spans multiple sessions or routes")
        if facts.chunk.index != expected_index or facts.chunk.index <= prior_index:
            _fail(f"{label} is not ordered contiguous indexes 0..n-1")
        prior_index = facts.chunk.index
        commitment_hash = hashlib.sha256(facts.raw).hexdigest()
        selector = (facts.chunk.object_key, facts.chunk.version_id)
        if commitment_hash in commitments or selector in selectors:
            _fail(f"{label} has duplicate immutable chunk evidence")
        commitments.add(commitment_hash)
        selectors.add(selector)
        observed.append(commitment)
    assert session_facts is not None
    return tuple(observed), session_facts


def _committed_chunk_set_sha256(
    *,
    normalized: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, ...],
    session: _SessionFacts,
) -> str:
    """Hash a previously validated contiguous set without caller input."""

    payload = {
        "schema": "gold-trade-physical-wal-chunked-base-backup-committed-chunk-set-v2",
        "session_sha256": hashlib.sha256(session.raw).hexdigest(),
        "binding": _binding_mapping(session.binding),
        "chunks": [
            {
                "index": item.chunk.index,
                "commitment_id": item.commitment_id,
                "commitment_sha256": hashlib.sha256(item.canonical_commitment).hexdigest(),
                "object_key": item.chunk.object_key,
                "version_id": item.chunk.version_id,
                "ciphertext_sha256": item.chunk.ciphertext_sha256,
                "ciphertext_bytes": item.chunk.ciphertext_bytes,
                "plaintext_sha256": item.chunk.plaintext_sha256,
                "plaintext_bytes": item.chunk.plaintext_bytes,
                "age_recipient": item.chunk.age_recipient,
            }
            for item in normalized
        ],
    }
    return hashlib.sha256(_canonical(payload, label="chunked base-backup committed chunk set")).hexdigest()


def begin_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
    *,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet:
    """Start an opaque Witness-owned accepted-commit state at index zero.

    A production Witness creates this state alongside its durable session row.
    It intentionally contains no generic ``add`` operation: the only
    successor constructor is ``append_*`` below, which accepts a verified
    Witness commitment and checks the exact next index.
    """

    session, facts = _session_from_verified(transfer_session, now=now, require_live=True)
    digest = _committed_chunk_set_sha256(normalized=(), session=facts)
    result = VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet(
        transfer_session=session,
        committed_chunks=(),
        committed_chunk_set_sha256=digest,
        next_chunk_index=0,
    )
    object.__setattr__(result, "_capability", _VERIFIED_ACCEPTED_CHUNK_SET_CAPABILITY)
    return result


def _accepted_chunk_set_from_verified(
    value: object,
    *,
    now: datetime,
    require_session_live: bool,
) -> tuple[VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet, _SessionFacts]:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet
        or value._capability is not _VERIFIED_ACCEPTED_CHUNK_SET_CAPABILITY
    ):
        _fail("verified chunked base-backup Witness accepted-chunk-set capability is required")
    session, session_facts = _session_from_verified(
        value.transfer_session, now=now, require_live=require_session_live
    )
    if type(value.committed_chunks) is not tuple:
        _fail("verified chunked base-backup Witness accepted-chunk-set was tampered")
    if not value.committed_chunks:
        normalized: tuple[VerifiedPhysicalWalChunkedBaseBackupChunkCommitment, ...] = ()
    else:
        normalized, chunks_session = _normalise_commitment_sequence(
            value.committed_chunks,
            now=now,
            label="verified chunked base-backup Witness accepted-chunk-set",
        )
        if chunks_session.raw != session_facts.raw:
            _fail("verified chunked base-backup Witness accepted-chunk-set spans a foreign session")
    if type(value.next_chunk_index) is not int or value.next_chunk_index != len(normalized):
        _fail("verified chunked base-backup Witness accepted-chunk-set index was tampered")
    if value.next_chunk_index > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail("verified chunked base-backup Witness accepted-chunk-set is too large")
    expected_digest = _committed_chunk_set_sha256(normalized=normalized, session=session_facts)
    if value.committed_chunk_set_sha256 != expected_digest:
        _fail("verified chunked base-backup Witness accepted-chunk-set digest was tampered")
    return value, session_facts


def require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet:
    """Revalidate opaque accepted state; it is not a generic caller list."""

    state, _facts = _accepted_chunk_set_from_verified(value, now=now, require_session_live=True)
    return state


def append_physical_wal_chunked_base_backup_witness_accepted_chunk(
    *,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    chunk_commitment: VerifiedPhysicalWalChunkedBaseBackupChunkCommitment,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet:
    """Advance Witness state by exactly one durable accepted next chunk.

    This is a pure transition.  The future Witness implementation must perform
    its durable nonce/index-ledger transaction before returning this successor
    to any finalization path.
    """

    state, session_facts = _accepted_chunk_set_from_verified(
        accepted_chunk_set, now=now, require_session_live=True
    )
    commitment, commitment_facts, _completion, _permit, commitment_session = _commitment_from_verified(
        chunk_commitment, now=now
    )
    if commitment_session.raw != session_facts.raw:
        _fail("chunked base-backup Witness commitment belongs to a foreign session")
    if commitment_facts.chunk.index != state.next_chunk_index:
        _fail("chunked base-backup Witness commitment is not the next contiguous accepted index")
    successor_chunks = state.committed_chunks + (commitment,)
    successor_digest = _committed_chunk_set_sha256(
        normalized=successor_chunks, session=session_facts
    )
    result = VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet(
        transfer_session=state.transfer_session,
        committed_chunks=successor_chunks,
        committed_chunk_set_sha256=successor_digest,
        next_chunk_index=state.next_chunk_index + 1,
    )
    object.__setattr__(result, "_capability", _VERIFIED_ACCEPTED_CHUNK_SET_CAPABILITY)
    return result


def derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
    *,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    now: datetime,
) -> str:
    """Return the digest from an opaque Witness-owned contiguous state."""

    state, _session = _accepted_chunk_set_from_verified(
        accepted_chunk_set, now=now, require_session_live=True
    )
    if not state.committed_chunks:
        _fail("chunked base-backup accepted chunk set has no committed chunks")
    return state.committed_chunk_set_sha256


def build_physical_wal_chunked_base_backup_finalization_permit(
    *,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    finalization_permit_id: str,
    finalization_permit_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    total_plaintext_sha256: str,
    total_plaintext_bytes: int,
    witness_signer: object,
) -> dict[str, Any]:
    """Pin a contiguous durable chunk set with one fresh Witness permit."""

    session, session_facts = _session_from_verified(transfer_session, now=issued_at, require_live=True)
    accepted, accepted_session = _accepted_chunk_set_from_verified(
        accepted_chunk_set, now=issued_at, require_session_live=True
    )
    if accepted_session.raw != session_facts.raw or not accepted.committed_chunks:
        _fail("chunked base-backup finalization requires a non-empty accepted chunk state for transfer session")
    permit = _id(finalization_permit_id, label="chunked base-backup finalization permit ID")
    nonce = _nonce(finalization_permit_nonce, label="chunked base-backup finalization permit nonce")
    if len({session_facts.session_id, permit, nonce}) != 3:
        _fail("chunked base-backup finalization permit identity reuses a session value")
    issued_text = _timestamp_text(issued_at, label="chunked base-backup finalization permit issued_at")
    expires_text = _timestamp_text(expires_at, label="chunked base-backup finalization permit expires_at")
    issued = _timestamp(issued_text, label="chunked base-backup finalization permit issued_at")
    expires = _timestamp(expires_text, label="chunked base-backup finalization permit expires_at")
    if (
        expires <= issued
        or expires - issued > timedelta(seconds=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_PERMIT_SECONDS)
        or issued < session_facts.issued_at
        or expires > session_facts.expires_at
    ):
        _fail("chunked base-backup finalization permit lifetime is invalid")
    _private, public, signer = _signer_from_private(witness_signer, label="chunked base-backup finalization permit")
    if public != session_facts.witness_public_key:
        _fail("chunked base-backup finalization permit signer does not match transfer session Witness")
    set_hash = derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
        accepted_chunk_set=accepted, now=issued
    )
    total_hash = _nonzero_sha256(
        total_plaintext_sha256, label="chunked base-backup finalization permit total plaintext hash"
    )
    total_bytes = _positive_int(
        total_plaintext_bytes,
        label="chunked base-backup finalization permit total plaintext bytes",
        maximum=2**63 - 1,
    )
    if total_bytes != sum(item.chunk.plaintext_bytes for item in accepted.committed_chunks):
        _fail("chunked base-backup finalization permit total plaintext bytes do not match accepted chunk set")
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_FINALIZATION_PERMIT_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_finalization_permit",
        "binding": _binding_mapping(session_facts.binding),
        "session_id": session_facts.session_id,
        "session_sha256": hashlib.sha256(session_facts.raw).hexdigest(),
        "finalization_permit_id": permit,
        "finalization_permit_nonce": nonce,
        "committed_chunk_set_sha256": set_hash,
        "committed_chunk_count": len(accepted.committed_chunks),
        "total_plaintext_sha256": total_hash,
        "total_plaintext_bytes": total_bytes,
        "issued_at": issued_text,
        "expires_at": expires_text,
        "witness_signer": signer,
    }
    return {**unsigned, "witness_signature": _sign(unsigned, domain=_FINALIZATION_DOMAIN, signer=witness_signer, label="chunked base-backup finalization permit")}


def canonical_physical_wal_chunked_base_backup_finalization_permit_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    return _parse_finalization(value).raw


def verify_physical_wal_chunked_base_backup_finalization_permit(
    *,
    finalization_permit: Mapping[str, Any] | bytes,
    transfer_session: VerifiedPhysicalWalChunkedBaseBackupTransferSession,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_finalization_permit_ids: Collection[str] = (),
    consumed_finalization_permit_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit:
    """Verify a live finalization pin over exactly contiguous accepted chunks."""

    session, session_facts = _session_from_verified(transfer_session, now=now, require_live=True)
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    if witness != session_facts.witness_public_key:
        _fail("expected Witness key does not match transfer session")
    facts = _parse_finalization(finalization_permit, expected_witness_public_key=witness)
    if (
        facts.binding != session_facts.binding
        or facts.session_id != session_facts.session_id
        or facts.session_sha256 != hashlib.sha256(session_facts.raw).hexdigest()
        or facts.issued_at < session_facts.issued_at
        or facts.expires_at > session_facts.expires_at
    ):
        _fail("chunked base-backup finalization permit route, session, term, or recipient is foreign")
    observed_now = _utc(now, label="chunked base-backup verification clock")
    _assert_live(facts.issued_at, facts.expires_at, now=observed_now, label="chunked base-backup finalization permit")
    accepted, accepted_session = _accepted_chunk_set_from_verified(
        accepted_chunk_set, now=observed_now, require_session_live=True
    )
    if accepted_session.raw != session_facts.raw or not accepted.committed_chunks:
        _fail("chunked base-backup finalization requires a non-empty accepted chunk state for transfer session")
    expected_hash = derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
        accepted_chunk_set=accepted, now=observed_now
    )
    if facts.committed_chunk_count != len(accepted.committed_chunks) or facts.committed_chunk_set_sha256 != expected_hash:
        _fail("chunked base-backup finalization permit does not pin exact contiguous chunks")
    if facts.total_plaintext_bytes != sum(item.chunk.plaintext_bytes for item in accepted.committed_chunks):
        _fail("chunked base-backup finalization permit total plaintext bytes do not match accepted chunk set")
    _assert_distinct_observed(value=facts.finalization_permit_id, observed=consumed_finalization_permit_ids, label="chunked base-backup finalization permit ID", validator=_id)
    _assert_distinct_observed(value=facts.finalization_permit_nonce, observed=consumed_finalization_permit_nonces, label="chunked base-backup finalization permit nonce", validator=_nonce)
    result = VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit(
        canonical_finalization_permit=facts.raw,
        session=session,
        finalization_permit_id=facts.finalization_permit_id,
        finalization_permit_nonce=facts.finalization_permit_nonce,
        committed_chunk_set_sha256=facts.committed_chunk_set_sha256,
        committed_chunk_count=facts.committed_chunk_count,
        total_plaintext_sha256=facts.total_plaintext_sha256,
        total_plaintext_bytes=facts.total_plaintext_bytes,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_FINALIZATION_CAPABILITY)
    return result


def require_verified_physical_wal_chunked_base_backup_finalization_permit(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit:
    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit
        or value._capability is not _VERIFIED_FINALIZATION_CAPABILITY
    ):
        _fail("verified chunked base-backup finalization permit capability is required")
    session, session_facts = _session_from_verified(value.session, now=now, require_live=True)
    facts = _parse_finalization(
        value.canonical_finalization_permit, expected_witness_public_key=session_facts.witness_public_key
    )
    if (
        facts.binding != session_facts.binding
        or facts.session_id != session_facts.session_id
        or facts.session_sha256 != hashlib.sha256(session_facts.raw).hexdigest()
        or facts.finalization_permit_id != value.finalization_permit_id
        or facts.finalization_permit_nonce != value.finalization_permit_nonce
        or facts.committed_chunk_set_sha256 != value.committed_chunk_set_sha256
        or facts.committed_chunk_count != value.committed_chunk_count
        or facts.total_plaintext_sha256 != value.total_plaintext_sha256
        or facts.total_plaintext_bytes != value.total_plaintext_bytes
        or facts.issued_at != value.issued_at
        or facts.expires_at != value.expires_at
        or facts.witness_public_key != value.witness_public_key
    ):
        _fail("verified chunked base-backup finalization permit was tampered")
    _assert_live(facts.issued_at, facts.expires_at, now=_utc(now, label="chunked base-backup verification clock"), label="chunked base-backup finalization permit")
    return value
