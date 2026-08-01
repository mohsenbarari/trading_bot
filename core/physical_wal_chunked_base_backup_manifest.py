"""Signed v2 manifest for a Witness-accepted chunked physical base backup.

The v1 physical-WAL manifest format remains untouched.  This isolated v2
contract consumes only a fresh finalization permit plus an opaque
Witness-owned accepted-chunk-set capability from
``physical_wal_chunked_base_backup_transfer``.  It never accepts a raw caller
list of chunks as enough evidence to finalize a backup.

The manifest binds immutable Object Storage selectors in exact index order,
both ciphertext and plaintext chunk claims, and a total plaintext hash/size.
It has no Object Storage, age, filesystem, database, network, or restore I/O.
A materializer must later read exact object *versions*, decrypt them, and
recompute all hash/size claims before use.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_chunked_base_backup_transfer import (
    MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
    PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupChunk,
    PhysicalWalChunkedBaseBackupTransferError,
    VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
    derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256,
    require_verified_physical_wal_chunked_base_backup_finalization_permit,
    require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set,
)


__all__ = (
    "MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_BYTES",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_SCHEMA",
    "PhysicalWalChunkedBaseBackupManifestChunkSelector",
    "PhysicalWalChunkedBaseBackupManifestError",
    "VerifiedPhysicalWalChunkedBaseBackupManifest",
    "build_physical_wal_chunked_base_backup_manifest",
    "canonical_physical_wal_chunked_base_backup_manifest_bytes",
    "require_verified_physical_wal_chunked_base_backup_manifest",
    "verify_physical_wal_chunked_base_backup_manifest",
)


PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-manifest-v2"
)
MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_BYTES = 128 * 1024 * 1024
_MANIFEST_DOMAIN = b"gold-trade-physical-wal-chunked-base-backup-manifest-v2\x00"
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
_SELECTOR_FIELDS = frozenset(
    {
        "index",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
        "age_recipient",
        "commitment_id",
        "commitment_sha256",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "session_id",
        "session_sha256",
        "finalization_permit_id",
        "finalization_permit_sha256",
        "committed_chunk_set_sha256",
        "committed_chunk_count",
        "manifest_id",
        "manifest_nonce",
        "created_at",
        "chunks",
        "total_plaintext_sha256",
        "total_plaintext_bytes",
        "witness_signer",
        "witness_signature",
    }
)
_VERIFIED_MANIFEST_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupManifestError(ValueError):
    """A v2 chunk-set manifest is malformed, foreign, or incomplete."""


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupManifestChunkSelector:
    """One exact immutable encrypted object selector in final byte order."""

    index: int
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str
    commitment_id: str
    commitment_sha256: str


@dataclass(frozen=True)
class VerifiedPhysicalWalChunkedBaseBackupManifest:
    """Opaque v2 manifest; evidence only, never restore/promotion authority."""

    canonical_manifest: bytes
    finalization_permit: VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet
    manifest_id: str
    manifest_nonce: str
    created_at: datetime
    chunks: tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...]
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ManifestFacts:
    raw: bytes
    binding_mapping: dict[str, Any]
    session_id: str
    session_sha256: str
    finalization_permit_id: str
    finalization_permit_sha256: str
    committed_chunk_set_sha256: str
    committed_chunk_count: int
    manifest_id: str
    manifest_nonce: str
    created_at: datetime
    chunks: tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...]
    total_plaintext_sha256: str
    total_plaintext_bytes: int
    witness_public_key: bytes


def _fail(message: str) -> None:
    raise PhysicalWalChunkedBaseBackupManifestError(message)


def _canonical(value: object, *, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupManifestError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("chunked base-backup manifest JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("chunked base-backup manifest JSON has a forbidden constant")


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


def _nonzero_sha256(value: object, *, label: str) -> str:
    digest = _text(value, label=label, pattern=SHA256_RE)
    if digest == "0" * 64:
        _fail(f"{label} is invalid")
    return digest


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _fail(f"{label} is invalid")
    return value


def _index(value: object, *, label: str) -> int:
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
        raise PhysicalWalChunkedBaseBackupManifestError(f"{label} signing is unavailable") from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(f"{label} signer is invalid")
    public_key = value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        value,
        _public_key(public_key, label=f"{label} signer public key"),
        {
            "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public_key).decode("ascii"),
            "key_id": _key_id(public_key),
        },
    )


def _sign(unsigned: Mapping[str, Any], *, signer: object, label: str) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(signer, label=label)
    try:
        signature = private.sign(_MANIFEST_DOMAIN + _canonical(dict(unsigned), label=label))
    except ValueError:
        _fail(f"{label} signer failed")
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail(f"{label} signer produced an invalid signature")
    return {
        "algorithm": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _verify_signature(payload: Mapping[str, Any], *, expected_key: bytes | None) -> bytes:
    public_key = _signer(payload.get("witness_signer"), label="chunked base-backup manifest")
    if expected_key is not None and public_key != expected_key:
        _fail("chunked base-backup manifest signer does not match expected Witness key")
    signature = _signature(payload.get("witness_signature"), label="chunked base-backup manifest")
    unsigned = {key: item for key, item in payload.items() if key != "witness_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, _MANIFEST_DOMAIN + _canonical(unsigned, label="chunked base-backup manifest")
        )
    except (InvalidSignature, ValueError):
        _fail("chunked base-backup manifest signature is invalid")
    return public_key


def _parse_canonical(value: object) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(payload, label="chunked base-backup manifest")
        except (TypeError, ValueError):
            _fail("chunked base-backup manifest is invalid")
    elif isinstance(value, bytes):
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_BYTES:
            _fail("chunked base-backup manifest byte size is invalid")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("chunked base-backup manifest is invalid JSON")
        if not isinstance(payload, dict) or _canonical(payload, label="chunked base-backup manifest") != raw:
            _fail("chunked base-backup manifest is not canonical JSON")
    else:
        _fail("chunked base-backup manifest is invalid")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_BYTES:
        _fail("chunked base-backup manifest byte size is invalid")
    return payload, raw


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
        "writer_term": {
            "writer_holder_site": value.writer_term.writer_holder_site,
            "writer_epoch": value.writer_term.writer_epoch,
            "writer_lease_id": value.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.writer_term.witnessed_term_proof_sha256,
        },
        "transport_plane": value.transport_plane,
        "direct_webapp_transport": value.direct_webapp_transport,
    }


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


def _selector_from_chunk(*, chunk: PhysicalWalChunkedBaseBackupChunk, commitment_id: str, commitment_sha256: str) -> PhysicalWalChunkedBaseBackupManifestChunkSelector:
    return PhysicalWalChunkedBaseBackupManifestChunkSelector(
        index=chunk.index,
        object_key=_object_key(chunk.object_key, label="chunked base-backup manifest chunk object key"),
        version_id=_version_id(chunk.version_id, label="chunked base-backup manifest chunk version ID"),
        ciphertext_sha256=_nonzero_sha256(chunk.ciphertext_sha256, label="chunked base-backup manifest chunk ciphertext hash"),
        ciphertext_bytes=_positive_int(chunk.ciphertext_bytes, label="chunked base-backup manifest chunk ciphertext bytes", maximum=2**63 - 1),
        plaintext_sha256=_nonzero_sha256(chunk.plaintext_sha256, label="chunked base-backup manifest chunk plaintext hash"),
        plaintext_bytes=_positive_int(chunk.plaintext_bytes, label="chunked base-backup manifest chunk plaintext bytes", maximum=2**63 - 1),
        age_recipient=_text(chunk.age_recipient, label="chunked base-backup manifest chunk age recipient", pattern=AGE_RECIPIENT_RE),
        commitment_id=_id(commitment_id, label="chunked base-backup manifest chunk commitment ID"),
        commitment_sha256=_nonzero_sha256(commitment_sha256, label="chunked base-backup manifest chunk commitment hash"),
    )


def _selector_mapping(value: PhysicalWalChunkedBaseBackupManifestChunkSelector) -> dict[str, Any]:
    return {
        "index": value.index,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "plaintext_sha256": value.plaintext_sha256,
        "plaintext_bytes": value.plaintext_bytes,
        "age_recipient": value.age_recipient,
        "commitment_id": value.commitment_id,
        "commitment_sha256": value.commitment_sha256,
    }


def _selector_from_mapping(value: object, *, label: str) -> PhysicalWalChunkedBaseBackupManifestChunkSelector:
    item = _exact_mapping(value, fields=_SELECTOR_FIELDS, label=label)
    return PhysicalWalChunkedBaseBackupManifestChunkSelector(
        index=_index(item["index"], label=f"{label} index"),
        object_key=_object_key(item["object_key"], label=f"{label} object key"),
        version_id=_version_id(item["version_id"], label=f"{label} version ID"),
        ciphertext_sha256=_nonzero_sha256(item["ciphertext_sha256"], label=f"{label} ciphertext hash"),
        ciphertext_bytes=_positive_int(item["ciphertext_bytes"], label=f"{label} ciphertext bytes", maximum=2**63 - 1),
        plaintext_sha256=_nonzero_sha256(item["plaintext_sha256"], label=f"{label} plaintext hash"),
        plaintext_bytes=_positive_int(item["plaintext_bytes"], label=f"{label} plaintext bytes", maximum=2**63 - 1),
        age_recipient=_text(item["age_recipient"], label=f"{label} age recipient", pattern=AGE_RECIPIENT_RE),
        commitment_id=_id(item["commitment_id"], label=f"{label} commitment ID"),
        commitment_sha256=_nonzero_sha256(item["commitment_sha256"], label=f"{label} commitment hash"),
    )


def _expected_selectors(
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    *,
    now: datetime,
) -> tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...]:
    accepted = require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
        accepted_chunk_set, now=now
    )
    if not accepted.committed_chunks:
        _fail("chunked base-backup manifest requires non-empty Witness accepted state")
    return tuple(
        _selector_from_chunk(
            chunk=item.chunk,
            commitment_id=item.commitment_id,
            commitment_sha256=hashlib.sha256(item.canonical_commitment).hexdigest(),
        )
        for item in accepted.committed_chunks
    )


def _parse_manifest(value: object, *, expected_witness_public_key: bytes | None = None) -> _ManifestFacts:
    payload, raw = _parse_canonical(value)
    manifest = _exact_mapping(payload, fields=_MANIFEST_FIELDS, label="chunked base-backup manifest")
    if (
        manifest["schema"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_SCHEMA
        or manifest["version"] != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION
        or manifest["kind"] != "physical_wal_chunked_base_backup_immutable_chunk_set_manifest"
    ):
        _fail("chunked base-backup manifest schema is invalid")
    witness = _verify_signature(manifest, expected_key=expected_witness_public_key)
    binding = _exact_mapping(manifest["binding"], fields=_BINDING_FIELDS, label="chunked base-backup manifest binding")
    # The binding is deliberately compared byte-for-byte with the independently
    # verified finalization permit below; local parsing here only blocks odd
    # shapes, duplicate fields, and non-ASCII accidental encodings.
    try:
        _canonical(binding, label="chunked base-backup manifest binding")
        _exact_mapping(binding["writer_term"], fields=_TERM_FIELDS, label="chunked base-backup manifest writer term")
    except PhysicalWalChunkedBaseBackupManifestError:
        raise
    chunks_value = manifest["chunks"]
    if isinstance(chunks_value, (str, bytes)) or not isinstance(chunks_value, Sequence):
        _fail("chunked base-backup manifest chunks are invalid")
    if not chunks_value or len(chunks_value) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS:
        _fail("chunked base-backup manifest chunks are invalid")
    chunks = tuple(
        _selector_from_mapping(item, label=f"chunked base-backup manifest chunk {index}")
        for index, item in enumerate(chunks_value)
    )
    return _ManifestFacts(
        raw=raw,
        binding_mapping=binding,
        session_id=_id(manifest["session_id"], label="chunked base-backup manifest session ID"),
        session_sha256=_nonzero_sha256(manifest["session_sha256"], label="chunked base-backup manifest session hash"),
        finalization_permit_id=_id(manifest["finalization_permit_id"], label="chunked base-backup manifest finalization permit ID"),
        finalization_permit_sha256=_nonzero_sha256(manifest["finalization_permit_sha256"], label="chunked base-backup manifest finalization permit hash"),
        committed_chunk_set_sha256=_nonzero_sha256(manifest["committed_chunk_set_sha256"], label="chunked base-backup manifest committed set hash"),
        committed_chunk_count=_positive_int(manifest["committed_chunk_count"], label="chunked base-backup manifest committed chunk count", maximum=MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_CHUNKS),
        manifest_id=_id(manifest["manifest_id"], label="chunked base-backup manifest ID"),
        manifest_nonce=_nonce(manifest["manifest_nonce"], label="chunked base-backup manifest nonce"),
        created_at=_timestamp(manifest["created_at"], label="chunked base-backup manifest created_at"),
        chunks=chunks,
        total_plaintext_sha256=_nonzero_sha256(manifest["total_plaintext_sha256"], label="chunked base-backup manifest total plaintext hash"),
        total_plaintext_bytes=_positive_int(manifest["total_plaintext_bytes"], label="chunked base-backup manifest total plaintext bytes", maximum=2**63 - 1),
        witness_public_key=witness,
    )


def _assert_manifest_matches_finalization(
    facts: _ManifestFacts,
    *,
    finalization_permit: VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    now: datetime,
) -> tuple[PhysicalWalChunkedBaseBackupManifestChunkSelector, ...]:
    permit = require_verified_physical_wal_chunked_base_backup_finalization_permit(
        finalization_permit, now=now
    )
    accepted = require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
        accepted_chunk_set, now=now
    )
    if accepted.transfer_session.canonical_session != permit.session.canonical_session:
        _fail("chunked base-backup manifest accepted state belongs to a foreign session")
    expected_binding = _binding_mapping(permit.session.binding)
    expected_set_hash = derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
        accepted_chunk_set=accepted, now=now
    )
    expected_selectors = _expected_selectors(accepted, now=now)
    if facts.total_plaintext_sha256 != permit.total_plaintext_sha256:
        _fail("chunked base-backup manifest total plaintext hash does not match finalization permit")
    if facts.total_plaintext_bytes != permit.total_plaintext_bytes:
        _fail("chunked base-backup manifest total plaintext bytes do not match finalization permit")
    if (
        facts.binding_mapping != expected_binding
        or facts.session_id != permit.session.session_id
        or facts.session_sha256 != hashlib.sha256(permit.session.canonical_session).hexdigest()
        or facts.finalization_permit_id != permit.finalization_permit_id
        or facts.finalization_permit_sha256 != hashlib.sha256(permit.canonical_finalization_permit).hexdigest()
        or facts.committed_chunk_set_sha256 != permit.committed_chunk_set_sha256
        or facts.committed_chunk_set_sha256 != expected_set_hash
        or facts.committed_chunk_count != permit.committed_chunk_count
        or facts.committed_chunk_count != len(expected_selectors)
        or facts.chunks != expected_selectors
    ):
        _fail("chunked base-backup manifest does not pin exact Witness accepted contiguous chunks")
    if facts.created_at < permit.issued_at or facts.created_at > permit.expires_at:
        _fail("chunked base-backup manifest was created outside fresh finalization permit")
    if facts.witness_public_key != permit.witness_public_key:
        _fail("chunked base-backup manifest Witness signer does not match finalization permit")
    if facts.manifest_id in {permit.session.session_id, permit.finalization_permit_id} or facts.manifest_nonce in {permit.session.session_nonce, permit.finalization_permit_nonce}:
        _fail("chunked base-backup manifest identity reuses session or permit value")
    if facts.total_plaintext_bytes != sum(item.plaintext_bytes for item in expected_selectors):
        _fail("chunked base-backup manifest total plaintext bytes do not match chunk set")
    return expected_selectors


def build_physical_wal_chunked_base_backup_manifest(
    *,
    finalization_permit: VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    manifest_id: str,
    manifest_nonce: str,
    created_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build one Witness-signed v2 manifest from opaque accepted state only."""

    permit = require_verified_physical_wal_chunked_base_backup_finalization_permit(
        finalization_permit, now=created_at
    )
    accepted = require_verified_physical_wal_chunked_base_backup_witness_accepted_chunk_set(
        accepted_chunk_set, now=created_at
    )
    if accepted.transfer_session.canonical_session != permit.session.canonical_session:
        _fail("chunked base-backup manifest accepted state belongs to a foreign session")
    expected_set_hash = derive_physical_wal_chunked_base_backup_committed_chunk_set_sha256(
        accepted_chunk_set=accepted, now=created_at
    )
    selectors = _expected_selectors(accepted, now=created_at)
    if (
        permit.committed_chunk_set_sha256 != expected_set_hash
        or permit.committed_chunk_count != len(selectors)
    ):
        _fail("chunked base-backup manifest finalization permit does not pin accepted state")
    manifest_identity = _id(manifest_id, label="chunked base-backup manifest ID")
    nonce = _nonce(manifest_nonce, label="chunked base-backup manifest nonce")
    if manifest_identity in {permit.session.session_id, permit.finalization_permit_id} or nonce in {permit.session.session_nonce, permit.finalization_permit_nonce}:
        _fail("chunked base-backup manifest identity reuses session or permit value")
    created_text = _timestamp_text(created_at, label="chunked base-backup manifest created_at")
    if permit.total_plaintext_bytes != sum(item.plaintext_bytes for item in selectors):
        _fail("chunked base-backup manifest total plaintext bytes do not match chunk set")
    _private, public, signer = _signer_from_private(witness_signer, label="chunked base-backup manifest")
    if public != permit.witness_public_key:
        _fail("chunked base-backup manifest signer does not match finalization permit Witness")
    unsigned = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_SCHEMA,
        "version": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_VERSION,
        "kind": "physical_wal_chunked_base_backup_immutable_chunk_set_manifest",
        "binding": _binding_mapping(permit.session.binding),
        "session_id": permit.session.session_id,
        "session_sha256": hashlib.sha256(permit.session.canonical_session).hexdigest(),
        "finalization_permit_id": permit.finalization_permit_id,
        "finalization_permit_sha256": hashlib.sha256(permit.canonical_finalization_permit).hexdigest(),
        "committed_chunk_set_sha256": expected_set_hash,
        "committed_chunk_count": len(selectors),
        "manifest_id": manifest_identity,
        "manifest_nonce": nonce,
        "created_at": created_text,
        "chunks": [_selector_mapping(item) for item in selectors],
        "total_plaintext_sha256": permit.total_plaintext_sha256,
        "total_plaintext_bytes": permit.total_plaintext_bytes,
        "witness_signer": signer,
    }
    result = {**unsigned, "witness_signature": _sign(unsigned, signer=witness_signer, label="chunked base-backup manifest")}
    raw = _canonical(result, label="chunked base-backup manifest")
    if len(raw) > MAX_PHYSICAL_WAL_CHUNKED_BASE_BACKUP_MANIFEST_BYTES:
        _fail("chunked base-backup manifest byte size is invalid")
    return result


def canonical_physical_wal_chunked_base_backup_manifest_bytes(value: Mapping[str, Any] | bytes) -> bytes:
    """Return canonical syntactically valid v2 bytes without execution I/O."""

    return _parse_manifest(value).raw


def verify_physical_wal_chunked_base_backup_manifest(
    *,
    manifest: Mapping[str, Any] | bytes,
    finalization_permit: VerifiedPhysicalWalChunkedBaseBackupFinalizationPermit,
    accepted_chunk_set: VerifiedPhysicalWalChunkedBaseBackupAcceptedChunkSet,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_manifest_ids: Collection[str] = (),
    consumed_manifest_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalChunkedBaseBackupManifest:
    """Verify a fresh v2 manifest against its opaque Witness accepted state."""

    permit = require_verified_physical_wal_chunked_base_backup_finalization_permit(
        finalization_permit, now=now
    )
    witness = _public_key(expected_witness_public_key, label="expected Witness public key")
    if witness != permit.witness_public_key:
        _fail("expected Witness key does not match finalization permit")
    facts = _parse_manifest(manifest, expected_witness_public_key=witness)
    _assert_manifest_matches_finalization(
        facts,
        finalization_permit=permit,
        accepted_chunk_set=accepted_chunk_set,
        now=now,
    )
    if facts.manifest_id in _id_values(consumed_manifest_ids, label="chunked base-backup manifest ID"):
        _fail("chunked base-backup manifest ID was replayed")
    if facts.manifest_nonce in _nonce_values(consumed_manifest_nonces, label="chunked base-backup manifest nonce"):
        _fail("chunked base-backup manifest nonce was replayed")
    result = VerifiedPhysicalWalChunkedBaseBackupManifest(
        canonical_manifest=facts.raw,
        finalization_permit=permit,
        accepted_chunk_set=accepted_chunk_set,
        manifest_id=facts.manifest_id,
        manifest_nonce=facts.manifest_nonce,
        created_at=facts.created_at,
        chunks=facts.chunks,
        total_plaintext_sha256=facts.total_plaintext_sha256,
        total_plaintext_bytes=facts.total_plaintext_bytes,
        witness_public_key=witness,
    )
    object.__setattr__(result, "_capability", _VERIFIED_MANIFEST_CAPABILITY)
    return result


def _id_values(value: object, *, label: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        _fail(f"{label} replay set is invalid")
    return frozenset(_id(item, label=f"{label} value") for item in value)


def _nonce_values(value: object, *, label: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        _fail(f"{label} replay set is invalid")
    return frozenset(_nonce(item, label=f"{label} value") for item in value)


def require_verified_physical_wal_chunked_base_backup_manifest(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupManifest:
    """Revalidate opaque manifest capability and its still-fresh permit."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupManifest
        or value._capability is not _VERIFIED_MANIFEST_CAPABILITY
    ):
        _fail("verified chunked base-backup manifest capability is required")
    permit = require_verified_physical_wal_chunked_base_backup_finalization_permit(
        value.finalization_permit, now=now
    )
    facts = _parse_manifest(value.canonical_manifest, expected_witness_public_key=permit.witness_public_key)
    selectors = _assert_manifest_matches_finalization(
        facts,
        finalization_permit=permit,
        accepted_chunk_set=value.accepted_chunk_set,
        now=now,
    )
    if (
        facts.manifest_id != value.manifest_id
        or facts.manifest_nonce != value.manifest_nonce
        or facts.created_at != value.created_at
        or facts.chunks != value.chunks
        or facts.chunks != selectors
        or facts.total_plaintext_sha256 != value.total_plaintext_sha256
        or facts.total_plaintext_bytes != value.total_plaintext_bytes
        or facts.witness_public_key != value.witness_public_key
    ):
        _fail("verified chunked base-backup manifest was tampered")
    return value
