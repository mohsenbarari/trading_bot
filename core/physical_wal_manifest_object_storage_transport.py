"""Pinned, encrypted Object-Storage transport for signed physical WAL manifests.

This is deliberately a narrow transport boundary around the *metadata* bundle
validated by :mod:`core.physical_wal_object_manifest`.  A source first passes
an already verified base/WAL/blob bundle plus explicit route, term, baseline,
and recipient pins.  Only then can this module package, domain-sign, age
encrypt, conditionally publish, and read back that immutable package.

The receiver accepts neither ``latest`` nor an Object Storage listing.  It
uses an explicit, independently pinned canonical publication receipt, key,
version ID, package SHA-256, and bundle SHA-256; downloads precisely that
version; decrypts it through an injected adapter; verifies the outer package
signature and every signed manifest; then writes a local metadata-only stage.

No client, credential, age subprocess, database connection, PostgreSQL
restore, replay receipt, promotion decision, Witness decision, or network
call is created by importing this module.  Every I/O-capable dependency is
injected, and both public operations remain disabled unless a root-owned
configuration explicitly enables them.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable, Protocol

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
from core.physical_wal_object_manifest import (
    MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES,
    MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
    PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
    verify_physical_wal_object_storage_bundle,
)


__all__ = (
    "MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_DEFAULT_ENABLED",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PACKAGE_SCHEMA",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PUBLISHED_STATUS",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS",
    "PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_TRANSPORT_SCHEMA",
    "PhysicalWalManifestAgeDecryptor",
    "PhysicalWalManifestAgeEncryptor",
    "PhysicalWalManifestObjectStorageClient",
    "PhysicalWalManifestObjectStoragePublisher",
    "PhysicalWalManifestObjectStoragePublishConfig",
    "PhysicalWalManifestObjectStorageReceiver",
    "PhysicalWalManifestObjectStorageReceiverConfig",
    "PhysicalWalManifestObjectStorageStageResult",
    "PhysicalWalManifestObjectStorageTransportBinding",
    "PhysicalWalManifestObjectStorageTransportError",
    "PhysicalWalManifestPackageSigner",
    "PhysicalWalManifestPackageVerifier",
    "PhysicalWalManifestPublicationReceipt",
    "PhysicalWalManifestReceiverPin",
    "derive_physical_wal_manifest_object_key",
    "parse_physical_wal_manifest_publication_receipt",
    "require_verified_physical_wal_manifest_object_storage_stage",
    "require_verified_physical_wal_manifest_publication_receipt",
    "verify_physical_wal_manifest_object_storage_package",
)


PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_TRANSPORT_SCHEMA = (
    "gold-trade-physical-wal-manifest-object-storage-transport-v1"
)
PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PACKAGE_SCHEMA = (
    "gold-trade-physical-wal-manifest-object-storage-package-v1"
)
PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-manifest-object-storage-receipt-v1"
)
PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_DEFAULT_ENABLED = False
PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PUBLISHED_STATUS = "published-readback-verified"
PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS = "staged-verified-not-consumed"

# The package contains signed metadata only, never a base backup, WAL payload,
# or blob content.  A deliberately finite cap prevents a hostile package from
# converting the metadata plane into an unbounded local staging allocation.
MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES = 16 * 1024 * 1024
MAX_PHYSICAL_WAL_MANIFEST_RECEIPT_BYTES = 64 * 1024
MAX_PHYSICAL_WAL_MANIFEST_CIPHERTEXT_OVERHEAD_BYTES = 1024 * 1024
MAX_PHYSICAL_WAL_MANIFEST_CIPHERTEXT_BYTES = (
    MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES
    + MAX_PHYSICAL_WAL_MANIFEST_CIPHERTEXT_OVERHEAD_BYTES
)
_READ_CHUNK_BYTES = 256 * 1024

_PACKAGE_SIGNATURE_DOMAIN = (
    b"gold-trade-physical-wal-manifest-object-storage-package-v1\x00"
)
_BUNDLE_DIGEST_DOMAIN = b"gold-trade-physical-wal-manifest-bundle-v1\x00"
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_MUTABLE_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})

_TERM_FIELDS = frozenset({"epoch", "lease_id", "witnessed_term_proof_sha256"})
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_PACKAGE_FIELDS = frozenset(
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
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "destination_age_recipient",
        "route_binding_sha256",
        "terminal_wal_lsn",
        "bundle_manifest_sha256",
        "manifest_sha256es",
        "base_backup_manifest_base64",
        "wal_segment_manifests_base64",
        "blob_frontier_manifest_base64",
        "source_signer",
        "transport_signature",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
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
        "baseline_wal_lsn",
        "wal_chain_start_lsn",
        "base_backup_end_lsn",
        "destination_age_recipient",
        "route_binding_sha256",
        "bucket",
        "region",
        "bundle_manifest_sha256",
        "package_sha256",
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_bytes",
        "encryption",
        "immutability",
        "receipt_sha256",
    }
)

_VERIFIED_RECEIPT_CAPABILITY = object()
_VERIFIED_STAGE_CAPABILITY = object()


class PhysicalWalManifestObjectStorageTransportError(ValueError):
    """The physical-WAL manifest transport request is unsafe or unbound."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PhysicalWalManifestAgeEncryptor(Protocol):
    """Injected age-v1 encryptor; it must use precisely the supplied recipient."""

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        """Write one new age-v1 ciphertext file or raise."""


class PhysicalWalManifestAgeDecryptor(Protocol):
    """Injected age-v1 decryptor; no subprocess implementation is provided."""

    def decrypt(
        self,
        *,
        expected_recipient: str,
        ciphertext_path: Path,
        plaintext_path: Path,
    ) -> None:
        """Write one new plaintext package file or raise."""


class PhysicalWalManifestPackageSigner(Protocol):
    """Domain-signing adapter, normally backed by the source Ed25519 key."""

    def public_key_bytes(self) -> bytes:
        """Return the exact 32-byte Ed25519 public key used for ``sign``."""

    def sign(self, *, message: bytes) -> bytes:
        """Return a 64-byte Ed25519 signature or raise."""


class PhysicalWalManifestPackageVerifier(Protocol):
    """Injected signature verifier; successful verification returns ``None``."""

    def verify(self, *, public_key: bytes, message: bytes, signature: bytes) -> None:
        """Verify one package signature or raise."""


class PhysicalWalManifestObjectStorageClient(Protocol):
    """Minimal injected S3-compatible client.  Listing is deliberately absent."""

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def get_bucket_acl(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def put_object(self, **request: Any) -> Mapping[str, Any]: ...

    def head_object(self, **request: Any) -> Mapping[str, Any]: ...

    def get_object(self, **request: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PhysicalWalManifestObjectStorageTransportBinding:
    """All non-secret facts a package must bind for one directed route."""

    source_site: str
    destination_site: str
    source_public_key: bytes
    campaign_id: str
    release_sha: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    destination_age_recipient: str
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalWalManifestObjectStoragePublishConfig:
    """Root-only, default-disabled source configuration for one direction."""

    source_site: str = ""
    destination_site: str = ""
    workspace: Path | None = None
    bucket: str = ""
    region: str = ""
    destination_age_recipient: str = ""
    enabled: bool = PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_DEFAULT_ENABLED
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWalManifestObjectStorageReceiverConfig:
    """Root-only, default-disabled local receiver configuration."""

    receiver_site: str = ""
    workspace: Path | None = None
    staging_root: Path | None = None
    bucket: str = ""
    region: str = ""
    enabled: bool = PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_DEFAULT_ENABLED
    direct_site_control: str = "forbidden"
    source_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class PhysicalWalManifestPublicationReceipt:
    """Canonical non-secret evidence of one exact encrypted package readback."""

    canonical_receipt: bytes
    receipt_sha256: str
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    destination_age_recipient: str
    route_binding_sha256: str
    bucket: str
    region: str
    bundle_manifest_sha256: str
    package_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    plaintext_bytes: int
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalManifestReceiverPin:
    """Explicit receiver pin: receipt, key, version, and package are all exact."""

    binding: PhysicalWalManifestObjectStorageTransportBinding
    expected_receipt_sha256: str
    expected_object_key: str
    expected_version_id: str
    expected_bundle_manifest_sha256: str
    expected_package_sha256: str


@dataclass(frozen=True)
class PhysicalWalManifestObjectStorageStageResult:
    """Metadata-only local stage, never a consume, replay, or writer proof."""

    status: str
    package_path: Path
    receipt_sha256: str
    package_sha256: str
    bundle_manifest_sha256: str
    verified_bundle: VerifiedPhysicalWalObjectStorageBundle
    idempotent: bool
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _BindingFacts:
    source_site: str
    destination_site: str
    source_public_key: bytes
    campaign_id: str
    release_sha: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    destination_age_recipient: str
    route_binding_sha256: str


@dataclass(frozen=True)
class _PublishConfigFacts:
    source_site: str
    destination_site: str
    workspace: Path
    bucket: str
    region: str
    destination_age_recipient: str


@dataclass(frozen=True)
class _ReceiverConfigFacts:
    receiver_site: str
    workspace: Path
    staging_root: Path
    bucket: str
    region: str


@dataclass(frozen=True)
class _PackageFacts:
    raw: bytes
    package_sha256: str
    bundle_manifest_sha256: str
    verified_bundle: VerifiedPhysicalWalObjectStorageBundle


def _fail(code: str) -> None:
    raise PhysicalWalManifestObjectStorageTransportError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("TRANSPORT_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("TRANSPORT_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc


def _parse_canonical_mapping(value: object, *, maximum_bytes: int, code: str) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, bytes) or not 1 <= len(value) <= maximum_bytes:
        _fail(code)
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalManifestObjectStorageTransportError:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc
    if not isinstance(parsed, dict) or _canonical(parsed, code=code) != value:
        _fail(code)
    return parsed, value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    result = _text(value, pattern=SHA256_RE, code=code)
    if result == "0" * 64:
        _fail(code)
    return result


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    result = _text(value, pattern=_LSN_RE, code=code)
    high, low = result.split("/", 1)
    return result, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _version_id(value: object, *, code: str) -> str:
    result = _text(value, pattern=VERSION_ID_RE, code=code)
    if result.casefold() in _MUTABLE_COMPONENTS | {"null", "none"}:
        _fail(code)
    return result


def _object_key(value: object, *, code: str) -> str:
    result = _text(value, pattern=OBJECT_KEY_RE, code=code)
    parts = result.split("/")
    if (
        not result.endswith(".age")
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            part.casefold() in _MUTABLE_COMPONENTS
            or part.split(".", 1)[0].casefold() in _MUTABLE_COMPONENTS
            for part in parts
        )
    ):
        _fail(code)
    return result


def _safe_bucket(value: object, *, code: str) -> str:
    return _text(value, pattern=_BUCKET_RE, code=code)


def _safe_region(value: object, *, code: str) -> str:
    return _text(value, pattern=_REGION_RE, code=code)


def _term_mapping(facts: _BindingFacts) -> dict[str, Any]:
    return {
        "epoch": facts.writer_epoch,
        "lease_id": facts.writer_lease_id,
        "witnessed_term_proof_sha256": facts.witnessed_term_proof_sha256,
    }


def _term_facts(value: object, *, code: str) -> tuple[int, str, str]:
    item = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    return (
        _positive_int(item["epoch"], maximum=2**63 - 1, code=code),
        _text(item["lease_id"], pattern=LEASE_ID_RE, code=code),
        _sha256(item["witnessed_term_proof_sha256"], code=code),
    )


def _binding_facts(value: object) -> _BindingFacts:
    if type(value) is not PhysicalWalManifestObjectStorageTransportBinding:
        _fail("TRANSPORT_BINDING_INVALID")
    source = _site(value.source_site, code="TRANSPORT_BINDING_ROUTE_INVALID")
    destination = _site(value.destination_site, code="TRANSPORT_BINDING_ROUTE_INVALID")
    if source == destination:
        _fail("TRANSPORT_BINDING_ROUTE_INVALID")
    campaign = _text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code="TRANSPORT_BINDING_INVALID")
    release = _text(value.release_sha, pattern=RELEASE_SHA_RE, code="TRANSPORT_BINDING_INVALID")
    generation = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="TRANSPORT_BINDING_INVALID",
    )
    system_identifier = _text(
        value.database_system_identifier,
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="TRANSPORT_BINDING_INVALID",
    )
    timeline = _positive_int(value.timeline_id, maximum=0xFFFFFFFF, code="TRANSPORT_BINDING_INVALID")
    wal_segment_size = _positive_int(
        value.wal_segment_size_bytes,
        maximum=1024 * 1024 * 1024,
        code="TRANSPORT_BINDING_INVALID",
    )
    baseline_lsn, baseline_value = _lsn(value.baseline_wal_lsn, code="TRANSPORT_BINDING_INVALID")
    chain_start, chain_value = _lsn(value.wal_chain_start_lsn, code="TRANSPORT_BINDING_INVALID")
    backup_end, backup_end_value = _lsn(value.base_backup_end_lsn, code="TRANSPORT_BINDING_INVALID")
    if (
        backup_end_value <= baseline_value
        or chain_value % wal_segment_size
        or chain_value > baseline_value
        or baseline_value >= chain_value + wal_segment_size
    ):
        _fail("TRANSPORT_BINDING_BASELINE_INVALID")
    return _BindingFacts(
        source_site=source,
        destination_site=destination,
        source_public_key=_public_key(value.source_public_key, code="TRANSPORT_BINDING_SOURCE_KEY_INVALID"),
        campaign_id=campaign,
        release_sha=release,
        writer_epoch=_positive_int(value.writer_epoch, maximum=2**63 - 1, code="TRANSPORT_BINDING_INVALID"),
        writer_lease_id=_text(value.writer_lease_id, pattern=LEASE_ID_RE, code="TRANSPORT_BINDING_INVALID"),
        witnessed_term_proof_sha256=_sha256(
            value.witnessed_term_proof_sha256, code="TRANSPORT_BINDING_INVALID"
        ),
        baseline_generation_id=generation,
        baseline_manifest_sha256=_sha256(
            value.baseline_manifest_sha256, code="TRANSPORT_BINDING_INVALID"
        ),
        database_system_identifier=system_identifier,
        timeline_id=timeline,
        wal_segment_size_bytes=wal_segment_size,
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=chain_start,
        base_backup_end_lsn=backup_end,
        destination_age_recipient=_text(
            value.destination_age_recipient,
            pattern=AGE_RECIPIENT_RE,
            code="TRANSPORT_BINDING_INVALID",
        ),
        route_binding_sha256=_sha256(value.route_binding_sha256, code="TRANSPORT_BINDING_INVALID"),
    )


def _secure_directory(value: object, *, label: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail(label)
    try:
        absolute = value.absolute()
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError:
        _fail(label)
    if (
        absolute != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(label)
    return resolved


def _publish_config_facts(value: object) -> _PublishConfigFacts:
    if type(value) is not PhysicalWalManifestObjectStoragePublishConfig:
        _fail("TRANSPORT_PUBLISH_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("TRANSPORT_PUBLISH_DISABLED")
    if os.geteuid() != 0:
        _fail("TRANSPORT_ROOT_REQUIRED")
    source = _site(value.source_site, code="TRANSPORT_PUBLISH_CONFIG_ROUTE_INVALID")
    destination = _site(value.destination_site, code="TRANSPORT_PUBLISH_CONFIG_ROUTE_INVALID")
    if source == destination:
        _fail("TRANSPORT_PUBLISH_CONFIG_ROUTE_INVALID")
    if value.direct_site_control != "forbidden":
        _fail("TRANSPORT_DIRECT_SITE_CONTROL_FORBIDDEN")
    if value.destination_object_ingest != "pull-only":
        _fail("TRANSPORT_DESTINATION_INGEST_MUST_BE_PULL_ONLY")
    return _PublishConfigFacts(
        source_site=source,
        destination_site=destination,
        workspace=_secure_directory(value.workspace, label="TRANSPORT_PUBLISH_WORKSPACE_UNSAFE"),
        bucket=_safe_bucket(value.bucket, code="TRANSPORT_PUBLISH_BUCKET_INVALID"),
        region=_safe_region(value.region, code="TRANSPORT_PUBLISH_REGION_INVALID"),
        destination_age_recipient=_text(
            value.destination_age_recipient,
            pattern=AGE_RECIPIENT_RE,
            code="TRANSPORT_PUBLISH_RECIPIENT_INVALID",
        ),
    )


def _receiver_config_facts(value: object) -> _ReceiverConfigFacts:
    if type(value) is not PhysicalWalManifestObjectStorageReceiverConfig:
        _fail("TRANSPORT_RECEIVER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("TRANSPORT_RECEIVER_DISABLED")
    if os.geteuid() != 0:
        _fail("TRANSPORT_ROOT_REQUIRED")
    if value.direct_site_control != "forbidden":
        _fail("TRANSPORT_DIRECT_SITE_CONTROL_FORBIDDEN")
    if value.source_object_ingest != "pull-only":
        _fail("TRANSPORT_SOURCE_INGEST_MUST_BE_PULL_ONLY")
    workspace = _secure_directory(value.workspace, label="TRANSPORT_RECEIVER_WORKSPACE_UNSAFE")
    staging_root = _secure_directory(value.staging_root, label="TRANSPORT_RECEIVER_STAGE_ROOT_UNSAFE")
    if workspace == staging_root:
        _fail("TRANSPORT_RECEIVER_ROOTS_OVERLAP")
    return _ReceiverConfigFacts(
        receiver_site=_site(value.receiver_site, code="TRANSPORT_RECEIVER_CONFIG_ROUTE_INVALID"),
        workspace=workspace,
        staging_root=staging_root,
        bucket=_safe_bucket(value.bucket, code="TRANSPORT_RECEIVER_BUCKET_INVALID"),
        region=_safe_region(value.region, code="TRANSPORT_RECEIVER_REGION_INVALID"),
    )


def _bundle_binding(
    value: object,
    *,
    binding: _BindingFacts,
) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except PhysicalWalObjectManifestError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_VERIFIED_BUNDLE_REQUIRED"
        ) from exc
    baseline = bundle.baseline
    if (
        baseline.source_public_key != binding.source_public_key
        or baseline.source_site != binding.source_site
        or baseline.destination_site != binding.destination_site
        or baseline.campaign_id != binding.campaign_id
        or baseline.release_sha != binding.release_sha
        or baseline.writer_term.epoch != binding.writer_epoch
        or baseline.writer_term.lease_id != binding.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or baseline.baseline_generation_id != binding.baseline_generation_id
        or baseline.manifest_sha256 != binding.baseline_manifest_sha256
        or baseline.database_system_identifier != binding.database_system_identifier
        or baseline.timeline_id != binding.timeline_id
        or baseline.wal_segment_size_bytes != binding.wal_segment_size_bytes
        or baseline.baseline_wal_lsn != binding.baseline_wal_lsn
        or baseline.wal_chain_start_lsn != binding.wal_chain_start_lsn
        or baseline.base_backup_end_lsn != binding.base_backup_end_lsn
        or baseline.base_backup_object.age_recipient != binding.destination_age_recipient
        or bundle.blob_frontier.blob_object_frontier_wal_lsn != bundle.terminal_wal_lsn
    ):
        _fail("TRANSPORT_BUNDLE_BINDING_MISMATCH")
    return bundle


def _bundle_manifest_sha256(
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    *,
    binding: _BindingFacts,
) -> str:
    payload = {
        "schema": "gold-trade-physical-wal-manifest-bundle-digest-v1",
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "writer_term": _term_mapping(binding),
        "baseline_generation_id": binding.baseline_generation_id,
        "baseline_manifest_sha256": binding.baseline_manifest_sha256,
        "destination_age_recipient": binding.destination_age_recipient,
        "route_binding_sha256": binding.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
    }
    return hashlib.sha256(_BUNDLE_DIGEST_DOMAIN + _canonical(payload, code="TRANSPORT_BUNDLE_DIGEST_INVALID")).hexdigest()


def derive_physical_wal_manifest_object_key(
    *,
    binding: PhysicalWalManifestObjectStorageTransportBinding,
    bundle_manifest_sha256: str,
) -> str:
    """Derive the only allowed immutable package key for one exact bundle.

    The path deliberately contains the directed route digest, base-manifest
    digest, and deterministic digest of all signed component manifests.  It
    contains no caller-controlled filename, pointer, latest alias, or version.
    """

    facts = _binding_facts(binding)
    bundle_sha = _sha256(bundle_manifest_sha256, code="TRANSPORT_BUNDLE_DIGEST_INVALID")
    key = "/".join(
        (
            "physical-wal-manifests",
            "v1",
            facts.campaign_id,
            facts.release_sha,
            f"{facts.source_site}-to-{facts.destination_site}",
            f"route-{facts.route_binding_sha256}",
            f"baseline-{facts.baseline_generation_id}-{facts.baseline_manifest_sha256}",
            f"bundle-{bundle_sha}.age",
        )
    )
    return _object_key(key, code="TRANSPORT_DERIVED_OBJECT_KEY_INVALID")


def _signer_fields(public_key: bytes) -> dict[str, str]:
    return {
        "algorithm": PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
        "public_key_base64": base64.b64encode(public_key).decode("ascii"),
        "key_id": _key_id(public_key),
    }


def _parse_signer(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if item["algorithm"] != PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM:
        _fail(code)
    if not isinstance(item["public_key_base64"], str):
        _fail(code)
    try:
        public_key = base64.b64decode(item["public_key_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    public_key = _public_key(public_key, code=code)
    if _text(item["key_id"], pattern=_KEY_ID_RE, code=code) != _key_id(public_key):
        _fail(code)
    return public_key


def _parse_signature(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if item["algorithm"] != PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM:
        _fail(code)
    if not isinstance(item["signature_base64"], str):
        _fail(code)
    try:
        signature = base64.b64decode(item["signature_base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if len(signature) != 64:
        _fail(code)
    return signature


def _signature_input(value: Mapping[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "transport_signature"}
    return _PACKAGE_SIGNATURE_DOMAIN + _canonical(unsigned, code="TRANSPORT_PACKAGE_CANONICAL_INVALID")


def _base64_manifest(value: object, *, code: str) -> bytes:
    if not isinstance(value, str) or not value:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not 1 <= len(raw) <= MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
        _fail(code)
    return raw


def _package_mapping(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: _BindingFacts,
    bundle_manifest_sha256: str,
    source_public_key: bytes,
    signer: PhysicalWalManifestPackageSigner,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PACKAGE_SCHEMA,
        "version": 1,
        "kind": "physical_postgresql_signed_manifest_bundle",
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "writer_term": _term_mapping(binding),
        "baseline_generation_id": binding.baseline_generation_id,
        "baseline_manifest_sha256": binding.baseline_manifest_sha256,
        "database_system_identifier": binding.database_system_identifier,
        "timeline_id": binding.timeline_id,
        "wal_segment_size_bytes": binding.wal_segment_size_bytes,
        "baseline_wal_lsn": binding.baseline_wal_lsn,
        "wal_chain_start_lsn": binding.wal_chain_start_lsn,
        "base_backup_end_lsn": binding.base_backup_end_lsn,
        "destination_age_recipient": binding.destination_age_recipient,
        "route_binding_sha256": binding.route_binding_sha256,
        "terminal_wal_lsn": bundle.terminal_wal_lsn,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "base_backup_manifest_base64": base64.b64encode(bundle.baseline.canonical_manifest).decode("ascii"),
        "wal_segment_manifests_base64": [
            base64.b64encode(item.canonical_manifest).decode("ascii") for item in bundle.wal_manifests
        ],
        "blob_frontier_manifest_base64": base64.b64encode(
            bundle.blob_frontier.canonical_manifest
        ).decode("ascii"),
        "source_signer": _signer_fields(source_public_key),
    }
    try:
        signature = signer.sign(message=_signature_input(unsigned))
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_PACKAGE_SIGNING_FAILED"
        ) from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        _fail("TRANSPORT_PACKAGE_SIGNING_FAILED")
    result = dict(unsigned)
    result["transport_signature"] = {
        "algorithm": PHYSICAL_WAL_OBJECT_MANIFEST_SIGNATURE_ALGORITHM,
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    return result


def _verify_injected_signature(
    verifier: object,
    *,
    public_key: bytes,
    message: bytes,
    signature: bytes,
    code: str,
) -> None:
    if not callable(getattr(verifier, "verify", None)):
        _fail(code)
    try:
        result = verifier.verify(public_key=public_key, message=message, signature=signature)
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc
    if result is not None:
        _fail(code)


def _verify_package(
    raw: object,
    *,
    binding: _BindingFacts,
    verifier: object,
) -> _PackageFacts:
    item, canonical_raw = _parse_canonical_mapping(
        raw,
        maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
        code="TRANSPORT_PACKAGE_INVALID",
    )
    item = _exact_mapping(item, fields=_PACKAGE_FIELDS, code="TRANSPORT_PACKAGE_INVALID")
    if (
        item["schema"] != PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PACKAGE_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
        or item["kind"] != "physical_postgresql_signed_manifest_bundle"
    ):
        _fail("TRANSPORT_PACKAGE_INVALID")
    if (
        item["source_site"] != binding.source_site
        or item["destination_site"] != binding.destination_site
        or item["campaign_id"] != binding.campaign_id
        or item["release_sha"] != binding.release_sha
        or _term_facts(item["writer_term"], code="TRANSPORT_PACKAGE_TERM_INVALID")
        != (
            binding.writer_epoch,
            binding.writer_lease_id,
            binding.witnessed_term_proof_sha256,
        )
        or item["baseline_generation_id"] != binding.baseline_generation_id
        or _sha256(item["baseline_manifest_sha256"], code="TRANSPORT_PACKAGE_INVALID")
        != binding.baseline_manifest_sha256
        or item["database_system_identifier"] != binding.database_system_identifier
        or type(item["timeline_id"]) is not int
        or item["timeline_id"] != binding.timeline_id
        or type(item["wal_segment_size_bytes"]) is not int
        or item["wal_segment_size_bytes"] != binding.wal_segment_size_bytes
        or item["baseline_wal_lsn"] != binding.baseline_wal_lsn
        or item["wal_chain_start_lsn"] != binding.wal_chain_start_lsn
        or item["base_backup_end_lsn"] != binding.base_backup_end_lsn
        or item["destination_age_recipient"] != binding.destination_age_recipient
        or _sha256(item["route_binding_sha256"], code="TRANSPORT_PACKAGE_INVALID")
        != binding.route_binding_sha256
    ):
        _fail("TRANSPORT_PACKAGE_BINDING_MISMATCH")
    # Re-validate grammar even where values are equal to a pre-validated pin;
    # the package is untrusted plaintext after decryption.
    _text(item["campaign_id"], pattern=CAMPAIGN_ID_RE, code="TRANSPORT_PACKAGE_INVALID")
    _text(item["release_sha"], pattern=RELEASE_SHA_RE, code="TRANSPORT_PACKAGE_INVALID")
    _text(
        item["baseline_generation_id"],
        pattern=STREAM_GENERATION_ID_RE,
        code="TRANSPORT_PACKAGE_INVALID",
    )
    _text(
        item["database_system_identifier"],
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="TRANSPORT_PACKAGE_INVALID",
    )
    _lsn(item["baseline_wal_lsn"], code="TRANSPORT_PACKAGE_INVALID")
    _lsn(item["wal_chain_start_lsn"], code="TRANSPORT_PACKAGE_INVALID")
    _lsn(item["base_backup_end_lsn"], code="TRANSPORT_PACKAGE_INVALID")
    terminal_lsn, _terminal_lsn_value = _lsn(item["terminal_wal_lsn"], code="TRANSPORT_PACKAGE_INVALID")
    _text(
        item["destination_age_recipient"],
        pattern=AGE_RECIPIENT_RE,
        code="TRANSPORT_PACKAGE_INVALID",
    )
    signer_key = _parse_signer(item["source_signer"], code="TRANSPORT_PACKAGE_SIGNER_INVALID")
    if signer_key != binding.source_public_key:
        _fail("TRANSPORT_PACKAGE_SIGNER_MISMATCH")
    signature = _parse_signature(item["transport_signature"], code="TRANSPORT_PACKAGE_SIGNATURE_INVALID")
    _verify_injected_signature(
        verifier,
        public_key=signer_key,
        message=_signature_input(item),
        signature=signature,
        code="TRANSPORT_PACKAGE_SIGNATURE_INVALID",
    )
    base_raw = _base64_manifest(item["base_backup_manifest_base64"], code="TRANSPORT_PACKAGE_INVALID")
    wal_encoded = item["wal_segment_manifests_base64"]
    if isinstance(wal_encoded, (str, bytes)) or not isinstance(wal_encoded, Sequence):
        _fail("TRANSPORT_PACKAGE_INVALID")
    if not 1 <= len(wal_encoded) <= MAX_PHYSICAL_WAL_SEGMENTS_PER_MANIFEST:
        _fail("TRANSPORT_PACKAGE_INVALID")
    wal_raw = tuple(_base64_manifest(value, code="TRANSPORT_PACKAGE_INVALID") for value in wal_encoded)
    blob_raw = _base64_manifest(item["blob_frontier_manifest_base64"], code="TRANSPORT_PACKAGE_INVALID")
    try:
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base_raw,
            wal_segment_manifests=wal_raw,
            blob_frontier_manifest=blob_raw,
            expected_source_public_key=binding.source_public_key,
            expected_source_site=binding.source_site,
            expected_destination_site=binding.destination_site,
            expected_campaign_id=binding.campaign_id,
            expected_release_sha=binding.release_sha,
            expected_writer_epoch=binding.writer_epoch,
            expected_writer_lease_id=binding.writer_lease_id,
            expected_witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
            expected_baseline_generation_id=binding.baseline_generation_id,
            expected_wal_segment_size_bytes=binding.wal_segment_size_bytes,
            expected_destination_age_recipient=binding.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_PACKAGE_BUNDLE_INVALID"
        ) from exc
    if terminal_lsn != bundle.terminal_wal_lsn:
        _fail("TRANSPORT_PACKAGE_TERMINAL_WAL_MISMATCH")
    expected_hashes = tuple(bundle.manifest_sha256es)
    supplied_hashes = item["manifest_sha256es"]
    if isinstance(supplied_hashes, (str, bytes)) or not isinstance(supplied_hashes, Sequence):
        _fail("TRANSPORT_PACKAGE_INVALID")
    normalized_hashes = tuple(_sha256(value, code="TRANSPORT_PACKAGE_INVALID") for value in supplied_hashes)
    if normalized_hashes != expected_hashes or len(set(normalized_hashes)) != len(normalized_hashes):
        _fail("TRANSPORT_PACKAGE_MANIFEST_HASH_MISMATCH")
    expected_bundle_sha = _bundle_manifest_sha256(bundle, binding=binding)
    if (
        _sha256(item["bundle_manifest_sha256"], code="TRANSPORT_PACKAGE_INVALID")
        != expected_bundle_sha
    ):
        _fail("TRANSPORT_PACKAGE_BUNDLE_DIGEST_MISMATCH")
    return _PackageFacts(
        raw=canonical_raw,
        package_sha256=hashlib.sha256(canonical_raw).hexdigest(),
        bundle_manifest_sha256=expected_bundle_sha,
        verified_bundle=bundle,
    )


def verify_physical_wal_manifest_object_storage_package(
    *,
    package_bytes: bytes,
    binding: PhysicalWalManifestObjectStorageTransportBinding,
    verifier: PhysicalWalManifestPackageVerifier,
) -> VerifiedPhysicalWalObjectStorageBundle:
    """Verify an encrypted-package plaintext after a caller decrypts it locally.

    This function performs no I/O.  It verifies the package's domain-separated
    source signature *and* invokes the existing base/WAL/blob signature and
    continuity verifier under every root-pinned binding fact.
    """

    facts = _binding_facts(binding)
    return _verify_package(package_bytes, binding=facts, verifier=verifier).verified_bundle


def _response_has_provider_side_encryption(value: object) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).replace("-", "").replace("_", "").lower()
            if key.startswith(("serversideencryption", "sse", "kms", "bucketkey")):
                return True
            if key == "httpheaders" and isinstance(item, Mapping):
                for header_name in item:
                    normalized = str(header_name).lower()
                    if normalized.startswith(
                        (
                            "x-amz-server-side-encryption",
                            "x-amz-sse",
                            "x-amz-kms",
                            "x-amz-bucket-key",
                        )
                    ):
                        return True
            if _response_has_provider_side_encryption(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_response_has_provider_side_encryption(item) for item in value)
    return False


def _private_versioned_bucket(client: object, *, bucket: str) -> None:
    if not callable(getattr(client, "get_bucket_versioning", None)) or not callable(
        getattr(client, "get_bucket_acl", None)
    ):
        _fail("TRANSPORT_BUCKET_PREFLIGHT_UNAVAILABLE")
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_BUCKET_VERSIONING_UNVERIFIABLE"
        ) from exc
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        _fail("TRANSPORT_BUCKET_VERSIONING_REQUIRED")
    try:
        acl = client.get_bucket_acl(Bucket=bucket)
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_BUCKET_ACL_UNVERIFIABLE"
        ) from exc
    if (
        not isinstance(acl, Mapping)
        or not isinstance(acl.get("Owner"), Mapping)
        or not isinstance(acl["Owner"].get("ID"), str)
        or not acl["Owner"]["ID"]
        or not isinstance(acl.get("Grants"), list)
        or not acl["Grants"]
    ):
        _fail("TRANSPORT_BUCKET_ACL_INVALID")
    owner_id = acl["Owner"]["ID"]
    for grant in acl["Grants"]:
        if (
            not isinstance(grant, Mapping)
            or not isinstance(grant.get("Grantee"), Mapping)
            or grant["Grantee"].get("Type") != "CanonicalUser"
            or grant["Grantee"].get("ID") != owner_id
            or grant.get("Permission") != "FULL_CONTROL"
        ):
            _fail("TRANSPORT_BUCKET_ACL_NOT_OWNER_ONLY")


def _hash_file(path: Path, *, maximum_bytes: int, code: str) -> tuple[str, int]:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("TRANSPORT_NOFOLLOW_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail(code)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            digest.update(chunk)
        return digest.hexdigest(), total
    finally:
        os.close(fd)


def _read_private_file(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    """Read a root-owned 0600 regular file through one non-symlink FD."""

    if not hasattr(os, "O_NOFOLLOW"):
        _fail("TRANSPORT_NOFOLLOW_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            _fail(code)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            chunks.append(chunk)
        after = os.fstat(fd)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or total != before.st_size
        ):
            _fail(code)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_new_private_file(path: Path, value: bytes, *, maximum_bytes: int, code: str) -> None:
    if not isinstance(value, bytes) or not 1 <= len(value) <= maximum_bytes:
        _fail(code)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(code) from exc
    try:
        offset = 0
        while offset < len(value):
            count = os.write(fd, value[offset:])
            if not isinstance(count, int) or count <= 0:
                _fail(code)
            offset += count
        os.fsync(fd)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(value)
        ):
            _fail(code)
    finally:
        os.close(fd)


def _validate_age_ciphertext(path: Path) -> tuple[str, int]:
    digest, size = _hash_file(
        path,
        maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_CIPHERTEXT_BYTES,
        code="TRANSPORT_CIPHERTEXT_UNSAFE",
    )
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb", closefd=True) as handle:
            header = handle.read(len(b"age-encryption.org/v1\n"))
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_CIPHERTEXT_UNSAFE"
        ) from exc
    if header != b"age-encryption.org/v1\n":
        _fail("TRANSPORT_CIPHERTEXT_NOT_AGE_V1")
    return digest, size


def _metadata(
    *,
    binding: _BindingFacts,
    bundle_manifest_sha256: str,
    package_sha256: str,
    plaintext_bytes: int,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> dict[str, str]:
    return {
        "transport-schema": PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_TRANSPORT_SCHEMA,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "route-binding-sha256": binding.route_binding_sha256,
        "baseline-manifest-sha256": binding.baseline_manifest_sha256,
        "bundle-manifest-sha256": bundle_manifest_sha256,
        "package-sha256": package_sha256,
        "destination-age-recipient": binding.destination_age_recipient,
        "plaintext-bytes": str(plaintext_bytes),
        "ciphertext-sha256": ciphertext_sha256,
        "ciphertext-bytes": str(ciphertext_bytes),
    }


def _head_exact(
    client: object,
    *,
    bucket: str,
    object_key: str,
    version_id: str,
    ciphertext_bytes: int,
    metadata: Mapping[str, str],
) -> None:
    if not callable(getattr(client, "head_object", None)):
        _fail("TRANSPORT_HEAD_UNAVAILABLE")
    try:
        response = client.head_object(Bucket=bucket, Key=object_key, VersionId=version_id)
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError("TRANSPORT_HEAD_FAILED") from exc
    if (
        not isinstance(response, Mapping)
        or _response_has_provider_side_encryption(response)
        or response.get("VersionId") != version_id
        or type(response.get("ContentLength")) is not int
        or response.get("ContentLength") != ciphertext_bytes
        or not isinstance(response.get("Metadata"), Mapping)
        or dict(response["Metadata"]) != dict(metadata)
    ):
        _fail("TRANSPORT_HEAD_IDENTITY_MISMATCH")


def _download_exact_to_file(
    client: object,
    *,
    bucket: str,
    object_key: str,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    metadata: Mapping[str, str],
    destination_path: Path,
) -> None:
    if not callable(getattr(client, "get_object", None)):
        _fail("TRANSPORT_GET_UNAVAILABLE")
    try:
        response = client.get_object(Bucket=bucket, Key=object_key, VersionId=version_id)
    except Exception as exc:
        raise PhysicalWalManifestObjectStorageTransportError("TRANSPORT_GET_FAILED") from exc
    if (
        not isinstance(response, Mapping)
        or _response_has_provider_side_encryption(response)
        or response.get("VersionId") != version_id
        or type(response.get("ContentLength")) is not int
        or response.get("ContentLength") != ciphertext_bytes
        or not isinstance(response.get("Metadata"), Mapping)
        or dict(response["Metadata"]) != dict(metadata)
    ):
        _fail("TRANSPORT_GET_IDENTITY_MISMATCH")
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        _fail("TRANSPORT_GET_BODY_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(destination_path, flags, 0o600)
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError("TRANSPORT_DOWNLOAD_WRITE_FAILED") from exc
    close = getattr(body, "close", None)
    try:
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = body.read(_READ_CHUNK_BYTES)
            except Exception as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_GET_BODY_FAILED"
                ) from exc
            if not isinstance(chunk, bytes):
                _fail("TRANSPORT_GET_BODY_INVALID")
            if not chunk:
                break
            total += len(chunk)
            if total > ciphertext_bytes:
                _fail("TRANSPORT_GET_IDENTITY_MISMATCH")
            offset = 0
            while offset < len(chunk):
                count = os.write(fd, chunk[offset:])
                if not isinstance(count, int) or count <= 0:
                    _fail("TRANSPORT_DOWNLOAD_WRITE_FAILED")
                offset += count
            digest.update(chunk)
        os.fsync(fd)
        metadata_local = os.fstat(fd)
        if (
            total != ciphertext_bytes
            or digest.hexdigest() != ciphertext_sha256
            or not stat.S_ISREG(metadata_local.st_mode)
            or metadata_local.st_nlink != 1
            or metadata_local.st_uid != 0
            or stat.S_IMODE(metadata_local.st_mode) != 0o600
            or metadata_local.st_size != ciphertext_bytes
        ):
            _fail("TRANSPORT_GET_IDENTITY_MISMATCH")
    finally:
        os.close(fd)
        if callable(close):
            try:
                close()
            except Exception as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_GET_BODY_CLOSE_FAILED"
                ) from exc


def _receipt_unsigned(
    *,
    binding: _BindingFacts,
    bucket: str,
    region: str,
    bundle_manifest_sha256: str,
    package_sha256: str,
    object_key: str,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    plaintext_bytes: int,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_RECEIPT_SCHEMA,
        "status": PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PUBLISHED_STATUS,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "writer_term": _term_mapping(binding),
        "baseline_generation_id": binding.baseline_generation_id,
        "baseline_manifest_sha256": binding.baseline_manifest_sha256,
        "database_system_identifier": binding.database_system_identifier,
        "timeline_id": binding.timeline_id,
        "wal_segment_size_bytes": binding.wal_segment_size_bytes,
        "baseline_wal_lsn": binding.baseline_wal_lsn,
        "wal_chain_start_lsn": binding.wal_chain_start_lsn,
        "base_backup_end_lsn": binding.base_backup_end_lsn,
        "destination_age_recipient": binding.destination_age_recipient,
        "route_binding_sha256": binding.route_binding_sha256,
        "bucket": bucket,
        "region": region,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "package_sha256": package_sha256,
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext_sha256,
        "ciphertext_bytes": ciphertext_bytes,
        "plaintext_bytes": plaintext_bytes,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    }


def _mint_receipt(value: Mapping[str, Any], *, raw: bytes) -> PhysicalWalManifestPublicationReceipt:
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    expected_receipt_sha256 = hashlib.sha256(
        _canonical(unsigned, code="TRANSPORT_RECEIPT_INVALID")
    ).hexdigest()
    receipt_sha256 = _sha256(value["receipt_sha256"], code="TRANSPORT_RECEIPT_INVALID")
    if receipt_sha256 != expected_receipt_sha256:
        _fail("TRANSPORT_RECEIPT_DIGEST_MISMATCH")
    term = _term_facts(value["writer_term"], code="TRANSPORT_RECEIPT_TERM_INVALID")
    result = PhysicalWalManifestPublicationReceipt(
        canonical_receipt=raw,
        receipt_sha256=receipt_sha256,
        source_site=_site(value["source_site"], code="TRANSPORT_RECEIPT_INVALID"),
        destination_site=_site(value["destination_site"], code="TRANSPORT_RECEIPT_INVALID"),
        campaign_id=_text(value["campaign_id"], pattern=CAMPAIGN_ID_RE, code="TRANSPORT_RECEIPT_INVALID"),
        release_sha=_text(value["release_sha"], pattern=RELEASE_SHA_RE, code="TRANSPORT_RECEIPT_INVALID"),
        writer_epoch=term[0],
        writer_lease_id=term[1],
        witnessed_term_proof_sha256=term[2],
        baseline_generation_id=_text(
            value["baseline_generation_id"],
            pattern=STREAM_GENERATION_ID_RE,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
        baseline_manifest_sha256=_sha256(value["baseline_manifest_sha256"], code="TRANSPORT_RECEIPT_INVALID"),
        database_system_identifier=_text(
            value["database_system_identifier"],
            pattern=_SYSTEM_IDENTIFIER_RE,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
        timeline_id=_positive_int(value["timeline_id"], maximum=0xFFFFFFFF, code="TRANSPORT_RECEIPT_INVALID"),
        wal_segment_size_bytes=_positive_int(
            value["wal_segment_size_bytes"],
            maximum=1024 * 1024 * 1024,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
        baseline_wal_lsn=_lsn(value["baseline_wal_lsn"], code="TRANSPORT_RECEIPT_INVALID")[0],
        wal_chain_start_lsn=_lsn(value["wal_chain_start_lsn"], code="TRANSPORT_RECEIPT_INVALID")[0],
        base_backup_end_lsn=_lsn(value["base_backup_end_lsn"], code="TRANSPORT_RECEIPT_INVALID")[0],
        destination_age_recipient=_text(
            value["destination_age_recipient"],
            pattern=AGE_RECIPIENT_RE,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
        route_binding_sha256=_sha256(value["route_binding_sha256"], code="TRANSPORT_RECEIPT_INVALID"),
        bucket=_safe_bucket(value["bucket"], code="TRANSPORT_RECEIPT_INVALID"),
        region=_safe_region(value["region"], code="TRANSPORT_RECEIPT_INVALID"),
        bundle_manifest_sha256=_sha256(value["bundle_manifest_sha256"], code="TRANSPORT_RECEIPT_INVALID"),
        package_sha256=_sha256(value["package_sha256"], code="TRANSPORT_RECEIPT_INVALID"),
        object_key=_object_key(value["object_key"], code="TRANSPORT_RECEIPT_INVALID"),
        version_id=_version_id(value["version_id"], code="TRANSPORT_RECEIPT_INVALID"),
        ciphertext_sha256=_sha256(value["ciphertext_sha256"], code="TRANSPORT_RECEIPT_INVALID"),
        ciphertext_bytes=_positive_int(
            value["ciphertext_bytes"],
            maximum=MAX_PHYSICAL_WAL_MANIFEST_CIPHERTEXT_BYTES,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
        plaintext_bytes=_positive_int(
            value["plaintext_bytes"],
            maximum=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
            code="TRANSPORT_RECEIPT_INVALID",
        ),
    )
    if result.source_site == result.destination_site:
        _fail("TRANSPORT_RECEIPT_INVALID")
    if (
        value["schema"] != PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_RECEIPT_SCHEMA
        or value["status"] != PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_PUBLISHED_STATUS
        or value["encryption"] != PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION
        or value["immutability"] != PHYSICAL_WAL_OBJECT_IMMUTABILITY
    ):
        _fail("TRANSPORT_RECEIPT_INVALID")
    object.__setattr__(result, "_capability", _VERIFIED_RECEIPT_CAPABILITY)
    return result


def parse_physical_wal_manifest_publication_receipt(
    receipt_bytes: bytes,
) -> PhysicalWalManifestPublicationReceipt:
    """Strictly parse a canonical receipt; route authorization remains caller-pinned."""

    value, raw = _parse_canonical_mapping(
        receipt_bytes,
        maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_RECEIPT_BYTES,
        code="TRANSPORT_RECEIPT_INVALID",
    )
    return _mint_receipt(_exact_mapping(value, fields=_RECEIPT_FIELDS, code="TRANSPORT_RECEIPT_INVALID"), raw=raw)


def require_verified_physical_wal_manifest_publication_receipt(
    value: object,
) -> PhysicalWalManifestPublicationReceipt:
    """Re-parse an opaque source receipt before a caller distributes or pins it."""

    if type(value) is not PhysicalWalManifestPublicationReceipt:
        _fail("TRANSPORT_VERIFIED_RECEIPT_REQUIRED")
    if value._capability is not _VERIFIED_RECEIPT_CAPABILITY:
        _fail("TRANSPORT_VERIFIED_RECEIPT_REQUIRED")
    normalized = parse_physical_wal_manifest_publication_receipt(value.canonical_receipt)
    if normalized != value:
        _fail("TRANSPORT_VERIFIED_RECEIPT_NOT_NORMALIZED")
    return value


def _pin_facts(value: object) -> tuple[_BindingFacts, str, str, str, str, str]:
    if type(value) is not PhysicalWalManifestReceiverPin:
        _fail("TRANSPORT_RECEIVER_PIN_INVALID")
    binding = _binding_facts(value.binding)
    bundle_sha = _sha256(value.expected_bundle_manifest_sha256, code="TRANSPORT_RECEIVER_PIN_INVALID")
    object_key = _object_key(value.expected_object_key, code="TRANSPORT_RECEIVER_PIN_INVALID")
    if object_key != derive_physical_wal_manifest_object_key(
        binding=value.binding,
        bundle_manifest_sha256=bundle_sha,
    ):
        _fail("TRANSPORT_RECEIVER_PIN_OBJECT_KEY_INVALID")
    return (
        binding,
        _sha256(value.expected_receipt_sha256, code="TRANSPORT_RECEIVER_PIN_INVALID"),
        object_key,
        _version_id(value.expected_version_id, code="TRANSPORT_RECEIVER_PIN_INVALID"),
        bundle_sha,
        _sha256(value.expected_package_sha256, code="TRANSPORT_RECEIVER_PIN_INVALID"),
    )


def _receipt_matches(
    receipt: PhysicalWalManifestPublicationReceipt,
    *,
    binding: _BindingFacts,
    expected_receipt_sha256: str,
    expected_object_key: str,
    expected_version_id: str,
    expected_bundle_manifest_sha256: str,
    expected_package_sha256: str,
    bucket: str,
    region: str,
) -> None:
    if (
        receipt.receipt_sha256 != expected_receipt_sha256
        or receipt.source_site != binding.source_site
        or receipt.destination_site != binding.destination_site
        or receipt.campaign_id != binding.campaign_id
        or receipt.release_sha != binding.release_sha
        or receipt.writer_epoch != binding.writer_epoch
        or receipt.writer_lease_id != binding.writer_lease_id
        or receipt.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or receipt.baseline_generation_id != binding.baseline_generation_id
        or receipt.baseline_manifest_sha256 != binding.baseline_manifest_sha256
        or receipt.database_system_identifier != binding.database_system_identifier
        or receipt.timeline_id != binding.timeline_id
        or receipt.wal_segment_size_bytes != binding.wal_segment_size_bytes
        or receipt.baseline_wal_lsn != binding.baseline_wal_lsn
        or receipt.wal_chain_start_lsn != binding.wal_chain_start_lsn
        or receipt.base_backup_end_lsn != binding.base_backup_end_lsn
        or receipt.destination_age_recipient != binding.destination_age_recipient
        or receipt.route_binding_sha256 != binding.route_binding_sha256
        or receipt.bucket != bucket
        or receipt.region != region
        or receipt.bundle_manifest_sha256 != expected_bundle_manifest_sha256
        or receipt.package_sha256 != expected_package_sha256
        or receipt.object_key != expected_object_key
        or receipt.version_id != expected_version_id
        or receipt.plaintext_bytes > MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES
        or receipt.object_key
        != derive_physical_wal_manifest_object_key(
            binding=PhysicalWalManifestObjectStorageTransportBinding(
                source_site=binding.source_site,
                destination_site=binding.destination_site,
                source_public_key=binding.source_public_key,
                campaign_id=binding.campaign_id,
                release_sha=binding.release_sha,
                writer_epoch=binding.writer_epoch,
                writer_lease_id=binding.writer_lease_id,
                witnessed_term_proof_sha256=binding.witnessed_term_proof_sha256,
                baseline_generation_id=binding.baseline_generation_id,
                baseline_manifest_sha256=binding.baseline_manifest_sha256,
                database_system_identifier=binding.database_system_identifier,
                timeline_id=binding.timeline_id,
                wal_segment_size_bytes=binding.wal_segment_size_bytes,
                baseline_wal_lsn=binding.baseline_wal_lsn,
                wal_chain_start_lsn=binding.wal_chain_start_lsn,
                base_backup_end_lsn=binding.base_backup_end_lsn,
                destination_age_recipient=binding.destination_age_recipient,
                route_binding_sha256=binding.route_binding_sha256,
            ),
            bundle_manifest_sha256=expected_bundle_manifest_sha256,
        )
    ):
        _fail("TRANSPORT_RECEIPT_PIN_MISMATCH")


def _stage_package_file(
    *,
    staging_root: Path,
    receipt_sha256: str,
    package_raw: bytes,
) -> tuple[Path, bool]:
    destination = staging_root / f"{receipt_sha256}.physical-wal-manifest.json"
    try:
        _write_new_private_file(
            destination,
            package_raw,
            maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
            code="TRANSPORT_STAGE_WRITE_FAILED",
        )
        return destination, False
    except PhysicalWalManifestObjectStorageTransportError as exc:
        if exc.code != "TRANSPORT_STAGE_WRITE_FAILED":
            raise
    # A narrow idempotency branch is safe only when the already-existing file
    # itself is protected and byte-identical to the freshly verified package.
    try:
        existing = _read_private_file(
            destination,
            maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
            code="TRANSPORT_STAGE_EXISTING_UNSAFE",
        )
    except PhysicalWalManifestObjectStorageTransportError:
        raise
    except OSError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_STAGE_EXISTING_UNSAFE"
        ) from exc
    if (
        len(existing) != len(package_raw)
        or hashlib.sha256(existing).hexdigest() != hashlib.sha256(package_raw).hexdigest()
        or existing != package_raw
    ):
        _fail("TRANSPORT_STAGE_EXISTING_MISMATCH")
    return destination, True


class PhysicalWalManifestObjectStoragePublisher:
    """Default-disabled source publisher for one exact signed manifest package."""

    def __init__(
        self,
        *,
        config: PhysicalWalManifestObjectStoragePublishConfig,
        age_encryptor_factory: Callable[[], PhysicalWalManifestAgeEncryptor] | None,
        client_factory: Callable[[], PhysicalWalManifestObjectStorageClient] | None,
        signer: PhysicalWalManifestPackageSigner | None,
        verifier: PhysicalWalManifestPackageVerifier | None,
    ) -> None:
        self._config = config
        self._age_encryptor_factory = age_encryptor_factory
        self._client_factory = client_factory
        self._signer = signer
        self._verifier = verifier

    def publish(
        self,
        *,
        verified_bundle: VerifiedPhysicalWalObjectStorageBundle,
        binding: PhysicalWalManifestObjectStorageTransportBinding,
    ) -> PhysicalWalManifestPublicationReceipt:
        """Publish one locally verified bundle via conditional immutable PUT/readback."""

        config = _publish_config_facts(self._config)
        facts = _binding_facts(binding)
        if (
            config.source_site != facts.source_site
            or config.destination_site != facts.destination_site
            or config.destination_age_recipient != facts.destination_age_recipient
        ):
            _fail("TRANSPORT_PUBLISH_CONFIG_BINDING_MISMATCH")
        bundle = _bundle_binding(verified_bundle, binding=facts)
        if self._signer is None or not callable(getattr(self._signer, "public_key_bytes", None)):
            _fail("TRANSPORT_PACKAGE_SIGNER_REQUIRED")
        if self._verifier is None:
            _fail("TRANSPORT_PACKAGE_VERIFIER_REQUIRED")
        try:
            source_public_key = self._signer.public_key_bytes()
        except Exception as exc:
            raise PhysicalWalManifestObjectStorageTransportError(
                "TRANSPORT_PACKAGE_SIGNER_REQUIRED"
            ) from exc
        source_public_key = _public_key(source_public_key, code="TRANSPORT_PACKAGE_SIGNER_REQUIRED")
        if source_public_key != facts.source_public_key or not callable(getattr(self._signer, "sign", None)):
            _fail("TRANSPORT_PACKAGE_SIGNER_MISMATCH")
        bundle_manifest_sha256 = _bundle_manifest_sha256(bundle, binding=facts)
        package = _package_mapping(
            bundle=bundle,
            binding=facts,
            bundle_manifest_sha256=bundle_manifest_sha256,
            source_public_key=source_public_key,
            signer=self._signer,
        )
        package_raw = _canonical(package, code="TRANSPORT_PACKAGE_CANONICAL_INVALID")
        package_facts = _verify_package(package_raw, binding=facts, verifier=self._verifier)
        if package_facts.verified_bundle != bundle or package_facts.bundle_manifest_sha256 != bundle_manifest_sha256:
            _fail("TRANSPORT_PACKAGE_LOCAL_VERIFY_MISMATCH")
        object_key = derive_physical_wal_manifest_object_key(
            binding=binding,
            bundle_manifest_sha256=bundle_manifest_sha256,
        )
        if self._age_encryptor_factory is None or not callable(self._age_encryptor_factory):
            _fail("TRANSPORT_AGE_ENCRYPTOR_FACTORY_REQUIRED")
        if self._client_factory is None or not callable(self._client_factory):
            _fail("TRANSPORT_OBJECT_CLIENT_FACTORY_REQUIRED")
        try:
            encryptor = self._age_encryptor_factory()
            client = self._client_factory()
        except Exception as exc:
            raise PhysicalWalManifestObjectStorageTransportError(
                "TRANSPORT_DEPENDENCY_FACTORY_FAILED"
            ) from exc
        if not callable(getattr(encryptor, "encrypt", None)):
            _fail("TRANSPORT_AGE_ENCRYPTOR_INVALID")
        if not callable(getattr(client, "put_object", None)):
            _fail("TRANSPORT_OBJECT_CLIENT_INVALID")
        _private_versioned_bucket(client, bucket=config.bucket)
        with tempfile.TemporaryDirectory(
            prefix="physical-wal-manifest-publish-", dir=str(config.workspace)
        ) as temporary:
            work = Path(temporary)
            try:
                os.chmod(work, 0o700)
            except OSError as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_PUBLISH_WORKSPACE_UNSAFE"
                ) from exc
            if _secure_directory(work, label="TRANSPORT_PUBLISH_WORKSPACE_UNSAFE") != work.resolve():
                _fail("TRANSPORT_PUBLISH_WORKSPACE_UNSAFE")
            plaintext_path = work / "manifest-package.json"
            ciphertext_path = work / "manifest-package.age"
            readback_path = work / "manifest-package-readback.age"
            _write_new_private_file(
                plaintext_path,
                package_raw,
                maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
                code="TRANSPORT_PACKAGE_WRITE_FAILED",
            )
            try:
                encryptor.encrypt(
                    recipient=facts.destination_age_recipient,
                    plaintext_path=plaintext_path,
                    ciphertext_path=ciphertext_path,
                )
            except Exception as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_AGE_ENCRYPTION_FAILED"
                ) from exc
            ciphertext_sha256, ciphertext_bytes = _validate_age_ciphertext(ciphertext_path)
            plaintext_sha256, plaintext_bytes = _hash_file(
                plaintext_path,
                maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
                code="TRANSPORT_PACKAGE_WRITE_FAILED",
            )
            if plaintext_sha256 != package_facts.package_sha256 or plaintext_bytes != len(package_raw):
                _fail("TRANSPORT_PACKAGE_MUTATED_DURING_ENCRYPTION")
            metadata = _metadata(
                binding=facts,
                bundle_manifest_sha256=bundle_manifest_sha256,
                package_sha256=package_facts.package_sha256,
                plaintext_bytes=plaintext_bytes,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
            )
            try:
                with os.fdopen(
                    os.open(ciphertext_path, os.O_RDONLY | os.O_NOFOLLOW), "rb", closefd=True
                ) as handle:
                    response = client.put_object(
                        Bucket=config.bucket,
                        Key=object_key,
                        Body=handle,
                        ContentLength=ciphertext_bytes,
                        Metadata=metadata,
                        ContentType="application/octet-stream",
                        IfNoneMatch="*",
                    )
            except Exception as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_CONDITIONAL_CREATE_ONLY_PUT_FAILED"
                ) from exc
            if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
                _fail("TRANSPORT_PUT_RESPONSE_INVALID")
            version_id = _version_id(response.get("VersionId"), code="TRANSPORT_PUT_VERSION_INVALID")
            _head_exact(
                client,
                bucket=config.bucket,
                object_key=object_key,
                version_id=version_id,
                ciphertext_bytes=ciphertext_bytes,
                metadata=metadata,
            )
            _download_exact_to_file(
                client,
                bucket=config.bucket,
                object_key=object_key,
                version_id=version_id,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
                metadata=metadata,
                destination_path=readback_path,
            )
            if _validate_age_ciphertext(readback_path) != (ciphertext_sha256, ciphertext_bytes):
                _fail("TRANSPORT_READBACK_CIPHERTEXT_MISMATCH")
        unsigned = _receipt_unsigned(
            binding=facts,
            bucket=config.bucket,
            region=config.region,
            bundle_manifest_sha256=bundle_manifest_sha256,
            package_sha256=package_facts.package_sha256,
            object_key=object_key,
            version_id=version_id,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
            plaintext_bytes=plaintext_bytes,
        )
        receipt = dict(unsigned)
        receipt["receipt_sha256"] = hashlib.sha256(
            _canonical(unsigned, code="TRANSPORT_RECEIPT_INVALID")
        ).hexdigest()
        raw_receipt = _canonical(receipt, code="TRANSPORT_RECEIPT_INVALID")
        return parse_physical_wal_manifest_publication_receipt(raw_receipt)


class PhysicalWalManifestObjectStorageReceiver:
    """Default-disabled receiver for one explicit immutable package receipt."""

    def __init__(
        self,
        *,
        config: PhysicalWalManifestObjectStorageReceiverConfig,
        client_factory: Callable[[], PhysicalWalManifestObjectStorageClient] | None,
        age_decryptor_factory: Callable[[], PhysicalWalManifestAgeDecryptor] | None,
        verifier: PhysicalWalManifestPackageVerifier | None,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._age_decryptor_factory = age_decryptor_factory
        self._verifier = verifier

    def stage(
        self,
        *,
        receipt_bytes: bytes,
        pin: PhysicalWalManifestReceiverPin,
    ) -> PhysicalWalManifestObjectStorageStageResult:
        """Fetch only the pinned exact version, decrypt, verify, and stage metadata."""

        config = _receiver_config_facts(self._config)
        (
            binding,
            expected_receipt_sha256,
            expected_object_key,
            expected_version_id,
            expected_bundle_manifest_sha256,
            expected_package_sha256,
        ) = _pin_facts(pin)
        if config.receiver_site != binding.destination_site:
            _fail("TRANSPORT_RECEIVER_CONFIG_BINDING_MISMATCH")
        receipt = parse_physical_wal_manifest_publication_receipt(receipt_bytes)
        _receipt_matches(
            receipt,
            binding=binding,
            expected_receipt_sha256=expected_receipt_sha256,
            expected_object_key=expected_object_key,
            expected_version_id=expected_version_id,
            expected_bundle_manifest_sha256=expected_bundle_manifest_sha256,
            expected_package_sha256=expected_package_sha256,
            bucket=config.bucket,
            region=config.region,
        )
        if self._client_factory is None or not callable(self._client_factory):
            _fail("TRANSPORT_OBJECT_CLIENT_FACTORY_REQUIRED")
        if self._age_decryptor_factory is None or not callable(self._age_decryptor_factory):
            _fail("TRANSPORT_AGE_DECRYPTOR_FACTORY_REQUIRED")
        if self._verifier is None:
            _fail("TRANSPORT_PACKAGE_VERIFIER_REQUIRED")
        try:
            client = self._client_factory()
            decryptor = self._age_decryptor_factory()
        except Exception as exc:
            raise PhysicalWalManifestObjectStorageTransportError(
                "TRANSPORT_DEPENDENCY_FACTORY_FAILED"
            ) from exc
        if not callable(getattr(client, "get_object", None)):
            _fail("TRANSPORT_OBJECT_CLIENT_INVALID")
        if not callable(getattr(decryptor, "decrypt", None)):
            _fail("TRANSPORT_AGE_DECRYPTOR_INVALID")
        _private_versioned_bucket(client, bucket=config.bucket)
        metadata = _metadata(
            binding=binding,
            bundle_manifest_sha256=receipt.bundle_manifest_sha256,
            package_sha256=receipt.package_sha256,
            plaintext_bytes=receipt.plaintext_bytes,
            ciphertext_sha256=receipt.ciphertext_sha256,
            ciphertext_bytes=receipt.ciphertext_bytes,
        )
        _head_exact(
            client,
            bucket=config.bucket,
            object_key=receipt.object_key,
            version_id=receipt.version_id,
            ciphertext_bytes=receipt.ciphertext_bytes,
            metadata=metadata,
        )
        with tempfile.TemporaryDirectory(
            prefix="physical-wal-manifest-receive-", dir=str(config.workspace)
        ) as temporary:
            work = Path(temporary)
            try:
                os.chmod(work, 0o700)
            except OSError as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_RECEIVER_WORKSPACE_UNSAFE"
                ) from exc
            if _secure_directory(work, label="TRANSPORT_RECEIVER_WORKSPACE_UNSAFE") != work.resolve():
                _fail("TRANSPORT_RECEIVER_WORKSPACE_UNSAFE")
            ciphertext_path = work / "manifest-package.age"
            plaintext_path = work / "manifest-package.json"
            _download_exact_to_file(
                client,
                bucket=config.bucket,
                object_key=receipt.object_key,
                version_id=receipt.version_id,
                ciphertext_sha256=receipt.ciphertext_sha256,
                ciphertext_bytes=receipt.ciphertext_bytes,
                metadata=metadata,
                destination_path=ciphertext_path,
            )
            if _validate_age_ciphertext(ciphertext_path) != (
                receipt.ciphertext_sha256,
                receipt.ciphertext_bytes,
            ):
                _fail("TRANSPORT_RECEIVER_CIPHERTEXT_MISMATCH")
            try:
                decryptor.decrypt(
                    expected_recipient=binding.destination_age_recipient,
                    ciphertext_path=ciphertext_path,
                    plaintext_path=plaintext_path,
                )
            except Exception as exc:
                raise PhysicalWalManifestObjectStorageTransportError(
                    "TRANSPORT_AGE_DECRYPTION_FAILED"
                ) from exc
            package_raw = _read_private_file(
                plaintext_path,
                maximum_bytes=MAX_PHYSICAL_WAL_MANIFEST_PACKAGE_BYTES,
                code="TRANSPORT_DECRYPTED_PACKAGE_UNSAFE",
            )
            if (
                len(package_raw) != receipt.plaintext_bytes
                or hashlib.sha256(package_raw).hexdigest() != receipt.package_sha256
            ):
                _fail("TRANSPORT_DECRYPTED_PACKAGE_IDENTITY_MISMATCH")
            package = _verify_package(package_raw, binding=binding, verifier=self._verifier)
            if (
                package.package_sha256 != expected_package_sha256
                or package.bundle_manifest_sha256 != expected_bundle_manifest_sha256
                or package.verified_bundle.terminal_wal_lsn
                != package.verified_bundle.blob_frontier.blob_object_frontier_wal_lsn
            ):
                _fail("TRANSPORT_RECEIVER_PACKAGE_PIN_MISMATCH")
            staged_path, idempotent = _stage_package_file(
                staging_root=config.staging_root,
                receipt_sha256=receipt.receipt_sha256,
                package_raw=package.raw,
            )
        result = PhysicalWalManifestObjectStorageStageResult(
            status=PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS,
            package_path=staged_path,
            receipt_sha256=receipt.receipt_sha256,
            package_sha256=package.package_sha256,
            bundle_manifest_sha256=package.bundle_manifest_sha256,
            verified_bundle=package.verified_bundle,
            idempotent=idempotent,
        )
        object.__setattr__(result, "_capability", _VERIFIED_STAGE_CAPABILITY)
        return result


def require_verified_physical_wal_manifest_object_storage_stage(
    value: object,
) -> PhysicalWalManifestObjectStorageStageResult:
    """Check the opaque local stage capability; this is not a consume proof."""

    if type(value) is not PhysicalWalManifestObjectStorageStageResult:
        _fail("TRANSPORT_VERIFIED_STAGE_REQUIRED")
    if value._capability is not _VERIFIED_STAGE_CAPABILITY:
        _fail("TRANSPORT_VERIFIED_STAGE_REQUIRED")
    try:
        normalized = require_verified_physical_wal_object_storage_bundle(value.verified_bundle)
    except PhysicalWalObjectManifestError as exc:
        raise PhysicalWalManifestObjectStorageTransportError(
            "TRANSPORT_VERIFIED_STAGE_REQUIRED"
        ) from exc
    if normalized != value.verified_bundle or value.status != PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS:
        _fail("TRANSPORT_VERIFIED_STAGE_NOT_NORMALIZED")
    return value
