"""Default-disabled encrypted, create-only Object-Storage uploader for recovery spools.

This module contains two narrow concrete implementations: one for
``PhysicalWalArchiveUploader`` and one for ``PhysicalWalBaseBackupUploader``.
Each consumes only its own canonical handoff descriptor and a regular
immutable local snapshot beneath the configured spool root.  It uses injected
age and Object-Storage client factories; importing this module or running its
tests does not create a network client, load credentials, or contact a bucket.

The adapter uploads encrypted recovery material only.  A successful result is
*not* a PostgreSQL synchronous acknowledgement, remote replay proof, writer
permit, or promotion authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_archive_spool import (
    PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA,
    PhysicalWalArchiveUploadReceipt,
)
from core.physical_wal_base_backup_spool import (
    MAX_BASE_BACKUP_ENCRYPTION_OVERHEAD_BYTES,
    MAX_PHYSICAL_BASE_BACKUP_BYTES,
    PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
    PhysicalWalBaseBackupUploadReceipt,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
    PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
)


__all__ = (
    "PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_SCHEMA",
    "PhysicalWalAgeEncryptor",
    "PhysicalWalBaseBackupObjectStorageUploader",
    "PhysicalWalObjectStorageClient",
    "PhysicalWalObjectStorageUploader",
    "PhysicalWalObjectStorageUploaderConfig",
    "PhysicalWalObjectStorageUploaderError",
)


PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_SCHEMA = (
    "gold-trade-physical-wal-object-storage-uploader-v1"
)
PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED = False

_MAX_DESCRIPTOR_BYTES = 32 * 1024
_WAL_MAX_CIPHERTEXT_OVERHEAD_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 256 * 1024
_MAX_VERSION_HISTORY_PAGES = 32
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_WITNESS_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)


class PhysicalWalObjectStorageUploaderError(ValueError):
    """The local encryption or immutable Object Storage handoff is unsafe."""


@dataclass(frozen=True)
class PhysicalWalObjectStorageUploaderConfig:
    """Non-secret, root-only configuration for one pinned failover direction.

    Both ``webapp_fi → webapp_ir`` and the reverse emergency direction are
    possible, but only as two distinct explicitly pinned configurations.  The
    adapter never creates a direct site-control path: the destination only
    pulls encrypted recovery material from private Object Storage.
    """

    source_site: str = ""
    destination_site: str = ""
    workspace: Path | None = None
    spool_root: Path | None = None
    spool_owner_uid: int | None = None
    bucket: str = ""
    region: str = ""
    destination_age_recipient: str = ""
    object_storage_namespace: str = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
    enabled: bool = PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED
    maximum_plaintext_bytes: int = 16 * 1024 * 1024
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


class PhysicalWalAgeEncryptor(Protocol):
    """Injected age-v1 encryptor; it must not choose its own recipient."""

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        """Create exactly one new age-v1 ciphertext file or raise."""


class PhysicalWalObjectStorageClient(Protocol):
    """Injected minimal S3-compatible client; no default factory exists."""

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def get_bucket_acl(self, *, Bucket: str) -> Mapping[str, Any]: ...

    def list_object_versions(self, **request: Any) -> Mapping[str, Any]: ...

    def put_object(self, **request: Any) -> Mapping[str, Any]: ...

    def head_object(self, **request: Any) -> Mapping[str, Any]: ...

    def get_object(self, **request: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class _ConfigFacts:
    source_site: str
    destination_site: str
    workspace: Path
    spool_root: Path
    spool_owner_uid: int
    bucket: str
    region: str
    destination_age_recipient: str
    object_storage_namespace: str
    maximum_plaintext_bytes: int


@dataclass(frozen=True)
class _DescriptorFacts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    archive_manifest_sha256: str
    route_binding_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    destination_age_recipient: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    wal_segment_name: str
    segment_ordinal: int
    start_lsn: str
    end_lsn: str
    snapshot_sha256: str
    snapshot_bytes: int
    object_key: str


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalWalObjectStorageUploaderError(
                "physical WAL handoff descriptor contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _canonical_json_bytes(value: Mapping[str, Any], *, label: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is not canonical JSON") from exc


def _exact_mapping(value: object, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PhysicalWalObjectStorageUploaderError(f"{label} fields are invalid")
    return dict(value)


def _safe_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalWalObjectStorageUploaderError(
            f"{label} contains a URL or secret-shaped value"
        )
    return value


def _object_storage_namespace(value: object) -> str:
    if type(value) is not str or value not in PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL Object Storage namespace is invalid"
        )
    return value


def _require_route_namespace(
    *,
    source_site: str,
    destination_site: str,
    object_storage_namespace: str,
) -> str:
    expected = (
        PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        if (source_site, destination_site) == ("webapp_fi", "webapp_ir")
        else PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    )
    if object_storage_namespace != expected:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL Object Storage namespace does not match the pinned route"
        )
    return object_storage_namespace


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is invalid")
    return value


def _lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _secure_directory(
    value: object,
    *,
    label: str,
    owner_uid: int,
    exact_mode: int,
) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise PhysicalWalObjectStorageUploaderError(f"{label} is invalid")
    if _SENSITIVE_VALUE_RE.search(str(value)) or _URL_VALUE_RE.search(str(value)):
        raise PhysicalWalObjectStorageUploaderError(f"{label} contains a URL or secret-shaped value")
    try:
        absolute = value.absolute()
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError as exc:
        raise PhysicalWalObjectStorageUploaderError(f"{label} is unavailable") from exc
    if (
        absolute != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != exact_mode
    ):
        raise PhysicalWalObjectStorageUploaderError(f"{label} ownership or mode is unsafe")
    return resolved


def _normalise_config(
    value: object,
    *,
    maximum_plaintext_ceiling: int,
) -> _ConfigFacts:
    if type(value) is not PhysicalWalObjectStorageUploaderConfig:
        raise PhysicalWalObjectStorageUploaderError("physical WAL Object Storage uploader config is invalid")
    if value.enabled is not True:
        raise PhysicalWalObjectStorageUploaderError("physical WAL Object Storage uploader is disabled")
    if os.geteuid() != 0:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL Object Storage uploader requires the root archive user"
        )
    if type(value.spool_owner_uid) is not int or value.spool_owner_uid < 0:
        raise PhysicalWalObjectStorageUploaderError("physical WAL spool owner UID is invalid")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
    ):
        raise PhysicalWalObjectStorageUploaderError("physical WAL Object Storage route site is invalid")
    if value.source_site == value.destination_site:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL Object Storage route endpoints must be distinct"
        )
    workspace = _secure_directory(
        value.workspace,
        label="physical WAL uploader workspace",
        owner_uid=0,
        exact_mode=0o700,
    )
    spool_root = _secure_directory(
        value.spool_root,
        label="physical WAL uploader spool root",
        owner_uid=value.spool_owner_uid,
        exact_mode=0o700,
    )
    if workspace == spool_root:
        raise PhysicalWalObjectStorageUploaderError("physical WAL workspace and spool root overlap")
    bucket = _safe_text(value.bucket, label="physical WAL Object Storage bucket", pattern=_BUCKET_RE)
    region = _safe_text(value.region, label="physical WAL Object Storage region", pattern=_REGION_RE)
    recipient = _safe_text(
        value.destination_age_recipient,
        label="physical WAL destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    object_storage_namespace = _require_route_namespace(
        source_site=value.source_site,
        destination_site=value.destination_site,
        object_storage_namespace=_object_storage_namespace(value.object_storage_namespace),
    )
    maximum_plaintext_bytes = _positive_int(
        value.maximum_plaintext_bytes,
        label="physical WAL maximum plaintext bytes",
        maximum=maximum_plaintext_ceiling,
    )
    if value.direct_site_control != "forbidden":
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL direct site control must remain forbidden"
        )
    if value.destination_object_ingest != "pull-only":
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL destination Object Storage ingest must remain pull-only"
        )
    return _ConfigFacts(
        source_site=value.source_site,
        destination_site=value.destination_site,
        workspace=workspace,
        spool_root=spool_root,
        spool_owner_uid=value.spool_owner_uid,
        bucket=bucket,
        region=region,
        destination_age_recipient=recipient,
        object_storage_namespace=object_storage_namespace,
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )


_DESCRIPTOR_FIELDS = {
    "schema",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "stream_generation_id",
    "baseline_generation_id",
    "baseline_manifest_sha256",
    "baseline_wal_lsn",
    "wal_chain_start_lsn",
    "archive_manifest_sha256",
    "route_binding_sha256",
    "object_storage_namespace",
    "database_system_identifier",
    "timeline_id",
    "wal_segment_size_bytes",
    "destination_age_recipient",
    "writer_term",
    "wal_segment_name",
    "segment_ordinal",
    "start_lsn",
    "end_lsn",
    "snapshot_sha256",
    "snapshot_bytes",
    "object_key",
}


def _parse_canonical_descriptor(
    descriptor_bytes: object,
    *,
    descriptor_sha256: object,
    config: _ConfigFacts,
) -> _DescriptorFacts:
    if not isinstance(descriptor_bytes, bytes) or not descriptor_bytes or len(descriptor_bytes) > _MAX_DESCRIPTOR_BYTES:
        raise PhysicalWalObjectStorageUploaderError("physical WAL handoff descriptor byte size is invalid")
    actual_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
    if _sha256(descriptor_sha256, label="physical WAL handoff descriptor SHA-256") != actual_sha256:
        raise PhysicalWalObjectStorageUploaderError("physical WAL handoff descriptor hash is invalid")
    try:
        payload = json.loads(
            descriptor_bytes.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
        )
    except PhysicalWalObjectStorageUploaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalObjectStorageUploaderError("physical WAL handoff descriptor is invalid JSON") from exc
    if not isinstance(payload, dict) or _canonical_json_bytes(
        payload, label="physical WAL handoff descriptor"
    ) != descriptor_bytes:
        raise PhysicalWalObjectStorageUploaderError("physical WAL handoff descriptor is not canonical")
    item = _exact_mapping(payload, label="physical WAL handoff descriptor", fields=_DESCRIPTOR_FIELDS)
    if (
        item["schema"] != PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA
        or item["kind"] != "physical_wal_segment_handoff"
    ):
        raise PhysicalWalObjectStorageUploaderError("physical WAL handoff descriptor schema is invalid")
    if (
        item["source_site"] != config.source_site
        or item["destination_site"] != config.destination_site
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL handoff route does not match the pinned uploader route"
        )
    if _object_storage_namespace(item["object_storage_namespace"]) != config.object_storage_namespace:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL handoff namespace does not match the pinned uploader namespace"
        )
    campaign = _safe_text(item["campaign_id"], label="physical WAL campaign", pattern=CAMPAIGN_ID_RE)
    release = _safe_text(item["release_sha"], label="physical WAL release", pattern=RELEASE_SHA_RE)
    stream = _safe_text(
        item["stream_generation_id"], label="physical WAL stream generation", pattern=STREAM_GENERATION_ID_RE
    )
    baseline = _safe_text(
        item["baseline_generation_id"], label="physical WAL baseline generation", pattern=STREAM_GENERATION_ID_RE
    )
    baseline_lsn, _baseline_value = _lsn(item["baseline_wal_lsn"], label="physical WAL baseline LSN")
    chain_start_lsn, chain_start_value = _lsn(
        item["wal_chain_start_lsn"], label="physical WAL chain start LSN"
    )
    start_lsn, start_value = _lsn(item["start_lsn"], label="physical WAL segment start LSN")
    end_lsn, end_value = _lsn(item["end_lsn"], label="physical WAL segment end LSN")
    segment_size = _positive_int(
        item["wal_segment_size_bytes"], label="physical WAL segment size", maximum=64 * 1024 * 1024
    )
    if (
        segment_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES
        or end_value != start_value + segment_size
    ):
        raise PhysicalWalObjectStorageUploaderError("physical WAL segment geometry is invalid")
    if start_value < chain_start_value:
        raise PhysicalWalObjectStorageUploaderError("physical WAL segment precedes its chain start")
    if (
        chain_start_value % segment_size
        or chain_start_value > _baseline_value
        or _baseline_value >= chain_start_value + segment_size
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL baseline and chain-start geometry is invalid"
        )
    if type(item["timeline_id"]) is not int or not 1 <= item["timeline_id"] <= 0xFFFFFFFF:
        raise PhysicalWalObjectStorageUploaderError("physical WAL timeline is invalid")
    if not isinstance(item["database_system_identifier"], str) or _SYSTEM_IDENTIFIER_RE.fullmatch(item["database_system_identifier"]) is None:
        raise PhysicalWalObjectStorageUploaderError("physical WAL database system identifier is invalid")
    if not isinstance(item["wal_segment_name"], str) or re.fullmatch(r"[0-9A-F]{24}", item["wal_segment_name"]) is None:
        raise PhysicalWalObjectStorageUploaderError("physical WAL segment name is invalid")
    parsed_timeline = int(item["wal_segment_name"][:8], 16)
    expected_wal_name = (
        f"{item['timeline_id']:08X}{start_value >> 32:08X}"
        f"{((start_value & 0xFFFFFFFF) // segment_size):08X}"
    )
    if parsed_timeline != item["timeline_id"] or item["wal_segment_name"] != expected_wal_name:
        raise PhysicalWalObjectStorageUploaderError("physical WAL segment timeline is invalid")
    if (
        type(item["segment_ordinal"]) is not int
        or item["segment_ordinal"] < 0
        or item["segment_ordinal"] != start_value // segment_size
    ):
        raise PhysicalWalObjectStorageUploaderError("physical WAL segment ordinal is invalid")
    writer_term = _exact_mapping(
        item["writer_term"],
        label="physical WAL writer term",
        fields={"holder_site", "writer_epoch", "writer_lease_id", "witnessed_term_proof_sha256"},
    )
    if writer_term["holder_site"] != config.source_site:
        raise PhysicalWalObjectStorageUploaderError("physical WAL writer term holder is invalid")
    epoch = _positive_int(writer_term["writer_epoch"], label="physical WAL writer epoch", maximum=2**63 - 1)
    writer_lease_id = _safe_text(
        writer_term["writer_lease_id"],
        label="physical WAL writer lease",
        pattern=LEASE_ID_RE,
    )
    recipient = _safe_text(
        item["destination_age_recipient"],
        label="physical WAL descriptor destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    if recipient != config.destination_age_recipient:
        raise PhysicalWalObjectStorageUploaderError(
            "physical WAL descriptor destination age recipient is not the pinned route recipient"
        )
    snapshot_sha256 = _sha256(item["snapshot_sha256"], label="physical WAL snapshot SHA-256")
    snapshot_bytes = _positive_int(
        item["snapshot_bytes"], label="physical WAL snapshot bytes", maximum=config.maximum_plaintext_bytes
    )
    if snapshot_bytes != segment_size:
        raise PhysicalWalObjectStorageUploaderError("physical WAL snapshot byte size does not match geometry")
    object_key = _safe_text(item["object_key"], label="physical WAL Object key", pattern=OBJECT_KEY_RE)
    if any(part in {"", ".", ".."} for part in object_key.split("/")):
        raise PhysicalWalObjectStorageUploaderError("physical WAL Object key is invalid")
    expected_object_key = _derive_object_key(
        campaign_id=campaign,
        release_sha=release,
        baseline_generation_id=baseline,
        object_storage_namespace=config.object_storage_namespace,
        source_site=config.source_site,
        destination_site=config.destination_site,
        timeline_id=item["timeline_id"],
        wal_segment_name=item["wal_segment_name"],
        snapshot_sha256=snapshot_sha256,
    )
    if object_key != expected_object_key:
        raise PhysicalWalObjectStorageUploaderError("physical WAL Object key is not deterministic")
    return _DescriptorFacts(
        source_site=config.source_site,
        destination_site=config.destination_site,
        campaign_id=campaign,
        release_sha=release,
        stream_generation_id=stream,
        baseline_generation_id=baseline,
        baseline_manifest_sha256=_sha256(item["baseline_manifest_sha256"], label="physical WAL baseline manifest hash"),
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=chain_start_lsn,
        archive_manifest_sha256=_sha256(item["archive_manifest_sha256"], label="physical WAL archive manifest hash"),
        route_binding_sha256=_sha256(item["route_binding_sha256"], label="physical WAL route binding hash"),
        database_system_identifier=item["database_system_identifier"],
        timeline_id=item["timeline_id"],
        wal_segment_size_bytes=segment_size,
        destination_age_recipient=recipient,
        writer_epoch=epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=_sha256(
            writer_term["witnessed_term_proof_sha256"], label="physical WAL Witness proof hash"
        ),
        wal_segment_name=item["wal_segment_name"],
        segment_ordinal=item["segment_ordinal"],
        start_lsn=start_lsn,
        end_lsn=end_lsn,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        object_key=object_key,
    )


def _derive_object_key(
    *,
    campaign_id: str,
    release_sha: str,
    baseline_generation_id: str,
    source_site: str,
    destination_site: str,
    object_storage_namespace: str,
    timeline_id: int,
    wal_segment_name: str,
    snapshot_sha256: str,
) -> str:
    return "/".join(
        (
            _object_storage_namespace(object_storage_namespace),
            campaign_id,
            release_sha,
            baseline_generation_id,
            f"{source_site}-to-{destination_site}",
            f"timeline-{timeline_id:08X}",
            wal_segment_name,
            f"{snapshot_sha256}.age",
        )
    )


_BASE_BACKUP_DESCRIPTOR_FIELDS = {
    "schema",
    "kind",
    "source_site",
    "destination_site",
    "campaign_id",
    "release_sha",
    "baseline_generation_id",
    "route_binding_sha256",
    "object_storage_namespace",
    "database_system_identifier",
    "timeline_id",
    "wal_segment_size_bytes",
    "baseline_wal_lsn",
    "wal_chain_start_lsn",
    "base_backup_end_lsn",
    "destination_age_recipient",
    "writer_term",
    "completed_source_artifact",
    "snapshot_path_name",
    "snapshot_sha256",
    "snapshot_bytes",
    "object_key",
    "not_a_remote_apply_proof",
    "not_a_strict_acknowledgement_proof",
}


@dataclass(frozen=True)
class _BaseBackupDescriptorFacts:
    source_site: str
    destination_site: str
    destination_age_recipient: str
    snapshot_sha256: str
    snapshot_bytes: int
    object_key: str


def _parse_canonical_base_backup_descriptor(
    descriptor_bytes: object,
    *,
    descriptor_sha256: object,
    config: _ConfigFacts,
) -> _BaseBackupDescriptorFacts:
    """Accept only the exact canonical base-backup spool descriptor.

    The base-backup grammar remains deliberately separate from the WAL
    grammar.  Sharing only the encryption/Object-Storage primitive cannot
    make either uploader accept the other's descriptor kind.
    """

    if (
        not isinstance(descriptor_bytes, bytes)
        or not descriptor_bytes
        or len(descriptor_bytes) > _MAX_DESCRIPTOR_BYTES
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup handoff descriptor byte size is invalid"
        )
    actual_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
    if _sha256(descriptor_sha256, label="base-backup handoff descriptor SHA-256") != actual_sha256:
        raise PhysicalWalObjectStorageUploaderError("base-backup handoff descriptor hash is invalid")
    try:
        payload = json.loads(
            descriptor_bytes.decode("utf-8", "strict"),
            object_pairs_hook=_strict_object,
        )
    except PhysicalWalObjectStorageUploaderError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup handoff descriptor is invalid JSON"
        ) from exc
    if not isinstance(payload, dict) or _canonical_json_bytes(
        payload, label="base-backup handoff descriptor"
    ) != descriptor_bytes:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup handoff descriptor is not canonical"
        )
    item = _exact_mapping(
        payload,
        label="base-backup handoff descriptor",
        fields=_BASE_BACKUP_DESCRIPTOR_FIELDS,
    )
    if (
        item["schema"] != PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA
        or item["kind"] != "physical_postgresql_base_backup_handoff"
    ):
        raise PhysicalWalObjectStorageUploaderError("base-backup handoff descriptor schema is invalid")
    if (
        item["source_site"] != config.source_site
        or item["destination_site"] != config.destination_site
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup handoff route does not match the pinned uploader route"
        )
    if _object_storage_namespace(item["object_storage_namespace"]) != config.object_storage_namespace:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup handoff namespace does not match the pinned uploader namespace"
        )
    campaign = _safe_text(item["campaign_id"], label="base-backup campaign", pattern=CAMPAIGN_ID_RE)
    release = _safe_text(item["release_sha"], label="base-backup release", pattern=RELEASE_SHA_RE)
    baseline_generation = _safe_text(
        item["baseline_generation_id"],
        label="base-backup generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    _sha256(item["route_binding_sha256"], label="base-backup route binding hash")
    if (
        not isinstance(item["database_system_identifier"], str)
        or _SYSTEM_IDENTIFIER_RE.fullmatch(item["database_system_identifier"]) is None
    ):
        raise PhysicalWalObjectStorageUploaderError("base-backup database system identifier is invalid")
    if type(item["timeline_id"]) is not int or not 1 <= item["timeline_id"] <= 0xFFFFFFFF:
        raise PhysicalWalObjectStorageUploaderError("base-backup timeline is invalid")
    segment_size = _positive_int(
        item["wal_segment_size_bytes"],
        label="base-backup WAL segment size",
        maximum=max(PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES),
    )
    if segment_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        raise PhysicalWalObjectStorageUploaderError("base-backup WAL segment size is invalid")
    baseline_lsn, baseline_value = _lsn(item["baseline_wal_lsn"], label="base-backup baseline LSN")
    chain_start_lsn, chain_start_value = _lsn(
        item["wal_chain_start_lsn"], label="base-backup chain-start LSN"
    )
    _backup_end_lsn, backup_end_value = _lsn(
        item["base_backup_end_lsn"], label="base-backup end LSN"
    )
    if backup_end_value <= baseline_value:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup end LSN must follow the baseline LSN"
        )
    if (
        chain_start_value % segment_size
        or chain_start_value > baseline_value
        or baseline_value >= chain_start_value + segment_size
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup chain-start geometry is invalid"
        )
    recipient = _safe_text(
        item["destination_age_recipient"],
        label="base-backup descriptor destination age recipient",
        pattern=AGE_RECIPIENT_RE,
    )
    if recipient != config.destination_age_recipient:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup descriptor destination age recipient is not the pinned route recipient"
        )
    writer_term = _exact_mapping(
        item["writer_term"],
        label="base-backup writer term",
        fields={
            "holder_site",
            "epoch",
            "lease_id",
            "witness_transition_id",
            "witnessed_term_proof_sha256",
        },
    )
    if writer_term["holder_site"] != config.source_site:
        raise PhysicalWalObjectStorageUploaderError("base-backup writer term holder is invalid")
    _positive_int(writer_term["epoch"], label="base-backup writer epoch", maximum=2**63 - 1)
    _safe_text(writer_term["lease_id"], label="base-backup writer lease", pattern=LEASE_ID_RE)
    _safe_text(
        writer_term["witness_transition_id"],
        label="base-backup Witness transition",
        pattern=_WITNESS_TRANSITION_ID_RE,
    )
    _sha256(writer_term["witnessed_term_proof_sha256"], label="base-backup Witness proof hash")
    artifact = _exact_mapping(
        item["completed_source_artifact"],
        label="base-backup completed source artifact",
        fields={
            "artifact_name",
            "plaintext_sha256",
            "plaintext_bytes",
            "completion_attestation_sha256",
        },
    )
    artifact_name = _safe_text(
        artifact["artifact_name"],
        label="base-backup artifact name",
        pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII),
    )
    artifact_sha256 = _sha256(artifact["plaintext_sha256"], label="base-backup artifact SHA-256")
    artifact_bytes = _positive_int(
        artifact["plaintext_bytes"],
        label="base-backup artifact bytes",
        maximum=config.maximum_plaintext_bytes,
    )
    _sha256(
        artifact["completion_attestation_sha256"],
        label="base-backup completion attestation hash",
    )
    snapshot_path_name = _safe_text(
        item["snapshot_path_name"],
        label="base-backup snapshot filename",
        pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII),
    )
    snapshot_sha256 = _sha256(item["snapshot_sha256"], label="base-backup snapshot SHA-256")
    snapshot_bytes = _positive_int(
        item["snapshot_bytes"],
        label="base-backup snapshot bytes",
        maximum=config.maximum_plaintext_bytes,
    )
    if snapshot_sha256 != artifact_sha256 or snapshot_bytes != artifact_bytes:
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup snapshot does not match completed source artifact"
        )
    if snapshot_path_name != f"{snapshot_sha256}.basebackup":
        raise PhysicalWalObjectStorageUploaderError("base-backup snapshot filename is invalid")
    if (
        item["not_a_remote_apply_proof"] is not True
        or item["not_a_strict_acknowledgement_proof"] is not True
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "base-backup descriptor must explicitly disclaim remote apply and strict acknowledgement"
        )
    object_key = _safe_text(item["object_key"], label="base-backup Object key", pattern=OBJECT_KEY_RE)
    if any(part in {"", ".", ".."} for part in object_key.split("/")):
        raise PhysicalWalObjectStorageUploaderError("base-backup Object key is invalid")
    expected_object_key = _derive_base_backup_object_key(
        campaign_id=campaign,
        release_sha=release,
        baseline_generation_id=baseline_generation,
        object_storage_namespace=config.object_storage_namespace,
        source_site=config.source_site,
        destination_site=config.destination_site,
        timeline_id=item["timeline_id"],
        snapshot_sha256=snapshot_sha256,
    )
    if object_key != expected_object_key:
        raise PhysicalWalObjectStorageUploaderError("base-backup Object key is not deterministic")
    # Keep these values named while parsing so a future refactor cannot omit a
    # descriptor field from the validation merely because it is not returned.
    del baseline_lsn, chain_start_lsn, artifact_name
    return _BaseBackupDescriptorFacts(
        source_site=config.source_site,
        destination_site=config.destination_site,
        destination_age_recipient=recipient,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        object_key=object_key,
    )


def _derive_base_backup_object_key(
    *,
    campaign_id: str,
    release_sha: str,
    baseline_generation_id: str,
    source_site: str,
    destination_site: str,
    object_storage_namespace: str,
    timeline_id: int,
    snapshot_sha256: str,
) -> str:
    return "/".join(
        (
            _object_storage_namespace(object_storage_namespace),
            campaign_id,
            release_sha,
            baseline_generation_id,
            f"{source_site}-to-{destination_site}",
            f"timeline-{timeline_id:08X}",
            "base-backup",
            f"{snapshot_sha256}.age",
        )
    )


def _validate_snapshot(
    value: object,
    *,
    config: _ConfigFacts,
    descriptor: _DescriptorFacts | _BaseBackupDescriptorFacts,
) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise PhysicalWalObjectStorageUploaderError("physical backup snapshot path is invalid")
    try:
        absolute = value.absolute()
        resolved = value.resolve(strict=True)
        relative = resolved.relative_to(config.spool_root)
        metadata = os.lstat(value)
    except (OSError, ValueError) as exc:
        raise PhysicalWalObjectStorageUploaderError("physical backup snapshot path is unsafe") from exc
    if (
        absolute != resolved
        or not relative.parts
        or relative.parts[0] != "snapshots"
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != config.spool_owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != descriptor.snapshot_bytes
    ):
        raise PhysicalWalObjectStorageUploaderError("physical backup snapshot is unsafe")
    actual_sha256, actual_bytes = _hash_file(resolved, label="physical backup snapshot")
    if actual_sha256 != descriptor.snapshot_sha256 or actual_bytes != descriptor.snapshot_bytes:
        raise PhysicalWalObjectStorageUploaderError("physical backup snapshot does not match descriptor")
    return resolved


def _hash_file(path: Path, *, label: str) -> tuple[str, int]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalWalObjectStorageUploaderError("platform lacks fail-closed non-symlink open")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalWalObjectStorageUploaderError(f"{label} cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PhysicalWalObjectStorageUploaderError(f"{label} is unsafe")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _private_versioned_bucket(client: object, *, bucket: str) -> None:
    for method_name in (
        "get_bucket_versioning",
        "get_bucket_acl",
    ):
        if not callable(getattr(client, method_name, None)):
            raise PhysicalWalObjectStorageUploaderError(
                "Object Storage client lacks required private bucket preflight"
            )
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
    except Exception as exc:
        raise PhysicalWalObjectStorageUploaderError(
            "cannot verify Object Storage bucket versioning"
        ) from exc
    if not isinstance(versioning, Mapping) or versioning.get("Status") != "Enabled":
        raise PhysicalWalObjectStorageUploaderError("Object Storage bucket versioning is not enabled")
    try:
        acl = client.get_bucket_acl(Bucket=bucket)
    except Exception as exc:
        raise PhysicalWalObjectStorageUploaderError(
            "cannot verify private Object Storage bucket ACL"
        ) from exc
    if (
        not isinstance(acl, Mapping)
        or not isinstance(acl.get("Owner"), Mapping)
        or not isinstance(acl["Owner"].get("ID"), str)
        or not acl["Owner"]["ID"]
    ):
        raise PhysicalWalObjectStorageUploaderError(
            "Object Storage bucket ACL is missing its canonical owner"
        )
    owner_id = acl["Owner"]["ID"]
    grants = acl.get("Grants")
    if not isinstance(grants, list) or not grants:
        raise PhysicalWalObjectStorageUploaderError("Object Storage bucket ACL is malformed")
    for grant in grants:
        if not isinstance(grant, Mapping) or not isinstance(grant.get("Grantee"), Mapping):
            raise PhysicalWalObjectStorageUploaderError("Object Storage bucket ACL is malformed")
        grantee = grant["Grantee"]
        if (
            grantee.get("Type") != "CanonicalUser"
            or grantee.get("ID") != owner_id
            or grant.get("Permission") != "FULL_CONTROL"
        ):
            raise PhysicalWalObjectStorageUploaderError(
                "Object Storage bucket ACL grants access outside its sole canonical owner"
            )


def _response_has_provider_side_encryption(value: object) -> bool:
    """Reject all visible provider SSE/KMS fields, including nested headers."""

    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).replace("-", "").replace("_", "").lower()
            if key.startswith(("serversideencryption", "sse", "kms", "bucketkey")):
                return True
            if key == "httpheaders" and isinstance(item, Mapping):
                for header_name in item:
                    normalized = str(header_name).lower()
                    if normalized.startswith(
                        ("x-amz-server-side-encryption", "x-amz-sse", "x-amz-kms", "x-amz-bucket-key")
                    ):
                        return True
            if _response_has_provider_side_encryption(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_response_has_provider_side_encryption(item) for item in value)
    return False


def _exact_object_history(client: object, *, bucket: str, key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
            raise PhysicalWalObjectStorageUploaderError(
                "cannot inspect immutable Object version history"
            ) from exc
        if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
            raise PhysicalWalObjectStorageUploaderError("Object version history is invalid")
        for name, destination in (("Versions", versions), ("DeleteMarkers", delete_markers)):
            items = response.get(name, [])
            if not isinstance(items, list):
                raise PhysicalWalObjectStorageUploaderError("Object version history is invalid")
            for item in items:
                if not isinstance(item, Mapping) or item.get("Key") != key:
                    raise PhysicalWalObjectStorageUploaderError("Object version history is ambiguous")
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
            raise PhysicalWalObjectStorageUploaderError("Object version history pagination is invalid")
        key_marker = next_key
        version_marker = next_version
    raise PhysicalWalObjectStorageUploaderError("Object version history pagination exceeds bound")


def _assert_object_absent(client: object, *, bucket: str, key: str) -> None:
    versions, delete_markers = _exact_object_history(client, bucket=bucket, key=key)
    if versions or delete_markers:
        raise PhysicalWalObjectStorageUploaderError(
            "refusing to reuse an immutable Object key with a version or delete marker"
        )


def _require_exact_version(
    client: object,
    *,
    bucket: str,
    key: str,
    expected_version_id: str,
) -> None:
    versions, delete_markers = _exact_object_history(client, bucket=bucket, key=key)
    if delete_markers or len(versions) != 1:
        raise PhysicalWalObjectStorageUploaderError("immutable Object history is not a single live version")
    version = versions[0]
    if (
        version.get("VersionId") != expected_version_id
        or version.get("IsLatest") is not True
    ):
        raise PhysicalWalObjectStorageUploaderError("immutable Object version does not match create-only PUT")


def _metadata_for_descriptor(
    *,
    descriptor_sha256: str,
    destination_age_recipient: str,
    ciphertext_sha256: str,
    ciphertext_bytes: int,
) -> dict[str, str]:
    return {
        "transport-schema": PHYSICAL_WAL_OBJECT_STORAGE_UPLOADER_SCHEMA,
        "encryption": PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        "descriptor-sha256": descriptor_sha256,
        "destination-age-recipient": destination_age_recipient,
        "ciphertext-sha256": ciphertext_sha256,
        "ciphertext-bytes": str(ciphertext_bytes),
    }


def _validate_ciphertext(
    path: Path,
    *,
    maximum_plaintext_bytes: int,
    maximum_ciphertext_overhead_bytes: int,
) -> tuple[str, int]:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PhysicalWalObjectStorageUploaderError("age ciphertext is unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size < 1
        or metadata.st_size > maximum_plaintext_bytes + maximum_ciphertext_overhead_bytes
    ):
        raise PhysicalWalObjectStorageUploaderError("age ciphertext is unsafe")
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb", closefd=True) as handle:
            header = handle.read(len(b"age-encryption.org/v1\n"))
    except OSError as exc:
        raise PhysicalWalObjectStorageUploaderError("age ciphertext cannot be opened safely") from exc
    if header != b"age-encryption.org/v1\n":
        raise PhysicalWalObjectStorageUploaderError("age ciphertext is not age-v1")
    return _hash_file(path, label="age ciphertext")


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
        raise PhysicalWalObjectStorageUploaderError("cannot read back exact immutable Object version") from exc
    if not isinstance(response, Mapping):
        raise PhysicalWalObjectStorageUploaderError("Object read-back response is invalid")
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        raise PhysicalWalObjectStorageUploaderError("Object read-back body is invalid")
    digest = hashlib.sha256()
    total = 0
    close = getattr(body, "close", None)
    try:
        if _response_has_provider_side_encryption(response):
            raise PhysicalWalObjectStorageUploaderError("Object read-back response is invalid")
        if response.get("VersionId") != version_id:
            raise PhysicalWalObjectStorageUploaderError("Object read-back returned a different VersionId")
        metadata = response.get("Metadata")
        if not isinstance(metadata, Mapping) or dict(metadata) != dict(expected_metadata):
            raise PhysicalWalObjectStorageUploaderError("Object read-back metadata is invalid")
        while True:
            try:
                chunk = body.read(_READ_CHUNK_BYTES)
            except Exception as exc:
                raise PhysicalWalObjectStorageUploaderError("Object read-back body failed") from exc
            if not isinstance(chunk, bytes):
                raise PhysicalWalObjectStorageUploaderError("Object read-back body is invalid")
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes:
                raise PhysicalWalObjectStorageUploaderError("Object read-back byte count is invalid")
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
            raise PhysicalWalObjectStorageUploaderError("Object read-back body cannot be closed") from exc
    if total != expected_bytes or digest.hexdigest() != expected_sha256:
        raise PhysicalWalObjectStorageUploaderError("Object read-back ciphertext does not match upload")


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
        raise PhysicalWalObjectStorageUploaderError("Object Storage client lacks exact Object head read-back")
    try:
        response = client.head_object(Bucket=bucket, Key=key, VersionId=version_id)
    except Exception as exc:
        raise PhysicalWalObjectStorageUploaderError("cannot head exact immutable Object version") from exc
    if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
        raise PhysicalWalObjectStorageUploaderError("Object head read-back response is invalid")
    if (
        response.get("VersionId") != version_id
        or response.get("ContentLength") != expected_bytes
        or not isinstance(response.get("Metadata"), Mapping)
        or dict(response["Metadata"]) != dict(expected_metadata)
    ):
        raise PhysicalWalObjectStorageUploaderError("Object head read-back does not match upload")


def _publish_encrypted_create_only(
    *,
    config: _ConfigFacts,
    descriptor: _DescriptorFacts | _BaseBackupDescriptorFacts,
    descriptor_sha256: str,
    snapshot_path: Path,
    age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None,
    client_factory: Callable[[], PhysicalWalObjectStorageClient] | None,
    maximum_ciphertext_overhead_bytes: int,
    object_label: str,
) -> tuple[str, str, int]:
    """Run the shared local encryption/create-only/readback primitive.

    This has no descriptor dispatch.  Its caller must have already selected
    and fully validated exactly one descriptor grammar, preserving a strict
    boundary between a WAL segment and a base backup.
    """

    snapshot = _validate_snapshot(snapshot_path, config=config, descriptor=descriptor)
    if age_encryptor_factory is None or not callable(age_encryptor_factory):
        raise PhysicalWalObjectStorageUploaderError("physical backup age encryptor factory is required")
    if client_factory is None or not callable(client_factory):
        raise PhysicalWalObjectStorageUploaderError(
            "physical backup Object Storage client factory is required"
        )
    try:
        encryptor = age_encryptor_factory()
        client = client_factory()
    except Exception as exc:
        raise PhysicalWalObjectStorageUploaderError("physical backup uploader factory failed") from exc
    if not callable(getattr(encryptor, "encrypt", None)):
        raise PhysicalWalObjectStorageUploaderError("physical backup age encryptor is invalid")
    _private_versioned_bucket(client, bucket=config.bucket)
    _assert_object_absent(client, bucket=config.bucket, key=descriptor.object_key)
    with tempfile.TemporaryDirectory(
        prefix="physical-backup-upload-", dir=str(config.workspace)
    ) as raw_workspace:
        workspace = Path(raw_workspace)
        try:
            os.chmod(workspace, 0o700)
            workspace_metadata = os.lstat(workspace)
        except OSError as exc:
            raise PhysicalWalObjectStorageUploaderError(
                "physical backup upload workspace is unsafe"
            ) from exc
        if (
            stat.S_ISLNK(workspace_metadata.st_mode)
            or not stat.S_ISDIR(workspace_metadata.st_mode)
            or workspace_metadata.st_uid != 0
            or stat.S_IMODE(workspace_metadata.st_mode) != 0o700
        ):
            raise PhysicalWalObjectStorageUploaderError("physical backup upload workspace is unsafe")
        ciphertext_path = workspace / "encrypted-recovery-material.age"
        try:
            encryptor.encrypt(
                recipient=config.destination_age_recipient,
                plaintext_path=snapshot,
                ciphertext_path=ciphertext_path,
            )
        except Exception as exc:
            raise PhysicalWalObjectStorageUploaderError(
                "physical backup age encryption failed"
            ) from exc
        ciphertext_sha256, ciphertext_bytes = _validate_ciphertext(
            ciphertext_path,
            maximum_plaintext_bytes=config.maximum_plaintext_bytes,
            maximum_ciphertext_overhead_bytes=maximum_ciphertext_overhead_bytes,
        )
        _validate_snapshot(snapshot, config=config, descriptor=descriptor)
        metadata = _metadata_for_descriptor(
            descriptor_sha256=descriptor_sha256,
            destination_age_recipient=config.destination_age_recipient,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
        )
        try:
            with os.fdopen(
                os.open(ciphertext_path, os.O_RDONLY | os.O_NOFOLLOW), "rb", closefd=True
            ) as handle:
                response = client.put_object(
                    Bucket=config.bucket,
                    Key=descriptor.object_key,
                    Body=handle,
                    ContentLength=ciphertext_bytes,
                    Metadata=metadata,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                )
        except Exception as exc:
            raise PhysicalWalObjectStorageUploaderError(
                f"{object_label} conditional create-only Object PUT failed"
            ) from exc
        if not isinstance(response, Mapping) or _response_has_provider_side_encryption(response):
            raise PhysicalWalObjectStorageUploaderError("Object PUT response is invalid")
        version_id = response.get("VersionId")
        if (
            not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or VERSION_ID_RE.fullmatch(version_id) is None
        ):
            raise PhysicalWalObjectStorageUploaderError("Object PUT did not return a valid VersionId")
        _require_exact_version(
            client,
            bucket=config.bucket,
            key=descriptor.object_key,
            expected_version_id=version_id,
        )
        _head_exact_ciphertext(
            client,
            bucket=config.bucket,
            key=descriptor.object_key,
            version_id=version_id,
            expected_bytes=ciphertext_bytes,
            expected_metadata=metadata,
        )
        _readback_ciphertext(
            client,
            bucket=config.bucket,
            key=descriptor.object_key,
            version_id=version_id,
            expected_sha256=ciphertext_sha256,
            expected_bytes=ciphertext_bytes,
            expected_metadata=metadata,
        )
    return version_id, ciphertext_sha256, ciphertext_bytes


class PhysicalWalObjectStorageUploader:
    """WAL-only adapter for one explicitly pinned FI↔IR archive direction."""

    def __init__(
        self,
        *,
        config: PhysicalWalObjectStorageUploaderConfig,
        age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None,
        client_factory: Callable[[], PhysicalWalObjectStorageClient] | None,
    ) -> None:
        self._config = config
        self._age_encryptor_factory = age_encryptor_factory
        self._client_factory = client_factory

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        """Encrypt one canonical WAL segment and prove exact immutable readback."""

        config = _normalise_config(
            self._config,
            maximum_plaintext_ceiling=max(PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES),
        )
        descriptor = _parse_canonical_descriptor(
            descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
            config=config,
        )
        if config.maximum_plaintext_bytes != descriptor.wal_segment_size_bytes:
            raise PhysicalWalObjectStorageUploaderError(
                "physical WAL uploader maximum plaintext bytes must equal the pinned WAL geometry"
            )
        version_id, ciphertext_sha256, ciphertext_bytes = _publish_encrypted_create_only(
            config=config,
            descriptor=descriptor,
            descriptor_sha256=descriptor_sha256,
            snapshot_path=snapshot_path,
            age_encryptor_factory=self._age_encryptor_factory,
            client_factory=self._client_factory,
            maximum_ciphertext_overhead_bytes=_WAL_MAX_CIPHERTEXT_OVERHEAD_BYTES,
            object_label="physical WAL",
        )
        return PhysicalWalArchiveUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=descriptor.object_key,
            version_id=version_id,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
            encryption=PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
            age_recipient=config.destination_age_recipient,
            immutability=PHYSICAL_WAL_OBJECT_IMMUTABILITY,
        )


class PhysicalWalBaseBackupObjectStorageUploader:
    """Base-backup-only adapter using the same safe storage primitive.

    It accepts no WAL descriptor and never claims a remote-apply or strict
    acknowledgement.  The output type intentionally matches only the narrow
    ``PhysicalWalBaseBackupUploader`` protocol.
    """

    def __init__(
        self,
        *,
        config: PhysicalWalObjectStorageUploaderConfig,
        age_encryptor_factory: Callable[[], PhysicalWalAgeEncryptor] | None,
        client_factory: Callable[[], PhysicalWalObjectStorageClient] | None,
    ) -> None:
        self._config = config
        self._age_encryptor_factory = age_encryptor_factory
        self._client_factory = client_factory

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalBaseBackupUploadReceipt:
        """Encrypt one canonical completed base backup and prove exact readback."""

        config = _normalise_config(
            self._config,
            maximum_plaintext_ceiling=MAX_PHYSICAL_BASE_BACKUP_BYTES,
        )
        descriptor = _parse_canonical_base_backup_descriptor(
            descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
            config=config,
        )
        version_id, ciphertext_sha256, ciphertext_bytes = _publish_encrypted_create_only(
            config=config,
            descriptor=descriptor,
            descriptor_sha256=descriptor_sha256,
            snapshot_path=snapshot_path,
            age_encryptor_factory=self._age_encryptor_factory,
            client_factory=self._client_factory,
            maximum_ciphertext_overhead_bytes=MAX_BASE_BACKUP_ENCRYPTION_OVERHEAD_BYTES,
            object_label="base-backup",
        )
        return PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=descriptor.object_key,
            version_id=version_id,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_bytes=ciphertext_bytes,
            encryption=PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
            age_recipient=config.destination_age_recipient,
            immutability=PHYSICAL_WAL_OBJECT_IMMUTABILITY,
        )
