"""Local-only, default-disabled spool for finalized database-visible blobs.

This boundary does not query a database, open a network connection, encrypt,
upload, restore, start PostgreSQL, or make a promotion decision.  A trusted
external extractor must provide frozen descriptors for already-finalized blob
records.  This module only verifies the pins, snapshots the exact local files
without modifying their source, and produces canonical handoff and inventory
plaintext artifacts for later independent stages.

In particular, a result is not a database snapshot-consistency proof, an
Object-Storage receipt, a blob-frontier manifest, a remote-apply proof, a
strict acknowledgement, or a writer/promotion authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Callable

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    WEBAPP_SITES,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE


__all__ = (
    "DEFAULT_MAX_PHYSICAL_BLOB_BYTES",
    "MAX_PHYSICAL_BLOB_BYTES",
    "PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA",
    "PHYSICAL_BLOB_ARTIFACT_SPOOL_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA",
    "PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA",
    "PhysicalBlobArtifactHandoffResult",
    "PhysicalBlobArtifactManifestBinding",
    "PhysicalBlobArtifactSpoolConfig",
    "PhysicalBlobArtifactSpoolError",
    "PhysicalBlobArtifactSpoolResult",
    "PhysicalBlobFrozenDescriptor",
    "PhysicalBlobInventoryShardPlaintext",
    "VerifiedPhysicalBlobArtifactBinding",
    "authorize_physical_blob_artifact_binding",
    "derive_physical_blob_artifact_object_key",
    "derive_physical_blob_uploads_root_identity",
    "require_verified_physical_blob_artifact_binding",
    "spool_finalized_physical_blob_artifacts",
)


PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-blob-artifact-spool-descriptor-v1"
)
PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA = "gold-trade-physical-blob-artifact-handoff-v1"
PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA = (
    "gold-trade-physical-blob-inventory-shard-plaintext-v1"
)
PHYSICAL_BLOB_ARTIFACT_SPOOL_DEFAULT_ENABLED = False

DEFAULT_MAX_PHYSICAL_BLOB_BYTES = 16 * 1024 * 1024 * 1024
MAX_PHYSICAL_BLOB_BYTES = 512 * 1024 * 1024 * 1024
MAX_BLOBS_PER_INVENTORY_SHARD = 16_384
MAX_INVENTORY_SHARD_PLAINTEXT_BYTES = 32 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_CANONICAL_RECORD_BYTES = 256 * 1024

_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SOURCE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_TEMPORARY_SOURCE_COMPONENTS = frozenset(
    {
        "tmp",
        "temp",
        "temporary",
        "inflight",
        "in-flight",
        "partial",
        "staging",
        "uploading",
        "unfinalized",
    }
)
_TEMPORARY_SOURCE_SUFFIXES = (".tmp", ".part", ".partial", ".inflight", ".uploading")
_VERIFIED_BINDING_CAPABILITY = object()


class PhysicalBlobArtifactSpoolError(ValueError):
    """The frozen blob descriptor or local artifact boundary is unsafe."""


@dataclass(frozen=True)
class PhysicalBlobArtifactManifestBinding:
    """Shared non-secret lineage pins for one ordered FI↔IR blob route."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    destination_age_recipient: str


@dataclass(frozen=True)
class PhysicalBlobFrozenDescriptor:
    """One externally extracted, frozen, database-visible finalized record.

    The source path is relative to the configured protected uploads root.  It
    is never treated as a database query or as a destination restore path.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    destination_age_recipient: str
    source_record_id: str
    source_relative_path: str
    uploads_root_identity_sha256: str
    declared_content_sha256: str
    declared_content_bytes: int
    database_visibility: str = "frozen_database_visible_finalized_v1"
    finalization_state: str = "finalized"
    temporary: bool = False
    inflight: bool = False


@dataclass(frozen=True)
class PhysicalBlobArtifactSpoolConfig:
    """Root-only fixed roots and a bounded local blob snapshot size."""

    uploads_root: Path | None = None
    spool_root: Path | None = None
    enabled: bool = PHYSICAL_BLOB_ARTIFACT_SPOOL_DEFAULT_ENABLED
    maximum_blob_bytes: int = DEFAULT_MAX_PHYSICAL_BLOB_BYTES


@dataclass(frozen=True)
class VerifiedPhysicalBlobArtifactBinding:
    """Opaque live-term-bound context; direct construction is untrusted."""

    manifest_binding: PhysicalBlobArtifactManifestBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    route_binding_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalBlobArtifactHandoffResult:
    """One retained local snapshot plus its canonical future-uploader handoff."""

    source_record_id: str
    snapshot_path: Path
    snapshot_sha256: str
    snapshot_bytes: int
    handoff_descriptor_path: Path
    handoff_descriptor_sha256: str
    object_key: str


@dataclass(frozen=True)
class PhysicalBlobInventoryShardPlaintext:
    """Plaintext facts a later uploader can bridge to ``PhysicalWalBlobInventoryShard``.

    It contains no encrypted inventory object/version receipt and is not a
    signed blob-frontier manifest.
    """

    shard_ordinal: int
    plaintext_path: Path
    plaintext_sha256: str
    plaintext_bytes: int
    entry_count: int


@dataclass(frozen=True)
class PhysicalBlobArtifactSpoolResult:
    """Local-only result; explicitly not a cross-system consistency claim."""

    artifacts: tuple[PhysicalBlobArtifactHandoffResult, ...]
    inventory_shard: PhysicalBlobInventoryShardPlaintext


@dataclass(frozen=True)
class _BindingFacts:
    manifest: PhysicalBlobArtifactManifestBinding
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    route_binding_sha256: str


@dataclass(frozen=True)
class _ConfigFacts:
    uploads_root: Path
    uploads_root_identity_sha256: str
    spool_root: Path
    maximum_blob_bytes: int


@dataclass(frozen=True)
class _DescriptorFacts:
    source_record_id: str
    source_relative_parts: tuple[str, ...]
    uploads_root_identity_sha256: str
    declared_content_sha256: str
    declared_content_bytes: int
    object_key: str


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
        raise PhysicalBlobArtifactSpoolError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalBlobArtifactSpoolError("physical blob spool JSON has duplicate fields")
        result[key] = value
    return result


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalBlobArtifactSpoolError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalBlobArtifactSpoolError(f"{label} is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalBlobArtifactSpoolError(f"{label} contains a URL or secret-shaped value")
    return value


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalBlobArtifactSpoolError(f"{label} is invalid")
    return value


def _positive_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PhysicalBlobArtifactSpoolError(f"{label} is invalid")
    return value


def _lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalBlobArtifactSpoolError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _normalise_manifest_binding(value: object) -> PhysicalBlobArtifactManifestBinding:
    if type(value) is not PhysicalBlobArtifactManifestBinding:
        raise PhysicalBlobArtifactSpoolError("physical blob manifest binding is invalid")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        raise PhysicalBlobArtifactSpoolError(
            "physical blob binding must name one ordered distinct WebApp route"
        )
    return PhysicalBlobArtifactManifestBinding(
        source_site=value.source_site,
        destination_site=value.destination_site,
        campaign_id=_text(value.campaign_id, label="physical blob campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_text(value.release_sha, label="physical blob release", pattern=RELEASE_SHA_RE),
        baseline_generation_id=_text(
            value.baseline_generation_id,
            label="physical blob baseline generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        baseline_manifest_sha256=_sha256(
            value.baseline_manifest_sha256,
            label="physical blob baseline manifest SHA-256",
        ),
        baseline_wal_lsn=_lsn(value.baseline_wal_lsn, label="physical blob baseline WAL LSN")[0],
        destination_age_recipient=_text(
            value.destination_age_recipient,
            label="physical blob destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        ),
    )


def _route_binding_sha256(
    manifest: PhysicalBlobArtifactManifestBinding,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "schema": PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA,
                "source_site": manifest.source_site,
                "destination_site": manifest.destination_site,
                "campaign_id": manifest.campaign_id,
                "release_sha": manifest.release_sha,
                "baseline_generation_id": manifest.baseline_generation_id,
                "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
                "baseline_wal_lsn": manifest.baseline_wal_lsn,
                "destination_age_recipient": manifest.destination_age_recipient,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            label="physical blob route binding",
        )
    ).hexdigest()


def _binding_facts(
    *,
    manifest_binding: object,
    witnessed_term: object,
    now: datetime,
) -> _BindingFacts:
    manifest = _normalise_manifest_binding(manifest_binding)
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(witnessed_term, now=now)
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalBlobArtifactSpoolError(
            "physical blob Witness term is not live and verified"
        ) from exc
    if term.holder_site != manifest.source_site:
        raise PhysicalBlobArtifactSpoolError(
            "physical blob source does not hold the active Witness writer term"
        )
    return _BindingFacts(
        manifest=manifest,
        term=term,
        route_binding_sha256=_route_binding_sha256(manifest, term),
    )


def authorize_physical_blob_artifact_binding(
    *,
    manifest_binding: PhysicalBlobArtifactManifestBinding,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> VerifiedPhysicalBlobArtifactBinding:
    """Bind a frozen-descriptor route to one live Writer-Witness term."""

    observed_now = _utc(now, label="physical blob authorization clock")
    facts = _binding_facts(
        manifest_binding=manifest_binding,
        witnessed_term=witnessed_term,
        now=observed_now,
    )
    result = VerifiedPhysicalBlobArtifactBinding(
        manifest_binding=facts.manifest,
        witnessed_term=facts.term,
        route_binding_sha256=facts.route_binding_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BINDING_CAPABILITY)
    return result


def require_verified_physical_blob_artifact_binding(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalBlobArtifactBinding:
    """Revalidate an opaque binding before every local snapshot operation."""

    observed_now = _utc(now, label="physical blob binding clock")
    if (
        type(value) is not VerifiedPhysicalBlobArtifactBinding
        or value._capability is not _VERIFIED_BINDING_CAPABILITY
    ):
        raise PhysicalBlobArtifactSpoolError("physical blob binding is not authorized")
    facts = _binding_facts(
        manifest_binding=value.manifest_binding,
        witnessed_term=value.witnessed_term,
        now=observed_now,
    )
    if (
        facts.manifest != value.manifest_binding
        or facts.term != value.witnessed_term
        or facts.route_binding_sha256 != value.route_binding_sha256
    ):
        raise PhysicalBlobArtifactSpoolError("physical blob binding was tampered")
    return value


def _secure_root(value: object, *, label: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise PhysicalBlobArtifactSpoolError(f"{label} must be an absolute Path")
    if _URL_VALUE_RE.search(str(value)) or _SENSITIVE_VALUE_RE.search(str(value)):
        raise PhysicalBlobArtifactSpoolError(f"{label} contains a URL or secret-shaped value")
    try:
        absolute = value.absolute()
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError(f"{label} is unavailable") from exc
    if (
        absolute != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PhysicalBlobArtifactSpoolError(f"{label} is not a protected root-only directory")
    return resolved


def _uploads_root_identity(metadata: os.stat_result) -> str:
    """Return a non-path identity that pins descriptors to one protected root."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "schema": PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA,
                "root_device": metadata.st_dev,
                "root_inode": metadata.st_ino,
                "root_uid": metadata.st_uid,
                "root_mode": stat.S_IMODE(metadata.st_mode),
            },
            label="physical blob uploads root identity",
        )
    ).hexdigest()


def derive_physical_blob_uploads_root_identity(*, uploads_root: Path) -> str:
    """Expose the non-secret protected-root pin needed by frozen descriptors."""

    root = _secure_root(uploads_root, label="physical blob uploads root")
    try:
        return _uploads_root_identity(os.lstat(root))
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob uploads root is unavailable") from exc


def _normalise_config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalBlobArtifactSpoolConfig:
        raise PhysicalBlobArtifactSpoolError("physical blob spool config is invalid")
    if value.enabled is not True:
        raise PhysicalBlobArtifactSpoolError("physical blob spool is disabled")
    if os.geteuid() != 0:
        raise PhysicalBlobArtifactSpoolError("physical blob spool requires the root archive user")
    uploads_root = _secure_root(value.uploads_root, label="physical blob uploads root")
    spool_root = _secure_root(value.spool_root, label="physical blob spool root")
    if uploads_root == spool_root:
        raise PhysicalBlobArtifactSpoolError("physical blob uploads and spool roots overlap")
    try:
        uploads_root.relative_to(spool_root)
        overlaps = True
    except ValueError:
        try:
            spool_root.relative_to(uploads_root)
            overlaps = True
        except ValueError:
            overlaps = False
    if overlaps:
        raise PhysicalBlobArtifactSpoolError("physical blob uploads and spool roots overlap")
    return _ConfigFacts(
        uploads_root=uploads_root,
        uploads_root_identity_sha256=derive_physical_blob_uploads_root_identity(
            uploads_root=uploads_root
        ),
        spool_root=spool_root,
        maximum_blob_bytes=_positive_int(
            value.maximum_blob_bytes,
            label="physical blob maximum bytes",
            maximum=MAX_PHYSICAL_BLOB_BYTES,
        ),
    )


def _source_relative_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise PhysicalBlobArtifactSpoolError("physical blob source-relative path is invalid")
    if value.startswith("/") or "\\" in value or _URL_VALUE_RE.search(value):
        raise PhysicalBlobArtifactSpoolError("physical blob source-relative path is unsafe")
    parts = tuple(value.split("/"))
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise PhysicalBlobArtifactSpoolError("physical blob source-relative path is unsafe")
    for part in parts:
        if _SOURCE_COMPONENT_RE.fullmatch(part) is None:
            raise PhysicalBlobArtifactSpoolError("physical blob source-relative path is invalid")
        lower = part.lower()
        if (
            lower in _TEMPORARY_SOURCE_COMPONENTS
            or lower.startswith(".")
            or lower.startswith("tmp-")
            or lower.endswith(_TEMPORARY_SOURCE_SUFFIXES)
        ):
            raise PhysicalBlobArtifactSpoolError(
                "physical blob source-relative path names a temporary or in-flight upload"
            )
    return parts


def derive_physical_blob_artifact_object_key(
    *,
    manifest_binding: PhysicalBlobArtifactManifestBinding,
    source_record_id: str,
    declared_content_sha256: str,
) -> str:
    """Derive the only future encrypted-object key for one frozen source record."""

    binding = _normalise_manifest_binding(manifest_binding)
    record_id = _text(source_record_id, label="physical blob source record ID", pattern=_SYSTEM_ID_RE)
    content_sha256 = _sha256(
        declared_content_sha256, label="physical blob declared content SHA-256"
    )
    record_digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
    key = "/".join(
        (
            "physical-blobs",
            binding.campaign_id,
            binding.release_sha,
            binding.baseline_generation_id,
            f"{binding.source_site}-to-{binding.destination_site}",
            "records",
            record_digest,
            f"{content_sha256}.age",
        )
    )
    if OBJECT_KEY_RE.fullmatch(key) is None:
        raise PhysicalBlobArtifactSpoolError("derived physical blob Object key is invalid")
    return key


def _normalise_descriptor(
    value: object,
    *,
    binding: VerifiedPhysicalBlobArtifactBinding,
    config: _ConfigFacts,
) -> _DescriptorFacts:
    if type(value) is not PhysicalBlobFrozenDescriptor:
        raise PhysicalBlobArtifactSpoolError("physical blob frozen descriptor is invalid")
    manifest = binding.manifest_binding
    term = binding.witnessed_term
    if (
        value.source_site != manifest.source_site
        or value.destination_site != manifest.destination_site
        or value.campaign_id != manifest.campaign_id
        or value.release_sha != manifest.release_sha
        or value.baseline_generation_id != manifest.baseline_generation_id
        or value.baseline_manifest_sha256 != manifest.baseline_manifest_sha256
        or value.baseline_wal_lsn != manifest.baseline_wal_lsn
    ):
        raise PhysicalBlobArtifactSpoolError(
            "physical blob descriptor does not match its pinned route or baseline"
        )
    if (
        value.writer_epoch != term.writer_epoch
        or value.writer_lease_id != term.writer_lease_id
        or value.witnessed_term_proof_sha256 != term.proof_sha256
    ):
        raise PhysicalBlobArtifactSpoolError(
            "physical blob descriptor does not match the active Witness writer term"
        )
    if value.destination_age_recipient != manifest.destination_age_recipient:
        raise PhysicalBlobArtifactSpoolError(
            "physical blob descriptor does not match the pinned destination age recipient"
        )
    if (
        value.database_visibility != "frozen_database_visible_finalized_v1"
        or value.finalization_state != "finalized"
        or value.temporary is not False
        or value.inflight is not False
    ):
        raise PhysicalBlobArtifactSpoolError(
            "physical blob descriptor is temporary, in-flight, unfinalized, or not frozen database-visible"
        )
    source_record_id = _text(
        value.source_record_id,
        label="physical blob source record ID",
        pattern=_SYSTEM_ID_RE,
    )
    source_relative_parts = _source_relative_parts(value.source_relative_path)
    uploads_root_identity_sha256 = _sha256(
        value.uploads_root_identity_sha256,
        label="physical blob descriptor uploads-root identity",
    )
    if uploads_root_identity_sha256 != config.uploads_root_identity_sha256:
        raise PhysicalBlobArtifactSpoolError(
            "physical blob descriptor does not match the fixed protected uploads root"
        )
    declared_content_sha256 = _sha256(
        value.declared_content_sha256,
        label="physical blob declared content SHA-256",
    )
    declared_content_bytes = _positive_int(
        value.declared_content_bytes,
        label="physical blob declared content bytes",
        maximum=config.maximum_blob_bytes,
    )
    return _DescriptorFacts(
        source_record_id=source_record_id,
        source_relative_parts=source_relative_parts,
        uploads_root_identity_sha256=uploads_root_identity_sha256,
        declared_content_sha256=declared_content_sha256,
        declared_content_bytes=declared_content_bytes,
        object_key=derive_physical_blob_artifact_object_key(
            manifest_binding=manifest,
            source_record_id=source_record_id,
            declared_content_sha256=declared_content_sha256,
        ),
    )


def _secure_child_directory(root: Path, *parts: str) -> Path:
    candidate = root
    for part in parts:
        if not isinstance(part, str) or not part or "/" in part or "\\" in part or part in {".", ".."}:
            raise PhysicalBlobArtifactSpoolError("physical blob spool child path is invalid")
        candidate = candidate / part
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            metadata = os.lstat(candidate)
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise PhysicalBlobArtifactSpoolError("physical blob spool child path is unavailable") from exc
        if (
            resolved != candidate.absolute()
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PhysicalBlobArtifactSpoolError("physical blob spool child path is unsafe")
    return candidate


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobArtifactSpoolError("platform lacks durable secure directory fsync")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob directory cannot be opened safely") from exc
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise PhysicalBlobArtifactSpoolError("physical blob spool write failed")
        offset += written


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
    )


def _hash_open_file(
    descriptor: int,
    *,
    label: str,
    expected_fingerprint: tuple[int, int, int, int, int, int, int, int] | None = None,
    maximum_bytes: int | None = None,
) -> tuple[str, int]:
    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError(f"{label} cannot be inspected") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PhysicalBlobArtifactSpoolError(f"{label} is not a single-link regular file")
    if expected_fingerprint is not None and _fingerprint(before) != expected_fingerprint:
        raise PhysicalBlobArtifactSpoolError(f"{label} changed before secure read")
    digest = hashlib.sha256()
    total = 0
    while True:
        try:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
        except OSError as exc:
            raise PhysicalBlobArtifactSpoolError(f"{label} cannot be read safely") from exc
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        if maximum_bytes is not None and total > maximum_bytes:
            raise PhysicalBlobArtifactSpoolError(f"{label} exceeds its bounded size")
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError(f"{label} cannot be inspected after read") from exc
    if _fingerprint(after) != _fingerprint(before):
        raise PhysicalBlobArtifactSpoolError(f"{label} changed during secure read")
    return digest.hexdigest(), total


def _read_secure_regular_bytes(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobArtifactSpoolError("platform lacks fail-closed non-symlink open")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError(f"{label} cannot be opened safely") from exc
    try:
        try:
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise PhysicalBlobArtifactSpoolError(f"{label} cannot be inspected") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < 1
            or metadata.st_size > maximum_bytes
        ):
            raise PhysicalBlobArtifactSpoolError(f"{label} is unsafe")
        chunks: list[bytes] = []
        total = 0
        initial = _fingerprint(metadata)
        while True:
            try:
                chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            except OSError as exc:
                raise PhysicalBlobArtifactSpoolError(f"{label} cannot be read safely") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise PhysicalBlobArtifactSpoolError(f"{label} exceeds its bounded size")
            chunks.append(chunk)
        try:
            final = os.fstat(descriptor)
        except OSError as exc:
            raise PhysicalBlobArtifactSpoolError(f"{label} cannot be inspected after read") from exc
        if _fingerprint(final) != initial:
            raise PhysicalBlobArtifactSpoolError(f"{label} changed during secure read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_existing_snapshot(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> None:
    raw = _read_secure_regular_bytes(
        path,
        label="physical blob retained snapshot",
        maximum_bytes=expected_bytes,
    )
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise PhysicalBlobArtifactSpoolError("physical blob retained snapshot was tampered")


def _open_exact_source_file(
    *,
    uploads_root: Path,
    expected_uploads_root_identity_sha256: str,
    source_relative_parts: tuple[str, ...],
    expected_bytes: int,
) -> tuple[int, tuple[int, int, int, int, int, int, int, int]]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalBlobArtifactSpoolError("platform lacks fail-closed secure source open")
    root_fd = -1
    directory_fd = -1
    source_fd = -1
    try:
        root_fd = os.open(uploads_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_metadata = os.fstat(root_fd)
        if (
            root_metadata.st_uid != 0
            or stat.S_IMODE(root_metadata.st_mode) != 0o700
            or not stat.S_ISDIR(root_metadata.st_mode)
        ):
            raise PhysicalBlobArtifactSpoolError("physical blob uploads root changed during open")
        if _uploads_root_identity(root_metadata) != expected_uploads_root_identity_sha256:
            raise PhysicalBlobArtifactSpoolError(
                "physical blob uploads root changed from its descriptor-bound identity"
            )
        directory_fd = root_fd
        for part in source_relative_parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            if directory_fd != root_fd:
                os.close(directory_fd)
            directory_fd = next_fd
            metadata = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise PhysicalBlobArtifactSpoolError("physical blob source parent directory is unsafe")
        source_fd = os.open(
            source_relative_parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != expected_bytes
        ):
            raise PhysicalBlobArtifactSpoolError(
                "physical blob source is not an exact protected finalized regular file"
            )
        return source_fd, _fingerprint(metadata)
    except PhysicalBlobArtifactSpoolError:
        if source_fd >= 0:
            os.close(source_fd)
        raise
    except OSError as exc:
        if source_fd >= 0:
            os.close(source_fd)
        raise PhysicalBlobArtifactSpoolError("physical blob source cannot be opened safely") from exc
    finally:
        if directory_fd >= 0 and directory_fd != root_fd:
            os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _copy_source_to_snapshot(
    *,
    config: _ConfigFacts,
    descriptor: _DescriptorFacts,
) -> Path:
    snapshot_directory = _secure_child_directory(
        config.spool_root, "snapshots", descriptor.declared_content_sha256[:2]
    )
    snapshot_path = snapshot_directory / f"{descriptor.declared_content_sha256}.blob"
    try:
        existing = os.lstat(snapshot_path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob retained snapshot cannot be inspected") from exc
    if existing is not None:
        source_fd, source_fingerprint = _open_exact_source_file(
            uploads_root=config.uploads_root,
            expected_uploads_root_identity_sha256=config.uploads_root_identity_sha256,
            source_relative_parts=descriptor.source_relative_parts,
            expected_bytes=descriptor.declared_content_bytes,
        )
        try:
            source_sha256, source_bytes = _hash_open_file(
                source_fd,
                label="physical blob source",
                expected_fingerprint=source_fingerprint,
                maximum_bytes=descriptor.declared_content_bytes,
            )
        finally:
            os.close(source_fd)
        if (
            source_sha256 != descriptor.declared_content_sha256
            or source_bytes != descriptor.declared_content_bytes
        ):
            raise PhysicalBlobArtifactSpoolError(
                "physical blob source does not match its frozen declared hash and size"
            )
        _verify_existing_snapshot(
            snapshot_path,
            expected_sha256=descriptor.declared_content_sha256,
            expected_bytes=descriptor.declared_content_bytes,
        )
        return snapshot_path

    temporary_directory = _secure_child_directory(config.spool_root, "tmp")
    temporary_path = temporary_directory / (
        f".{descriptor.declared_content_sha256}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    )
    source_fd, source_before = _open_exact_source_file(
        uploads_root=config.uploads_root,
        expected_uploads_root_identity_sha256=config.uploads_root_identity_sha256,
        source_relative_parts=descriptor.source_relative_parts,
        expected_bytes=descriptor.declared_content_bytes,
    )
    output_fd = -1
    try:
        output_fd = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            try:
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
            except OSError as exc:
                raise PhysicalBlobArtifactSpoolError("physical blob source cannot be read safely") from exc
            if not chunk:
                break
            copied += len(chunk)
            if copied > descriptor.declared_content_bytes:
                raise PhysicalBlobArtifactSpoolError("physical blob source exceeds its declared size")
            digest.update(chunk)
            _write_all(output_fd, chunk)
        os.fsync(output_fd)
        if _fingerprint(os.fstat(source_fd)) != source_before:
            raise PhysicalBlobArtifactSpoolError("physical blob source changed during immutable snapshot")
        if copied != descriptor.declared_content_bytes or digest.hexdigest() != descriptor.declared_content_sha256:
            raise PhysicalBlobArtifactSpoolError(
                "physical blob source does not match its frozen declared hash and size"
            )
    except Exception:
        if output_fd >= 0:
            os.close(output_fd)
            output_fd = -1
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        os.close(source_fd)

    try:
        os.link(temporary_path, snapshot_path, follow_symlinks=False)
    except FileExistsError:
        temporary_path.unlink(missing_ok=True)
        _verify_existing_snapshot(
            snapshot_path,
            expected_sha256=descriptor.declared_content_sha256,
            expected_bytes=descriptor.declared_content_bytes,
        )
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PhysicalBlobArtifactSpoolError("physical blob snapshot cannot be finalized") from exc
    else:
        temporary_path.unlink(missing_ok=True)
    _fsync_directory(snapshot_directory)
    _verify_existing_snapshot(
        snapshot_path,
        expected_sha256=descriptor.declared_content_sha256,
        expected_bytes=descriptor.declared_content_bytes,
    )
    return snapshot_path


def _write_immutable_artifact(
    *,
    directory: Path,
    filename: str,
    content: bytes,
    label: str,
    maximum_bytes: int = _MAX_CANONICAL_RECORD_BYTES,
) -> Path:
    if not filename or "/" in filename or "\\" in filename:
        raise PhysicalBlobArtifactSpoolError(f"{label} filename is invalid")
    if not content or len(content) > maximum_bytes:
        raise PhysicalBlobArtifactSpoolError(f"{label} byte size is invalid")
    final_path = directory / filename
    expected_sha256 = hashlib.sha256(content).hexdigest()
    try:
        existing = os.lstat(final_path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError(f"{label} cannot be inspected") from exc
    if existing is not None:
        raw = _read_secure_regular_bytes(
            final_path,
            label=f"existing {label}",
            maximum_bytes=maximum_bytes,
        )
        if hashlib.sha256(raw).hexdigest() != expected_sha256 or raw != content:
            raise PhysicalBlobArtifactSpoolError(f"existing {label} was tampered or replayed")
        return final_path

    temporary_path = directory / f".{filename}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary_path, final_path, follow_symlinks=False)
        except FileExistsError:
            raw = _read_secure_regular_bytes(
                final_path,
                label=f"raced {label}",
                maximum_bytes=maximum_bytes,
            )
            temporary_path.unlink(missing_ok=True)
            if raw != content:
                raise PhysicalBlobArtifactSpoolError(f"raced {label} was tampered or replayed")
        else:
            temporary_path.unlink(missing_ok=True)
        _fsync_directory(directory)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return final_path


def _handoff_descriptor(
    *,
    binding: VerifiedPhysicalBlobArtifactBinding,
    descriptor: _DescriptorFacts,
) -> bytes:
    manifest = binding.manifest_binding
    term = binding.witnessed_term
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA,
            "kind": "finalized_database_visible_blob_local_handoff",
            "source_site": manifest.source_site,
            "destination_site": manifest.destination_site,
            "campaign_id": manifest.campaign_id,
            "release_sha": manifest.release_sha,
            "baseline_generation_id": manifest.baseline_generation_id,
            "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
            "baseline_wal_lsn": manifest.baseline_wal_lsn,
            "route_binding_sha256": binding.route_binding_sha256,
            "writer_term": {
                "holder_site": term.holder_site,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            "destination_age_recipient": manifest.destination_age_recipient,
            "uploads_root_identity_sha256": descriptor.uploads_root_identity_sha256,
            "source_record": {
                "record_id": descriptor.source_record_id,
                "database_visibility": "frozen_database_visible_finalized_v1",
                "finalization_state": "finalized",
                "temporary": False,
                "inflight": False,
            },
            "declared_content": {
                "sha256": descriptor.declared_content_sha256,
                "bytes": descriptor.declared_content_bytes,
            },
            "snapshot": {
                "sha256": descriptor.declared_content_sha256,
                "bytes": descriptor.declared_content_bytes,
            },
            "object_key": descriptor.object_key,
            "not_a_database_snapshot_consistency_proof": True,
            "not_a_blob_frontier_manifest": True,
            "not_a_remote_apply_proof": True,
            "not_a_strict_acknowledgement_proof": True,
        },
        label="physical blob handoff descriptor",
    )


_SOURCE_RECORD_INDEX_FIELDS = {
    "schema",
    "kind",
    "source_record_id",
    "handoff_descriptor_sha256",
    "snapshot_sha256",
    "snapshot_bytes",
    "object_key",
    "uploads_root_identity_sha256",
}


def _source_record_index(
    *,
    descriptor: _DescriptorFacts,
    handoff_descriptor_sha256: str,
) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA,
            "kind": "finalized_blob_source_record_index",
            "source_record_id": descriptor.source_record_id,
            "handoff_descriptor_sha256": handoff_descriptor_sha256,
            "snapshot_sha256": descriptor.declared_content_sha256,
            "snapshot_bytes": descriptor.declared_content_bytes,
            "object_key": descriptor.object_key,
            "uploads_root_identity_sha256": descriptor.uploads_root_identity_sha256,
        },
        label="physical blob source-record index",
    )


def _source_record_index_path(directory: Path, *, source_record_id: str) -> Path:
    return directory / f"{hashlib.sha256(source_record_id.encode('utf-8')).hexdigest()}.json"


def _check_existing_source_record_index(
    *,
    directory: Path,
    descriptor: _DescriptorFacts,
    expected: bytes,
) -> None:
    path = _source_record_index_path(directory, source_record_id=descriptor.source_record_id)
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob source-record index cannot be inspected") from exc
    raw = _read_secure_regular_bytes(
        path,
        label="physical blob source-record index",
        maximum_bytes=_MAX_CANONICAL_RECORD_BYTES,
    )
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob source-record index is invalid JSON") from exc
    if (
        not isinstance(parsed, dict)
        or set(parsed) != _SOURCE_RECORD_INDEX_FIELDS
        or _canonical_json_bytes(parsed, label="physical blob source-record index") != raw
    ):
        raise PhysicalBlobArtifactSpoolError("physical blob source-record index is invalid")
    if raw != expected:
        raise PhysicalBlobArtifactSpoolError(
            "physical blob source record was replayed with different frozen descriptor facts"
        )


def _inventory_plaintext(
    *,
    binding: VerifiedPhysicalBlobArtifactBinding,
    shard_ordinal: int,
    artifacts: Sequence[PhysicalBlobArtifactHandoffResult],
    uploads_root_identity_sha256: str,
) -> bytes:
    if not artifacts or len(artifacts) > MAX_BLOBS_PER_INVENTORY_SHARD:
        raise PhysicalBlobArtifactSpoolError("physical blob inventory shard entry count is invalid")
    manifest = binding.manifest_binding
    term = binding.witnessed_term
    entries = [
        {
            "ordinal": ordinal,
            "source_record_id": item.source_record_id,
            "content_sha256": item.snapshot_sha256,
            "content_bytes": item.snapshot_bytes,
            "handoff_descriptor_sha256": item.handoff_descriptor_sha256,
            "object_key": item.object_key,
        }
        for ordinal, item in enumerate(artifacts, start=1)
    ]
    if len({item["source_record_id"] for item in entries}) != len(entries):
        raise PhysicalBlobArtifactSpoolError("physical blob inventory shard replays a source record")
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA,
            "kind": "finalized_database_visible_blob_inventory_shard_plaintext",
            "source_site": manifest.source_site,
            "destination_site": manifest.destination_site,
            "campaign_id": manifest.campaign_id,
            "release_sha": manifest.release_sha,
            "baseline_generation_id": manifest.baseline_generation_id,
            "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
            "baseline_wal_lsn": manifest.baseline_wal_lsn,
            "route_binding_sha256": binding.route_binding_sha256,
            "writer_term": {
                "holder_site": term.holder_site,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            "destination_age_recipient": manifest.destination_age_recipient,
            "uploads_root_identity_sha256": uploads_root_identity_sha256,
            "shard_ordinal": shard_ordinal,
            "entries": entries,
            "not_a_database_snapshot_consistency_proof": True,
            "not_a_blob_frontier_manifest": True,
            "not_a_remote_apply_proof": True,
            "not_a_strict_acknowledgement_proof": True,
        },
        label="physical blob inventory shard plaintext",
    )


def _inventory_index(*, shard_ordinal: int, plaintext_sha256: str, entry_count: int) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_BLOB_ARTIFACT_SPOOL_DESCRIPTOR_SCHEMA,
            "kind": "physical_blob_inventory_shard_index",
            "shard_ordinal": shard_ordinal,
            "plaintext_sha256": plaintext_sha256,
            "entry_count": entry_count,
        },
        label="physical blob inventory shard index",
    )


def _write_inventory_shard(
    *,
    spool_root: Path,
    shard_ordinal: int,
    plaintext: bytes,
    entry_count: int,
) -> PhysicalBlobInventoryShardPlaintext:
    if len(plaintext) > MAX_INVENTORY_SHARD_PLAINTEXT_BYTES:
        raise PhysicalBlobArtifactSpoolError("physical blob inventory shard plaintext exceeds its bound")
    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
    inventory_directory = _secure_child_directory(spool_root, "inventory")
    index_directory = _secure_child_directory(spool_root, "inventory-index")
    index = _inventory_index(
        shard_ordinal=shard_ordinal,
        plaintext_sha256=plaintext_sha256,
        entry_count=entry_count,
    )
    index_path = index_directory / f"{shard_ordinal:08d}.json"
    try:
        existing = os.lstat(index_path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise PhysicalBlobArtifactSpoolError("physical blob inventory index cannot be inspected") from exc
    if existing is not None:
        raw = _read_secure_regular_bytes(
            index_path,
            label="physical blob inventory index",
            maximum_bytes=_MAX_CANONICAL_RECORD_BYTES,
        )
        if raw != index:
            raise PhysicalBlobArtifactSpoolError(
                "physical blob inventory shard ordinal was replayed with different plaintext"
            )
    _write_immutable_artifact(
        directory=index_directory,
        filename=f"{shard_ordinal:08d}.json",
        content=index,
        label="physical blob inventory shard index",
    )
    filename = f"shard-{shard_ordinal:08d}-{plaintext_sha256}.json"
    plaintext_path = _write_immutable_artifact(
        directory=inventory_directory,
        filename=filename,
        content=plaintext,
        label="physical blob inventory shard plaintext",
        maximum_bytes=MAX_INVENTORY_SHARD_PLAINTEXT_BYTES,
    )
    return PhysicalBlobInventoryShardPlaintext(
        shard_ordinal=shard_ordinal,
        plaintext_path=plaintext_path,
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=len(plaintext),
        entry_count=entry_count,
    )


def spool_finalized_physical_blob_artifacts(
    *,
    config: PhysicalBlobArtifactSpoolConfig,
    verified_binding: VerifiedPhysicalBlobArtifactBinding,
    frozen_descriptors: Sequence[PhysicalBlobFrozenDescriptor],
    inventory_shard_ordinal: int,
    now: datetime,
    term_recheck_clock: Callable[[], datetime] | None,
) -> PhysicalBlobArtifactSpoolResult:
    """Snapshot finalized frozen blob records and build one unsigned inventory shard.

    The caller supplies all database-visible facts; this function never opens
    a database.  A live Witness term is revalidated before result publication,
    but this is still only local archive preparation, not a remote guarantee.
    """

    if term_recheck_clock is None or not callable(term_recheck_clock):
        raise PhysicalBlobArtifactSpoolError("physical blob term recheck clock is required")
    observed_now = _utc(now, label="physical blob spool clock")
    binding = require_verified_physical_blob_artifact_binding(
        verified_binding, now=observed_now
    )
    normalized_config = _normalise_config(config)
    shard_ordinal = _positive_int(
        inventory_shard_ordinal,
        label="physical blob inventory shard ordinal",
        maximum=2**63 - 1,
    )
    if isinstance(frozen_descriptors, (str, bytes)) or not isinstance(frozen_descriptors, Sequence):
        raise PhysicalBlobArtifactSpoolError("physical blob frozen descriptors are invalid")
    if not frozen_descriptors or len(frozen_descriptors) > MAX_BLOBS_PER_INVENTORY_SHARD:
        raise PhysicalBlobArtifactSpoolError("physical blob frozen descriptor count is invalid")
    normalized_descriptors = tuple(
        _normalise_descriptor(item, binding=binding, config=normalized_config)
        for item in frozen_descriptors
    )
    ordered_descriptors = tuple(
        sorted(normalized_descriptors, key=lambda item: item.source_record_id)
    )
    if len({item.source_record_id for item in ordered_descriptors}) != len(ordered_descriptors):
        raise PhysicalBlobArtifactSpoolError("physical blob frozen descriptors replay a source record")

    handoff_directory = _secure_child_directory(normalized_config.spool_root, "handoffs")
    source_index_directory = _secure_child_directory(
        normalized_config.spool_root, "source-record-index"
    )
    prepared: list[tuple[_DescriptorFacts, bytes, str, bytes]] = []
    for descriptor in ordered_descriptors:
        handoff = _handoff_descriptor(binding=binding, descriptor=descriptor)
        handoff_sha256 = hashlib.sha256(handoff).hexdigest()
        source_index = _source_record_index(
            descriptor=descriptor,
            handoff_descriptor_sha256=handoff_sha256,
        )
        _check_existing_source_record_index(
            directory=source_index_directory,
            descriptor=descriptor,
            expected=source_index,
        )
        prepared.append((descriptor, handoff, handoff_sha256, source_index))

    artifacts: list[PhysicalBlobArtifactHandoffResult] = []
    for descriptor, handoff, handoff_sha256, source_index in prepared:
        snapshot_path = _copy_source_to_snapshot(
            config=normalized_config,
            descriptor=descriptor,
        )
        _write_immutable_artifact(
            directory=source_index_directory,
            filename=_source_record_index_path(
                source_index_directory, source_record_id=descriptor.source_record_id
            ).name,
            content=source_index,
            label="physical blob source-record index",
        )
        handoff_path = _write_immutable_artifact(
            directory=handoff_directory,
            filename=f"{handoff_sha256}.json",
            content=handoff,
            label="physical blob handoff descriptor",
        )
        artifacts.append(
            PhysicalBlobArtifactHandoffResult(
                source_record_id=descriptor.source_record_id,
                snapshot_path=snapshot_path,
                snapshot_sha256=descriptor.declared_content_sha256,
                snapshot_bytes=descriptor.declared_content_bytes,
                handoff_descriptor_path=handoff_path,
                handoff_descriptor_sha256=handoff_sha256,
                object_key=descriptor.object_key,
            )
        )

    completion_now = _utc(term_recheck_clock(), label="physical blob completion clock")
    if completion_now < observed_now:
        raise PhysicalBlobArtifactSpoolError("physical blob completion clock moved backwards")
    require_verified_physical_blob_artifact_binding(binding, now=completion_now)
    inventory_plaintext = _inventory_plaintext(
        binding=binding,
        shard_ordinal=shard_ordinal,
        artifacts=artifacts,
        uploads_root_identity_sha256=normalized_config.uploads_root_identity_sha256,
    )
    inventory_shard = _write_inventory_shard(
        spool_root=normalized_config.spool_root,
        shard_ordinal=shard_ordinal,
        plaintext=inventory_plaintext,
        entry_count=len(artifacts),
    )
    final_now = _utc(term_recheck_clock(), label="physical blob final completion clock")
    if final_now < completion_now:
        raise PhysicalBlobArtifactSpoolError("physical blob completion clock moved backwards")
    require_verified_physical_blob_artifact_binding(binding, now=final_now)
    return PhysicalBlobArtifactSpoolResult(
        artifacts=tuple(artifacts),
        inventory_shard=inventory_shard,
    )
