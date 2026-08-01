"""Canonical, signed metadata for the physical PostgreSQL/Object-Storage plane.

This is an intentionally local-only boundary for the replacement of the
incomplete Object-delta MVP.  It binds three kinds of encrypted, versioned
Object Storage material:

* one physical PostgreSQL base backup;
* an ordered, contiguous chain of WAL ranges; and
* a complete blob-inventory frontier.

The module never opens a file, encrypts or decrypts with age, contacts
PostgreSQL, Object Storage, a Witness, SSH, or a network endpoint.  A future
root-only uploader/receiver adapter supplies read-back metadata and raw
canonical manifest bytes to this boundary.  It must still do the I/O,
durably consume accepted manifest hashes, verify decrypted inventory shards,
and establish a live Witness term before it can start or promote a writer.

``physical_wal_promotion_gate`` deliberately remains independent.  The names
``baseline_generation_id``, ``baseline_manifest_sha256``, ``baseline_wal_lsn``
and ``blob_object_frontier_wal_lsn`` below are compatible projections for that
later gate, not an import or an activation path.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
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
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


PHYSICAL_WAL_OBJECT_MANIFEST_VERSION = 1
PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA = (
    "gold-trade-physical-wal-base-backup-manifest-v1"
)
PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA = "gold-trade-physical-wal-segment-manifest-v1"
PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA = (
    "gold-trade-physical-wal-blob-frontier-manifest-v1"
)
PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA = "gold-trade-physical-wal-object-descriptor-v1"
PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION = 1
PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM = "ed25519"
PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION = "age-v1"
PHYSICAL_WAL_OBJECT_IMMUTABILITY = "versioned_create_only_readback_v1"
# The two directions deliberately have disjoint top-level Object Storage
# namespaces.  Keeping these names in the common canonical-manifest module
# avoids a publisher, spool, receiver, or scoped client independently
# inventing a similar-looking prefix.  The normal path remains the historical
# ``physical-wal`` namespace; the promoted-IR failback path must opt in to the
# separate ``physical-failback`` namespace explicitly.
PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE = "physical-wal"
PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE = "physical-failback"
PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES = frozenset(
    {
        PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    }
)
PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256 = "0" * 64
PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256 = "0" * 64
# PostgreSQL 15's supported production image is built with the default 16 MiB
# WAL segment size.  A new value must be explicitly reviewed here and pinned
# in every signed lineage; callers cannot silently select arbitrary geometry.
PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES = (16 * 1024 * 1024,)

MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES = 1024 * 1024 * 1024 * 1024 * 1024
MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST = 4096
MAX_PHYSICAL_WAL_BLOB_INVENTORY_SHARDS = 4096

_SIGNATURE_DOMAINS = {
    PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA: (
        b"gold-trade-physical-wal-base-backup-manifest-v1\x00"
    ),
    PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA: (
        b"gold-trade-physical-wal-segment-manifest-v1\x00"
    ),
    PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA: (
        b"gold-trade-physical-wal-blob-frontier-manifest-v1\x00"
    ),
}
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_WAL_SEGMENT_NAME_RE = re.compile(r"^[0-9A-F]{24}$")
_MUTABLE_ALIAS_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})

_BASE_BACKUP_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "writer_term",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "base_backup_object",
        "source_signer",
        "source_signature",
    }
)
_SEGMENT_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "writer_term",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "previous_manifest_sha256",
        "previous_end_lsn",
        "previous_segment_ordinal",
        "segments",
        "source_signer",
        "source_signature",
    }
)
_BLOB_FRONTIER_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "writer_term",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "previous_manifest_sha256",
        "previous_frontier_wal_lsn",
        "blob_object_frontier_wal_lsn",
        "objects_complete",
        "inventory_shards",
        "source_signer",
        "source_signature",
    }
)
_TERM_FIELDS = frozenset({"epoch", "lease_id", "witnessed_term_proof_sha256"})
_OBJECT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "object_kind",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "encryption",
        "age_recipient",
        "immutability",
    }
)
_SEGMENT_FIELDS = frozenset(
    {"ordinal", "wal_segment_name", "timeline_id", "start_lsn", "end_lsn", "object"}
)
_INVENTORY_SHARD_FIELDS = frozenset(
    {"ordinal", "plaintext_sha256", "plaintext_bytes", "entry_count", "object"}
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})

_VERIFIED_BASE_BACKUP_CAPABILITY = object()
_VERIFIED_SEGMENT_CAPABILITY = object()
_VERIFIED_BLOB_FRONTIER_CAPABILITY = object()
_VERIFIED_BUNDLE_CAPABILITY = object()


class PhysicalWalObjectManifestError(ValueError):
    """A physical-WAL Object Storage manifest is malformed or unbound."""


@dataclass(frozen=True)
class PhysicalWalWriterTermBinding:
    """The signed Witness-term identity bound into storage metadata.

    This is only a binding.  It is not a Witness proof and cannot authorize a
    writer by itself.
    """

    epoch: int
    lease_id: str
    witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class PhysicalWalImmutableObject:
    """One age-encrypted Object Storage version, never a mutable alias."""

    object_kind: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    age_recipient: str


@dataclass(frozen=True)
class PhysicalWalSegment:
    """A single contiguous, exclusive-end PostgreSQL WAL range."""

    ordinal: int
    wal_segment_name: str
    timeline_id: int
    start_lsn: str
    end_lsn: str
    object: PhysicalWalImmutableObject


@dataclass(frozen=True)
class PhysicalWalBlobInventoryShard:
    """One encrypted inventory shard; the adapter verifies its plaintext later."""

    ordinal: int
    plaintext_sha256: str
    plaintext_bytes: int
    entry_count: int
    object: PhysicalWalImmutableObject


@dataclass(frozen=True)
class VerifiedPhysicalWalBaseBackupManifest:
    """Opaque source-signed base-backup claim, revalidated on every use."""

    canonical_manifest: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    base_backup_object: PhysicalWalImmutableObject
    manifest_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalSegmentManifest:
    """Opaque source-signed WAL-chain link, not an apply or promotion permit."""

    canonical_manifest: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    previous_manifest_sha256: str
    previous_end_lsn: str
    previous_segment_ordinal: int
    segments: tuple[PhysicalWalSegment, ...]
    manifest_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def first_lsn(self) -> str:
        return self.segments[0].start_lsn

    @property
    def end_lsn(self) -> str:
        return self.segments[-1].end_lsn

    @property
    def last_segment_ordinal(self) -> int:
        return self.segments[-1].ordinal


@dataclass(frozen=True)
class VerifiedPhysicalWalBlobFrontierManifest:
    """Opaque source-signed blob-inventory frontier, not an availability claim."""

    canonical_manifest: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    previous_manifest_sha256: str
    previous_frontier_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    objects_complete: bool
    inventory_shards: tuple[PhysicalWalBlobInventoryShard, ...]
    manifest_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalObjectStorageBundle:
    """A locally verified base/WAL/blob bundle; never a writer capability."""

    baseline: VerifiedPhysicalWalBaseBackupManifest
    wal_manifests: tuple[VerifiedPhysicalWalSegmentManifest, ...]
    blob_frontier: VerifiedPhysicalWalBlobFrontierManifest
    terminal_wal_lsn: str
    manifest_sha256es: tuple[str, ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _BaseFacts:
    raw: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    base_backup_object: PhysicalWalImmutableObject
    manifest_sha256: str


@dataclass(frozen=True)
class _SegmentFacts:
    raw: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    previous_manifest_sha256: str
    previous_end_lsn: str
    previous_segment_ordinal: int
    segments: tuple[PhysicalWalSegment, ...]
    manifest_sha256: str


@dataclass(frozen=True)
class _BlobFacts:
    raw: bytes
    source_public_key: bytes
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: PhysicalWalWriterTermBinding
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    previous_manifest_sha256: str
    previous_frontier_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    objects_complete: bool
    inventory_shards: tuple[PhysicalWalBlobInventoryShard, ...]
    manifest_sha256: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalWalObjectManifestError("manifest contains duplicate JSON fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PhysicalWalObjectManifestError(f"manifest JSON constant is forbidden: {value}")


def _canonical(value: object, *, label: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalObjectManifestError(f"{label} is not canonical JSON") from exc


def _exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalWalObjectManifestError(f"{label} fields are invalid")
    return dict(value)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise PhysicalWalObjectManifestError(f"{label} is invalid") from exc
    return value


def _positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    return value


def _nonnegative_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    return value


def _site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    return value


def _sha256(value: object, *, label: str) -> str:
    return _text(value, label=label, pattern=SHA256_RE)


def _lsn(value: object, *, label: str) -> tuple[str, int]:
    text = _text(value, label=label, pattern=_LSN_RE)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _timeline_id(value: object, *, label: str) -> int:
    return _positive_int(value, label=label, maximum=0xFFFFFFFF)


def _wal_segment_size(value: object, *, label: str) -> int:
    size = _positive_int(value, label=label)
    if size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        raise PhysicalWalObjectManifestError(f"{label} is not a supported PostgreSQL WAL segment size")
    return size


def _wal_filename_for(*, timeline_id: int, start_lsn_value: int, segment_size: int) -> str:
    if start_lsn_value % segment_size:
        raise PhysicalWalObjectManifestError("WAL segment start LSN is not segment-aligned")
    segments_per_log = 0x100000000 // segment_size
    segment_number = start_lsn_value // segment_size
    log = segment_number // segments_per_log
    segment = segment_number % segments_per_log
    if log > 0xFFFFFFFF or segment > 0xFFFFFFFF:
        raise PhysicalWalObjectManifestError("WAL segment LSN exceeds PostgreSQL filename geometry")
    return f"{timeline_id:08X}{log:08X}{segment:08X}"


def _wal_segment_ordinal_for_start(
    start_lsn_value: int,
    *,
    segment_size: int,
    label: str,
) -> int:
    """Return the zero-based absolute WAL segment ordinal for one start LSN.

    PostgreSQL's segment filename geometry is absolute, not relative to a
    particular base backup.  Keeping the manifest ordinal absolute prevents a
    valid archive stream that starts at a later LSN from being reinterpreted as
    a fabricated first segment.  The predecessor of a genesis chain is thus
    derived from ``wal_chain_start_lsn / segment_size - 1``.
    """

    if start_lsn_value % segment_size:
        raise PhysicalWalObjectManifestError(f"{label} start LSN is not segment-aligned")
    ordinal = start_lsn_value // segment_size
    if ordinal > 2**63 - 1:
        raise PhysicalWalObjectManifestError(f"{label} ordinal exceeds the supported range")
    return ordinal


def _previous_wal_segment_ordinal(
    value: object,
    *,
    previous_manifest_sha256: str,
    label: str,
) -> int:
    """Validate a WAL-chain predecessor, including the segment-zero genesis."""

    if type(value) is not int or value < -1 or value > 2**63 - 1:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    if value == -1 and previous_manifest_sha256 != PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256:
        raise PhysicalWalObjectManifestError(f"{label} may be -1 only at WAL-chain genesis")
    return value


def _object_key(value: object, *, label: str) -> str:
    key = _text(value, label=label, pattern=OBJECT_KEY_RE)
    components = key.split("/")
    if (
        ".." in components
        or not key.endswith(".age")
        or any(
            component.casefold() in _MUTABLE_ALIAS_COMPONENTS
            or component.split(".", 1)[0].casefold() in _MUTABLE_ALIAS_COMPONENTS
            for component in components
        )
    ):
        raise PhysicalWalObjectManifestError(f"{label} must not be a mutable alias")
    return key


def _version_id(value: object, *, label: str) -> str:
    version = _text(value, label=label, pattern=VERSION_ID_RE)
    if version.casefold() in _MUTABLE_ALIAS_COMPONENTS | {"null", "none"}:
        raise PhysicalWalObjectManifestError(f"{label} must name an immutable object version")
    return version


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalObjectManifestError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    return decoded


def _public_key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise PhysicalWalObjectManifestError(f"{label} is invalid") from exc
    return value


def _writer_term(value: object, *, label: str) -> PhysicalWalWriterTermBinding:
    term = _exact_mapping(value, fields=_TERM_FIELDS, label=f"{label} term")
    return PhysicalWalWriterTermBinding(
        epoch=_positive_int(term["epoch"], label=f"{label} term epoch"),
        lease_id=_text(term["lease_id"], label=f"{label} term lease", pattern=LEASE_ID_RE),
        witnessed_term_proof_sha256=_sha256(
            term["witnessed_term_proof_sha256"], label=f"{label} term proof hash"
        ),
    )


def _immutable_object(
    value: object,
    *,
    label: str,
    expected_kind: str,
) -> PhysicalWalImmutableObject:
    descriptor = _exact_mapping(value, fields=_OBJECT_FIELDS, label=label)
    if (
        descriptor["schema"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA
        or descriptor["version"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION
        or descriptor["object_kind"] != expected_kind
        or descriptor["encryption"] != PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION
        or descriptor["immutability"] != PHYSICAL_WAL_OBJECT_IMMUTABILITY
    ):
        raise PhysicalWalObjectManifestError(f"{label} schema, kind, encryption, or immutability is invalid")
    return PhysicalWalImmutableObject(
        object_kind=expected_kind,
        object_key=_object_key(descriptor["object_key"], label=f"{label} object key"),
        version_id=_version_id(descriptor["version_id"], label=f"{label} version ID"),
        ciphertext_sha256=_sha256(
            descriptor["ciphertext_sha256"], label=f"{label} ciphertext hash"
        ),
        ciphertext_bytes=_positive_int(
            descriptor["ciphertext_bytes"],
            label=f"{label} ciphertext bytes",
            maximum=MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES,
        ),
        age_recipient=_text(
            descriptor["age_recipient"], label=f"{label} age recipient", pattern=AGE_RECIPIENT_RE
        ),
    )


def _source_signer(value: object, *, label: str) -> bytes:
    signer = _exact_mapping(value, fields=_SIGNER_FIELDS, label=f"{label} signer")
    if signer["algorithm"] != PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM:
        raise PhysicalWalObjectManifestError(f"{label} signer algorithm is invalid")
    public_key = _decode_base64(
        signer["public_key_base64"], label=f"{label} signer public key", expected_bytes=32
    )
    _public_key(public_key, label=f"{label} signer public key")
    if _text(signer["key_id"], label=f"{label} signer key ID", pattern=_KEY_ID_RE) != _public_key_id(
        public_key
    ):
        raise PhysicalWalObjectManifestError(f"{label} signer key ID does not match its public key")
    return public_key


def _signature(value: object, *, label: str) -> bytes:
    signature = _exact_mapping(value, fields=_SIGNATURE_FIELDS, label=f"{label} signature")
    if signature["algorithm"] != PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM:
        raise PhysicalWalObjectManifestError(f"{label} signature algorithm is invalid")
    return _decode_base64(
        signature["signature_base64"], label=f"{label} signature", expected_bytes=64
    )


def _signature_input(manifest: Mapping[str, Any]) -> bytes:
    schema = manifest.get("schema")
    try:
        domain = _SIGNATURE_DOMAINS[schema]
    except (KeyError, TypeError) as exc:
        raise PhysicalWalObjectManifestError("manifest schema is invalid") from exc
    unsigned = {key: value for key, value in manifest.items() if key != "source_signature"}
    return domain + _canonical(unsigned, label="manifest")


def _parse_raw_manifest(value: object, *, label: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, str):
        try:
            value = value.encode("ascii", "strict")
        except UnicodeEncodeError as exc:
            raise PhysicalWalObjectManifestError(f"{label} is invalid JSON") from exc
    if isinstance(value, Mapping):
        try:
            manifest = dict(value)
            raw = _canonical(manifest, label=label)
        except (TypeError, ValueError) as exc:  # Defensive against exotic Mapping implementations.
            raise PhysicalWalObjectManifestError(f"{label} is invalid") from exc
        if not raw or len(raw) > MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
            raise PhysicalWalObjectManifestError(f"{label} byte size is invalid")
        return manifest, raw
    if not isinstance(value, bytes) or not value or len(value) > MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
        raise PhysicalWalObjectManifestError(f"{label} is invalid")
    try:
        decoded = value.decode("ascii", "strict")
        manifest = json.loads(
            decoded,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalObjectManifestError:
        raise
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalWalObjectManifestError(f"{label} is invalid JSON") from exc
    if not isinstance(manifest, dict) or _canonical(manifest, label=label) != value:
        raise PhysicalWalObjectManifestError(f"{label} is not canonical JSON")
    return manifest, value


def _verify_signature(manifest: Mapping[str, Any], *, label: str, source_public_key: bytes) -> None:
    signature = _signature(manifest.get("source_signature"), label=label)
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(source_public_key).verify(
            signature,
            _signature_input(manifest),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PhysicalWalObjectManifestError(f"{label} signature is invalid") from exc


def _base_facts(manifest: Mapping[str, Any], *, raw: bytes, verify: bool) -> _BaseFacts:
    value = _exact_mapping(manifest, fields=_BASE_BACKUP_FIELDS, label="base backup manifest")
    if (
        value["schema"] != PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA
        or value["version"] != PHYSICAL_WAL_OBJECT_MANIFEST_VERSION
        or value["kind"] != "physical_postgresql_base_backup"
    ):
        raise PhysicalWalObjectManifestError("base backup manifest schema, version, or kind is invalid")
    source = _site(value["source_site"], label="base backup source site")
    destination = _site(value["destination_site"], label="base backup destination site")
    if source == destination:
        raise PhysicalWalObjectManifestError("base backup source and destination overlap")
    wal_segment_size = _wal_segment_size(
        value["wal_segment_size_bytes"], label="base backup WAL segment size"
    )
    baseline_lsn, baseline_lsn_value = _lsn(value["baseline_wal_lsn"], label="base backup baseline WAL LSN")
    wal_chain_start_lsn, wal_chain_start_value = _lsn(
        value["wal_chain_start_lsn"], label="base backup WAL chain start LSN"
    )
    backup_end_lsn, backup_end_lsn_value = _lsn(
        value["base_backup_end_lsn"], label="base backup end WAL LSN"
    )
    if backup_end_lsn_value <= baseline_lsn_value:
        raise PhysicalWalObjectManifestError("base backup end WAL LSN must follow the baseline WAL LSN")
    if (
        wal_chain_start_value % wal_segment_size
        or wal_chain_start_value > baseline_lsn_value
        or baseline_lsn_value >= wal_chain_start_value + wal_segment_size
    ):
        raise PhysicalWalObjectManifestError(
            "base backup WAL chain start does not cover the baseline LSN on a segment boundary"
        )
    source_key = _source_signer(value["source_signer"], label="base backup")
    _signature(value["source_signature"], label="base backup")
    if verify:
        _verify_signature(value, label="base backup", source_public_key=source_key)
    return _BaseFacts(
        raw=raw,
        source_public_key=source_key,
        source_site=source,
        destination_site=destination,
        campaign_id=_text(value["campaign_id"], label="base backup campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_text(value["release_sha"], label="base backup release", pattern=RELEASE_SHA_RE),
        writer_term=_writer_term(value["writer_term"], label="base backup"),
        baseline_generation_id=_text(
            value["baseline_generation_id"],
            label="base backup generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        database_system_identifier=_text(
            value["database_system_identifier"],
            label="base backup database system identifier",
            pattern=_SYSTEM_IDENTIFIER_RE,
        ),
        timeline_id=_timeline_id(value["timeline_id"], label="base backup timeline"),
        wal_segment_size_bytes=wal_segment_size,
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=wal_chain_start_lsn,
        base_backup_end_lsn=backup_end_lsn,
        base_backup_object=_immutable_object(
            value["base_backup_object"],
            label="base backup object",
            expected_kind="physical_postgresql_base_backup",
        ),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _segment(
    value: object,
    *,
    label: str,
    expected_timeline: int,
    wal_segment_size: int,
) -> PhysicalWalSegment:
    segment = _exact_mapping(value, fields=_SEGMENT_FIELDS, label=label)
    timeline_id = _timeline_id(segment["timeline_id"], label=f"{label} timeline")
    if timeline_id != expected_timeline:
        raise PhysicalWalObjectManifestError(f"{label} timeline is not continuous with its base backup")
    wal_name = _text(segment["wal_segment_name"], label=f"{label} WAL segment name", pattern=_WAL_SEGMENT_NAME_RE)
    start_lsn, start_value = _lsn(segment["start_lsn"], label=f"{label} start LSN")
    end_lsn, end_value = _lsn(segment["end_lsn"], label=f"{label} end LSN")
    expected_name = _wal_filename_for(
        timeline_id=timeline_id,
        start_lsn_value=start_value,
        segment_size=wal_segment_size,
    )
    ordinal = _nonnegative_int(segment["ordinal"], label=f"{label} ordinal", maximum=2**63 - 1)
    expected_ordinal = _wal_segment_ordinal_for_start(
        start_value,
        segment_size=wal_segment_size,
        label=f"{label} WAL segment",
    )
    if wal_name != expected_name:
        raise PhysicalWalObjectManifestError(
            f"{label} WAL segment name does not match PostgreSQL WAL geometry"
        )
    if ordinal != expected_ordinal:
        raise PhysicalWalObjectManifestError(
            f"{label} WAL segment ordinal does not match PostgreSQL WAL geometry"
        )
    if end_value != start_value + wal_segment_size:
        raise PhysicalWalObjectManifestError(
            f"{label} WAL range does not match the pinned PostgreSQL WAL segment size"
        )
    return PhysicalWalSegment(
        ordinal=ordinal,
        wal_segment_name=wal_name,
        timeline_id=timeline_id,
        start_lsn=start_lsn,
        end_lsn=end_lsn,
        object=_immutable_object(
            segment["object"], label=f"{label} object", expected_kind="postgresql_wal_segment"
        ),
    )


def _segments(
    value: object,
    *,
    timeline_id: int,
    wal_segment_size: int,
) -> tuple[PhysicalWalSegment, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PhysicalWalObjectManifestError("WAL segments are invalid")
    if not value or len(value) > MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST:
        raise PhysicalWalObjectManifestError("WAL segments are invalid")
    result: list[PhysicalWalSegment] = []
    names: set[str] = set()
    for index, item in enumerate(value, start=1):
        segment = _segment(
            item,
            label=f"WAL segment {index}",
            expected_timeline=timeline_id,
            wal_segment_size=wal_segment_size,
        )
        if segment.wal_segment_name in names:
            raise PhysicalWalObjectManifestError(
                "WAL segments contain a duplicate PostgreSQL WAL filename"
            )
        names.add(segment.wal_segment_name)
        if result:
            prior = result[-1]
            _prior_end, prior_end_value = _lsn(prior.end_lsn, label="prior WAL end LSN")
            _start, start_value = _lsn(segment.start_lsn, label="WAL start LSN")
            if segment.ordinal != prior.ordinal + 1 or start_value != prior_end_value:
                raise PhysicalWalObjectManifestError("WAL segments are not ordered and contiguous")
        result.append(segment)
    if len({segment.object.age_recipient for segment in result}) != 1:
        raise PhysicalWalObjectManifestError("WAL segments do not share one destination age recipient")
    return tuple(result)


def _segment_facts(manifest: Mapping[str, Any], *, raw: bytes, verify: bool) -> _SegmentFacts:
    value = _exact_mapping(manifest, fields=_SEGMENT_MANIFEST_FIELDS, label="WAL segment manifest")
    if (
        value["schema"] != PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA
        or value["version"] != PHYSICAL_WAL_OBJECT_MANIFEST_VERSION
        or value["kind"] != "postgresql_wal_segment_chain"
    ):
        raise PhysicalWalObjectManifestError("WAL segment manifest schema, version, or kind is invalid")
    source = _site(value["source_site"], label="WAL source site")
    destination = _site(value["destination_site"], label="WAL destination site")
    if source == destination:
        raise PhysicalWalObjectManifestError("WAL source and destination overlap")
    timeline_id = _timeline_id(value["timeline_id"], label="WAL timeline")
    wal_segment_size = _wal_segment_size(value["wal_segment_size_bytes"], label="WAL segment size")
    source_key = _source_signer(value["source_signer"], label="WAL")
    _signature(value["source_signature"], label="WAL")
    if verify:
        _verify_signature(value, label="WAL", source_public_key=source_key)
    previous_end_lsn = _lsn(value["previous_end_lsn"], label="WAL previous end LSN")[0]
    previous_manifest_sha256 = _sha256(
        value["previous_manifest_sha256"], label="WAL previous manifest hash"
    )
    previous_segment_ordinal = _previous_wal_segment_ordinal(
        value["previous_segment_ordinal"],
        previous_manifest_sha256=previous_manifest_sha256,
        label="WAL previous segment ordinal",
    )
    segments = _segments(
        value["segments"], timeline_id=timeline_id, wal_segment_size=wal_segment_size
    )
    if (
        segments[0].ordinal != previous_segment_ordinal + 1
        or segments[0].start_lsn != previous_end_lsn
    ):
        raise PhysicalWalObjectManifestError("WAL chain link has a frontier hole, replay, or reorder")
    return _SegmentFacts(
        raw=raw,
        source_public_key=source_key,
        source_site=source,
        destination_site=destination,
        campaign_id=_text(value["campaign_id"], label="WAL campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_text(value["release_sha"], label="WAL release", pattern=RELEASE_SHA_RE),
        writer_term=_writer_term(value["writer_term"], label="WAL"),
        baseline_generation_id=_text(
            value["baseline_generation_id"], label="WAL base generation", pattern=STREAM_GENERATION_ID_RE
        ),
        baseline_manifest_sha256=_sha256(
            value["baseline_manifest_sha256"], label="WAL baseline manifest hash"
        ),
        database_system_identifier=_text(
            value["database_system_identifier"],
            label="WAL database system identifier",
            pattern=_SYSTEM_IDENTIFIER_RE,
        ),
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size,
        previous_manifest_sha256=previous_manifest_sha256,
        previous_end_lsn=previous_end_lsn,
        previous_segment_ordinal=previous_segment_ordinal,
        segments=segments,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _inventory_shards(value: object) -> tuple[PhysicalWalBlobInventoryShard, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PhysicalWalObjectManifestError("blob inventory shards are invalid")
    if not value or len(value) > MAX_PHYSICAL_WAL_BLOB_INVENTORY_SHARDS:
        raise PhysicalWalObjectManifestError("blob inventory shards are invalid")
    result: list[PhysicalWalBlobInventoryShard] = []
    for index, item in enumerate(value, start=1):
        shard = _exact_mapping(item, fields=_INVENTORY_SHARD_FIELDS, label=f"blob inventory shard {index}")
        ordinal = _positive_int(shard["ordinal"], label=f"blob inventory shard {index} ordinal")
        if ordinal != index:
            raise PhysicalWalObjectManifestError("blob inventory shards are not ordered and contiguous")
        result.append(
            PhysicalWalBlobInventoryShard(
                ordinal=ordinal,
                plaintext_sha256=_sha256(
                    shard["plaintext_sha256"], label=f"blob inventory shard {index} plaintext hash"
                ),
                plaintext_bytes=_positive_int(
                    shard["plaintext_bytes"],
                    label=f"blob inventory shard {index} plaintext bytes",
                    maximum=MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES,
                ),
                entry_count=_positive_int(
                    shard["entry_count"],
                    label=f"blob inventory shard {index} entry count",
                    maximum=2**63 - 1,
                ),
                object=_immutable_object(
                    shard["object"],
                    label=f"blob inventory shard {index} object",
                    expected_kind="blob_inventory_shard",
                ),
            )
        )
    if len({shard.object.age_recipient for shard in result}) != 1:
        raise PhysicalWalObjectManifestError("blob inventory shards do not share one destination age recipient")
    return tuple(result)


def _blob_facts(manifest: Mapping[str, Any], *, raw: bytes, verify: bool) -> _BlobFacts:
    value = _exact_mapping(manifest, fields=_BLOB_FRONTIER_FIELDS, label="blob frontier manifest")
    if (
        value["schema"] != PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA
        or value["version"] != PHYSICAL_WAL_OBJECT_MANIFEST_VERSION
        or value["kind"] != "blob_inventory_frontier"
    ):
        raise PhysicalWalObjectManifestError("blob frontier schema, version, or kind is invalid")
    source = _site(value["source_site"], label="blob frontier source site")
    destination = _site(value["destination_site"], label="blob frontier destination site")
    if source == destination:
        raise PhysicalWalObjectManifestError("blob frontier source and destination overlap")
    wal_segment_size = _wal_segment_size(
        value["wal_segment_size_bytes"], label="blob frontier WAL segment size"
    )
    previous_lsn, previous_value = _lsn(
        value["previous_frontier_wal_lsn"], label="blob previous frontier WAL LSN"
    )
    frontier_lsn, frontier_value = _lsn(
        value["blob_object_frontier_wal_lsn"], label="blob object frontier WAL LSN"
    )
    if frontier_value < previous_value:
        raise PhysicalWalObjectManifestError("blob frontier WAL LSN regresses")
    if type(value["objects_complete"]) is not bool or value["objects_complete"] is not True:
        raise PhysicalWalObjectManifestError("blob frontier must explicitly declare complete objects")
    source_key = _source_signer(value["source_signer"], label="blob frontier")
    _signature(value["source_signature"], label="blob frontier")
    if verify:
        _verify_signature(value, label="blob frontier", source_public_key=source_key)
    return _BlobFacts(
        raw=raw,
        source_public_key=source_key,
        source_site=source,
        destination_site=destination,
        campaign_id=_text(value["campaign_id"], label="blob frontier campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_text(value["release_sha"], label="blob frontier release", pattern=RELEASE_SHA_RE),
        writer_term=_writer_term(value["writer_term"], label="blob frontier"),
        baseline_generation_id=_text(
            value["baseline_generation_id"],
            label="blob frontier base generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        baseline_manifest_sha256=_sha256(
            value["baseline_manifest_sha256"], label="blob frontier baseline manifest hash"
        ),
        database_system_identifier=_text(
            value["database_system_identifier"],
            label="blob frontier database system identifier",
            pattern=_SYSTEM_IDENTIFIER_RE,
        ),
        timeline_id=_timeline_id(value["timeline_id"], label="blob frontier timeline"),
        wal_segment_size_bytes=wal_segment_size,
        previous_manifest_sha256=_sha256(
            value["previous_manifest_sha256"], label="blob previous manifest hash"
        ),
        previous_frontier_wal_lsn=previous_lsn,
        blob_object_frontier_wal_lsn=frontier_lsn,
        objects_complete=True,
        inventory_shards=_inventory_shards(value["inventory_shards"]),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_physical_wal_object_manifest_json(raw: bytes | str) -> dict[str, Any]:
    """Strictly parse canonical raw JSON without treating it as authenticated.

    This is intended for a future adapter immediately after age decryption.
    It rejects duplicate keys and any byte spelling that differs from the
    canonical signed representation, but callers still must call one of the
    ``verify_*`` functions with a root-pinned source public key.
    """

    manifest, _canonical_raw = _parse_raw_manifest(raw, label="manifest JSON")
    schema = manifest.get("schema")
    if schema == PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA:
        _base_facts(manifest, raw=_canonical_raw, verify=False)
    elif schema == PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA:
        _segment_facts(manifest, raw=_canonical_raw, verify=False)
    elif schema == PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA:
        _blob_facts(manifest, raw=_canonical_raw, verify=False)
    else:
        raise PhysicalWalObjectManifestError("manifest schema is invalid")
    return manifest


def _private_signer_public_key(value: object) -> bytes:
    try:
        from cryptography.hazmat.primitives import serialization

        public_key = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise PhysicalWalObjectManifestError("source signer is invalid") from exc
    return _public_key(public_key, label="source signer public key")


def _sign_manifest(unsigned: dict[str, Any], *, source_signer: object) -> dict[str, Any]:
    public_key = _private_signer_public_key(source_signer)
    value = dict(unsigned)
    value["source_signer"] = {
        "algorithm": PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _public_key_id(public_key),
    }
    value["source_signature"] = {
        "algorithm": PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(b"\x00" * 64).decode("ascii"),
    }
    try:
        signature = source_signer.sign(_signature_input(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalObjectManifestError("source signer is invalid") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise PhysicalWalObjectManifestError("source signer returned an invalid signature")
    value["source_signature"] = {
        "algorithm": PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    return value


def _object_mapping(value: Mapping[str, Any], *, expected_kind: str) -> dict[str, Any]:
    descriptor = _immutable_object(value, label="Object descriptor", expected_kind=expected_kind)
    return {
        "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
        "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
        "object_kind": descriptor.object_kind,
        "object_key": descriptor.object_key,
        "version_id": descriptor.version_id,
        "ciphertext_sha256": descriptor.ciphertext_sha256,
        "ciphertext_bytes": descriptor.ciphertext_bytes,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "age_recipient": descriptor.age_recipient,
        "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    }


def _immutable_object_mapping(value: PhysicalWalImmutableObject) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
        "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
        "object_kind": value.object_kind,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "age_recipient": value.age_recipient,
        "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    }


def _term_mapping(*, writer_epoch: object, writer_lease_id: object, witnessed_term_proof_sha256: object) -> dict[str, Any]:
    term = _writer_term(
        {
            "epoch": writer_epoch,
            "lease_id": writer_lease_id,
            "witnessed_term_proof_sha256": witnessed_term_proof_sha256,
        },
        label="writer",
    )
    return {
        "epoch": term.epoch,
        "lease_id": term.lease_id,
        "witnessed_term_proof_sha256": term.witnessed_term_proof_sha256,
    }


def _common_unsigned(
    *,
    schema: str,
    kind: str,
    source_site: object,
    destination_site: object,
    campaign_id: object,
    release_sha: object,
    writer_epoch: object,
    writer_lease_id: object,
    witnessed_term_proof_sha256: object,
    baseline_generation_id: object,
    database_system_identifier: object,
) -> dict[str, Any]:
    source = _site(source_site, label="source site")
    destination = _site(destination_site, label="destination site")
    if source == destination:
        raise PhysicalWalObjectManifestError("source and destination overlap")
    return {
        "schema": schema,
        "version": PHYSICAL_WAL_OBJECT_MANIFEST_VERSION,
        "kind": kind,
        "source_site": source,
        "destination_site": destination,
        "campaign_id": _text(campaign_id, label="campaign", pattern=CAMPAIGN_ID_RE),
        "release_sha": _text(release_sha, label="release", pattern=RELEASE_SHA_RE),
        "writer_term": _term_mapping(
            writer_epoch=writer_epoch,
            writer_lease_id=writer_lease_id,
            witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        ),
        "baseline_generation_id": _text(
            baseline_generation_id, label="baseline generation", pattern=STREAM_GENERATION_ID_RE
        ),
        "database_system_identifier": _text(
            database_system_identifier,
            label="database system identifier",
            pattern=_SYSTEM_IDENTIFIER_RE,
        ),
    }


def build_physical_wal_base_backup_manifest(
    *,
    source_site: object,
    destination_site: object,
    campaign_id: object,
    release_sha: object,
    writer_epoch: object,
    writer_lease_id: object,
    witnessed_term_proof_sha256: object,
    baseline_generation_id: object,
    database_system_identifier: object,
    timeline_id: object,
    wal_segment_size_bytes: object,
    baseline_wal_lsn: object,
    wal_chain_start_lsn: object,
    base_backup_end_lsn: object,
    base_backup_object: Mapping[str, Any],
    source_signer: object,
) -> dict[str, Any]:
    """Build a signed physical base-backup manifest without doing any I/O."""

    value = _common_unsigned(
        schema=PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA,
        kind="physical_postgresql_base_backup",
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        database_system_identifier=database_system_identifier,
    )
    wal_segment_size = _wal_segment_size(
        wal_segment_size_bytes, label="base backup WAL segment size"
    )
    start, start_value = _lsn(baseline_wal_lsn, label="base backup baseline WAL LSN")
    chain_start, chain_start_value = _lsn(
        wal_chain_start_lsn, label="base backup WAL chain start LSN"
    )
    end, end_value = _lsn(base_backup_end_lsn, label="base backup end WAL LSN")
    if end_value <= start_value:
        raise PhysicalWalObjectManifestError("base backup end WAL LSN must follow the baseline WAL LSN")
    if (
        chain_start_value % wal_segment_size
        or chain_start_value > start_value
        or start_value >= chain_start_value + wal_segment_size
    ):
        raise PhysicalWalObjectManifestError(
            "base backup WAL chain start does not cover the baseline LSN on a segment boundary"
        )
    value.update(
        {
            "timeline_id": _timeline_id(timeline_id, label="base backup timeline"),
            "wal_segment_size_bytes": wal_segment_size,
            "baseline_wal_lsn": start,
            "wal_chain_start_lsn": chain_start,
            "base_backup_end_lsn": end,
            "base_backup_object": _object_mapping(
                base_backup_object, expected_kind="physical_postgresql_base_backup"
            ),
        }
    )
    manifest = _sign_manifest(value, source_signer=source_signer)
    _base_facts(manifest, raw=_canonical(manifest, label="base backup manifest"), verify=True)
    return manifest


def _segment_mapping(
    value: Mapping[str, Any], *, timeline_id: int, wal_segment_size: int
) -> dict[str, Any]:
    segment = _segment(
        value,
        label="WAL segment",
        expected_timeline=timeline_id,
        wal_segment_size=wal_segment_size,
    )
    return {
        "ordinal": segment.ordinal,
        "wal_segment_name": segment.wal_segment_name,
        "timeline_id": segment.timeline_id,
        "start_lsn": segment.start_lsn,
        "end_lsn": segment.end_lsn,
        "object": _immutable_object_mapping(segment.object),
    }


def build_physical_wal_segment_manifest(
    *,
    source_site: object,
    destination_site: object,
    campaign_id: object,
    release_sha: object,
    writer_epoch: object,
    writer_lease_id: object,
    witnessed_term_proof_sha256: object,
    baseline_generation_id: object,
    baseline_manifest_sha256: object,
    database_system_identifier: object,
    timeline_id: object,
    wal_segment_size_bytes: object,
    previous_manifest_sha256: object,
    previous_end_lsn: object,
    previous_segment_ordinal: object,
    segments: Sequence[Mapping[str, Any]],
    source_signer: object,
) -> dict[str, Any]:
    """Build one signed append-only WAL-chain link without an upload."""

    timeline = _timeline_id(timeline_id, label="WAL timeline")
    wal_segment_size = _wal_segment_size(wal_segment_size_bytes, label="WAL segment size")
    prior_hash = _sha256(previous_manifest_sha256, label="WAL previous manifest hash")
    prior_ordinal = _previous_wal_segment_ordinal(
        previous_segment_ordinal,
        previous_manifest_sha256=prior_hash,
        label="WAL previous segment ordinal",
    )
    previous_lsn = _lsn(previous_end_lsn, label="WAL previous end LSN")[0]
    if isinstance(segments, (str, bytes)) or not isinstance(segments, Sequence):
        raise PhysicalWalObjectManifestError("WAL segments are invalid")
    normalized = tuple(
        _segment_mapping(item, timeline_id=timeline, wal_segment_size=wal_segment_size)
        for item in segments
    )
    checked = _segments(
        normalized, timeline_id=timeline, wal_segment_size=wal_segment_size
    )
    if checked[0].ordinal != prior_ordinal + 1 or checked[0].start_lsn != previous_lsn:
        raise PhysicalWalObjectManifestError("WAL chain link does not follow its prior frontier")
    value = _common_unsigned(
        schema=PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA,
        kind="postgresql_wal_segment_chain",
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        database_system_identifier=database_system_identifier,
    )
    value.update(
        {
            "baseline_manifest_sha256": _sha256(
                baseline_manifest_sha256, label="WAL baseline manifest hash"
            ),
            "timeline_id": timeline,
            "wal_segment_size_bytes": wal_segment_size,
            "previous_manifest_sha256": prior_hash,
            "previous_end_lsn": previous_lsn,
            "previous_segment_ordinal": prior_ordinal,
            "segments": list(normalized),
        }
    )
    manifest = _sign_manifest(value, source_signer=source_signer)
    _segment_facts(manifest, raw=_canonical(manifest, label="WAL segment manifest"), verify=True)
    return manifest


def _inventory_shard_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    parsed = _exact_mapping(value, fields=_INVENTORY_SHARD_FIELDS, label="blob inventory shard")
    ordinal = _positive_int(parsed["ordinal"], label="blob inventory shard ordinal")
    return {
        "ordinal": ordinal,
        "plaintext_sha256": _sha256(parsed["plaintext_sha256"], label="blob inventory plaintext hash"),
        "plaintext_bytes": _positive_int(
            parsed["plaintext_bytes"],
            label="blob inventory plaintext bytes",
            maximum=MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES,
        ),
        "entry_count": _positive_int(
            parsed["entry_count"], label="blob inventory entry count", maximum=2**63 - 1
        ),
        "object": _object_mapping(parsed["object"], expected_kind="blob_inventory_shard"),
    }


def build_physical_wal_blob_frontier_manifest(
    *,
    source_site: object,
    destination_site: object,
    campaign_id: object,
    release_sha: object,
    writer_epoch: object,
    writer_lease_id: object,
    witnessed_term_proof_sha256: object,
    baseline_generation_id: object,
    baseline_manifest_sha256: object,
    database_system_identifier: object,
    timeline_id: object,
    wal_segment_size_bytes: object,
    previous_manifest_sha256: object,
    previous_frontier_wal_lsn: object,
    blob_object_frontier_wal_lsn: object,
    inventory_shards: Sequence[Mapping[str, Any]],
    source_signer: object,
) -> dict[str, Any]:
    """Build a signed complete blob-inventory frontier without Object Storage I/O."""

    prior, prior_value = _lsn(previous_frontier_wal_lsn, label="blob previous frontier WAL LSN")
    frontier, frontier_value = _lsn(
        blob_object_frontier_wal_lsn, label="blob object frontier WAL LSN"
    )
    if frontier_value < prior_value:
        raise PhysicalWalObjectManifestError("blob frontier WAL LSN regresses")
    if isinstance(inventory_shards, (str, bytes)) or not isinstance(inventory_shards, Sequence):
        raise PhysicalWalObjectManifestError("blob inventory shards are invalid")
    normalized = tuple(_inventory_shard_mapping(item) for item in inventory_shards)
    _inventory_shards(normalized)
    value = _common_unsigned(
        schema=PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA,
        kind="blob_inventory_frontier",
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        database_system_identifier=database_system_identifier,
    )
    value.update(
        {
            "baseline_manifest_sha256": _sha256(
                baseline_manifest_sha256, label="blob baseline manifest hash"
            ),
            "timeline_id": _timeline_id(timeline_id, label="blob frontier timeline"),
            "wal_segment_size_bytes": _wal_segment_size(
                wal_segment_size_bytes, label="blob frontier WAL segment size"
            ),
            "previous_manifest_sha256": _sha256(
                previous_manifest_sha256, label="blob previous manifest hash"
            ),
            "previous_frontier_wal_lsn": prior,
            "blob_object_frontier_wal_lsn": frontier,
            "objects_complete": True,
            "inventory_shards": list(normalized),
        }
    )
    manifest = _sign_manifest(value, source_signer=source_signer)
    _blob_facts(manifest, raw=_canonical(manifest, label="blob frontier manifest"), verify=True)
    return manifest


def _expected_optional(value: object | None, actual: object, *, label: str) -> None:
    if value is not None and value != actual:
        raise PhysicalWalObjectManifestError(f"{label} does not match the expected binding")


def _expected_term(
    actual: PhysicalWalWriterTermBinding,
    *,
    expected_writer_epoch: int | None,
    expected_writer_lease_id: str | None,
    expected_witnessed_term_proof_sha256: str | None,
) -> None:
    supplied = (
        expected_writer_epoch,
        expected_writer_lease_id,
        expected_witnessed_term_proof_sha256,
    )
    if any(item is not None for item in supplied) and any(item is None for item in supplied):
        raise PhysicalWalObjectManifestError("expected Writer Witness term is incomplete")
    if expected_writer_epoch is None:
        return
    expected = _writer_term(
        {
            "epoch": expected_writer_epoch,
            "lease_id": expected_writer_lease_id,
            "witnessed_term_proof_sha256": expected_witnessed_term_proof_sha256,
        },
        label="expected writer",
    )
    if actual != expected:
        raise PhysicalWalObjectManifestError("manifest Writer Witness term does not match the expected term")


def _expect_common(
    facts: _BaseFacts | _SegmentFacts | _BlobFacts,
    *,
    expected_source_public_key: bytes,
    expected_source_site: str | None,
    expected_destination_site: str | None,
    expected_campaign_id: str | None,
    expected_release_sha: str | None,
    expected_writer_epoch: int | None,
    expected_writer_lease_id: str | None,
    expected_witnessed_term_proof_sha256: str | None,
) -> None:
    expected_key = _public_key(expected_source_public_key, label="expected source public key")
    if facts.source_public_key != expected_key:
        raise PhysicalWalObjectManifestError("manifest signer does not match the expected source public key")
    if expected_source_site is not None:
        _expected_optional(_site(expected_source_site, label="expected source site"), facts.source_site, label="source site")
    if expected_destination_site is not None:
        _expected_optional(
            _site(expected_destination_site, label="expected destination site"),
            facts.destination_site,
            label="destination site",
        )
    if expected_campaign_id is not None:
        _expected_optional(
            _text(expected_campaign_id, label="expected campaign", pattern=CAMPAIGN_ID_RE),
            facts.campaign_id,
            label="campaign",
        )
    if expected_release_sha is not None:
        _expected_optional(
            _text(expected_release_sha, label="expected release", pattern=RELEASE_SHA_RE),
            facts.release_sha,
            label="release",
        )
    _expected_term(
        facts.writer_term,
        expected_writer_epoch=expected_writer_epoch,
        expected_writer_lease_id=expected_writer_lease_id,
        expected_witnessed_term_proof_sha256=expected_witnessed_term_proof_sha256,
    )


def _facts_age_recipients(
    facts: _BaseFacts | _SegmentFacts | _BlobFacts,
) -> tuple[str, ...]:
    if isinstance(facts, _BaseFacts):
        return (facts.base_backup_object.age_recipient,)
    if isinstance(facts, _SegmentFacts):
        return tuple(segment.object.age_recipient for segment in facts.segments)
    return tuple(shard.object.age_recipient for shard in facts.inventory_shards)


def _expect_destination_age_recipient(
    facts: _BaseFacts | _SegmentFacts | _BlobFacts,
    *,
    expected_destination_age_recipient: str | None,
) -> None:
    if expected_destination_age_recipient is None:
        return
    expected = _text(
        expected_destination_age_recipient,
        label="expected destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    if any(value != expected for value in _facts_age_recipients(facts)):
        raise PhysicalWalObjectManifestError(
            "manifest age recipient does not match the pinned destination recipient"
        )


def _mint_base(facts: _BaseFacts) -> VerifiedPhysicalWalBaseBackupManifest:
    result = VerifiedPhysicalWalBaseBackupManifest(
        canonical_manifest=facts.raw,
        source_public_key=facts.source_public_key,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        writer_term=facts.writer_term,
        baseline_generation_id=facts.baseline_generation_id,
        database_system_identifier=facts.database_system_identifier,
        timeline_id=facts.timeline_id,
        wal_segment_size_bytes=facts.wal_segment_size_bytes,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        wal_chain_start_lsn=facts.wal_chain_start_lsn,
        base_backup_end_lsn=facts.base_backup_end_lsn,
        base_backup_object=facts.base_backup_object,
        manifest_sha256=facts.manifest_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BASE_BACKUP_CAPABILITY)
    return result


def verify_physical_wal_base_backup_manifest(
    value: Mapping[str, Any] | bytes | str,
    *,
    expected_source_public_key: bytes,
    expected_source_site: str | None = None,
    expected_destination_site: str | None = None,
    expected_campaign_id: str | None = None,
    expected_release_sha: str | None = None,
    expected_writer_epoch: int | None = None,
    expected_writer_lease_id: str | None = None,
    expected_witnessed_term_proof_sha256: str | None = None,
    expected_baseline_generation_id: str | None = None,
    expected_wal_segment_size_bytes: int | None = None,
    expected_destination_age_recipient: str | None = None,
) -> VerifiedPhysicalWalBaseBackupManifest:
    """Verify a source-signed physical base backup against root-pinned facts."""

    manifest, raw = _parse_raw_manifest(value, label="base backup manifest")
    facts = _base_facts(manifest, raw=raw, verify=True)
    _expect_common(
        facts,
        expected_source_public_key=expected_source_public_key,
        expected_source_site=expected_source_site,
        expected_destination_site=expected_destination_site,
        expected_campaign_id=expected_campaign_id,
        expected_release_sha=expected_release_sha,
        expected_writer_epoch=expected_writer_epoch,
        expected_writer_lease_id=expected_writer_lease_id,
        expected_witnessed_term_proof_sha256=expected_witnessed_term_proof_sha256,
    )
    if expected_baseline_generation_id is not None:
        _expected_optional(
            _text(
                expected_baseline_generation_id,
                label="expected baseline generation",
                pattern=STREAM_GENERATION_ID_RE,
            ),
            facts.baseline_generation_id,
            label="baseline generation",
        )
    if expected_wal_segment_size_bytes is not None:
        _expected_optional(
            _wal_segment_size(
                expected_wal_segment_size_bytes,
                label="expected base backup WAL segment size",
            ),
            facts.wal_segment_size_bytes,
            label="base backup WAL segment size",
        )
    _expect_destination_age_recipient(
        facts, expected_destination_age_recipient=expected_destination_age_recipient
    )
    return _mint_base(facts)


def require_verified_physical_wal_base_backup_manifest(
    value: object,
) -> VerifiedPhysicalWalBaseBackupManifest:
    """Re-verify an opaque base-backup capability before using its facts."""

    if type(value) is not VerifiedPhysicalWalBaseBackupManifest:
        raise PhysicalWalObjectManifestError("verified base backup capability is required")
    if value._capability is not _VERIFIED_BASE_BACKUP_CAPABILITY:
        raise PhysicalWalObjectManifestError("verified base backup was not authorized")
    normalized = verify_physical_wal_base_backup_manifest(
        value.canonical_manifest,
        expected_source_public_key=value.source_public_key,
    )
    if normalized != value:
        raise PhysicalWalObjectManifestError("verified base backup is not normalized")
    return value


def _mint_segment(facts: _SegmentFacts) -> VerifiedPhysicalWalSegmentManifest:
    result = VerifiedPhysicalWalSegmentManifest(
        canonical_manifest=facts.raw,
        source_public_key=facts.source_public_key,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        writer_term=facts.writer_term,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        database_system_identifier=facts.database_system_identifier,
        timeline_id=facts.timeline_id,
        wal_segment_size_bytes=facts.wal_segment_size_bytes,
        previous_manifest_sha256=facts.previous_manifest_sha256,
        previous_end_lsn=facts.previous_end_lsn,
        previous_segment_ordinal=facts.previous_segment_ordinal,
        segments=facts.segments,
        manifest_sha256=facts.manifest_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_SEGMENT_CAPABILITY)
    return result


def _expect_derivative_base(
    facts: _SegmentFacts | _BlobFacts,
    baseline: VerifiedPhysicalWalBaseBackupManifest,
) -> None:
    if (
        facts.source_public_key != baseline.source_public_key
        or facts.source_site != baseline.source_site
        or facts.destination_site != baseline.destination_site
        or facts.campaign_id != baseline.campaign_id
        or facts.release_sha != baseline.release_sha
        or facts.writer_term != baseline.writer_term
        or facts.baseline_generation_id != baseline.baseline_generation_id
        or facts.database_system_identifier != baseline.database_system_identifier
        or facts.timeline_id != baseline.timeline_id
        or facts.wal_segment_size_bytes != baseline.wal_segment_size_bytes
        or facts.baseline_manifest_sha256 != baseline.manifest_sha256
    ):
        raise PhysicalWalObjectManifestError("manifest is not bound to the verified base-backup lineage")


def verify_physical_wal_segment_manifest(
    value: Mapping[str, Any] | bytes | str,
    *,
    expected_source_public_key: bytes,
    expected_baseline: VerifiedPhysicalWalBaseBackupManifest | None = None,
    expected_previous_manifest_sha256: str | None = None,
    expected_previous_end_lsn: str | None = None,
    expected_previous_segment_ordinal: int | None = None,
    expected_destination_age_recipient: str | None = None,
) -> VerifiedPhysicalWalSegmentManifest:
    """Verify one signed WAL-chain link and, when supplied, its predecessor."""

    manifest, raw = _parse_raw_manifest(value, label="WAL segment manifest")
    facts = _segment_facts(manifest, raw=raw, verify=True)
    _expect_common(
        facts,
        expected_source_public_key=expected_source_public_key,
        expected_source_site=None,
        expected_destination_site=None,
        expected_campaign_id=None,
        expected_release_sha=None,
        expected_writer_epoch=None,
        expected_writer_lease_id=None,
        expected_witnessed_term_proof_sha256=None,
    )
    if expected_baseline is not None:
        baseline = require_verified_physical_wal_base_backup_manifest(expected_baseline)
        _expect_derivative_base(facts, baseline)
    if expected_previous_manifest_sha256 is not None:
        _expected_optional(
            _sha256(expected_previous_manifest_sha256, label="expected previous WAL manifest hash"),
            facts.previous_manifest_sha256,
            label="previous WAL manifest",
        )
    if expected_previous_end_lsn is not None:
        previous, previous_value = _lsn(
            expected_previous_end_lsn, label="expected previous WAL end LSN"
        )
        _expected_optional(previous, facts.previous_end_lsn, label="previous WAL end LSN")
        _first, first_value = _lsn(facts.segments[0].start_lsn, label="first WAL segment start LSN")
        if previous_value != first_value:
            raise PhysicalWalObjectManifestError("WAL chain link has a frontier hole or reorder")
    if expected_previous_segment_ordinal is not None:
        expected_ordinal = _previous_wal_segment_ordinal(
            expected_previous_segment_ordinal,
            previous_manifest_sha256=facts.previous_manifest_sha256,
            label="expected previous WAL segment ordinal",
        )
        if facts.previous_segment_ordinal != expected_ordinal or facts.segments[0].ordinal != expected_ordinal + 1:
            raise PhysicalWalObjectManifestError("WAL chain link ordinal is replayed, reordered, or has a hole")
    _expect_destination_age_recipient(
        facts, expected_destination_age_recipient=expected_destination_age_recipient
    )
    return _mint_segment(facts)


def require_verified_physical_wal_segment_manifest(
    value: object,
) -> VerifiedPhysicalWalSegmentManifest:
    """Re-verify opaque WAL-chain facts before using their predecessor links."""

    if type(value) is not VerifiedPhysicalWalSegmentManifest:
        raise PhysicalWalObjectManifestError("verified WAL segment capability is required")
    if value._capability is not _VERIFIED_SEGMENT_CAPABILITY:
        raise PhysicalWalObjectManifestError("verified WAL segment manifest was not authorized")
    normalized = verify_physical_wal_segment_manifest(
        value.canonical_manifest,
        expected_source_public_key=value.source_public_key,
    )
    if normalized != value:
        raise PhysicalWalObjectManifestError("verified WAL segment manifest is not normalized")
    return value


def _mint_blob(facts: _BlobFacts) -> VerifiedPhysicalWalBlobFrontierManifest:
    result = VerifiedPhysicalWalBlobFrontierManifest(
        canonical_manifest=facts.raw,
        source_public_key=facts.source_public_key,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        writer_term=facts.writer_term,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        database_system_identifier=facts.database_system_identifier,
        timeline_id=facts.timeline_id,
        wal_segment_size_bytes=facts.wal_segment_size_bytes,
        previous_manifest_sha256=facts.previous_manifest_sha256,
        previous_frontier_wal_lsn=facts.previous_frontier_wal_lsn,
        blob_object_frontier_wal_lsn=facts.blob_object_frontier_wal_lsn,
        objects_complete=facts.objects_complete,
        inventory_shards=facts.inventory_shards,
        manifest_sha256=facts.manifest_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BLOB_FRONTIER_CAPABILITY)
    return result


def verify_physical_wal_blob_frontier_manifest(
    value: Mapping[str, Any] | bytes | str,
    *,
    expected_source_public_key: bytes,
    expected_baseline: VerifiedPhysicalWalBaseBackupManifest | None = None,
    expected_previous_manifest_sha256: str | None = None,
    expected_previous_frontier_wal_lsn: str | None = None,
    expected_wal_frontier_lsn: str | None = None,
    expected_destination_age_recipient: str | None = None,
) -> VerifiedPhysicalWalBlobFrontierManifest:
    """Verify a signed blob-inventory frontier against optional lineage pins."""

    manifest, raw = _parse_raw_manifest(value, label="blob frontier manifest")
    facts = _blob_facts(manifest, raw=raw, verify=True)
    _expect_common(
        facts,
        expected_source_public_key=expected_source_public_key,
        expected_source_site=None,
        expected_destination_site=None,
        expected_campaign_id=None,
        expected_release_sha=None,
        expected_writer_epoch=None,
        expected_writer_lease_id=None,
        expected_witnessed_term_proof_sha256=None,
    )
    if expected_baseline is not None:
        baseline = require_verified_physical_wal_base_backup_manifest(expected_baseline)
        _expect_derivative_base(facts, baseline)
    if expected_previous_manifest_sha256 is not None:
        _expected_optional(
            _sha256(expected_previous_manifest_sha256, label="expected previous blob manifest hash"),
            facts.previous_manifest_sha256,
            label="previous blob manifest",
        )
    if expected_previous_frontier_wal_lsn is not None:
        expected, _ignored = _lsn(
            expected_previous_frontier_wal_lsn, label="expected previous blob frontier WAL LSN"
        )
        _expected_optional(expected, facts.previous_frontier_wal_lsn, label="previous blob frontier")
    if expected_wal_frontier_lsn is not None:
        expected, _ignored = _lsn(expected_wal_frontier_lsn, label="expected WAL frontier LSN")
        if facts.blob_object_frontier_wal_lsn != expected:
            raise PhysicalWalObjectManifestError("blob frontier does not match the expected WAL frontier")
    _expect_destination_age_recipient(
        facts, expected_destination_age_recipient=expected_destination_age_recipient
    )
    return _mint_blob(facts)


def require_verified_physical_wal_blob_frontier_manifest(
    value: object,
) -> VerifiedPhysicalWalBlobFrontierManifest:
    """Re-verify opaque blob-frontier facts before a receiver inspects them."""

    if type(value) is not VerifiedPhysicalWalBlobFrontierManifest:
        raise PhysicalWalObjectManifestError("verified blob frontier capability is required")
    if value._capability is not _VERIFIED_BLOB_FRONTIER_CAPABILITY:
        raise PhysicalWalObjectManifestError("verified blob frontier was not authorized")
    normalized = verify_physical_wal_blob_frontier_manifest(
        value.canonical_manifest,
        expected_source_public_key=value.source_public_key,
    )
    if normalized != value:
        raise PhysicalWalObjectManifestError("verified blob frontier is not normalized")
    return value


def _object_version_pairs(
    baseline: VerifiedPhysicalWalBaseBackupManifest,
    wal_manifests: Sequence[VerifiedPhysicalWalSegmentManifest],
    blob_frontier: VerifiedPhysicalWalBlobFrontierManifest,
) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = [
        (baseline.base_backup_object.object_key, baseline.base_backup_object.version_id)
    ]
    for manifest in wal_manifests:
        pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
    pairs.extend(
        (shard.object.object_key, shard.object.version_id) for shard in blob_frontier.inventory_shards
    )
    return tuple(pairs)


def _accepted_manifest_hashes(value: Collection[str]) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        raise PhysicalWalObjectManifestError("accepted manifest hashes are invalid")
    return frozenset(_sha256(item, label="accepted manifest hash") for item in value)


def _accepted_object_versions(value: Collection[tuple[str, str]]) -> frozenset[tuple[str, str]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Collection):
        raise PhysicalWalObjectManifestError("accepted Object versions are invalid")
    result: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise PhysicalWalObjectManifestError("accepted Object version is invalid")
        result.add(
            (
                _object_key(item[0], label="accepted Object key"),
                _version_id(item[1], label="accepted Object version ID"),
            )
        )
    return frozenset(result)


def verify_physical_wal_object_storage_bundle(
    *,
    base_backup_manifest: Mapping[str, Any] | bytes | str,
    wal_segment_manifests: Sequence[Mapping[str, Any] | bytes | str],
    blob_frontier_manifest: Mapping[str, Any] | bytes | str,
    expected_source_public_key: bytes,
    expected_source_site: str,
    expected_destination_site: str,
    expected_campaign_id: str,
    expected_release_sha: str,
    expected_writer_epoch: int,
    expected_writer_lease_id: str,
    expected_witnessed_term_proof_sha256: str,
    expected_baseline_generation_id: str,
    expected_wal_segment_size_bytes: int,
    expected_destination_age_recipient: str,
    accepted_manifest_sha256es: Collection[str] = (),
    accepted_object_versions: Collection[tuple[str, str]] = (),
) -> VerifiedPhysicalWalObjectStorageBundle:
    """Verify an exact base/WAL/blob continuity point without I/O.

    The caller must pass a durable set of already-consumed manifest hashes.
    This function rejects an in-memory replay against that set, but it cannot
    replace the future receiver adapter's transactional consume/CAS record.
    """

    baseline = verify_physical_wal_base_backup_manifest(
        base_backup_manifest,
        expected_source_public_key=expected_source_public_key,
        expected_source_site=expected_source_site,
        expected_destination_site=expected_destination_site,
        expected_campaign_id=expected_campaign_id,
        expected_release_sha=expected_release_sha,
        expected_writer_epoch=expected_writer_epoch,
        expected_writer_lease_id=expected_writer_lease_id,
        expected_witnessed_term_proof_sha256=expected_witnessed_term_proof_sha256,
        expected_baseline_generation_id=expected_baseline_generation_id,
        expected_wal_segment_size_bytes=expected_wal_segment_size_bytes,
        expected_destination_age_recipient=expected_destination_age_recipient,
    )
    if isinstance(wal_segment_manifests, (str, bytes)) or not isinstance(wal_segment_manifests, Sequence):
        raise PhysicalWalObjectManifestError("WAL segment manifest chain is invalid")
    if not wal_segment_manifests:
        raise PhysicalWalObjectManifestError("physical base backup is missing its required WAL chain")
    prior_manifest = PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256
    prior_end_lsn = baseline.wal_chain_start_lsn
    _chain_start, chain_start_value = _lsn(
        baseline.wal_chain_start_lsn,
        label="base backup WAL chain start LSN",
    )
    prior_ordinal = _wal_segment_ordinal_for_start(
        chain_start_value,
        segment_size=baseline.wal_segment_size_bytes,
        label="base backup WAL chain",
    ) - 1
    wal_results: list[VerifiedPhysicalWalSegmentManifest] = []
    for raw in wal_segment_manifests:
        current = verify_physical_wal_segment_manifest(
            raw,
            expected_source_public_key=expected_source_public_key,
            expected_baseline=baseline,
            expected_previous_manifest_sha256=prior_manifest,
            expected_previous_end_lsn=prior_end_lsn,
            expected_previous_segment_ordinal=prior_ordinal,
            expected_destination_age_recipient=expected_destination_age_recipient,
        )
        wal_results.append(current)
        prior_manifest = current.manifest_sha256
        prior_end_lsn = current.end_lsn
        prior_ordinal = current.last_segment_ordinal
    _backup_end, backup_end_value = _lsn(baseline.base_backup_end_lsn, label="base backup end WAL LSN")
    _terminal, terminal_value = _lsn(prior_end_lsn, label="WAL chain terminal LSN")
    if terminal_value < backup_end_value:
        raise PhysicalWalObjectManifestError("WAL chain does not recover the base backup to its stop LSN")
    blob = verify_physical_wal_blob_frontier_manifest(
        blob_frontier_manifest,
        expected_source_public_key=expected_source_public_key,
        expected_baseline=baseline,
        expected_previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
        expected_previous_frontier_wal_lsn=baseline.baseline_wal_lsn,
        expected_wal_frontier_lsn=prior_end_lsn,
        expected_destination_age_recipient=expected_destination_age_recipient,
    )
    manifest_hashes = (baseline.manifest_sha256,) + tuple(
        manifest.manifest_sha256 for manifest in wal_results
    ) + (blob.manifest_sha256,)
    if len(set(manifest_hashes)) != len(manifest_hashes):
        raise PhysicalWalObjectManifestError("manifest chain contains a replayed manifest")
    accepted = _accepted_manifest_hashes(accepted_manifest_sha256es)
    if accepted.intersection(manifest_hashes):
        raise PhysicalWalObjectManifestError("manifest bundle replays a previously consumed manifest")
    pairs = _object_version_pairs(baseline, wal_results, blob)
    if len(set(pairs)) != len(pairs):
        raise PhysicalWalObjectManifestError("manifest bundle reuses an immutable Object version")
    if _accepted_object_versions(accepted_object_versions).intersection(pairs):
        raise PhysicalWalObjectManifestError("manifest bundle replays a previously consumed Object version")
    result = VerifiedPhysicalWalObjectStorageBundle(
        baseline=baseline,
        wal_manifests=tuple(wal_results),
        blob_frontier=blob,
        terminal_wal_lsn=prior_end_lsn,
        manifest_sha256es=manifest_hashes,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BUNDLE_CAPABILITY)
    return result


def require_verified_physical_wal_object_storage_bundle(
    value: object,
) -> VerifiedPhysicalWalObjectStorageBundle:
    """Check only the local opaque bundle shape; it does not re-consume it."""

    if type(value) is not VerifiedPhysicalWalObjectStorageBundle:
        raise PhysicalWalObjectManifestError("verified physical WAL bundle capability is required")
    if value._capability is not _VERIFIED_BUNDLE_CAPABILITY:
        raise PhysicalWalObjectManifestError("verified physical WAL bundle was not authorized")
    baseline = require_verified_physical_wal_base_backup_manifest(value.baseline)
    recipient = baseline.base_backup_object.age_recipient
    normalized = verify_physical_wal_object_storage_bundle(
        base_backup_manifest=baseline.canonical_manifest,
        wal_segment_manifests=tuple(item.canonical_manifest for item in value.wal_manifests),
        blob_frontier_manifest=value.blob_frontier.canonical_manifest,
        expected_source_public_key=baseline.source_public_key,
        expected_source_site=baseline.source_site,
        expected_destination_site=baseline.destination_site,
        expected_campaign_id=baseline.campaign_id,
        expected_release_sha=baseline.release_sha,
        expected_writer_epoch=baseline.writer_term.epoch,
        expected_writer_lease_id=baseline.writer_term.lease_id,
        expected_witnessed_term_proof_sha256=baseline.writer_term.witnessed_term_proof_sha256,
        expected_baseline_generation_id=baseline.baseline_generation_id,
        expected_wal_segment_size_bytes=baseline.wal_segment_size_bytes,
        expected_destination_age_recipient=recipient,
    )
    if normalized != value:
        raise PhysicalWalObjectManifestError("verified physical WAL bundle is not normalized")
    return value


__all__ = (
    "MAX_PHYSICAL_WAL_BLOB_INVENTORY_SHARDS",
    "MAX_PHYSICAL_WAL_OBJECT_CIPHERTEXT_BYTES",
    "MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES",
    "MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST",
    "PHYSICAL_WAL_BASE_BACKUP_MANIFEST_SCHEMA",
    "PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256",
    "PHYSICAL_WAL_BLOB_FRONTIER_MANIFEST_SCHEMA",
    "PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA",
    "PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION",
    "PHYSICAL_WAL_OBJECT_IMMUTABILITY",
    "PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION",
    "PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM",
    "PHYSICAL_WAL_OBJECT_MANIFEST_VERSION",
    "PHYSICAL_WAL_SEGMENT_MANIFEST_SCHEMA",
    "PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES",
    "PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256",
    "PhysicalWalBlobInventoryShard",
    "PhysicalWalImmutableObject",
    "PhysicalWalObjectManifestError",
    "PhysicalWalSegment",
    "PhysicalWalWriterTermBinding",
    "VerifiedPhysicalWalBaseBackupManifest",
    "VerifiedPhysicalWalBlobFrontierManifest",
    "VerifiedPhysicalWalObjectStorageBundle",
    "VerifiedPhysicalWalSegmentManifest",
    "build_physical_wal_base_backup_manifest",
    "build_physical_wal_blob_frontier_manifest",
    "build_physical_wal_segment_manifest",
    "parse_physical_wal_object_manifest_json",
    "require_verified_physical_wal_base_backup_manifest",
    "require_verified_physical_wal_blob_frontier_manifest",
    "require_verified_physical_wal_object_storage_bundle",
    "require_verified_physical_wal_segment_manifest",
    "verify_physical_wal_base_backup_manifest",
    "verify_physical_wal_blob_frontier_manifest",
    "verify_physical_wal_object_storage_bundle",
    "verify_physical_wal_segment_manifest",
)
