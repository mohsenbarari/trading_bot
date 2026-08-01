"""Fail-closed encrypted Object-Storage publication for frozen blob artifacts.

This adapter is deliberately narrower than a blob synchronizer.  It consumes
only the immutable local handoff and inventory artifacts emitted by
``physical_blob_artifact_spool``; encrypts them with an injected age adapter;
and proves a create-only, version-bound Object-Storage readback through an
injected client.  It never opens a database, lists a bucket to authorize a
write, restores a blob, contacts another WebApp directly, or makes a writer
or promotion decision.

The storage key used here is a v2 publication key.  The spool's local handoff
key is independently verified as part of its v1 descriptor, but is not reused
as the Object-Storage coordinate because it omits the PostgreSQL timeline and
the exact Writer-Witness term.  The v2 key binds route, baseline, timeline,
term, source-record (blob) identifier, and plaintext hash.  A future manifest
builder must consume the typed signed receipts from this module rather than
treating the local spool key as a published-object receipt.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_blob_artifact_spool import (
    DEFAULT_MAX_PHYSICAL_BLOB_BYTES,
    MAX_BLOBS_PER_INVENTORY_SHARD,
    MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    MAX_PHYSICAL_BLOB_BYTES,
    PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA,
    PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA,
    PhysicalBlobArtifactHandoffResult,
    PhysicalBlobArtifactManifestBinding,
    PhysicalBlobArtifactSpoolError,
    PhysicalBlobInventoryShardPlaintext,
    VerifiedPhysicalBlobArtifactBinding,
    derive_physical_blob_artifact_object_key,
    require_verified_physical_blob_artifact_binding,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
)


__all__ = (
    "PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION",
    "PHYSICAL_BLOB_OBJECT_STORAGE_IMMUTABILITY",
    "PHYSICAL_BLOB_OBJECT_STORAGE_RECEIPT_SCHEMA",
    "PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA",
    "PhysicalBlobAgeEncryptor",
    "PhysicalBlobInventoryShardObjectStorageReceipt",
    "PhysicalBlobObjectStorageClient",
    "PhysicalBlobObjectStorageReceipt",
    "PhysicalBlobObjectStorageUploader",
    "PhysicalBlobObjectStorageUploaderConfig",
    "PhysicalBlobObjectStorageUploaderError",
    "PhysicalBlobReceiptSigner",
    "VerifiedPhysicalBlobObjectStorageBinding",
    "authorize_physical_blob_object_storage_binding",
    "build_physical_wal_blob_inventory_shard_from_receipt",
    "derive_physical_blob_inventory_object_storage_key",
    "derive_physical_blob_object_storage_key",
    "require_verified_physical_blob_object_storage_binding",
    "verify_physical_blob_object_storage_receipt",
)


PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA = (
    "gold-trade-physical-blob-object-storage-uploader-v2"
)
PHYSICAL_BLOB_OBJECT_STORAGE_RECEIPT_SCHEMA = (
    "gold-trade-physical-blob-object-storage-receipt-v1"
)
PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED = False
# The receipts use the exact immutable-object vocabulary consumed by the
# existing signed blob-frontier builder.  Keeping aliases here makes that
# compatibility deliberate rather than an undocumented string coincidence.
PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION = PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION
PHYSICAL_BLOB_OBJECT_STORAGE_IMMUTABILITY = PHYSICAL_WAL_OBJECT_IMMUTABILITY

_MAX_DESCRIPTOR_BYTES = 128 * 1024
_MAX_RECEIPT_BYTES = 128 * 1024
_MAX_CIPHERTEXT_OVERHEAD_BYTES = 32 * 1024 * 1024
_MAX_INVENTORY_CIPHERTEXT_OVERHEAD_BYTES = 2 * 1024 * 1024
_READ_CHUNK_BYTES = 256 * 1024
_MAX_VERSION_HISTORY_PAGES = 32
_MAX_RESPONSE_SCAN_DEPTH = 32
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_VERIFIED_BINDING_CAPABILITY = object()


class PhysicalBlobObjectStorageUploaderError(ValueError):
    """A frozen blob publication input or immutable storage proof is unsafe."""


class PhysicalBlobAgeEncryptor(Protocol):
    """Injected age-v1 encryptor; it must use precisely the supplied recipient."""

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        """Write exactly one new age-v1 ciphertext or raise."""


class PhysicalBlobObjectStorageClient(Protocol):
    """The minimal injected S3-compatible interface; no default client exists."""

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def get_bucket_acl(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def list_object_versions(self, **request: Any) -> Mapping[str, Any]: ...

    def put_object(self, **request: Any) -> Mapping[str, Any]: ...

    def head_object(self, **request: Any) -> Mapping[str, Any]: ...

    def get_object(self, **request: Any) -> Mapping[str, Any]: ...


class PhysicalBlobReceiptSigner(Protocol):
    """An injected Ed25519 signer whose output is verified immediately."""

    def sign(self, data: bytes) -> bytes:
        """Return a 64-byte Ed25519 signature for exactly ``data``."""


@dataclass(frozen=True)
class PhysicalBlobObjectStorageUploaderConfig:
    """Explicit, non-secret, one-route configuration for this adapter.

    All mutable implementation objects are supplied through factories.  The
    public receipt-verification key is a deployment pin, not a credential.
    """

    source_site: str = ""
    destination_site: str = ""
    workspace: Path | None = None
    spool_root: Path | None = None
    bucket: str = ""
    region: str = ""
    destination_age_recipient: str = ""
    receipt_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED
    maximum_blob_plaintext_bytes: int = DEFAULT_MAX_PHYSICAL_BLOB_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class VerifiedPhysicalBlobObjectStorageBinding:
    """Opaque live-term binding with a separately pinned PostgreSQL timeline."""

    artifact_binding: VerifiedPhysicalBlobArtifactBinding
    timeline_id: int
    route_binding_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalBlobObjectStorageReceipt:
    """Typed, source-signed, exact-readback receipt for one frozen blob."""

    signed_receipt: bytes
    receipt_sha256: str
    source_record_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    handoff_descriptor_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    timeline_id: int
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalBlobInventoryShardObjectStorageReceipt:
    """Typed, source-signed, exact-readback receipt for one inventory shard."""

    signed_receipt: bytes
    receipt_sha256: str
    shard_ordinal: int
    entry_count: int
    plaintext_sha256: str
    plaintext_bytes: int
    blob_receipts_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    timeline_id: int
    route_binding_sha256: str


@dataclass(frozen=True)
class _ManifestFacts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    destination_age_recipient: str


@dataclass(frozen=True)
class _BindingFacts:
    manifest: _ManifestFacts
    timeline_id: int
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    route_binding_sha256: str


@dataclass(frozen=True)
class _ConfigFacts:
    source_site: str
    destination_site: str
    workspace: Path
    spool_root: Path
    bucket: str
    region: str
    destination_age_recipient: str
    receipt_signer_public_key: bytes
    maximum_blob_plaintext_bytes: int


@dataclass(frozen=True)
class _BlobArtifactFacts:
    source_record_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    handoff_descriptor_sha256: str
    spool_object_key: str
    storage_object_key: str
    snapshot_path: Path


@dataclass(frozen=True)
class _InventoryEntryFacts:
    ordinal: int
    source_record_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    handoff_descriptor_sha256: str
    spool_object_key: str
    storage_object_key: str


@dataclass(frozen=True)
class _InventoryFacts:
    shard_ordinal: int
    plaintext_sha256: str
    plaintext_bytes: int
    entry_count: int
    storage_object_key: str
    plaintext_path: Path
    entries: tuple[_InventoryEntryFacts, ...]


@dataclass(frozen=True)
class _ReceiptFacts:
    kind: str
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    destination_age_recipient: str
    timeline_id: int
    receipt_signer_public_key_sha256: str
    plaintext_sha256: str
    plaintext_bytes: int
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    source_record_id: str | None
    handoff_descriptor_sha256: str | None
    shard_ordinal: int | None
    entry_count: int | None
    blob_receipts_sha256: str | None
    raw: bytes


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob Object-Storage JSON contains duplicate fields"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PhysicalBlobObjectStorageUploaderError(
        f"physical blob Object-Storage JSON constant is forbidden: {value}"
    )


def _canonical_json_bytes(value: Mapping[str, Any], *, label: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} is not canonical JSON"
        ) from exc


def _exact_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} fields are invalid")
    return dict(value)


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _safe_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} contains a URL or secret-shaped value"
        )
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    return value


def _timeline_id(value: object, *, label: str) -> int:
    return _positive_int(value, label=label, maximum=0xFFFFFFFF)


def _lsn(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    return value


def _manifest_facts(value: object) -> _ManifestFacts:
    if type(value) is not PhysicalBlobArtifactManifestBinding:
        raise PhysicalBlobObjectStorageUploaderError("physical blob manifest binding is invalid")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        raise PhysicalBlobObjectStorageUploaderError("physical blob manifest route is invalid")
    return _ManifestFacts(
        source_site=value.source_site,
        destination_site=value.destination_site,
        campaign_id=_safe_text(value.campaign_id, label="physical blob campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_safe_text(value.release_sha, label="physical blob release", pattern=RELEASE_SHA_RE),
        baseline_generation_id=_safe_text(
            value.baseline_generation_id,
            label="physical blob baseline generation",
            pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII),
        ),
        baseline_manifest_sha256=_sha256(
            value.baseline_manifest_sha256,
            label="physical blob baseline manifest SHA-256",
        ),
        baseline_wal_lsn=_lsn(value.baseline_wal_lsn, label="physical blob baseline WAL LSN"),
        destination_age_recipient=_safe_text(
            value.destination_age_recipient,
            label="physical blob destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        ),
    )


def authorize_physical_blob_object_storage_binding(
    *,
    artifact_binding: VerifiedPhysicalBlobArtifactBinding,
    timeline_id: int,
    now: datetime,
) -> VerifiedPhysicalBlobObjectStorageBinding:
    """Add one exact timeline pin to a live Blob-spool route capability.

    The caller is expected to obtain ``timeline_id`` from a separately
    verified base/WAL lineage.  This function deliberately does not assert
    that fact itself; it only prevents the value from being omitted or changed
    between storage publication calls.
    """

    observed_now = _utc(now, label="physical blob Object-Storage authorization clock")
    try:
        verified = require_verified_physical_blob_artifact_binding(
            artifact_binding, now=observed_now
        )
    except PhysicalBlobArtifactSpoolError as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob artifact binding is not live and authorized"
        ) from exc
    facts = _manifest_facts(verified.manifest_binding)
    timeline = _timeline_id(timeline_id, label="physical blob PostgreSQL timeline")
    result = VerifiedPhysicalBlobObjectStorageBinding(
        artifact_binding=verified,
        timeline_id=timeline,
        route_binding_sha256=_sha256(
            verified.route_binding_sha256,
            label="physical blob route binding SHA-256",
        ),
    )
    # Reuse the parsed facts so static analysis cannot mistake the binding for
    # a loose container whose route fields were never examined.
    del facts
    object.__setattr__(result, "_capability", _VERIFIED_BINDING_CAPABILITY)
    return result


def _binding_facts(
    value: object,
    *,
    now: datetime,
) -> _BindingFacts:
    if (
        type(value) is not VerifiedPhysicalBlobObjectStorageBinding
        or value._capability is not _VERIFIED_BINDING_CAPABILITY
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object-Storage binding is not authorized"
        )
    observed_now = _utc(now, label="physical blob Object-Storage binding clock")
    try:
        artifact_binding = require_verified_physical_blob_artifact_binding(
            value.artifact_binding, now=observed_now
        )
    except PhysicalBlobArtifactSpoolError as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob artifact binding is not live and authorized"
        ) from exc
    manifest = _manifest_facts(artifact_binding.manifest_binding)
    route_binding_sha256 = _sha256(
        artifact_binding.route_binding_sha256,
        label="physical blob route binding SHA-256",
    )
    if route_binding_sha256 != value.route_binding_sha256:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object-Storage binding was tampered"
        )
    term = artifact_binding.witnessed_term
    epoch = _positive_int(
        term.writer_epoch,
        label="physical blob Witness writer epoch",
        maximum=2**63 - 1,
    )
    lease = _safe_text(
        term.writer_lease_id,
        label="physical blob Witness writer lease",
        pattern=LEASE_ID_RE,
    )
    proof = _sha256(
        term.proof_sha256,
        label="physical blob Witness term proof SHA-256",
    )
    if term.holder_site != manifest.source_site:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object-Storage binding holder is not its source site"
        )
    return _BindingFacts(
        manifest=manifest,
        timeline_id=_timeline_id(value.timeline_id, label="physical blob PostgreSQL timeline"),
        writer_epoch=epoch,
        writer_lease_id=lease,
        witnessed_term_proof_sha256=proof,
        route_binding_sha256=route_binding_sha256,
    )


def require_verified_physical_blob_object_storage_binding(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalBlobObjectStorageBinding:
    """Revalidate the opaque live route capability before each publication."""

    _binding_facts(value, now=now)
    return value


def _secure_directory(value: object, *, label: str, owner_uid: int = 0) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    if _URL_VALUE_RE.search(str(value)) or _SENSITIVE_VALUE_RE.search(str(value)):
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} contains a URL or secret-shaped value"
        )
    try:
        absolute = value.absolute()
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is unavailable") from exc
    if (
        absolute != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} ownership or mode is unsafe")
    return resolved


def _normalise_config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalBlobObjectStorageUploaderConfig:
        raise PhysicalBlobObjectStorageUploaderError("physical blob uploader config is invalid")
    if value.enabled is not True:
        raise PhysicalBlobObjectStorageUploaderError("physical blob Object-Storage uploader is disabled")
    if os.geteuid() != 0:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object-Storage uploader requires the root archive user"
        )
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        raise PhysicalBlobObjectStorageUploaderError("physical blob Object-Storage route is invalid")
    workspace = _secure_directory(value.workspace, label="physical blob uploader workspace")
    spool_root = _secure_directory(value.spool_root, label="physical blob uploader spool root")
    if workspace == spool_root:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob uploader workspace and spool root overlap"
        )
    for left, right in ((workspace, spool_root), (spool_root, workspace)):
        try:
            left.relative_to(right)
        except ValueError:
            continue
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob uploader workspace and spool root overlap"
        )
    if value.direct_site_control != "forbidden":
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob direct site control must remain forbidden"
        )
    if value.destination_object_ingest != "pull-only":
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob destination Object-Storage ingest must remain pull-only"
        )
    public_key = value.receipt_signer_public_key
    if not isinstance(public_key, bytes) or len(public_key) != 32 or public_key == b"\x00" * 32:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob receipt signer public key is invalid"
        )
    try:
        Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob receipt signer public key is invalid"
        ) from exc
    return _ConfigFacts(
        source_site=value.source_site,
        destination_site=value.destination_site,
        workspace=workspace,
        spool_root=spool_root,
        bucket=_safe_text(value.bucket, label="physical blob Object-Storage bucket", pattern=_BUCKET_RE),
        region=_safe_text(value.region, label="physical blob Object-Storage region", pattern=_REGION_RE),
        destination_age_recipient=_safe_text(
            value.destination_age_recipient,
            label="physical blob destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        ),
        receipt_signer_public_key=public_key,
        maximum_blob_plaintext_bytes=_positive_int(
            value.maximum_blob_plaintext_bytes,
            label="physical blob maximum plaintext bytes",
            maximum=MAX_PHYSICAL_BLOB_BYTES,
        ),
    )


def _require_config_binding_match(config: _ConfigFacts, binding: _BindingFacts) -> None:
    manifest = binding.manifest
    if (
        config.source_site != manifest.source_site
        or config.destination_site != manifest.destination_site
        or config.destination_age_recipient != manifest.destination_age_recipient
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob uploader route or recipient does not match its verified binding"
        )


def _term_component(binding: _BindingFacts) -> str:
    return "-".join(
        (
            f"term-{binding.writer_epoch:020d}",
            hashlib.sha256(binding.writer_lease_id.encode("utf-8")).hexdigest(),
            binding.witnessed_term_proof_sha256,
        )
    )


def _derive_blob_object_key(
    *,
    binding: _BindingFacts,
    source_record_id: str,
    plaintext_sha256: str,
) -> str:
    blob_id = _safe_text(
        source_record_id,
        label="physical blob source record ID",
        pattern=_SYSTEM_ID_RE,
    )
    plaintext = _sha256(plaintext_sha256, label="physical blob plaintext SHA-256")
    manifest = binding.manifest
    key = "/".join(
        (
            "physical-blobs-v2",
            manifest.campaign_id,
            manifest.release_sha,
            manifest.baseline_generation_id,
            f"{manifest.source_site}-to-{manifest.destination_site}",
            f"timeline-{binding.timeline_id:08X}",
            f"route-{binding.route_binding_sha256}",
            _term_component(binding),
            "blobs",
            f"blob-{hashlib.sha256(blob_id.encode('utf-8')).hexdigest()}",
            f"{plaintext}.age",
        )
    )
    if OBJECT_KEY_RE.fullmatch(key) is None or any(
        part in {"", ".", ".."} for part in key.split("/")
    ):
        raise PhysicalBlobObjectStorageUploaderError("derived physical blob Object key is invalid")
    return key


def derive_physical_blob_object_storage_key(
    *,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    source_record_id: str,
    plaintext_sha256: str,
    now: datetime,
) -> str:
    """Derive the only accepted v2 storage key for one frozen blob."""

    return _derive_blob_object_key(
        binding=_binding_facts(verified_binding, now=now),
        source_record_id=source_record_id,
        plaintext_sha256=plaintext_sha256,
    )


def _derive_inventory_object_key(
    *,
    binding: _BindingFacts,
    shard_ordinal: int,
    plaintext_sha256: str,
) -> str:
    ordinal = _positive_int(
        shard_ordinal,
        label="physical blob inventory shard ordinal",
        maximum=2**63 - 1,
    )
    plaintext = _sha256(plaintext_sha256, label="physical blob inventory plaintext SHA-256")
    manifest = binding.manifest
    key = "/".join(
        (
            "physical-blobs-v2",
            manifest.campaign_id,
            manifest.release_sha,
            manifest.baseline_generation_id,
            f"{manifest.source_site}-to-{manifest.destination_site}",
            f"timeline-{binding.timeline_id:08X}",
            f"route-{binding.route_binding_sha256}",
            _term_component(binding),
            "inventory",
            f"shard-{ordinal:020d}-{plaintext}.age",
        )
    )
    if OBJECT_KEY_RE.fullmatch(key) is None or any(
        part in {"", ".", ".."} for part in key.split("/")
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "derived physical blob inventory Object key is invalid"
        )
    return key


def derive_physical_blob_inventory_object_storage_key(
    *,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    shard_ordinal: int,
    plaintext_sha256: str,
    now: datetime,
) -> str:
    """Derive the only accepted v2 storage key for one inventory shard."""

    return _derive_inventory_object_key(
        binding=_binding_facts(verified_binding, now=now),
        shard_ordinal=shard_ordinal,
        plaintext_sha256=plaintext_sha256,
    )


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _secure_relative_file(
    *,
    path: object,
    spool_root: Path,
    expected_relative_parts: tuple[str, ...],
    label: str,
    maximum_bytes: int,
) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or not expected_relative_parts:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} path is invalid")
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(spool_root)
    except (OSError, ValueError) as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} path is unsafe") from exc
    if absolute != resolved or tuple(relative.parts) != expected_relative_parts:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} path is unsafe")
    current = spool_root
    directories = (spool_root, *[spool_root.joinpath(*expected_relative_parts[:index]) for index in range(1, len(expected_relative_parts))])
    for directory in directories:
        try:
            metadata = os.lstat(directory)
            directory_resolved = directory.resolve(strict=True)
        except OSError as exc:
            raise PhysicalBlobObjectStorageUploaderError(f"{label} parent is unavailable") from exc
        if (
            directory_resolved != directory.absolute()
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PhysicalBlobObjectStorageUploaderError(f"{label} parent is unsafe")
        current = directory
    try:
        metadata = os.lstat(resolved)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size < 1
        or metadata.st_size > maximum_bytes
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is unsafe")
    return resolved


def _hash_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[str, int]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobObjectStorageUploaderError(
            "platform lacks fail-closed non-symlink file open"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} cannot be opened safely") from exc
    try:
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != 0
            or initial.st_nlink != 1
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_size < 1
            or initial.st_size > maximum_bytes
        ):
            raise PhysicalBlobObjectStorageUploaderError(f"{label} is unsafe")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            except OSError as exc:
                raise PhysicalBlobObjectStorageUploaderError(f"{label} cannot be read safely") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise PhysicalBlobObjectStorageUploaderError(f"{label} exceeds its bounded size")
            digest.update(chunk)
        final = os.fstat(descriptor)
        if _fingerprint(initial) != _fingerprint(final):
            raise PhysicalBlobObjectStorageUploaderError(f"{label} changed during secure read")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobObjectStorageUploaderError(
            "platform lacks fail-closed non-symlink file open"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} cannot be opened safely") from exc
    try:
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != 0
            or initial.st_nlink != 1
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_size < 1
            or initial.st_size > maximum_bytes
        ):
            raise PhysicalBlobObjectStorageUploaderError(f"{label} is unsafe")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise PhysicalBlobObjectStorageUploaderError(f"{label} exceeds its bounded size")
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if _fingerprint(initial) != _fingerprint(final) or total != initial.st_size:
            raise PhysicalBlobObjectStorageUploaderError(f"{label} changed during secure read")
        return b"".join(chunks)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} cannot be read safely") from exc
    finally:
        os.close(descriptor)


_BLOB_HANDOFF_FIELDS = {
    "schema",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "route_binding_sha256",
    "writer_term",
    "destination_age_recipient",
    "uploads_root_identity_sha256",
    "source_record",
    "declared_content",
    "snapshot",
    "object_key",
    "not_a_database_snapshot_consistency_proof",
    "not_a_blob_frontier_manifest",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
}


def _parse_canonical_json(raw: object, *, label: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum_bytes:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} byte size is invalid")
    try:
        parsed = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalBlobObjectStorageUploaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid JSON") from exc
    if not isinstance(parsed, dict) or _canonical_json_bytes(parsed, label=label) != raw:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is not canonical")
    return parsed


def _require_descriptor_binding(
    item: Mapping[str, Any],
    *,
    binding: _BindingFacts,
    label: str,
) -> None:
    manifest = binding.manifest
    if (
        item["source_site"] != manifest.source_site
        or item["destination_site"] != manifest.destination_site
        or item["campaign_id"] != manifest.campaign_id
        or item["release_sha"] != manifest.release_sha
        or item["baseline_generation_id"] != manifest.baseline_generation_id
        or item["baseline_manifest_sha256"] != manifest.baseline_manifest_sha256
        or item["baseline_wal_lsn"] != manifest.baseline_wal_lsn
        or item["route_binding_sha256"] != binding.route_binding_sha256
        or item["destination_age_recipient"] != manifest.destination_age_recipient
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} does not match its pinned route, baseline, or recipient"
        )
    term = _exact_mapping(
        item["writer_term"],
        label=f"{label} writer term",
        fields={"holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"},
    )
    if (
        term["holder_site"] != manifest.source_site
        or _positive_int(term["writer_epoch"], label=f"{label} writer epoch", maximum=2**63 - 1)
        != binding.writer_epoch
        or _safe_text(term["writer_lease_id"], label=f"{label} writer lease", pattern=LEASE_ID_RE)
        != binding.writer_lease_id
        or _sha256(term["witnessed_term_proof_sha256"], label=f"{label} Witness proof SHA-256")
        != binding.witnessed_term_proof_sha256
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} does not match the active Writer-Witness term"
        )


def _parse_blob_handoff(
    *,
    raw: bytes,
    descriptor_sha256: str,
    artifact: PhysicalBlobArtifactHandoffResult,
    binding: _BindingFacts,
    config: _ConfigFacts,
) -> _BlobArtifactFacts:
    if _sha256(descriptor_sha256, label="physical blob handoff descriptor SHA-256") != hashlib.sha256(raw).hexdigest():
        raise PhysicalBlobObjectStorageUploaderError("physical blob handoff descriptor hash is invalid")
    item = _exact_mapping(
        _parse_canonical_json(
            raw,
            label="physical blob handoff descriptor",
            maximum_bytes=_MAX_DESCRIPTOR_BYTES,
        ),
        label="physical blob handoff descriptor",
        fields=_BLOB_HANDOFF_FIELDS,
    )
    if (
        item["schema"] != PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA
        or item["kind"] != "finalized_database_visible_blob_local_handoff"
    ):
        raise PhysicalBlobObjectStorageUploaderError("physical blob handoff descriptor schema is invalid")
    _require_descriptor_binding(item, binding=binding, label="physical blob handoff descriptor")
    _sha256(item["uploads_root_identity_sha256"], label="physical blob uploads-root identity SHA-256")
    source_record = _exact_mapping(
        item["source_record"],
        label="physical blob source record",
        fields={"record_id", "database_visibility", "finalization_state", "temporary", "inflight"},
    )
    if (
        source_record["database_visibility"] != "frozen_database_visible_finalized_v1"
        or source_record["finalization_state"] != "finalized"
        or source_record["temporary"] is not False
        or source_record["inflight"] is not False
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob handoff descriptor is not an immutable finalized artifact"
        )
    source_record_id = _safe_text(
        source_record["record_id"],
        label="physical blob source record ID",
        pattern=_SYSTEM_ID_RE,
    )
    declared = _exact_mapping(
        item["declared_content"],
        label="physical blob declared content",
        fields={"sha256", "bytes"},
    )
    snapshot = _exact_mapping(
        item["snapshot"],
        label="physical blob snapshot",
        fields={"sha256", "bytes"},
    )
    plaintext_sha256 = _sha256(declared["sha256"], label="physical blob declared content SHA-256")
    plaintext_bytes = _positive_int(
        declared["bytes"],
        label="physical blob declared content bytes",
        maximum=config.maximum_blob_plaintext_bytes,
    )
    if (
        _sha256(snapshot["sha256"], label="physical blob snapshot SHA-256") != plaintext_sha256
        or _positive_int(
            snapshot["bytes"],
            label="physical blob snapshot bytes",
            maximum=config.maximum_blob_plaintext_bytes,
        )
        != plaintext_bytes
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob handoff snapshot does not match declared content"
        )
    if not all(
        item[name] is True
        for name in (
            "not_a_database_snapshot_consistency_proof",
            "not_a_blob_frontier_manifest",
            "not_a_remote_apply_proof",
            "not_a_strict_acknowledgement_proof",
        )
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob handoff descriptor must retain its non-authority disclaimers"
        )
    spool_object_key = _safe_text(item["object_key"], label="physical blob spool Object key", pattern=OBJECT_KEY_RE)
    if any(part in {"", ".", ".."} for part in spool_object_key.split("/")):
        raise PhysicalBlobObjectStorageUploaderError("physical blob spool Object key is invalid")
    expected_spool_key = derive_physical_blob_artifact_object_key(
        manifest_binding=_artifact_manifest_binding_from_facts(binding),
        source_record_id=source_record_id,
        declared_content_sha256=plaintext_sha256,
    )
    if spool_object_key != expected_spool_key:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob spool Object key is not deterministic"
        )
    if (
        artifact.source_record_id != source_record_id
        or artifact.snapshot_sha256 != plaintext_sha256
        or artifact.snapshot_bytes != plaintext_bytes
        or artifact.handoff_descriptor_sha256 != descriptor_sha256
        or artifact.object_key != spool_object_key
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob handoff result does not match its canonical descriptor"
        )
    snapshot_path = _secure_relative_file(
        path=artifact.snapshot_path,
        spool_root=config.spool_root,
        expected_relative_parts=("snapshots", plaintext_sha256[:2], f"{plaintext_sha256}.blob"),
        label="physical blob immutable snapshot",
        maximum_bytes=config.maximum_blob_plaintext_bytes,
    )
    actual_sha256, actual_bytes = _hash_regular_file(
        snapshot_path,
        label="physical blob immutable snapshot",
        maximum_bytes=config.maximum_blob_plaintext_bytes,
    )
    if actual_sha256 != plaintext_sha256 or actual_bytes != plaintext_bytes:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob immutable snapshot does not match its handoff descriptor"
        )
    return _BlobArtifactFacts(
        source_record_id=source_record_id,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        handoff_descriptor_sha256=descriptor_sha256,
        spool_object_key=spool_object_key,
        storage_object_key=_derive_blob_object_key(
            binding=binding,
            source_record_id=source_record_id,
            plaintext_sha256=plaintext_sha256,
        ),
        snapshot_path=snapshot_path,
    )


def _validate_artifact_wrapper(
    artifact: PhysicalBlobArtifactHandoffResult,
    *,
    config: _ConfigFacts,
) -> str:
    """Reject loose dataclass values before they influence a protected path."""

    descriptor_sha256 = _sha256(
        artifact.handoff_descriptor_sha256,
        label="physical blob handoff-result descriptor SHA-256",
    )
    _safe_text(
        artifact.source_record_id,
        label="physical blob handoff-result source record ID",
        pattern=_SYSTEM_ID_RE,
    )
    _sha256(
        artifact.snapshot_sha256,
        label="physical blob handoff-result snapshot SHA-256",
    )
    _positive_int(
        artifact.snapshot_bytes,
        label="physical blob handoff-result snapshot bytes",
        maximum=config.maximum_blob_plaintext_bytes,
    )
    object_key = _safe_text(
        artifact.object_key,
        label="physical blob handoff-result spool Object key",
        pattern=OBJECT_KEY_RE,
    )
    if any(part in {"", ".", ".."} for part in object_key.split("/")):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob handoff-result spool Object key is invalid"
        )
    return descriptor_sha256


def _artifact_manifest_binding_from_facts(binding: _BindingFacts) -> PhysicalBlobArtifactManifestBinding:
    """Reconstruct only validated non-secret fields for spool-key verification."""

    manifest = binding.manifest
    return PhysicalBlobArtifactManifestBinding(
        source_site=manifest.source_site,
        destination_site=manifest.destination_site,
        campaign_id=manifest.campaign_id,
        release_sha=manifest.release_sha,
        baseline_generation_id=manifest.baseline_generation_id,
        baseline_manifest_sha256=manifest.baseline_manifest_sha256,
        baseline_wal_lsn=manifest.baseline_wal_lsn,
        destination_age_recipient=manifest.destination_age_recipient,
    )


_INVENTORY_FIELDS = {
    "schema",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "route_binding_sha256",
    "writer_term",
    "destination_age_recipient",
    "uploads_root_identity_sha256",
    "shard_ordinal",
    "entries",
    "not_a_database_snapshot_consistency_proof",
    "not_a_blob_frontier_manifest",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
}


def _parse_inventory(
    *,
    inventory: PhysicalBlobInventoryShardPlaintext,
    binding: _BindingFacts,
    config: _ConfigFacts,
) -> _InventoryFacts:
    if type(inventory) is not PhysicalBlobInventoryShardPlaintext:
        raise PhysicalBlobObjectStorageUploaderError("physical blob inventory shard is invalid")
    shard_ordinal = _positive_int(
        inventory.shard_ordinal,
        label="physical blob inventory shard ordinal",
        maximum=2**63 - 1,
    )
    plaintext_sha256 = _sha256(
        inventory.plaintext_sha256,
        label="physical blob inventory plaintext SHA-256",
    )
    plaintext_bytes = _positive_int(
        inventory.plaintext_bytes,
        label="physical blob inventory plaintext bytes",
        maximum=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    entry_count = _positive_int(
        inventory.entry_count,
        label="physical blob inventory entry count",
        maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
    )
    plaintext_path = _secure_relative_file(
        path=inventory.plaintext_path,
        spool_root=config.spool_root,
        expected_relative_parts=(
            "inventory",
            f"shard-{shard_ordinal:08d}-{plaintext_sha256}.json",
        ),
        label="physical blob immutable inventory plaintext",
        maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    raw = _read_regular_file(
        plaintext_path,
        label="physical blob immutable inventory plaintext",
        maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    if len(raw) != plaintext_bytes or hashlib.sha256(raw).hexdigest() != plaintext_sha256:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob immutable inventory does not match its declared hash"
        )
    item = _exact_mapping(
        _parse_canonical_json(
            raw,
            label="physical blob inventory plaintext",
            maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
        ),
        label="physical blob inventory plaintext",
        fields=_INVENTORY_FIELDS,
    )
    if (
        item["schema"] != PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA
        or item["kind"] != "finalized_database_visible_blob_inventory_shard_plaintext"
    ):
        raise PhysicalBlobObjectStorageUploaderError("physical blob inventory plaintext schema is invalid")
    _require_descriptor_binding(item, binding=binding, label="physical blob inventory plaintext")
    _sha256(item["uploads_root_identity_sha256"], label="physical blob inventory uploads-root identity")
    if _positive_int(
        item["shard_ordinal"],
        label="physical blob inventory plaintext ordinal",
        maximum=2**63 - 1,
    ) != shard_ordinal:
        raise PhysicalBlobObjectStorageUploaderError("physical blob inventory shard ordinal is invalid")
    if not all(
        item[name] is True
        for name in (
            "not_a_database_snapshot_consistency_proof",
            "not_a_blob_frontier_manifest",
            "not_a_remote_apply_proof",
            "not_a_strict_acknowledgement_proof",
        )
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob inventory must retain its non-authority disclaimers"
        )
    raw_entries = item["entries"]
    if isinstance(raw_entries, (str, bytes)) or not isinstance(raw_entries, Sequence):
        raise PhysicalBlobObjectStorageUploaderError("physical blob inventory entries are invalid")
    if len(raw_entries) != entry_count or not raw_entries or len(raw_entries) > MAX_BLOBS_PER_INVENTORY_SHARD:
        raise PhysicalBlobObjectStorageUploaderError("physical blob inventory entry count is invalid")
    entries: list[_InventoryEntryFacts] = []
    seen_ids: set[str] = set()
    for expected_ordinal, raw_entry in enumerate(raw_entries, start=1):
        entry = _exact_mapping(
            raw_entry,
            label=f"physical blob inventory entry {expected_ordinal}",
            fields={
                "ordinal",
                "source_record_id",
                "content_sha256",
                "content_bytes",
                "handoff_descriptor_sha256",
                "object_key",
            },
        )
        if _positive_int(
            entry["ordinal"],
            label=f"physical blob inventory entry {expected_ordinal} ordinal",
            maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
        ) != expected_ordinal:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory entries are not ordered and contiguous"
            )
        source_record_id = _safe_text(
            entry["source_record_id"],
            label=f"physical blob inventory entry {expected_ordinal} record ID",
            pattern=_SYSTEM_ID_RE,
        )
        if source_record_id in seen_ids:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory replays a source record"
            )
        seen_ids.add(source_record_id)
        content_sha256 = _sha256(
            entry["content_sha256"],
            label=f"physical blob inventory entry {expected_ordinal} content SHA-256",
        )
        content_bytes = _positive_int(
            entry["content_bytes"],
            label=f"physical blob inventory entry {expected_ordinal} content bytes",
            maximum=config.maximum_blob_plaintext_bytes,
        )
        handoff_sha256 = _sha256(
            entry["handoff_descriptor_sha256"],
            label=f"physical blob inventory entry {expected_ordinal} handoff SHA-256",
        )
        spool_object_key = _safe_text(
            entry["object_key"],
            label=f"physical blob inventory entry {expected_ordinal} spool Object key",
            pattern=OBJECT_KEY_RE,
        )
        expected_spool_key = derive_physical_blob_artifact_object_key(
            manifest_binding=_artifact_manifest_binding_from_facts(binding),
            source_record_id=source_record_id,
            declared_content_sha256=content_sha256,
        )
        if spool_object_key != expected_spool_key:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory spool Object key is not deterministic"
            )
        entries.append(
            _InventoryEntryFacts(
                ordinal=expected_ordinal,
                source_record_id=source_record_id,
                plaintext_sha256=content_sha256,
                plaintext_bytes=content_bytes,
                handoff_descriptor_sha256=handoff_sha256,
                spool_object_key=spool_object_key,
                storage_object_key=_derive_blob_object_key(
                    binding=binding,
                    source_record_id=source_record_id,
                    plaintext_sha256=content_sha256,
                ),
            )
        )
    return _InventoryFacts(
        shard_ordinal=shard_ordinal,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=plaintext_bytes,
        entry_count=entry_count,
        storage_object_key=_derive_inventory_object_key(
            binding=binding,
            shard_ordinal=shard_ordinal,
            plaintext_sha256=plaintext_sha256,
        ),
        plaintext_path=plaintext_path,
        entries=tuple(entries),
    )


def _private_versioned_bucket(client: object, *, bucket: str) -> None:
    for method_name in ("get_bucket_versioning", "get_bucket_acl"):
        if not callable(getattr(client, method_name, None)):
            raise PhysicalBlobObjectStorageUploaderError(
                "Object Storage client lacks required private bucket preflight"
            )
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "cannot verify Object Storage bucket versioning"
        ) from exc
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        raise PhysicalBlobObjectStorageUploaderError("Object Storage bucket versioning is not enabled")
    try:
        acl = client.get_bucket_acl(Bucket=bucket)
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "cannot verify private Object Storage bucket ACL"
        ) from exc
    if (
        not isinstance(acl, Mapping)
        or not isinstance(acl.get("Owner"), Mapping)
        or not isinstance(acl["Owner"].get("ID"), str)
        or not acl["Owner"]["ID"]
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "Object Storage bucket ACL is missing its canonical owner"
        )
    owner_id = acl["Owner"]["ID"]
    grants = acl.get("Grants")
    if not isinstance(grants, list) or not grants:
        raise PhysicalBlobObjectStorageUploaderError("Object Storage bucket ACL is malformed")
    for grant in grants:
        if not isinstance(grant, Mapping) or not isinstance(grant.get("Grantee"), Mapping):
            raise PhysicalBlobObjectStorageUploaderError("Object Storage bucket ACL is malformed")
        grantee = grant["Grantee"]
        if (
            grantee.get("Type") != "CanonicalUser"
            or grantee.get("ID") != owner_id
            or grant.get("Permission") != "FULL_CONTROL"
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "Object Storage bucket ACL grants access outside its sole canonical owner"
            )


def _response_has_provider_side_encryption(
    value: object,
    *,
    seen: set[int] | None = None,
    depth: int = 0,
) -> bool:
    """Fail closed for provider-side encryption fields, cycles, or deep maps."""

    if depth > _MAX_RESPONSE_SCAN_DEPTH:
        return True
    if isinstance(value, Mapping):
        identities = seen if seen is not None else set()
        identity = id(value)
        if identity in identities:
            return True
        identities.add(identity)
        try:
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
                if _response_has_provider_side_encryption(item, seen=identities, depth=depth + 1):
                    return True
        except Exception:
            return True
        finally:
            identities.discard(identity)
        return False
    if isinstance(value, (list, tuple)):
        return any(
            _response_has_provider_side_encryption(item, seen=seen, depth=depth + 1)
            for item in value
        )
    return False


def _exact_object_history(
    client: object,
    *,
    bucket: str,
    key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not callable(getattr(client, "list_object_versions", None)):
        raise PhysicalBlobObjectStorageUploaderError(
            "Object Storage client lacks immutable version-history verification"
        )
    versions: list[dict[str, Any]] = []
    delete_markers: list[dict[str, Any]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    for _ in range(_MAX_VERSION_HISTORY_PAGES):
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": key}
        if key_marker is not None:
            request["KeyMarker"] = key_marker
        if version_marker is not None:
            request["VersionIdMarker"] = version_marker
        try:
            response = client.list_object_versions(**request)
        except Exception as exc:
            raise PhysicalBlobObjectStorageUploaderError(
                "cannot verify immutable Object version history"
            ) from exc
        if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
            raise PhysicalBlobObjectStorageUploaderError("Object version history is invalid")
        for name, destination in (("Versions", versions), ("DeleteMarkers", delete_markers)):
            items = response.get(name, [])
            if not isinstance(items, list):
                raise PhysicalBlobObjectStorageUploaderError("Object version history is invalid")
            for item in items:
                if not isinstance(item, Mapping) or item.get("Key") != key:
                    raise PhysicalBlobObjectStorageUploaderError("Object version history is ambiguous")
                destination.append(dict(item))
        if response.get("IsTruncated") is not True:
            return versions, delete_markers
        next_key = response.get("NextKeyMarker")
        next_version = response.get("NextVersionIdMarker")
        if (
            not isinstance(next_key, str)
            or not next_key
            or not isinstance(next_version, str)
            or not next_version
        ):
            raise PhysicalBlobObjectStorageUploaderError("Object version history pagination is invalid")
        key_marker = next_key
        version_marker = next_version
    raise PhysicalBlobObjectStorageUploaderError(
        "Object version history pagination exceeds its bound"
    )


def _require_exact_version(
    client: object,
    *,
    bucket: str,
    key: str,
    expected_version_id: str,
) -> None:
    """Check history only after conditional PUT; it is never absence authority."""

    versions, delete_markers = _exact_object_history(client, bucket=bucket, key=key)
    if delete_markers or len(versions) != 1:
        raise PhysicalBlobObjectStorageUploaderError(
            "immutable Object history is not a single live version"
        )
    version = versions[0]
    if version.get("VersionId") != expected_version_id or version.get("IsLatest") is not True:
        raise PhysicalBlobObjectStorageUploaderError(
            "immutable Object version does not match create-only PUT"
        )


def _validate_ciphertext(
    path: Path,
    *,
    maximum_plaintext_bytes: int,
    maximum_overhead_bytes: int,
) -> tuple[str, int]:
    maximum = maximum_plaintext_bytes + maximum_overhead_bytes
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError("age ciphertext is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size < 1
        or metadata.st_size > maximum
    ):
        raise PhysicalBlobObjectStorageUploaderError("age ciphertext is unsafe")
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobObjectStorageUploaderError(
            "platform lacks fail-closed non-symlink file open"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError("age ciphertext cannot be opened safely") from exc
    try:
        header = os.read(descriptor, len(b"age-encryption.org/v1\n"))
    except OSError as exc:
        raise PhysicalBlobObjectStorageUploaderError("age ciphertext cannot be read safely") from exc
    finally:
        os.close(descriptor)
    if header != b"age-encryption.org/v1\n":
        raise PhysicalBlobObjectStorageUploaderError("age ciphertext is not age-v1")
    return _hash_regular_file(path, label="age ciphertext", maximum_bytes=maximum)


def _head_exact_ciphertext(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_bytes: int,
    expected_metadata: Mapping[str, str],
) -> None:
    if not callable(getattr(client, "head_object", None)):
        raise PhysicalBlobObjectStorageUploaderError(
            "Object Storage client lacks exact Object head read-back"
        )
    try:
        response = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "cannot head exact immutable Object version"
        ) from exc
    if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
        raise PhysicalBlobObjectStorageUploaderError("Object head read-back response is invalid")
    if (
        response.get("VersionId") != version_id
        or response.get("ContentLength") != expected_bytes
        or not isinstance(response.get("Metadata"), Mapping)
        or dict(response["Metadata"]) != dict(expected_metadata)
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "Object head read-back does not match upload"
        )


def _readback_ciphertext(
    client: object,
    *,
    bucket: str,
    key: str,
    version_id: str,
    expected_sha256: str,
    expected_bytes: int,
    expected_metadata: Mapping[str, str],
) -> None:
    try:
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "cannot read back exact immutable Object version"
        ) from exc
    if not isinstance(response, Mapping):
        raise PhysicalBlobObjectStorageUploaderError("Object read-back response is invalid")
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        raise PhysicalBlobObjectStorageUploaderError("Object read-back body is invalid")
    close = getattr(body, "close", None)
    try:
        if _response_has_provider_side_encryption(response):
            raise PhysicalBlobObjectStorageUploaderError("Object read-back response is invalid")
        if response.get("VersionId") != version_id:
            raise PhysicalBlobObjectStorageUploaderError(
                "Object read-back returned a different VersionId"
            )
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping) or dict(metadata) != dict(expected_metadata):
            raise PhysicalBlobObjectStorageUploaderError("Object read-back metadata is invalid")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = body.read(_READ_CHUNK_BYTES)
            except Exception as exc:
                raise PhysicalBlobObjectStorageUploaderError("Object read-back body failed") from exc
            if not isinstance(chunk, bytes):
                raise PhysicalBlobObjectStorageUploaderError("Object read-back body is invalid")
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes:
                raise PhysicalBlobObjectStorageUploaderError(
                    "Object read-back byte count is invalid"
                )
            digest.update(chunk)
    except BaseException:
        if callable(close):
            try:
                close()
            except Exception:
                pass
        raise
    if callable(close):
        try:
            close()
        except Exception as exc:
            raise PhysicalBlobObjectStorageUploaderError(
                "Object read-back body cannot be closed"
            ) from exc
    if total != expected_bytes or digest.hexdigest() != expected_sha256:
        raise PhysicalBlobObjectStorageUploaderError(
            "Object read-back ciphertext does not match upload"
        )


def _metadata(
    *,
    artifact_kind: str,
    descriptor_or_inventory_sha256: str,
    binding: _BindingFacts,
    plaintext_sha256: str,
    plaintext_bytes: int,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    transport_schema: str = PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA,
) -> dict[str, str]:
    return {
        "transport-schema": transport_schema,
        "artifact-kind": artifact_kind,
        "route-binding-sha256": binding.route_binding_sha256,
        "timeline-id": str(binding.timeline_id),
        "writer-epoch": str(binding.writer_epoch),
        "witnessed-term-proof-sha256": binding.witnessed_term_proof_sha256,
        "destination-age-recipient": binding.manifest.destination_age_recipient,
        "descriptor-or-inventory-sha256": descriptor_or_inventory_sha256,
        "plaintext-sha256": plaintext_sha256,
        "plaintext-bytes": str(plaintext_bytes),
        "encryption": PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION,
        "ciphertext-sha256": ciphertext_sha256,
        "ciphertext-bytes": str(ciphertext_bytes),
    }


def _publish_encrypted_create_only(
    *,
    config: _ConfigFacts,
    binding: _BindingFacts,
    plaintext_path: Path,
    plaintext_sha256: str,
    plaintext_bytes: int,
    maximum_overhead_bytes: int,
    object_key: str,
    artifact_kind: str,
    descriptor_or_inventory_sha256: str,
    age_encryptor_factory: Callable[[], PhysicalBlobAgeEncryptor] | None,
    client_factory: Callable[[], PhysicalBlobObjectStorageClient] | None,
    transport_schema: str = PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_SCHEMA,
) -> tuple[str, str, int]:
    actual_sha256, actual_bytes = _hash_regular_file(
        plaintext_path,
        label="physical blob immutable plaintext",
        maximum_bytes=plaintext_bytes,
    )
    if actual_sha256 != plaintext_sha256 or actual_bytes != plaintext_bytes:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob immutable plaintext changed before encryption"
        )
    if age_encryptor_factory is None or not callable(age_encryptor_factory):
        raise PhysicalBlobObjectStorageUploaderError("physical blob age encryptor factory is required")
    if client_factory is None or not callable(client_factory):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object Storage client factory is required"
        )
    try:
        encryptor = age_encryptor_factory()
        client = client_factory()
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError("physical blob uploader factory failed") from exc
    if not callable(getattr(encryptor, "encrypt", None)):
        raise PhysicalBlobObjectStorageUploaderError("physical blob age encryptor is invalid")
    _private_versioned_bucket(client, bucket=config.bucket)
    # The conditional PUT is the no-overwrite authority.  The history check
    # below is deliberately post-PUT evidence, never a pre-write listing gate.
    with tempfile.TemporaryDirectory(
        prefix="physical-blob-upload-", dir=str(config.workspace)
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        try:
            os.chmod(workspace, 0o700)
            workspace_metadata = os.lstat(workspace)
        except OSError as exc:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob upload workspace is unsafe"
            ) from exc
        if (
            stat.S_ISLNK(workspace_metadata.st_mode)
            or not stat.S_ISDIR(workspace_metadata.st_mode)
            or workspace_metadata.st_uid != 0
            or stat.S_IMODE(workspace_metadata.st_mode) != 0o700
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob upload workspace is unsafe"
            )
        ciphertext_path = workspace / "encrypted-finalized-blob.age"
        try:
            encryptor.encrypt(
                recipient=binding.manifest.destination_age_recipient,
                plaintext_path=plaintext_path,
                ciphertext_path=ciphertext_path,
            )
        except Exception as exc:
            raise PhysicalBlobObjectStorageUploaderError("physical blob age encryption failed") from exc
        ciphertext_sha256, ciphertext_bytes = _validate_ciphertext(
            ciphertext_path,
            maximum_plaintext_bytes=plaintext_bytes,
            maximum_overhead_bytes=maximum_overhead_bytes,
        )
        after_sha256, after_bytes = _hash_regular_file(
            plaintext_path,
            label="physical blob immutable plaintext",
            maximum_bytes=plaintext_bytes,
        )
        if after_sha256 != plaintext_sha256 or after_bytes != plaintext_bytes:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob immutable plaintext changed during encryption"
            )
        metadata = _metadata(
            artifact_kind=artifact_kind,
            descriptor_or_inventory_sha256=descriptor_or_inventory_sha256,
            binding=binding,
            plaintext_sha256=plaintext_sha256,
            plaintext_bytes=plaintext_bytes,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
            transport_schema=transport_schema,
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
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob conditional create-only Object PUT failed"
            ) from exc
        if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
            raise PhysicalBlobObjectStorageUploaderError("Object PUT response is invalid")
        version_id = response.get("VersionId")
        if (
            not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or VERSION_ID_RE.fullmatch(version_id) is None
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "Object PUT did not return a valid VersionId"
            )
        _require_exact_version(
            client,
            bucket=config.bucket,
            key=object_key,
            expected_version_id=version_id,
        )
        _head_exact_ciphertext(
            client,
            bucket=config.bucket,
            key=object_key,
            version_id=version_id,
            expected_bytes=ciphertext_bytes,
            expected_metadata=metadata,
        )
        _readback_ciphertext(
            client,
            bucket=config.bucket,
            key=object_key,
            version_id=version_id,
            expected_sha256=ciphertext_sha256,
            expected_bytes=ciphertext_bytes,
            expected_metadata=metadata,
        )
    return version_id, ciphertext_sha256, ciphertext_bytes


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_b64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not value or _BASE64URL_RE.fullmatch(value) is None:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes or _b64(decoded) != value:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} is invalid")
    return decoded


_RECEIPT_COMMON_FIELDS = {
    "schema",
    "version",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "route_binding_sha256",
    "writer_term",
    "timeline_id",
    "destination_age_recipient",
    "receipt_signer_public_key_sha256",
    "plaintext",
    "object",
    "readback_verified",
    "not_a_database_snapshot_consistency_proof",
    "not_a_blob_frontier_manifest",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
    "source_receipt_signature",
}


def _receipt_unsigned(
    *,
    kind: str,
    binding: _BindingFacts,
    receipt_signer_public_key: bytes,
    plaintext_sha256: str,
    plaintext_bytes: int,
    object_key: str,
    version_id: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = binding.manifest
    return {
        "schema": PHYSICAL_BLOB_OBJECT_STORAGE_RECEIPT_SCHEMA,
        "version": 1,
        "kind": kind,
        "source_site": manifest.source_site,
        "destination_site": manifest.destination_site,
        "campaign_id": manifest.campaign_id,
        "release_sha": manifest.release_sha,
        "baseline_generation_id": manifest.baseline_generation_id,
        "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
        "baseline_wal_lsn": manifest.baseline_wal_lsn,
        "route_binding_sha256": binding.route_binding_sha256,
        "writer_term": {
            "holder_site": manifest.source_site,
            "writer_epoch": binding.writer_epoch,
            "writer_lease_id": binding.writer_lease_id,
            "witnessed_term_proof_sha256": binding.witnessed_term_proof_sha256,
        },
        "timeline_id": binding.timeline_id,
        "destination_age_recipient": manifest.destination_age_recipient,
        "receipt_signer_public_key_sha256": hashlib.sha256(
            receipt_signer_public_key
        ).hexdigest(),
        "plaintext": {"sha256": plaintext_sha256, "bytes": plaintext_bytes},
        "object": {
            "kind": "blob" if kind == "finalized_blob_object" else "blob_inventory_shard",
            "object_key": object_key,
            "version_id": version_id,
            "ciphertext_sha256": ciphertext_sha256,
            "ciphertext_bytes": ciphertext_bytes,
            "encryption": PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION,
            "immutability": PHYSICAL_BLOB_OBJECT_STORAGE_IMMUTABILITY,
        },
        **dict(artifact),
        "readback_verified": True,
        "not_a_database_snapshot_consistency_proof": True,
        "not_a_blob_frontier_manifest": True,
        "not_a_remote_apply_proof": True,
        "not_a_strict_acknowledgement_proof": True,
    }


def _sign_receipt(
    *,
    unsigned: Mapping[str, Any],
    receipt_signer_factory: Callable[[], PhysicalBlobReceiptSigner] | None,
    receipt_signer_public_key: bytes,
) -> bytes:
    if receipt_signer_factory is None or not callable(receipt_signer_factory):
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signer factory is required")
    try:
        signer = receipt_signer_factory()
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signer factory failed") from exc
    if not callable(getattr(signer, "sign", None)):
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signer is invalid")
    signing_bytes = _canonical_json_bytes(unsigned, label="physical blob Object-Storage receipt")
    try:
        signature = signer.sign(signing_bytes)
    except Exception as exc:
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signing failed") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signature is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(receipt_signer_public_key).verify(signature, signing_bytes)
    except (ValueError, InvalidSignature) as exc:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob receipt signature does not match the pinned public key"
        ) from exc
    signed = dict(unsigned)
    signed["source_receipt_signature"] = _b64(signature)
    return _canonical_json_bytes(signed, label="physical blob signed Object-Storage receipt")


def _receipt_common_facts(
    item: Mapping[str, Any],
    *,
    expected_public_key: bytes,
    label: str,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    str,
    str,
    int,
    str,
    str,
    str,
    int,
]:
    if (
        item["schema"] != PHYSICAL_BLOB_OBJECT_STORAGE_RECEIPT_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} schema is invalid")
    source_site = item["source_site"]
    destination_site = item["destination_site"]
    if (
        not isinstance(source_site, str)
        or not isinstance(destination_site, str)
        or source_site not in WEBAPP_SITES
        or destination_site not in WEBAPP_SITES
        or source_site == destination_site
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} route is invalid")
    campaign = _safe_text(item["campaign_id"], label=f"{label} campaign", pattern=CAMPAIGN_ID_RE)
    release = _safe_text(item["release_sha"], label=f"{label} release", pattern=RELEASE_SHA_RE)
    generation = _safe_text(
        item["baseline_generation_id"],
        label=f"{label} baseline generation",
        pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII),
    )
    baseline_hash = _sha256(item["baseline_manifest_sha256"], label=f"{label} baseline hash")
    baseline_lsn = _lsn(item["baseline_wal_lsn"], label=f"{label} baseline LSN")
    route_hash = _sha256(item["route_binding_sha256"], label=f"{label} route binding")
    writer_term = _exact_mapping(
        item["writer_term"],
        label=f"{label} writer term",
        fields={"holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"},
    )
    if writer_term["holder_site"] != source_site:
        raise PhysicalBlobObjectStorageUploaderError(f"{label} writer-term holder is invalid")
    epoch = _positive_int(
        writer_term["writer_epoch"], label=f"{label} writer epoch", maximum=2**63 - 1
    )
    lease = _safe_text(writer_term["writer_lease_id"], label=f"{label} writer lease", pattern=LEASE_ID_RE)
    proof = _sha256(
        writer_term["witnessed_term_proof_sha256"], label=f"{label} Witness proof"
    )
    timeline = _timeline_id(item["timeline_id"], label=f"{label} timeline")
    recipient = _safe_text(
        item["destination_age_recipient"], label=f"{label} recipient", pattern=AGE_RECIPIENT_RE
    )
    signer_key_hash = _sha256(
        item["receipt_signer_public_key_sha256"], label=f"{label} receipt signer key hash"
    )
    if signer_key_hash != hashlib.sha256(expected_public_key).hexdigest():
        raise PhysicalBlobObjectStorageUploaderError(
            f"{label} receipt signer key does not match the pinned key"
        )
    plaintext = _exact_mapping(
        item["plaintext"], label=f"{label} plaintext", fields={"sha256", "bytes"}
    )
    plaintext_hash = _sha256(plaintext["sha256"], label=f"{label} plaintext SHA-256")
    plaintext_bytes = _positive_int(
        plaintext["bytes"], label=f"{label} plaintext bytes", maximum=MAX_PHYSICAL_BLOB_BYTES
    )
    object_value = _exact_mapping(
        item["object"],
        label=f"{label} object",
        fields={
            "kind",
            "object_key",
            "version_id",
            "ciphertext_sha256",
            "ciphertext_bytes",
            "encryption",
            "immutability",
        },
    )
    object_key = _safe_text(object_value["object_key"], label=f"{label} Object key", pattern=OBJECT_KEY_RE)
    if any(part in {"", ".", ".."} for part in object_key.split("/")):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} Object key is invalid")
    version_id = object_value["version_id"]
    if (
        not isinstance(version_id, str)
        or not version_id
        or version_id == "null"
        or VERSION_ID_RE.fullmatch(version_id) is None
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} VersionId is invalid")
    ciphertext_hash = _sha256(
        object_value["ciphertext_sha256"], label=f"{label} ciphertext SHA-256"
    )
    ciphertext_bytes = _positive_int(
        object_value["ciphertext_bytes"],
        label=f"{label} ciphertext bytes",
        maximum=MAX_PHYSICAL_BLOB_BYTES + _MAX_CIPHERTEXT_OVERHEAD_BYTES,
    )
    if (
        object_value["encryption"] != PHYSICAL_BLOB_OBJECT_STORAGE_ENCRYPTION
        or object_value["immutability"] != PHYSICAL_BLOB_OBJECT_STORAGE_IMMUTABILITY
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} object policy is invalid")
    if not all(
        item[name] is True
        for name in (
            "readback_verified",
            "not_a_database_snapshot_consistency_proof",
            "not_a_blob_frontier_manifest",
            "not_a_remote_apply_proof",
            "not_a_strict_acknowledgement_proof",
        )
    ):
        raise PhysicalBlobObjectStorageUploaderError(f"{label} proof flags are invalid")
    return (
        source_site,
        destination_site,
        campaign,
        release,
        generation,
        baseline_hash,
        baseline_lsn,
        route_hash,
        recipient,
        timeline,
        lease,
        proof,
        epoch,
        signer_key_hash,
        plaintext_hash,
        object_key,
        version_id,
        ciphertext_bytes,
    )


def _parse_receipt(
    raw: object,
    *,
    receipt_signer_public_key: bytes,
) -> _ReceiptFacts:
    item = _parse_canonical_json(
        raw,
        label="physical blob signed Object-Storage receipt",
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    kind = item.get("kind")
    if kind == "finalized_blob_object":
        fields = _RECEIPT_COMMON_FIELDS | {"blob"}
    elif kind == "blob_inventory_shard_object":
        fields = _RECEIPT_COMMON_FIELDS | {"inventory_shard"}
    else:
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt kind is invalid")
    item = _exact_mapping(item, label="physical blob signed Object-Storage receipt", fields=fields)
    signature = _decode_b64(
        item["source_receipt_signature"],
        label="physical blob receipt signature",
        expected_bytes=64,
    )
    unsigned = dict(item)
    del unsigned["source_receipt_signature"]
    signing_bytes = _canonical_json_bytes(unsigned, label="physical blob unsigned Object-Storage receipt")
    try:
        Ed25519PublicKey.from_public_bytes(receipt_signer_public_key).verify(signature, signing_bytes)
    except (ValueError, InvalidSignature) as exc:
        raise PhysicalBlobObjectStorageUploaderError("physical blob receipt signature is invalid") from exc
    (
        source_site,
        destination_site,
        campaign,
        release,
        generation,
        baseline_hash,
        baseline_lsn,
        route_hash,
        recipient,
        timeline,
        lease,
        proof,
        epoch,
        signer_key_hash,
        plaintext_hash,
        object_key,
        version_id,
        ciphertext_bytes,
    ) = _receipt_common_facts(
        item,
        expected_public_key=receipt_signer_public_key,
        label="physical blob signed Object-Storage receipt",
    )
    object_value = item["object"]
    ciphertext_hash = _sha256(
        object_value["ciphertext_sha256"],
        label="physical blob receipt ciphertext SHA-256",
    )
    plaintext_bytes = _positive_int(
        item["plaintext"]["bytes"],
        label="physical blob receipt plaintext bytes",
        maximum=MAX_PHYSICAL_BLOB_BYTES,
    )
    source_record_id: str | None = None
    handoff_descriptor_sha256: str | None = None
    shard_ordinal: int | None = None
    entry_count: int | None = None
    blob_receipts_sha256: str | None = None
    if kind == "finalized_blob_object":
        if object_value["kind"] != "blob":
            raise PhysicalBlobObjectStorageUploaderError("physical blob receipt object kind is invalid")
        blob = _exact_mapping(
            item["blob"],
            label="physical blob receipt blob binding",
            fields={"source_record_id", "source_record_id_sha256", "handoff_descriptor_sha256"},
        )
        source_record_id = _safe_text(
            blob["source_record_id"],
            label="physical blob receipt source record ID",
            pattern=_SYSTEM_ID_RE,
        )
        if _sha256(
            blob["source_record_id_sha256"],
            label="physical blob receipt source record ID SHA-256",
        ) != hashlib.sha256(source_record_id.encode("utf-8")).hexdigest():
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob receipt source record ID hash is invalid"
            )
        handoff_descriptor_sha256 = _sha256(
            blob["handoff_descriptor_sha256"],
            label="physical blob receipt handoff descriptor SHA-256",
        )
    else:
        if object_value["kind"] != "blob_inventory_shard":
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt object kind is invalid"
            )
        if plaintext_bytes > MAX_INVENTORY_SHARD_PLAINTEXT_BYTES:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt plaintext exceeds its bound"
            )
        if ciphertext_bytes > (
            MAX_INVENTORY_SHARD_PLAINTEXT_BYTES + _MAX_INVENTORY_CIPHERTEXT_OVERHEAD_BYTES
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt ciphertext exceeds its bound"
            )
        inventory = _exact_mapping(
            item["inventory_shard"],
            label="physical blob inventory receipt binding",
            fields={"shard_ordinal", "entry_count", "blob_receipts_sha256"},
        )
        shard_ordinal = _positive_int(
            inventory["shard_ordinal"],
            label="physical blob inventory receipt shard ordinal",
            maximum=2**63 - 1,
        )
        entry_count = _positive_int(
            inventory["entry_count"],
            label="physical blob inventory receipt entry count",
            maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
        )
        blob_receipts_sha256 = _sha256(
            inventory["blob_receipts_sha256"],
            label="physical blob inventory receipt blob receipts SHA-256",
        )
    return _ReceiptFacts(
        kind=kind,
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign,
        release_sha=release,
        baseline_generation_id=generation,
        baseline_manifest_sha256=baseline_hash,
        baseline_wal_lsn=baseline_lsn,
        route_binding_sha256=route_hash,
        writer_epoch=epoch,
        writer_lease_id=lease,
        witnessed_term_proof_sha256=proof,
        destination_age_recipient=recipient,
        timeline_id=timeline,
        receipt_signer_public_key_sha256=signer_key_hash,
        plaintext_sha256=plaintext_hash,
        plaintext_bytes=plaintext_bytes,
        object_key=object_key,
        version_id=version_id,
        ciphertext_sha256=ciphertext_hash,
        ciphertext_bytes=ciphertext_bytes,
        source_record_id=source_record_id,
        handoff_descriptor_sha256=handoff_descriptor_sha256,
        shard_ordinal=shard_ordinal,
        entry_count=entry_count,
        blob_receipts_sha256=blob_receipts_sha256,
        raw=raw,
    )


def _typed_receipt(facts: _ReceiptFacts) -> PhysicalBlobObjectStorageReceipt | PhysicalBlobInventoryShardObjectStorageReceipt:
    receipt_sha256 = hashlib.sha256(facts.raw).hexdigest()
    if facts.kind == "finalized_blob_object":
        if facts.source_record_id is None or facts.handoff_descriptor_sha256 is None:
            raise PhysicalBlobObjectStorageUploaderError("physical blob receipt is internally incomplete")
        return PhysicalBlobObjectStorageReceipt(
            signed_receipt=facts.raw,
            receipt_sha256=receipt_sha256,
            source_record_id=facts.source_record_id,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            handoff_descriptor_sha256=facts.handoff_descriptor_sha256,
            object_key=facts.object_key,
            version_id=facts.version_id,
            ciphertext_sha256=facts.ciphertext_sha256,
            ciphertext_bytes=facts.ciphertext_bytes,
            timeline_id=facts.timeline_id,
            route_binding_sha256=facts.route_binding_sha256,
        )
    if (
        facts.shard_ordinal is None
        or facts.entry_count is None
        or facts.blob_receipts_sha256 is None
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob inventory receipt is internally incomplete"
        )
    return PhysicalBlobInventoryShardObjectStorageReceipt(
        signed_receipt=facts.raw,
        receipt_sha256=receipt_sha256,
        shard_ordinal=facts.shard_ordinal,
        entry_count=facts.entry_count,
        plaintext_sha256=facts.plaintext_sha256,
        plaintext_bytes=facts.plaintext_bytes,
        blob_receipts_sha256=facts.blob_receipts_sha256,
        object_key=facts.object_key,
        version_id=facts.version_id,
        ciphertext_sha256=facts.ciphertext_sha256,
        ciphertext_bytes=facts.ciphertext_bytes,
        timeline_id=facts.timeline_id,
        route_binding_sha256=facts.route_binding_sha256,
    )


def _validate_typed_receipt_wrapper(
    value: PhysicalBlobObjectStorageReceipt | PhysicalBlobInventoryShardObjectStorageReceipt,
) -> None:
    """Defend equality checks from Python's ``True == 1`` coercion."""

    if type(value.signed_receipt) is not bytes:
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt bytes are invalid")
    _sha256(value.receipt_sha256, label="physical blob storage receipt SHA-256")
    _sha256(value.plaintext_sha256, label="physical blob storage receipt plaintext SHA-256")
    _positive_int(
        value.plaintext_bytes,
        label="physical blob storage receipt plaintext bytes",
        maximum=MAX_PHYSICAL_BLOB_BYTES,
    )
    object_key = _safe_text(
        value.object_key,
        label="physical blob storage receipt Object key",
        pattern=OBJECT_KEY_RE,
    )
    if any(part in {"", ".", ".."} for part in object_key.split("/")):
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt Object key is invalid")
    version_id = value.version_id
    if (
        not isinstance(version_id, str)
        or not version_id
        or version_id == "null"
        or VERSION_ID_RE.fullmatch(version_id) is None
    ):
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt VersionId is invalid")
    _sha256(value.ciphertext_sha256, label="physical blob storage receipt ciphertext SHA-256")
    _positive_int(
        value.ciphertext_bytes,
        label="physical blob storage receipt ciphertext bytes",
        maximum=MAX_PHYSICAL_BLOB_BYTES + _MAX_CIPHERTEXT_OVERHEAD_BYTES,
    )
    _timeline_id(value.timeline_id, label="physical blob storage receipt timeline")
    _sha256(value.route_binding_sha256, label="physical blob storage receipt route binding")
    if type(value) is PhysicalBlobObjectStorageReceipt:
        _safe_text(
            value.source_record_id,
            label="physical blob storage receipt source record ID",
            pattern=_SYSTEM_ID_RE,
        )
        _sha256(
            value.handoff_descriptor_sha256,
            label="physical blob storage receipt handoff descriptor SHA-256",
        )
    elif type(value) is PhysicalBlobInventoryShardObjectStorageReceipt:
        if value.plaintext_bytes > MAX_INVENTORY_SHARD_PLAINTEXT_BYTES:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt plaintext exceeds its bound"
            )
        if value.ciphertext_bytes > (
            MAX_INVENTORY_SHARD_PLAINTEXT_BYTES + _MAX_INVENTORY_CIPHERTEXT_OVERHEAD_BYTES
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt ciphertext exceeds its bound"
            )
        _positive_int(
            value.shard_ordinal,
            label="physical blob storage receipt shard ordinal",
            maximum=2**63 - 1,
        )
        _positive_int(
            value.entry_count,
            label="physical blob storage receipt entry count",
            maximum=MAX_BLOBS_PER_INVENTORY_SHARD,
        )
        _sha256(
            value.blob_receipts_sha256,
            label="physical blob storage receipt blob-receipts SHA-256",
        )
    else:
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt type is invalid")


def verify_physical_blob_object_storage_receipt(
    *,
    receipt: bytes | PhysicalBlobObjectStorageReceipt | PhysicalBlobInventoryShardObjectStorageReceipt,
    receipt_signer_public_key: bytes,
) -> PhysicalBlobObjectStorageReceipt | PhysicalBlobInventoryShardObjectStorageReceipt:
    """Verify and normalize one source-signed immutable storage receipt."""

    if type(receipt) in {
        PhysicalBlobObjectStorageReceipt,
        PhysicalBlobInventoryShardObjectStorageReceipt,
    }:
        _validate_typed_receipt_wrapper(receipt)
        raw = receipt.signed_receipt
    elif isinstance(receipt, bytes):
        raw = receipt
    else:
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt is invalid")
    if (
        not isinstance(receipt_signer_public_key, bytes)
        or len(receipt_signer_public_key) != 32
        or receipt_signer_public_key == b"\x00" * 32
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob receipt signer public key is invalid"
        )
    facts = _parse_receipt(raw, receipt_signer_public_key=receipt_signer_public_key)
    typed = _typed_receipt(facts)
    if type(receipt) in {
        PhysicalBlobObjectStorageReceipt,
        PhysicalBlobInventoryShardObjectStorageReceipt,
    } and receipt != typed:
        raise PhysicalBlobObjectStorageUploaderError("physical blob storage receipt wrapper was tampered")
    return typed


def _require_receipt_binding(receipt: _ReceiptFacts, *, binding: _BindingFacts) -> None:
    manifest = binding.manifest
    if (
        receipt.source_site != manifest.source_site
        or receipt.destination_site != manifest.destination_site
        or receipt.campaign_id != manifest.campaign_id
        or receipt.release_sha != manifest.release_sha
        or receipt.baseline_generation_id != manifest.baseline_generation_id
        or receipt.baseline_manifest_sha256 != manifest.baseline_manifest_sha256
        or receipt.baseline_wal_lsn != manifest.baseline_wal_lsn
        or receipt.route_binding_sha256 != binding.route_binding_sha256
        or receipt.writer_epoch != binding.writer_epoch
        or receipt.writer_lease_id != binding.writer_lease_id
        or receipt.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or receipt.destination_age_recipient != manifest.destination_age_recipient
        or receipt.timeline_id != binding.timeline_id
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical blob Object-Storage receipt does not match its live route binding"
        )


def build_physical_wal_blob_inventory_shard_from_receipt(
    *,
    receipt: PhysicalBlobInventoryShardObjectStorageReceipt,
    blob_receipts: Sequence[PhysicalBlobObjectStorageReceipt],
    receipt_signer_public_key: bytes,
    verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
    now: datetime,
) -> dict[str, Any]:
    """Return the exact typed mapping consumed by the blob-frontier builder.

    This is intentionally a narrow bridge, not a frontier signer.  It verifies
    the Ed25519 receipt again, checks the still-live route/term/baseline/
    timeline binding, and rederives the inventory storage key before exposing
    the ``PhysicalWalBlobInventoryShard`` input shape.  It also requires the
    exact ordered Blob receipt set and checks its signed digest, so a source
    assembler cannot silently treat an inventory as complete without the
    corresponding immutable Blob objects.  Consequently a future caller can
    pass the returned mapping directly as one item of
    ``build_physical_wal_blob_frontier_manifest(inventory_shards=...)``.
    """

    if type(receipt) is not PhysicalBlobInventoryShardObjectStorageReceipt:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge requires a typed inventory receipt"
        )
    binding = _binding_facts(verified_binding, now=now)
    verified = verify_physical_blob_object_storage_receipt(
        receipt=receipt,
        receipt_signer_public_key=receipt_signer_public_key,
    )
    if type(verified) is not PhysicalBlobInventoryShardObjectStorageReceipt:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge receipt type is invalid"
        )
    facts = _parse_receipt(
        verified.signed_receipt,
        receipt_signer_public_key=receipt_signer_public_key,
    )
    _require_receipt_binding(facts, binding=binding)
    if (
        facts.kind != "blob_inventory_shard_object"
        or facts.shard_ordinal is None
        or facts.entry_count is None
        or facts.object_key
        != _derive_inventory_object_key(
            binding=binding,
            shard_ordinal=facts.shard_ordinal,
            plaintext_sha256=facts.plaintext_sha256,
        )
    ):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge receipt is not exactly pinned"
        )
    if isinstance(blob_receipts, (str, bytes)) or not isinstance(blob_receipts, Sequence):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge receipts are invalid"
        )
    if len(blob_receipts) != facts.entry_count:
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge receipts do not cover the inventory exactly"
        )
    verified_blob_receipts: list[_ReceiptFacts] = []
    seen_source_record_ids: set[str] = set()
    for blob_receipt in blob_receipts:
        if type(blob_receipt) is not PhysicalBlobObjectStorageReceipt:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical WAL blob frontier bridge requires typed blob receipts only"
            )
        normalized_blob_receipt = verify_physical_blob_object_storage_receipt(
            receipt=blob_receipt,
            receipt_signer_public_key=receipt_signer_public_key,
        )
        if type(normalized_blob_receipt) is not PhysicalBlobObjectStorageReceipt:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical WAL blob frontier bridge blob receipt type is invalid"
            )
        blob_facts = _parse_receipt(
            normalized_blob_receipt.signed_receipt,
            receipt_signer_public_key=receipt_signer_public_key,
        )
        _require_receipt_binding(blob_facts, binding=binding)
        if (
            blob_facts.kind != "finalized_blob_object"
            or blob_facts.source_record_id is None
            or blob_facts.handoff_descriptor_sha256 is None
            or blob_facts.source_record_id in seen_source_record_ids
            or blob_facts.object_key
            != _derive_blob_object_key(
                binding=binding,
                source_record_id=blob_facts.source_record_id,
                plaintext_sha256=blob_facts.plaintext_sha256,
            )
        ):
            raise PhysicalBlobObjectStorageUploaderError(
                "physical WAL blob frontier bridge blob receipt is not exactly pinned"
            )
        seen_source_record_ids.add(blob_facts.source_record_id)
        # Appending in caller order is deliberate: the signed digest preserves
        # inventory order rather than accepting an unordered Blob object set.
        verified_blob_receipts.append(blob_facts)
    if facts.blob_receipts_sha256 != _blob_receipts_digest(tuple(verified_blob_receipts)):
        raise PhysicalBlobObjectStorageUploaderError(
            "physical WAL blob frontier bridge receipt-set digest is invalid"
        )
    return {
        "ordinal": facts.shard_ordinal,
        "plaintext_sha256": facts.plaintext_sha256,
        "plaintext_bytes": facts.plaintext_bytes,
        "entry_count": facts.entry_count,
        "object": {
            "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
            "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
            "object_kind": "blob_inventory_shard",
            "object_key": facts.object_key,
            "version_id": facts.version_id,
            "ciphertext_sha256": facts.ciphertext_sha256,
            "ciphertext_bytes": facts.ciphertext_bytes,
            "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
            "age_recipient": binding.manifest.destination_age_recipient,
            "immutability": PHYSICAL_WAL_OBJECT_IMMUTABILITY,
        },
    }


def _blob_receipts_digest(
    receipts: Sequence[_ReceiptFacts],
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "kind": "physical_blob_inventory_receipt_set",
                "receipts": [
                    {
                        "source_record_id": item.source_record_id,
                        "handoff_descriptor_sha256": item.handoff_descriptor_sha256,
                        "plaintext_sha256": item.plaintext_sha256,
                        "plaintext_bytes": item.plaintext_bytes,
                        "object_key": item.object_key,
                        "version_id": item.version_id,
                        "ciphertext_sha256": item.ciphertext_sha256,
                        "ciphertext_bytes": item.ciphertext_bytes,
                        "receipt_sha256": hashlib.sha256(item.raw).hexdigest(),
                    }
                    for item in receipts
                ],
            },
            label="physical blob inventory receipt set",
        )
    ).hexdigest()


class PhysicalBlobObjectStorageUploader:
    """Default-disabled publisher for frozen blobs and their inventory shards.

    It does not have a generic ``upload`` dispatcher: a blob handoff and an
    inventory shard use distinct methods, distinct input types, distinct
    storage-key derivations, and distinct signed receipt types.
    """

    def __init__(
        self,
        *,
        config: PhysicalBlobObjectStorageUploaderConfig,
        age_encryptor_factory: Callable[[], PhysicalBlobAgeEncryptor] | None,
        client_factory: Callable[[], PhysicalBlobObjectStorageClient] | None,
        receipt_signer_factory: Callable[[], PhysicalBlobReceiptSigner] | None,
    ) -> None:
        self._config = config
        self._age_encryptor_factory = age_encryptor_factory
        self._client_factory = client_factory
        self._receipt_signer_factory = receipt_signer_factory

    def upload_blob(
        self,
        *,
        artifact: PhysicalBlobArtifactHandoffResult,
        verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
        term_recheck_clock: Callable[[], datetime] | None,
    ) -> PhysicalBlobObjectStorageReceipt:
        """Encrypt one exact spool handoff and mint a signed readback receipt."""

        if type(artifact) is not PhysicalBlobArtifactHandoffResult:
            raise PhysicalBlobObjectStorageUploaderError("physical blob handoff result is invalid")
        if term_recheck_clock is None or not callable(term_recheck_clock):
            raise PhysicalBlobObjectStorageUploaderError("physical blob term recheck clock is required")
        observed_now = _utc(now, label="physical blob upload clock")
        binding = _binding_facts(verified_binding, now=observed_now)
        config = _normalise_config(self._config)
        _require_config_binding_match(config, binding)
        descriptor_sha256 = _validate_artifact_wrapper(artifact, config=config)
        handoff_path = _secure_relative_file(
            path=artifact.handoff_descriptor_path,
            spool_root=config.spool_root,
            expected_relative_parts=("handoffs", f"{descriptor_sha256}.json"),
            label="physical blob canonical handoff descriptor",
            maximum_bytes=_MAX_DESCRIPTOR_BYTES,
        )
        raw = _read_regular_file(
            handoff_path,
            label="physical blob canonical handoff descriptor",
            maximum_bytes=_MAX_DESCRIPTOR_BYTES,
        )
        facts = _parse_blob_handoff(
            raw=raw,
            descriptor_sha256=descriptor_sha256,
            artifact=artifact,
            binding=binding,
            config=config,
        )
        version_id, ciphertext_sha256, ciphertext_bytes = _publish_encrypted_create_only(
            config=config,
            binding=binding,
            plaintext_path=facts.snapshot_path,
            plaintext_sha256=facts.plaintext_sha256,
            plaintext_bytes=facts.plaintext_bytes,
            maximum_overhead_bytes=_MAX_CIPHERTEXT_OVERHEAD_BYTES,
            object_key=facts.storage_object_key,
            artifact_kind="finalized_blob",
            descriptor_or_inventory_sha256=facts.handoff_descriptor_sha256,
            age_encryptor_factory=self._age_encryptor_factory,
            client_factory=self._client_factory,
        )
        completion_now = _utc(term_recheck_clock(), label="physical blob upload completion clock")
        if completion_now < observed_now:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob upload completion clock moved backwards"
            )
        final_binding = _binding_facts(verified_binding, now=completion_now)
        if final_binding != binding:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob live route binding changed during publication"
            )
        raw_receipt = _sign_receipt(
            unsigned=_receipt_unsigned(
                kind="finalized_blob_object",
                binding=binding,
                receipt_signer_public_key=config.receipt_signer_public_key,
                plaintext_sha256=facts.plaintext_sha256,
                plaintext_bytes=facts.plaintext_bytes,
                object_key=facts.storage_object_key,
                version_id=version_id,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
                artifact={
                    "blob": {
                        "source_record_id": facts.source_record_id,
                        "source_record_id_sha256": hashlib.sha256(
                            facts.source_record_id.encode("utf-8")
                        ).hexdigest(),
                        "handoff_descriptor_sha256": facts.handoff_descriptor_sha256,
                    }
                },
            ),
            receipt_signer_factory=self._receipt_signer_factory,
            receipt_signer_public_key=config.receipt_signer_public_key,
        )
        typed = verify_physical_blob_object_storage_receipt(
            receipt=raw_receipt,
            receipt_signer_public_key=config.receipt_signer_public_key,
        )
        if type(typed) is not PhysicalBlobObjectStorageReceipt:
            raise PhysicalBlobObjectStorageUploaderError("physical blob receipt type is invalid")
        return typed

    def upload_inventory_shard(
        self,
        *,
        inventory_shard: PhysicalBlobInventoryShardPlaintext,
        blob_receipts: Sequence[PhysicalBlobObjectStorageReceipt],
        verified_binding: VerifiedPhysicalBlobObjectStorageBinding,
        now: datetime,
        term_recheck_clock: Callable[[], datetime] | None,
    ) -> PhysicalBlobInventoryShardObjectStorageReceipt:
        """Publish one inventory only after every listed blob receipt verifies."""

        if term_recheck_clock is None or not callable(term_recheck_clock):
            raise PhysicalBlobObjectStorageUploaderError("physical blob term recheck clock is required")
        observed_now = _utc(now, label="physical blob inventory upload clock")
        binding = _binding_facts(verified_binding, now=observed_now)
        config = _normalise_config(self._config)
        _require_config_binding_match(config, binding)
        inventory = _parse_inventory(inventory=inventory_shard, binding=binding, config=config)
        if isinstance(blob_receipts, (str, bytes)) or not isinstance(blob_receipts, Sequence):
            raise PhysicalBlobObjectStorageUploaderError("physical blob upload receipts are invalid")
        if len(blob_receipts) != inventory.entry_count:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob upload receipts do not cover the inventory exactly"
            )
        verified_receipts: list[_ReceiptFacts] = []
        for entry, receipt in zip(inventory.entries, blob_receipts, strict=True):
            if type(receipt) is not PhysicalBlobObjectStorageReceipt:
                raise PhysicalBlobObjectStorageUploaderError(
                    "physical blob inventory requires typed blob receipts only"
                )
            verified = verify_physical_blob_object_storage_receipt(
                receipt=receipt,
                receipt_signer_public_key=config.receipt_signer_public_key,
            )
            if type(verified) is not PhysicalBlobObjectStorageReceipt:
                raise PhysicalBlobObjectStorageUploaderError("physical blob receipt type is invalid")
            receipt_facts = _parse_receipt(
                verified.signed_receipt,
                receipt_signer_public_key=config.receipt_signer_public_key,
            )
            _require_receipt_binding(receipt_facts, binding=binding)
            if (
                receipt_facts.kind != "finalized_blob_object"
                or receipt_facts.source_record_id != entry.source_record_id
                or receipt_facts.handoff_descriptor_sha256 != entry.handoff_descriptor_sha256
                or receipt_facts.plaintext_sha256 != entry.plaintext_sha256
                or receipt_facts.plaintext_bytes != entry.plaintext_bytes
                or receipt_facts.object_key != entry.storage_object_key
            ):
                raise PhysicalBlobObjectStorageUploaderError(
                    "physical blob receipt does not exactly cover its inventory entry"
                )
            verified_receipts.append(receipt_facts)
        receipts_sha256 = _blob_receipts_digest(tuple(verified_receipts))
        version_id, ciphertext_sha256, ciphertext_bytes = _publish_encrypted_create_only(
            config=config,
            binding=binding,
            plaintext_path=inventory.plaintext_path,
            plaintext_sha256=inventory.plaintext_sha256,
            plaintext_bytes=inventory.plaintext_bytes,
            maximum_overhead_bytes=_MAX_INVENTORY_CIPHERTEXT_OVERHEAD_BYTES,
            object_key=inventory.storage_object_key,
            artifact_kind="blob_inventory_shard",
            descriptor_or_inventory_sha256=inventory.plaintext_sha256,
            age_encryptor_factory=self._age_encryptor_factory,
            client_factory=self._client_factory,
        )
        completion_now = _utc(
            term_recheck_clock(), label="physical blob inventory completion clock"
        )
        if completion_now < observed_now:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory completion clock moved backwards"
            )
        final_binding = _binding_facts(verified_binding, now=completion_now)
        if final_binding != binding:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob live route binding changed during inventory publication"
            )
        raw_receipt = _sign_receipt(
            unsigned=_receipt_unsigned(
                kind="blob_inventory_shard_object",
                binding=binding,
                receipt_signer_public_key=config.receipt_signer_public_key,
                plaintext_sha256=inventory.plaintext_sha256,
                plaintext_bytes=inventory.plaintext_bytes,
                object_key=inventory.storage_object_key,
                version_id=version_id,
                ciphertext_sha256=ciphertext_sha256,
                ciphertext_bytes=ciphertext_bytes,
                artifact={
                    "inventory_shard": {
                        "shard_ordinal": inventory.shard_ordinal,
                        "entry_count": inventory.entry_count,
                        "blob_receipts_sha256": receipts_sha256,
                    }
                },
            ),
            receipt_signer_factory=self._receipt_signer_factory,
            receipt_signer_public_key=config.receipt_signer_public_key,
        )
        typed = verify_physical_blob_object_storage_receipt(
            receipt=raw_receipt,
            receipt_signer_public_key=config.receipt_signer_public_key,
        )
        if type(typed) is not PhysicalBlobInventoryShardObjectStorageReceipt:
            raise PhysicalBlobObjectStorageUploaderError(
                "physical blob inventory receipt type is invalid"
            )
        return typed
