"""V2-only signed Blob object-version frontier coverage for a later ack join.

This module is deliberately a *pure evidence boundary*.  It has no Object
Storage client, network, filesystem, PostgreSQL, restore, promotion, remote
ack, readiness, or execution-driver dependency.  A trusted owner signs a
canonical, exhaustive V2 Blob object-version set at one target WAL LSN.  The
signature is the narrow trust boundary: a caller cannot turn a boolean
completion declaration, a raw inventory, or a plain list into coverage.

The final frontier capability is minted only from that already-verified owner
capability and a separately verified V2 chunked base-backup manifest plus
fresh Witness handoff.  It pins the exact manifest hash, mediated route,
Writer term, recipient, baseline and WAL geometry, target LSN, and every Blob
object version.  It is intentionally non-authorizing.  A later V2-only remote
ack join still needs independently audited live Blob uploader/receiver
readback evidence; this contract neither performs nor substitutes for that
live proof.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
from core.physical_wal_chunked_base_backup_handoff_receipt import (
    PhysicalWalChunkedBaseBackupHandoffReceiptError,
    VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    require_verified_physical_wal_chunked_base_backup_handoff_receipt,
)
from core.physical_wal_chunked_base_backup_manifest import (
    PhysicalWalChunkedBaseBackupManifestError,
    VerifiedPhysicalWalChunkedBaseBackupManifest,
    require_verified_physical_wal_chunked_base_backup_manifest,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
    PhysicalWalChunkedBaseBackupTransferError,
    build_physical_wal_chunked_base_backup_binding,
)


__all__ = (
    "MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_AGE_SECONDS",
    "MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_BYTES",
    "MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_OBJECTS",
    "PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA",
    "PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA",
    "PhysicalWalChunkedBaseBackupBlobFrontierCoverageError",
    "PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope",
    "PhysicalWalV2BlobObjectVersionSelector",
    "VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage",
    "VerifiedPhysicalWalV2BlobObjectVersionCoverage",
    "build_physical_wal_v2_blob_object_version_coverage",
    "canonical_physical_wal_v2_blob_object_version_coverage_bytes",
    "derive_physical_wal_v2_blob_object_version_prefix",
    "mint_physical_wal_chunked_base_backup_blob_frontier_coverage",
    "require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage",
    "require_verified_physical_wal_v2_blob_object_version_coverage",
    "verify_physical_wal_v2_blob_object_version_coverage",
)


PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA = (
    "gold-trade-physical-wal-v2-blob-object-version-coverage-v2"
)
PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA = (
    "gold-trade-physical-wal-chunked-base-backup-blob-frontier-coverage-v2"
)
PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_VERSION = 2
PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SIGNATURE_ALGORITHM = "ed25519"
MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_BYTES = 64 * 1024 * 1024
MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_OBJECTS = 16_384
MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_AGE_SECONDS = 120
MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_FUTURE_SKEW_SECONDS = 5

_COVERAGE_DOMAIN = b"gold-trade-physical-wal-v2-blob-object-version-coverage-v2\x00"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
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
        "ordinal",
        "blob_id",
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
_COVERAGE_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "canonical_base_backup_manifest_sha256",
        "lineage_sha256",
        "baseline_generation_id",
        "database_system_identifier",
        "timeline_id",
        "wal_segment_size_bytes",
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "target_wal_lsn",
        "coverage_id",
        "coverage_nonce",
        "observed_at",
        "expires_at",
        "objects",
        "object_version_set_sha256",
        "object_count",
        "owner_signer",
        "owner_signature",
    }
)

_OWNER_COVERAGE_CAPABILITY = object()
_FRONTIER_COVERAGE_CAPABILITY = object()


class PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(ValueError):
    """V2 Blob object-version coverage is malformed, stale, or unbound."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2BlobObjectVersionSelector:
    """One immutable encrypted Blob object version in canonical frontier order."""

    ordinal: int
    blob_id: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_sha256: str
    plaintext_bytes: int
    age_recipient: str


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalV2BlobObjectVersionCoverage:
    """Owner-signed V2 coverage assertion, not remote-ack or promotion authority.

    This capability proves only that the configured owner signing key asserted
    this exact canonical set during its short validity window.  It does not
    itself prove a live Object Storage upload or receiver readback.
    """

    schema: str
    canonical_coverage: bytes
    coverage_sha256: str
    coverage_id: str
    coverage_nonce: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    canonical_base_backup_manifest_sha256: str
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    target_wal_lsn: str
    object_version_set_sha256: str
    objects: tuple[PhysicalWalV2BlobObjectVersionSelector, ...]
    observed_at: datetime
    expires_at: datetime
    owner_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_BLOB_OWNER_COVERAGE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope:
    """Policy pins for a later V2 remote-ack join.

    ``required_blob_object_versions`` is an exact typed V2 selector set, not
    a raw inventory receipt and not a completion flag.  A future coordinator
    must source it from independently verified live Blob evidence.  This pure
    contract compares it exactly against the owner-signed coverage set.
    """

    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    lineage_sha256: str
    target_wal_lsn: str
    required_blob_object_versions: tuple[PhysicalWalV2BlobObjectVersionSelector, ...]


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage:
    """Non-authorizing joined V2 Blob frontier evidence for a future ack join."""

    schema: str
    owner_coverage_sha256: str
    canonical_base_backup_manifest_sha256: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    handoff_receipt_id: str
    handoff_receipt_nonce: str
    handoff_expires_at: datetime
    lineage_sha256: str
    coverage_id: str
    coverage_nonce: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    target_wal_lsn: str
    object_version_set_sha256: str
    objects: tuple[PhysicalWalV2BlobObjectVersionSelector, ...]
    scope_sha256: str
    observed_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _CoverageFacts:
    raw: bytes
    coverage_sha256: str
    coverage_id: str
    coverage_nonce: str
    transfer_binding: PhysicalWalChunkedBaseBackupBinding
    canonical_base_backup_manifest_sha256: str
    lineage_sha256: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    target_wal_lsn: str
    object_version_set_sha256: str
    objects: tuple[PhysicalWalV2BlobObjectVersionSelector, ...]
    observed_at: datetime
    expires_at: datetime
    owner_public_key: bytes


@dataclass(frozen=True)
class _JoinFacts:
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt
    owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage
    scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope
    scope_sha256: str
    manifest_sha256: str


def _fail(code: str) -> None:
    raise PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_BLOB_OWNER_COVERAGE_NONCANONICAL")


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    return _text(value, pattern=_ID_RE, code=code)


def _nonce(value: object, *, code: str) -> str:
    return _text(value, pattern=_NONCE_RE, code=code)


def _nonzero_sha256(value: object, *, code: str) -> str:
    digest = _text(value, pattern=SHA256_RE, code=code)
    if digest == "0" * 64:
        _fail(code)
    return digest


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _fail(code)
    return value


def _ordinal(value: object, *, code: str) -> int:
    if type(value) is not int or value < 0 or value >= MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_OBJECTS:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _timestamp_text(value: object, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _decode_base64(value: object, *, expected_bytes: int, code: str) -> bytes:
    if type(value) is not str:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(decoded) != expected_bytes:
        _fail(code)
    return decoded


def _signer(value: object, *, code: str) -> bytes:
    mapping = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if mapping["algorithm"] != PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SIGNATURE_ALGORITHM:
        _fail(code)
    public = _public_key(
        _decode_base64(mapping["public_key_base64"], expected_bytes=32, code=code),
        code=code,
    )
    if _text(mapping["key_id"], pattern=_KEY_ID_RE, code=code) != _key_id(public):
        _fail(code)
    return public


def _signature(value: object, *, code: str) -> bytes:
    mapping = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if mapping["algorithm"] != PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SIGNATURE_ALGORITHM:
        _fail(code)
    return _decode_base64(mapping["signature_base64"], expected_bytes=64, code=code)


def _signer_from_private(value: object, *, code: str) -> tuple[object, bytes, dict[str, str]]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(code) from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail(code)
    public = value.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        value,
        _public_key(public, code=code),
        {
            "algorithm": PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SIGNATURE_ALGORITHM,
            "public_key_base64": base64.b64encode(public).decode("ascii"),
            "key_id": _key_id(public),
        },
    )


def _sign(unsigned: Mapping[str, Any], *, signer: object) -> dict[str, str]:
    private, _public, _mapping = _signer_from_private(
        signer,
        code="V2_BLOB_OWNER_COVERAGE_SIGNER_INVALID",
    )
    try:
        signature = private.sign(
            _COVERAGE_DOMAIN
            + _canonical(unsigned, code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
        )
    except ValueError:
        _fail("V2_BLOB_OWNER_COVERAGE_SIGNER_FAILED")
    if type(signature) is not bytes or len(signature) != 64:
        _fail("V2_BLOB_OWNER_COVERAGE_SIGNER_FAILED")
    return {
        "algorithm": PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def _verify_signature(payload: Mapping[str, Any], *, expected_owner_public_key: bytes | None) -> bytes:
    public = _signer(payload.get("owner_signer"), code="V2_BLOB_OWNER_COVERAGE_SIGNER_INVALID")
    if expected_owner_public_key is not None and public != expected_owner_public_key:
        _fail("V2_BLOB_OWNER_COVERAGE_SIGNER_MISMATCH")
    signature = _signature(payload.get("owner_signature"), code="V2_BLOB_OWNER_COVERAGE_SIGNATURE_INVALID")
    unsigned = {key: value for key, value in payload.items() if key != "owner_signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public).verify(
            signature,
            _COVERAGE_DOMAIN
            + _canonical(unsigned, code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL"),
        )
    except (InvalidSignature, ValueError):
        _fail("V2_BLOB_OWNER_COVERAGE_SIGNATURE_INVALID")
    return public


def _parse_canonical(value: object) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        try:
            payload = dict(value)
            raw = _canonical(payload, code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
        except (TypeError, ValueError):
            _fail("V2_BLOB_OWNER_COVERAGE_INVALID")
    elif type(value) is bytes:
        raw = value
        if not raw or len(raw) > MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_BYTES:
            _fail("V2_BLOB_OWNER_COVERAGE_BYTES_INVALID")
        try:
            payload = json.loads(
                raw.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            _fail("V2_BLOB_OWNER_COVERAGE_INVALID")
        if not isinstance(payload, dict) or _canonical(
            payload,
            code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL",
        ) != raw:
            _fail("V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
    else:
        _fail("V2_BLOB_OWNER_COVERAGE_INVALID")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_BYTES:
        _fail("V2_BLOB_OWNER_COVERAGE_BYTES_INVALID")
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


def derive_physical_wal_v2_blob_object_version_prefix(
    *,
    transfer_binding: PhysicalWalChunkedBaseBackupBinding,
    lineage_sha256: str,
) -> str:
    """Return the only route-bound V2 Blob key prefix accepted by this contract."""

    if type(transfer_binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("V2_BLOB_OWNER_COVERAGE_BINDING_INVALID")
    binding = _binding_from_mapping(
        _binding_mapping(transfer_binding),
        code="V2_BLOB_OWNER_COVERAGE_BINDING_INVALID",
    )
    lineage = _nonzero_sha256(
        lineage_sha256,
        code="V2_BLOB_OWNER_COVERAGE_LINEAGE_INVALID",
    )
    return (
        f"{binding.object_storage_namespace}/{binding.campaign_id}/"
        f"{binding.release_sha}/blob-v2/{lineage}/"
    )


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
    except (PhysicalWalChunkedBaseBackupTransferError, TypeError):
        _fail(code)
    if mapping != _binding_mapping(binding):
        _fail(code)
    return binding


def _object_key(value: object, *, code: str) -> str:
    key = _text(value, pattern=OBJECT_KEY_RE, code=code)
    components = key.split("/")
    if (
        not key.endswith(".age")
        or ".." in components
        or any(
            part.casefold() in _MUTABLE_ALIAS_COMPONENTS
            or part.split(".", 1)[0].casefold() in _MUTABLE_ALIAS_COMPONENTS
            for part in components
        )
    ):
        _fail(code)
    return key


def _version_id(value: object, *, code: str) -> str:
    version = _text(value, pattern=VERSION_ID_RE, code=code)
    if version.casefold() in _MUTABLE_ALIAS_COMPONENTS | {"null", "none"}:
        _fail(code)
    return version


def _selector_mapping(value: PhysicalWalV2BlobObjectVersionSelector) -> dict[str, Any]:
    return {
        "ordinal": value.ordinal,
        "blob_id": value.blob_id,
        "object_key": value.object_key,
        "version_id": value.version_id,
        "ciphertext_sha256": value.ciphertext_sha256,
        "ciphertext_bytes": value.ciphertext_bytes,
        "plaintext_sha256": value.plaintext_sha256,
        "plaintext_bytes": value.plaintext_bytes,
        "age_recipient": value.age_recipient,
    }


def _selector_from_mapping(value: object, *, code: str) -> PhysicalWalV2BlobObjectVersionSelector:
    mapping = _exact_mapping(value, fields=_SELECTOR_FIELDS, code=code)
    return PhysicalWalV2BlobObjectVersionSelector(
        ordinal=_ordinal(mapping["ordinal"], code=code),
        blob_id=_identifier(mapping["blob_id"], code=code),
        object_key=_object_key(mapping["object_key"], code=code),
        version_id=_version_id(mapping["version_id"], code=code),
        ciphertext_sha256=_nonzero_sha256(mapping["ciphertext_sha256"], code=code),
        ciphertext_bytes=_positive_int(
            mapping["ciphertext_bytes"], maximum=2**63 - 1, code=code
        ),
        plaintext_sha256=_nonzero_sha256(mapping["plaintext_sha256"], code=code),
        plaintext_bytes=_positive_int(
            mapping["plaintext_bytes"], maximum=2**63 - 1, code=code
        ),
        age_recipient=_text(mapping["age_recipient"], pattern=AGE_RECIPIENT_RE, code=code),
    )


def _selector_from_typed(value: object, *, code: str) -> PhysicalWalV2BlobObjectVersionSelector:
    if type(value) is not PhysicalWalV2BlobObjectVersionSelector:
        _fail(code)
    return _selector_from_mapping(_selector_mapping(value), code=code)


def _selectors_from_sequence(
    value: object,
    *,
    typed: bool,
    code: str,
) -> tuple[PhysicalWalV2BlobObjectVersionSelector, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(code)
    if not value or len(value) > MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_OBJECTS:
        _fail(code)
    if typed:
        selectors = tuple(_selector_from_typed(item, code=code) for item in value)
    else:
        selectors = tuple(_selector_from_mapping(item, code=code) for item in value)
    if tuple(item.ordinal for item in selectors) != tuple(range(len(selectors))):
        _fail(code)
    if len({item.blob_id for item in selectors}) != len(selectors):
        _fail(code)
    object_versions = {(item.object_key, item.version_id) for item in selectors}
    if len(object_versions) != len(selectors) or len({item.object_key for item in selectors}) != len(selectors):
        _fail(code)
    return selectors


def _require_blob_prefix(
    selectors: tuple[PhysicalWalV2BlobObjectVersionSelector, ...],
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    lineage_sha256: str,
    code: str,
) -> None:
    prefix = derive_physical_wal_v2_blob_object_version_prefix(
        transfer_binding=binding,
        lineage_sha256=lineage_sha256,
    )
    if any(
        not item.object_key.startswith(prefix)
        or len(item.object_key) == len(prefix)
        or item.object_key[len(prefix)] == "/"
        for item in selectors
    ):
        _fail(code)


def _object_version_set_sha256(
    selectors: tuple[PhysicalWalV2BlobObjectVersionSelector, ...]
) -> str:
    payload = {
        "schema": PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA,
        "objects": [_selector_mapping(item) for item in selectors],
    }
    return hashlib.sha256(
        _canonical(payload, code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
    ).hexdigest()


def _parse_coverage(
    value: object,
    *,
    expected_owner_public_key: bytes | None,
    now: datetime | None,
    require_fresh: bool = True,
) -> _CoverageFacts:
    payload, raw = _parse_canonical(value)
    coverage = _exact_mapping(payload, fields=_COVERAGE_FIELDS, code="V2_BLOB_OWNER_COVERAGE_FIELDS_INVALID")
    if (
        coverage["schema"] != PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA
        or coverage["version"] != PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_VERSION
        or coverage["kind"] != "physical_wal_v2_blob_exact_object_version_frontier"
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_SCHEMA_INVALID")
    owner_public = _verify_signature(
        coverage,
        expected_owner_public_key=expected_owner_public_key,
    )
    binding = _binding_from_mapping(
        coverage["binding"],
        code="V2_BLOB_OWNER_COVERAGE_BINDING_INVALID",
    )
    lineage = _nonzero_sha256(
        coverage["lineage_sha256"],
        code="V2_BLOB_OWNER_COVERAGE_LINEAGE_INVALID",
    )
    selectors = _selectors_from_sequence(
        coverage["objects"],
        typed=False,
        code="V2_BLOB_OWNER_COVERAGE_OBJECTS_INVALID",
    )
    _require_blob_prefix(
        selectors,
        binding=binding,
        lineage_sha256=lineage,
        code="V2_BLOB_OWNER_COVERAGE_OBJECT_PREFIX_INVALID",
    )
    object_set_hash = _nonzero_sha256(
        coverage["object_version_set_sha256"],
        code="V2_BLOB_OWNER_COVERAGE_OBJECT_SET_HASH_INVALID",
    )
    if object_set_hash != _object_version_set_sha256(selectors):
        _fail("V2_BLOB_OWNER_COVERAGE_OBJECT_SET_HASH_MISMATCH")
    object_count = _positive_int(
        coverage["object_count"],
        maximum=MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_OBJECTS,
        code="V2_BLOB_OWNER_COVERAGE_OBJECT_COUNT_INVALID",
    )
    if object_count != len(selectors):
        _fail("V2_BLOB_OWNER_COVERAGE_OBJECT_COUNT_MISMATCH")
    observed = _timestamp(
        coverage["observed_at"],
        code="V2_BLOB_OWNER_COVERAGE_OBSERVED_AT_INVALID",
    )
    expires = _timestamp(
        coverage["expires_at"],
        code="V2_BLOB_OWNER_COVERAGE_EXPIRES_AT_INVALID",
    )
    current = (
        _utc(now, code="V2_BLOB_OWNER_COVERAGE_NOW_INVALID")
        if require_fresh
        else None
    )
    if (
        expires <= observed
        or expires - observed
        > timedelta(seconds=MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_AGE_SECONDS)
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_STALE")
    if require_fresh and (
        observed
        > current
        + timedelta(seconds=MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_FUTURE_SKEW_SECONDS)
        or expires <= current
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_STALE")
    target_text, target_value = _lsn(
        coverage["target_wal_lsn"],
        code="V2_BLOB_OWNER_COVERAGE_TARGET_WAL_LSN_INVALID",
    )
    base_end, base_end_value = _lsn(
        coverage["base_backup_end_lsn"],
        code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
    )
    if target_value < base_end_value:
        _fail("V2_BLOB_OWNER_COVERAGE_TARGET_PRECEDES_BASE_BACKUP")
    return _CoverageFacts(
        raw=raw,
        coverage_sha256=hashlib.sha256(raw).hexdigest(),
        coverage_id=_identifier(coverage["coverage_id"], code="V2_BLOB_OWNER_COVERAGE_ID_INVALID"),
        coverage_nonce=_nonce(coverage["coverage_nonce"], code="V2_BLOB_OWNER_COVERAGE_NONCE_INVALID"),
        transfer_binding=binding,
        canonical_base_backup_manifest_sha256=_nonzero_sha256(
            coverage["canonical_base_backup_manifest_sha256"],
            code="V2_BLOB_OWNER_COVERAGE_MANIFEST_HASH_INVALID",
        ),
        lineage_sha256=lineage,
        baseline_generation_id=_identifier(
            coverage["baseline_generation_id"],
            code="V2_BLOB_OWNER_COVERAGE_BASELINE_INVALID",
        ),
        database_system_identifier=_text(
            coverage["database_system_identifier"],
            pattern=re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII),
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        timeline_id=_positive_int(
            coverage["timeline_id"],
            maximum=0xFFFFFFFF,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        wal_segment_size_bytes=_positive_int(
            coverage["wal_segment_size_bytes"],
            maximum=2**63 - 1,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        baseline_wal_lsn=_lsn(
            coverage["baseline_wal_lsn"],
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        )[0],
        wal_chain_start_lsn=_lsn(
            coverage["wal_chain_start_lsn"],
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        )[0],
        base_backup_end_lsn=base_end,
        target_wal_lsn=target_text,
        object_version_set_sha256=object_set_hash,
        objects=selectors,
        observed_at=observed,
        expires_at=expires,
        owner_public_key=owner_public,
    )


def _coverage_from_facts(facts: _CoverageFacts) -> VerifiedPhysicalWalV2BlobObjectVersionCoverage:
    result = VerifiedPhysicalWalV2BlobObjectVersionCoverage(
        schema=PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA,
        canonical_coverage=facts.raw,
        coverage_sha256=facts.coverage_sha256,
        coverage_id=facts.coverage_id,
        coverage_nonce=facts.coverage_nonce,
        transfer_binding=facts.transfer_binding,
        canonical_base_backup_manifest_sha256=facts.canonical_base_backup_manifest_sha256,
        lineage_sha256=facts.lineage_sha256,
        baseline_generation_id=facts.baseline_generation_id,
        database_system_identifier=facts.database_system_identifier,
        timeline_id=facts.timeline_id,
        wal_segment_size_bytes=facts.wal_segment_size_bytes,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        wal_chain_start_lsn=facts.wal_chain_start_lsn,
        base_backup_end_lsn=facts.base_backup_end_lsn,
        target_wal_lsn=facts.target_wal_lsn,
        object_version_set_sha256=facts.object_version_set_sha256,
        objects=facts.objects,
        observed_at=facts.observed_at,
        expires_at=facts.expires_at,
        owner_public_key=facts.owner_public_key,
    )
    object.__setattr__(result, "_capability", _OWNER_COVERAGE_CAPABILITY)
    return result


def _require_owner_coverage(
    value: object,
    *,
    expected_owner_public_key: bytes,
    now: datetime,
) -> VerifiedPhysicalWalV2BlobObjectVersionCoverage:
    expected_owner = _public_key(
        expected_owner_public_key,
        code="V2_BLOB_OWNER_COVERAGE_EXPECTED_SIGNER_INVALID",
    )
    if (
        type(value) is not VerifiedPhysicalWalV2BlobObjectVersionCoverage
        or value._capability is not _OWNER_COVERAGE_CAPABILITY
        or value.schema != PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_CAPABILITY_REQUIRED")
    facts = _parse_coverage(
        value.canonical_coverage,
        expected_owner_public_key=expected_owner,
        now=now,
    )
    if (
        value.coverage_sha256 != facts.coverage_sha256
        or value.coverage_id != facts.coverage_id
        or value.coverage_nonce != facts.coverage_nonce
        or value.transfer_binding != facts.transfer_binding
        or value.canonical_base_backup_manifest_sha256
        != facts.canonical_base_backup_manifest_sha256
        or value.lineage_sha256 != facts.lineage_sha256
        or value.baseline_generation_id != facts.baseline_generation_id
        or value.database_system_identifier != facts.database_system_identifier
        or value.timeline_id != facts.timeline_id
        or value.wal_segment_size_bytes != facts.wal_segment_size_bytes
        or value.baseline_wal_lsn != facts.baseline_wal_lsn
        or value.wal_chain_start_lsn != facts.wal_chain_start_lsn
        or value.base_backup_end_lsn != facts.base_backup_end_lsn
        or value.target_wal_lsn != facts.target_wal_lsn
        or value.object_version_set_sha256 != facts.object_version_set_sha256
        or value.objects != facts.objects
        or value.observed_at != facts.observed_at
        or value.expires_at != facts.expires_at
        or value.owner_public_key != facts.owner_public_key
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_CAPABILITY_TAMPERED")
    return value


def _scope_sha256(
    scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    *,
    selectors: tuple[PhysicalWalV2BlobObjectVersionSelector, ...],
) -> str:
    payload = {
        "schema": PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA,
        "binding": _binding_mapping(scope.transfer_binding),
        "lineage_sha256": scope.lineage_sha256,
        "target_wal_lsn": scope.target_wal_lsn,
        "required_blob_object_versions": [_selector_mapping(item) for item in selectors],
    }
    return hashlib.sha256(
        _canonical(payload, code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_INVALID")
    ).hexdigest()


def _require_scope(
    value: object,
    *,
    binding: PhysicalWalChunkedBaseBackupBinding,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
) -> tuple[
    PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    tuple[PhysicalWalV2BlobObjectVersionSelector, ...],
    str,
]:
    if type(value) is not PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_REQUIRED")
    scope = value
    if type(scope.transfer_binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_BINDING_INVALID")
    try:
        candidate = _binding_from_mapping(
            _binding_mapping(scope.transfer_binding),
            code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_BINDING_INVALID",
        )
    except (AttributeError, TypeError):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_BINDING_INVALID")
    if scope.transfer_binding != candidate:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_BINDING_INVALID")
    if candidate != binding:
        if (
            candidate.source_site != binding.source_site
            or candidate.destination_site != binding.destination_site
            or candidate.campaign_id != binding.campaign_id
            or candidate.release_sha != binding.release_sha
            or candidate.route_commitment_sha256 != binding.route_commitment_sha256
        ):
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_ROUTE_MISMATCH")
        if candidate.destination_age_recipient != binding.destination_age_recipient:
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_RECIPIENT_MISMATCH")
        if candidate.writer_term != binding.writer_term:
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_TERM_MISMATCH")
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_BINDING_MISMATCH")
    target_text, target_value = _lsn(
        scope.target_wal_lsn,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_TARGET_INVALID",
    )
    _base_end, base_end_value = _lsn(
        handoff.base_backup_end_lsn,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_WAL_GEOMETRY_INVALID",
    )
    if target_value < base_end_value:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_TARGET_PRECEDES_BASE_BACKUP")
    lineage = _nonzero_sha256(
        scope.lineage_sha256,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_LINEAGE_INVALID",
    )
    if lineage != handoff.lineage_sha256:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_LINEAGE_MISMATCH")
    selectors = _selectors_from_sequence(
        scope.required_blob_object_versions,
        typed=True,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_OBJECTS_INVALID",
    )
    _require_blob_prefix(
        selectors,
        binding=binding,
        lineage_sha256=lineage,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_SCOPE_OBJECT_PREFIX_INVALID",
    )
    normalized_scope = PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope(
        transfer_binding=binding,
        lineage_sha256=lineage,
        target_wal_lsn=target_text,
        required_blob_object_versions=selectors,
    )
    return normalized_scope, selectors, _scope_sha256(normalized_scope, selectors=selectors)


def _require_cross_pins(
    coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    *,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    selectors: tuple[PhysicalWalV2BlobObjectVersionSelector, ...],
    manifest_sha256: str,
) -> None:
    binding = manifest.finalization_permit.session.binding
    if coverage.transfer_binding != binding:
        candidate = coverage.transfer_binding
        if (
            candidate.source_site != binding.source_site
            or candidate.destination_site != binding.destination_site
            or candidate.campaign_id != binding.campaign_id
            or candidate.release_sha != binding.release_sha
            or candidate.route_commitment_sha256 != binding.route_commitment_sha256
        ):
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_ROUTE_MISMATCH")
        if candidate.destination_age_recipient != binding.destination_age_recipient:
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_RECIPIENT_MISMATCH")
        if candidate.writer_term != binding.writer_term:
            _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_TERM_MISMATCH")
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_BINDING_MISMATCH")
    if coverage.canonical_base_backup_manifest_sha256 != manifest_sha256:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_MANIFEST_HASH_MISMATCH")
    if (
        coverage.lineage_sha256 != handoff.lineage_sha256
        or scope.lineage_sha256 != handoff.lineage_sha256
    ):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_LINEAGE_MISMATCH")
    if (
        coverage.baseline_generation_id != handoff.baseline_generation_id
        or coverage.database_system_identifier != handoff.database_system_identifier
        or coverage.timeline_id != handoff.timeline_id
        or coverage.wal_segment_size_bytes != handoff.wal_segment_size_bytes
        or coverage.baseline_wal_lsn != handoff.baseline_wal_lsn
        or coverage.wal_chain_start_lsn != handoff.wal_chain_start_lsn
        or coverage.base_backup_end_lsn != handoff.base_backup_end_lsn
    ):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_WAL_GEOMETRY_MISMATCH")
    if coverage.target_wal_lsn != scope.target_wal_lsn:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_FRONTIER_MISMATCH")
    if coverage.objects != selectors:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_OBJECT_VERSION_MISMATCH")
    if coverage.object_version_set_sha256 != _object_version_set_sha256(selectors):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_OBJECT_SET_MISMATCH")
    if any(item.age_recipient != binding.destination_age_recipient for item in coverage.objects):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_RECIPIENT_MISMATCH")
    _require_blob_prefix(
        coverage.objects,
        binding=binding,
        lineage_sha256=handoff.lineage_sha256,
        code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_OWNER_OBJECT_PREFIX_INVALID",
    )
    if handoff.binding_sha256 == "0" * 64 or handoff.destination_age_recipient != binding.destination_age_recipient:
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_HANDOFF_BINDING_MISMATCH")


def _derive_join_facts(
    *,
    owner_coverage: object,
    expected_owner_public_key: bytes,
    manifest: object,
    handoff_receipt: object,
    scope: object,
    now: datetime,
) -> _JoinFacts:
    current = _utc(now, code="CHUNKED_BASE_BACKUP_BLOB_FRONTIER_NOW_INVALID")
    try:
        verified_manifest = require_verified_physical_wal_chunked_base_backup_manifest(
            manifest,
            now=current,
        )
    except PhysicalWalChunkedBaseBackupManifestError as exc:
        raise PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(
            "CHUNKED_BASE_BACKUP_BLOB_FRONTIER_MANIFEST_INVALID"
        ) from exc
    try:
        handoff = require_verified_physical_wal_chunked_base_backup_handoff_receipt(
            handoff_receipt,
            manifest=verified_manifest,
            now=current,
        )
    except PhysicalWalChunkedBaseBackupHandoffReceiptError as exc:
        raise PhysicalWalChunkedBaseBackupBlobFrontierCoverageError(
            "CHUNKED_BASE_BACKUP_BLOB_FRONTIER_HANDOFF_INVALID"
        ) from exc
    binding = verified_manifest.finalization_permit.session.binding
    typed_scope, selectors, scope_sha = _require_scope(
        scope,
        binding=binding,
        handoff=handoff,
    )
    verified_owner = _require_owner_coverage(
        owner_coverage,
        expected_owner_public_key=expected_owner_public_key,
        now=current,
    )
    manifest_sha = hashlib.sha256(verified_manifest.canonical_manifest).hexdigest()
    _require_cross_pins(
        verified_owner,
        manifest=verified_manifest,
        handoff=handoff,
        scope=typed_scope,
        selectors=selectors,
        manifest_sha256=manifest_sha,
    )
    return _JoinFacts(
        manifest=verified_manifest,
        handoff=handoff,
        owner_coverage=verified_owner,
        scope=typed_scope,
        scope_sha256=scope_sha,
        manifest_sha256=manifest_sha,
    )


def build_physical_wal_v2_blob_object_version_coverage(
    *,
    transfer_binding: PhysicalWalChunkedBaseBackupBinding,
    canonical_base_backup_manifest_sha256: str,
    lineage_sha256: str,
    baseline_generation_id: str,
    database_system_identifier: str,
    timeline_id: int,
    wal_segment_size_bytes: int,
    baseline_wal_lsn: str,
    wal_chain_start_lsn: str,
    base_backup_end_lsn: str,
    target_wal_lsn: str,
    coverage_id: str,
    coverage_nonce: str,
    observed_at: datetime,
    expires_at: datetime,
    objects: Sequence[PhysicalWalV2BlobObjectVersionSelector],
    owner_signer: object,
) -> dict[str, Any]:
    """Sign one canonical V2 Blob exact-version coverage assertion.

    This is an explicit owner signing boundary, not a live uploader or
    receiver implementation.  It never accepts a boolean completion flag
    or a raw legacy inventory receipt; selector completeness is represented by
    the signed exact canonical set and its derived digest.
    """

    if type(transfer_binding) is not PhysicalWalChunkedBaseBackupBinding:
        _fail("V2_BLOB_OWNER_COVERAGE_BINDING_INVALID")
    binding = _binding_from_mapping(
        _binding_mapping(transfer_binding),
        code="V2_BLOB_OWNER_COVERAGE_BINDING_INVALID",
    )
    lineage = _nonzero_sha256(
        lineage_sha256,
        code="V2_BLOB_OWNER_COVERAGE_LINEAGE_INVALID",
    )
    selectors = _selectors_from_sequence(
        objects,
        typed=True,
        code="V2_BLOB_OWNER_COVERAGE_OBJECTS_INVALID",
    )
    if any(item.age_recipient != binding.destination_age_recipient for item in selectors):
        _fail("V2_BLOB_OWNER_COVERAGE_RECIPIENT_MISMATCH")
    _require_blob_prefix(
        selectors,
        binding=binding,
        lineage_sha256=lineage,
        code="V2_BLOB_OWNER_COVERAGE_OBJECT_PREFIX_INVALID",
    )
    observed = _utc(observed_at, code="V2_BLOB_OWNER_COVERAGE_OBSERVED_AT_INVALID")
    expires = _utc(expires_at, code="V2_BLOB_OWNER_COVERAGE_EXPIRES_AT_INVALID")
    if (
        expires <= observed
        or expires - observed
        > timedelta(seconds=MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_AGE_SECONDS)
    ):
        _fail("V2_BLOB_OWNER_COVERAGE_VALIDITY_INVALID")
    base_end, base_end_value = _lsn(
        base_backup_end_lsn,
        code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
    )
    target, target_value = _lsn(
        target_wal_lsn,
        code="V2_BLOB_OWNER_COVERAGE_TARGET_WAL_LSN_INVALID",
    )
    if target_value < base_end_value:
        _fail("V2_BLOB_OWNER_COVERAGE_TARGET_PRECEDES_BASE_BACKUP")
    _private, _public, signer_mapping = _signer_from_private(
        owner_signer,
        code="V2_BLOB_OWNER_COVERAGE_SIGNER_INVALID",
    )
    unsigned = {
        "schema": PHYSICAL_WAL_V2_BLOB_OBJECT_VERSION_COVERAGE_SCHEMA,
        "version": PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_VERSION,
        "kind": "physical_wal_v2_blob_exact_object_version_frontier",
        "binding": _binding_mapping(binding),
        "canonical_base_backup_manifest_sha256": _nonzero_sha256(
            canonical_base_backup_manifest_sha256,
            code="V2_BLOB_OWNER_COVERAGE_MANIFEST_HASH_INVALID",
        ),
        "lineage_sha256": lineage,
        "baseline_generation_id": _identifier(
            baseline_generation_id,
            code="V2_BLOB_OWNER_COVERAGE_BASELINE_INVALID",
        ),
        "database_system_identifier": _text(
            database_system_identifier,
            pattern=re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII),
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        "timeline_id": _positive_int(
            timeline_id,
            maximum=0xFFFFFFFF,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        "wal_segment_size_bytes": _positive_int(
            wal_segment_size_bytes,
            maximum=2**63 - 1,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        ),
        "baseline_wal_lsn": _lsn(
            baseline_wal_lsn,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        )[0],
        "wal_chain_start_lsn": _lsn(
            wal_chain_start_lsn,
            code="V2_BLOB_OWNER_COVERAGE_WAL_GEOMETRY_INVALID",
        )[0],
        "base_backup_end_lsn": base_end,
        "target_wal_lsn": target,
        "coverage_id": _identifier(coverage_id, code="V2_BLOB_OWNER_COVERAGE_ID_INVALID"),
        "coverage_nonce": _nonce(coverage_nonce, code="V2_BLOB_OWNER_COVERAGE_NONCE_INVALID"),
        "observed_at": _timestamp_text(
            observed,
            code="V2_BLOB_OWNER_COVERAGE_OBSERVED_AT_INVALID",
        ),
        "expires_at": _timestamp_text(
            expires,
            code="V2_BLOB_OWNER_COVERAGE_EXPIRES_AT_INVALID",
        ),
        "objects": [_selector_mapping(item) for item in selectors],
        "object_version_set_sha256": _object_version_set_sha256(selectors),
        "object_count": len(selectors),
        "owner_signer": signer_mapping,
    }
    result = {**unsigned, "owner_signature": _sign(unsigned, signer=owner_signer)}
    raw = _canonical(result, code="V2_BLOB_OWNER_COVERAGE_NONCANONICAL")
    if len(raw) > MAX_PHYSICAL_WAL_V2_BLOB_FRONTIER_COVERAGE_BYTES:
        _fail("V2_BLOB_OWNER_COVERAGE_BYTES_INVALID")
    return result


def canonical_physical_wal_v2_blob_object_version_coverage_bytes(
    value: Mapping[str, Any] | bytes,
) -> bytes:
    """Return canonical syntactically valid signed V2 coverage bytes only."""

    return _parse_coverage(
        value,
        expected_owner_public_key=None,
        now=None,
        require_fresh=False,
    ).raw


def verify_physical_wal_v2_blob_object_version_coverage(
    *,
    coverage: Mapping[str, Any] | bytes,
    expected_owner_public_key: bytes,
    now: datetime,
) -> VerifiedPhysicalWalV2BlobObjectVersionCoverage:
    """Verify a fresh owner-signed V2 coverage artifact into an opaque capability."""

    owner = _public_key(
        expected_owner_public_key,
        code="V2_BLOB_OWNER_COVERAGE_EXPECTED_SIGNER_INVALID",
    )
    facts = _parse_coverage(
        coverage,
        expected_owner_public_key=owner,
        now=now,
    )
    result = _coverage_from_facts(facts)
    return _require_owner_coverage(
        result,
        expected_owner_public_key=owner,
        now=now,
    )


def require_verified_physical_wal_v2_blob_object_version_coverage(
    value: object,
    *,
    expected_owner_public_key: bytes,
    now: datetime,
) -> VerifiedPhysicalWalV2BlobObjectVersionCoverage:
    """Recheck an owner capability's signature, canonical bytes, and freshness."""

    return _require_owner_coverage(
        value,
        expected_owner_public_key=expected_owner_public_key,
        now=now,
    )


def mint_physical_wal_chunked_base_backup_blob_frontier_coverage(
    *,
    owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    expected_owner_public_key: bytes,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage:
    """Join verified V2 Blob coverage to a fresh V2 base-backup handoff.

    Raw coverage bytes, mappings, V1 inventory receipts, and caller flags are
    rejected here.  The result is evidence only; it cannot issue a generic
    remote acknowledgement, restore, promotion, or writer authorization.
    """

    facts = _derive_join_facts(
        owner_coverage=owner_coverage,
        expected_owner_public_key=expected_owner_public_key,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    result = VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage(
        schema=PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA,
        owner_coverage_sha256=facts.owner_coverage.coverage_sha256,
        canonical_base_backup_manifest_sha256=facts.manifest_sha256,
        transfer_binding=facts.manifest.finalization_permit.session.binding,
        handoff_receipt_id=facts.handoff.receipt_id,
        handoff_receipt_nonce=facts.handoff.receipt_nonce,
        handoff_expires_at=facts.handoff.expires_at,
        lineage_sha256=facts.handoff.lineage_sha256,
        coverage_id=facts.owner_coverage.coverage_id,
        coverage_nonce=facts.owner_coverage.coverage_nonce,
        baseline_generation_id=facts.handoff.baseline_generation_id,
        database_system_identifier=facts.handoff.database_system_identifier,
        timeline_id=facts.handoff.timeline_id,
        wal_segment_size_bytes=facts.handoff.wal_segment_size_bytes,
        baseline_wal_lsn=facts.handoff.baseline_wal_lsn,
        wal_chain_start_lsn=facts.handoff.wal_chain_start_lsn,
        base_backup_end_lsn=facts.handoff.base_backup_end_lsn,
        target_wal_lsn=facts.owner_coverage.target_wal_lsn,
        object_version_set_sha256=facts.owner_coverage.object_version_set_sha256,
        objects=facts.owner_coverage.objects,
        scope_sha256=facts.scope_sha256,
        observed_at=facts.owner_coverage.observed_at,
        expires_at=facts.owner_coverage.expires_at,
    )
    object.__setattr__(result, "_capability", _FRONTIER_COVERAGE_CAPABILITY)
    return require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage(
        result,
        owner_coverage=owner_coverage,
        expected_owner_public_key=expected_owner_public_key,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )


def require_verified_physical_wal_chunked_base_backup_blob_frontier_coverage(
    value: object,
    *,
    owner_coverage: VerifiedPhysicalWalV2BlobObjectVersionCoverage,
    expected_owner_public_key: bytes,
    manifest: VerifiedPhysicalWalChunkedBaseBackupManifest,
    handoff_receipt: VerifiedPhysicalWalChunkedBaseBackupHandoffReceipt,
    scope: PhysicalWalChunkedBaseBackupBlobFrontierCoverageScope,
    now: datetime,
) -> VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage:
    """Revalidate joined V2 coverage and all still-fresh base/Witness pins."""

    if (
        type(value) is not VerifiedPhysicalWalChunkedBaseBackupBlobFrontierCoverage
        or value._capability is not _FRONTIER_COVERAGE_CAPABILITY
        or value.schema != PHYSICAL_WAL_CHUNKED_BASE_BACKUP_BLOB_FRONTIER_COVERAGE_SCHEMA
    ):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_CAPABILITY_REQUIRED")
    facts = _derive_join_facts(
        owner_coverage=owner_coverage,
        expected_owner_public_key=expected_owner_public_key,
        manifest=manifest,
        handoff_receipt=handoff_receipt,
        scope=scope,
        now=now,
    )
    if (
        value.owner_coverage_sha256 != facts.owner_coverage.coverage_sha256
        or value.canonical_base_backup_manifest_sha256 != facts.manifest_sha256
        or value.transfer_binding != facts.manifest.finalization_permit.session.binding
        or value.handoff_receipt_id != facts.handoff.receipt_id
        or value.handoff_receipt_nonce != facts.handoff.receipt_nonce
        or value.handoff_expires_at != facts.handoff.expires_at
        or value.lineage_sha256 != facts.handoff.lineage_sha256
        or value.coverage_id != facts.owner_coverage.coverage_id
        or value.coverage_nonce != facts.owner_coverage.coverage_nonce
        or value.baseline_generation_id != facts.handoff.baseline_generation_id
        or value.database_system_identifier != facts.handoff.database_system_identifier
        or value.timeline_id != facts.handoff.timeline_id
        or value.wal_segment_size_bytes != facts.handoff.wal_segment_size_bytes
        or value.baseline_wal_lsn != facts.handoff.baseline_wal_lsn
        or value.wal_chain_start_lsn != facts.handoff.wal_chain_start_lsn
        or value.base_backup_end_lsn != facts.handoff.base_backup_end_lsn
        or value.target_wal_lsn != facts.owner_coverage.target_wal_lsn
        or value.object_version_set_sha256 != facts.owner_coverage.object_version_set_sha256
        or value.objects != facts.owner_coverage.objects
        or value.scope_sha256 != facts.scope_sha256
        or value.observed_at != facts.owner_coverage.observed_at
        or value.expires_at != facts.owner_coverage.expires_at
    ):
        _fail("CHUNKED_BASE_BACKUP_BLOB_FRONTIER_CAPABILITY_TAMPERED")
    return value
