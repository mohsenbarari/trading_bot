"""Local-only, fail-closed handoff producer for physical PostgreSQL WAL.

This module is designed to sit behind a future PostgreSQL ``archive_command``
adapter.  It intentionally implements neither that adapter nor any S3, HTTP,
SSH, Docker, database, encryption, or remote transport action.  It only:

1. copies one completed canonical WAL segment from one fixed local mount into
   an immutable local spool snapshot;
2. builds a canonical, term/release/baseline/route-bound handoff descriptor;
3. invokes an *injected* uploader interface; and
4. records a canonical local upload manifest only after a validated receipt.

Archive completion is recovery evidence, never a synchronous remote-apply
acknowledgement.  The result is not writer, promotion, or Object Storage
authority.  A missing uploader, expired/forged Witness term, malformed
manifest binding, unsafe source, tampered snapshot, or malformed upload
receipt fails closed and never returns success.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Callable, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
    PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
)


__all__ = (
    "DEFAULT_WAL_SEGMENT_SIZE_BYTES",
    "MAX_WAL_SEGMENT_SIZE_BYTES",
    "PHYSICAL_WAL_ARCHIVE_SPOOL_DEFAULT_ENABLED",
    "PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA",
    "PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA",
    "PhysicalWalArchiveManifestBinding",
    "PhysicalWalArchiveSpoolConfig",
    "PhysicalWalArchiveSpoolError",
    "PhysicalWalArchiveSpoolResult",
    "PhysicalWalArchiveUploadReceipt",
    "PhysicalWalArchiveUploader",
    "VerifiedPhysicalWalArchiveBinding",
    "archive_physical_wal_segment",
    "authorize_physical_wal_archive_binding",
    "derive_physical_wal_archive_object_key",
    "parse_postgresql_wal_segment_name",
    "require_verified_physical_wal_archive_binding",
)


PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-wal-archive-spool-descriptor-v1"
)
PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA = (
    "gold-trade-physical-wal-archive-spool-manifest-v1"
)
PHYSICAL_WAL_ARCHIVE_SPOOL_DEFAULT_ENABLED = False

DEFAULT_WAL_SEGMENT_SIZE_BYTES = PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES[0]
MAX_WAL_SEGMENT_SIZE_BYTES = max(PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES)
_COPY_CHUNK_BYTES = 256 * 1024

_WAL_SEGMENT_NAME_RE = re.compile(r"^[0-9A-F]{24}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,511}$", re.ASCII)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_VERIFIED_BINDING_CAPABILITY = object()
_MAX_LOCAL_MANIFEST_BYTES = 64 * 1024


class PhysicalWalArchiveSpoolError(ValueError):
    """The local WAL capture or injected handoff cannot be trusted."""


@dataclass(frozen=True)
class PhysicalWalArchiveManifestBinding:
    """Non-secret fields expected from the approved physical-WAL manifest.

    This intentionally does not depend on a future Object Storage manifest
    module.  Its hash fields are opaque pins.  The factory below binds them to
    a *live opaque* Writer-Witness term before the local producer accepts it.
    """

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
    database_system_identifier: str
    timeline_id: int
    destination_age_recipient: str
    object_storage_namespace: str = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


@dataclass(frozen=True)
class PhysicalWalArchiveSpoolConfig:
    """Fixed local paths and bounded WAL segment size for one producer."""

    wal_source_root: Path
    spool_root: Path
    wal_segment_size_bytes: int = DEFAULT_WAL_SEGMENT_SIZE_BYTES


@dataclass(frozen=True)
class PhysicalWalArchiveUploadReceipt:
    """Non-secret result returned by a future injected uploader.

    The uploader may encrypt and publish elsewhere, but must return only this
    bounded descriptor.  It cannot redirect a producer to an arbitrary key or
    claim a different local handoff descriptor.
    """

    descriptor_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    encryption: str
    age_recipient: str
    immutability: str


class PhysicalWalArchiveUploader(Protocol):
    """Narrow injected handoff; no default network implementation exists."""

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        """Upload exactly one immutable local snapshot or raise an error."""


@dataclass(frozen=True)
class VerifiedPhysicalWalArchiveBinding:
    """Opaque live-term-bound configuration for the local producer."""

    manifest_binding: PhysicalWalArchiveManifestBinding
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    route_binding_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalArchiveSpoolResult:
    """Successful local snapshot + validated injected-uploader handoff result."""

    snapshot_path: Path
    snapshot_sha256: str
    snapshot_bytes: int
    handoff_descriptor_path: Path
    handoff_descriptor_sha256: str
    upload_manifest_path: Path
    upload_manifest_sha256: str
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class _BindingFacts:
    binding: PhysicalWalArchiveManifestBinding
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    route_binding_sha256: str


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
        raise PhysicalWalArchiveSpoolError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise PhysicalWalArchiveSpoolError("physical WAL local manifest contains duplicate JSON fields")
        result[key] = item
    return result


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalWalArchiveSpoolError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalWalArchiveSpoolError(f"{label} is invalid")
    return value


def _require_safe_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise PhysicalWalArchiveSpoolError(f"{label} is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalWalArchiveSpoolError(f"{label} contains a URL or secret-shaped value")
    return value


def _require_object_storage_namespace(
    value: object,
    *,
    source_site: str,
    destination_site: str,
) -> str:
    if type(value) is not str or value not in PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES:
        raise PhysicalWalArchiveSpoolError("physical WAL Object Storage namespace is invalid")
    expected = (
        PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        if (source_site, destination_site) == ("webapp_fi", "webapp_ir")
        else PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    )
    if value != expected:
        raise PhysicalWalArchiveSpoolError(
            "physical WAL Object Storage namespace does not match the pinned route"
        )
    return value


def _require_lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalWalArchiveSpoolError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _lsn_text(value: int) -> str:
    if value < 0 or value > (2**64 - 1):
        raise PhysicalWalArchiveSpoolError("WAL LSN is invalid")
    return f"{value >> 32:X}/{value & 0xFFFFFFFF:X}"


def _normalise_manifest_binding(value: object) -> PhysicalWalArchiveManifestBinding:
    if type(value) is not PhysicalWalArchiveManifestBinding:
        raise PhysicalWalArchiveSpoolError("physical WAL archive manifest binding is invalid")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        raise PhysicalWalArchiveSpoolError(
            "physical WAL archive binding must name distinct WA source and destination sites"
        )
    if not isinstance(value.campaign_id, str) or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        raise PhysicalWalArchiveSpoolError("physical WAL archive campaign is invalid")
    if not isinstance(value.release_sha, str) or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        raise PhysicalWalArchiveSpoolError("physical WAL archive release is invalid")
    if (
        not isinstance(value.stream_generation_id, str)
        or STREAM_GENERATION_ID_RE.fullmatch(value.stream_generation_id) is None
    ):
        raise PhysicalWalArchiveSpoolError("physical WAL archive stream generation is invalid")
    baseline_generation = value.baseline_generation_id
    if (
        not isinstance(baseline_generation, str)
        or STREAM_GENERATION_ID_RE.fullmatch(baseline_generation) is None
    ):
        raise PhysicalWalArchiveSpoolError("physical WAL archive baseline generation is invalid")
    baseline_lsn, baseline_lsn_value = _require_lsn(
        value.baseline_wal_lsn, label="physical WAL baseline LSN"
    )
    chain_start_lsn, chain_start_value = _require_lsn(
        value.wal_chain_start_lsn, label="physical WAL chain start LSN"
    )
    system_identifier = value.database_system_identifier
    if not isinstance(system_identifier, str) or _SYSTEM_IDENTIFIER_RE.fullmatch(system_identifier) is None:
        raise PhysicalWalArchiveSpoolError("physical WAL database system identifier is invalid")
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        raise PhysicalWalArchiveSpoolError("physical WAL timeline ID is invalid")
    segment_size = DEFAULT_WAL_SEGMENT_SIZE_BYTES
    if (
        chain_start_value % segment_size
        or chain_start_value > baseline_lsn_value
        or baseline_lsn_value >= chain_start_value + segment_size
    ):
        raise PhysicalWalArchiveSpoolError(
            "physical WAL chain start does not cover the baseline LSN on a segment boundary"
        )
    destination_age_recipient = _require_age_recipient(value.destination_age_recipient)
    return PhysicalWalArchiveManifestBinding(
        source_site=value.source_site,
        destination_site=value.destination_site,
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        stream_generation_id=value.stream_generation_id,
        baseline_generation_id=baseline_generation,
        baseline_manifest_sha256=_require_sha256(
            value.baseline_manifest_sha256, label="physical WAL baseline manifest SHA-256"
        ),
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=chain_start_lsn,
        archive_manifest_sha256=_require_sha256(
            value.archive_manifest_sha256, label="physical WAL archive manifest SHA-256"
        ),
        database_system_identifier=system_identifier,
        timeline_id=value.timeline_id,
        destination_age_recipient=destination_age_recipient,
        object_storage_namespace=_require_object_storage_namespace(
            value.object_storage_namespace,
            source_site=value.source_site,
            destination_site=value.destination_site,
        ),
    )


def _route_binding_sha256(
    binding: PhysicalWalArchiveManifestBinding,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "source_site": binding.source_site,
                "destination_site": binding.destination_site,
                "campaign_id": binding.campaign_id,
                "release_sha": binding.release_sha,
                "stream_generation_id": binding.stream_generation_id,
                "baseline_generation_id": binding.baseline_generation_id,
                "baseline_manifest_sha256": binding.baseline_manifest_sha256,
                "baseline_wal_lsn": binding.baseline_wal_lsn,
                "wal_chain_start_lsn": binding.wal_chain_start_lsn,
                "archive_manifest_sha256": binding.archive_manifest_sha256,
                "database_system_identifier": binding.database_system_identifier,
                "timeline_id": binding.timeline_id,
                "destination_age_recipient": binding.destination_age_recipient,
                "object_storage_namespace": binding.object_storage_namespace,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            label="physical WAL archive route binding",
        )
    ).hexdigest()


def _binding_facts(
    *,
    manifest_binding: object,
    witnessed_term: object,
    now: datetime,
) -> _BindingFacts:
    normalized_binding = _normalise_manifest_binding(manifest_binding)
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(
            witnessed_term, now=now
        )
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalWalArchiveSpoolError(
            "physical WAL archive Witness term is not live and verified"
        ) from exc
    if term.holder_site != normalized_binding.source_site:
        raise PhysicalWalArchiveSpoolError(
            "physical WAL archive source does not hold the live Witness term"
        )
    return _BindingFacts(
        binding=normalized_binding,
        term=term,
        route_binding_sha256=_route_binding_sha256(normalized_binding, term),
    )


def authorize_physical_wal_archive_binding(
    *,
    manifest_binding: PhysicalWalArchiveManifestBinding,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> VerifiedPhysicalWalArchiveBinding:
    """Bind route pins to a live Witness term held by that route's source.

    Normal operation is FI→IR.  After a fenced IR promotion, the same
    constrained Object-Storage-only primitive is deliberately available for
    IR→FI failback.  The source/destination pair remains closed to the two
    named application sites, must be distinct, and the live term holder is
    always required to equal the selected source.
    """

    observed_now = _utc(now, label="physical WAL archive binding clock")
    facts = _binding_facts(
        manifest_binding=manifest_binding,
        witnessed_term=witnessed_term,
        now=observed_now,
    )
    result = VerifiedPhysicalWalArchiveBinding(
        manifest_binding=facts.binding,
        witnessed_term=facts.term,
        route_binding_sha256=facts.route_binding_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BINDING_CAPABILITY)
    return result


def require_verified_physical_wal_archive_binding(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalArchiveBinding:
    """Revalidate the binding and live term before every local handoff."""

    if (
        type(value) is not VerifiedPhysicalWalArchiveBinding
        or value._capability is not _VERIFIED_BINDING_CAPABILITY
    ):
        raise PhysicalWalArchiveSpoolError("physical WAL archive binding is not authorized")
    observed_now = _utc(now, label="physical WAL archive handoff clock")
    facts = _binding_facts(
        manifest_binding=value.manifest_binding,
        witnessed_term=value.witnessed_term,
        now=observed_now,
    )
    if (
        facts.binding != value.manifest_binding
        or facts.term != value.witnessed_term
        or facts.route_binding_sha256 != value.route_binding_sha256
    ):
        raise PhysicalWalArchiveSpoolError("physical WAL archive binding was tampered")
    return value


def _validate_wal_segment_size(value: object) -> int:
    if type(value) is not int or value not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        raise PhysicalWalArchiveSpoolError("physical WAL segment size is invalid")
    return value


def parse_postgresql_wal_segment_name(
    segment_name: object,
    *,
    wal_segment_size_bytes: int = DEFAULT_WAL_SEGMENT_SIZE_BYTES,
) -> tuple[int, int, str, str, int]:
    """Return timeline, ordinal, canonical start/end LSN, and exact name.

    The input is a basename only: no slash, relative path, backup label, or
    arbitrary PostgreSQL archive-command placeholder is accepted.
    """

    segment_size = _validate_wal_segment_size(wal_segment_size_bytes)
    if not isinstance(segment_name, str) or _WAL_SEGMENT_NAME_RE.fullmatch(segment_name) is None:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment name is invalid")
    timeline = int(segment_name[0:8], 16)
    log = int(segment_name[8:16], 16)
    segment = int(segment_name[16:24], 16)
    if timeline < 1:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment timeline is invalid")
    segments_per_log = (1 << 32) // segment_size
    if segment >= segments_per_log:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment ordinal is invalid")
    start = (log << 32) + (segment * segment_size)
    end = start + segment_size
    if end > 2**64 - 1:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment LSN overflows")
    ordinal = start // segment_size
    return timeline, ordinal, _lsn_text(start), _lsn_text(end), segment_size


def _secure_root(path: object, *, label: str, spool_root: bool) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise PhysicalWalArchiveSpoolError(f"{label} must be an absolute Path")
    try:
        absolute = path.absolute()
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError as exc:
        raise PhysicalWalArchiveSpoolError(f"{label} is unavailable") from exc
    if absolute != resolved or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PhysicalWalArchiveSpoolError(f"{label} is not a non-symlink directory")
    effective_uid = os.geteuid()
    mode = stat.S_IMODE(metadata.st_mode)
    if spool_root:
        if metadata.st_uid != effective_uid:
            raise PhysicalWalArchiveSpoolError(f"{label} is not owned by the archive user")
        if mode != 0o700:
            raise PhysicalWalArchiveSpoolError(f"{label} must have mode 0700")
    else:
        if metadata.st_uid not in {effective_uid, 0}:
            raise PhysicalWalArchiveSpoolError(
                f"{label} is not owned by the archive user or root"
            )
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PhysicalWalArchiveSpoolError(f"{label} is group/world writable")
    return resolved


def _normalise_config(value: object) -> PhysicalWalArchiveSpoolConfig:
    if type(value) is not PhysicalWalArchiveSpoolConfig:
        raise PhysicalWalArchiveSpoolError("physical WAL archive spool configuration is invalid")
    source_root = _secure_root(
        value.wal_source_root, label="physical WAL source root", spool_root=False
    )
    spool_root = _secure_root(
        value.spool_root, label="physical WAL spool root", spool_root=True
    )
    try:
        source_root.relative_to(spool_root)
        overlaps = True
    except ValueError:
        try:
            spool_root.relative_to(source_root)
            overlaps = True
        except ValueError:
            overlaps = False
    if overlaps:
        raise PhysicalWalArchiveSpoolError("physical WAL source and spool roots overlap")
    return PhysicalWalArchiveSpoolConfig(
        wal_source_root=source_root,
        spool_root=spool_root,
        wal_segment_size_bytes=_validate_wal_segment_size(value.wal_segment_size_bytes),
    )


def _secure_child_directory(root: Path, *parts: str) -> Path:
    candidate = root
    for part in parts:
        if not isinstance(part, str) or not part or "/" in part or "\\" in part or part in {".", ".."}:
            raise PhysicalWalArchiveSpoolError("physical WAL spool child path is invalid")
        candidate = candidate / part
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            metadata = os.lstat(candidate)
        except OSError as exc:
            raise PhysicalWalArchiveSpoolError("physical WAL spool child path is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PhysicalWalArchiveSpoolError("physical WAL spool child path is unsafe")
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise PhysicalWalArchiveSpoolError(
                "physical WAL spool child path ownership or mode is unsafe"
            )
    return candidate


def _open_regular_readonly(path: Path, *, label: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalWalArchiveSpoolError("platform lacks fail-closed non-symlink open")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PhysicalWalArchiveSpoolError(f"{label} cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PhysicalWalArchiveSpoolError(f"{label} is not a single-link regular file")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _source_segment_fd(
    *,
    source_root: Path,
    segment_name: str,
    expected_bytes: int,
) -> tuple[int, tuple[int, int, int, int, int, int, int]]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalWalArchiveSpoolError("platform lacks fail-closed directory open")
    root_fd = -1
    segment_fd = -1
    try:
        try:
            root_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            segment_fd = os.open(segment_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        except OSError as exc:
            raise PhysicalWalArchiveSpoolError(
                "PostgreSQL WAL source cannot be opened safely"
            ) from exc
        metadata = os.fstat(segment_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != expected_bytes
        ):
            raise PhysicalWalArchiveSpoolError(
                "PostgreSQL WAL source is not a bounded single-link regular segment"
            )
        fingerprint = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_mode,
            metadata.st_nlink,
        )
        return segment_fd, fingerprint
    except Exception:
        if segment_fd >= 0:
            os.close(segment_fd)
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise PhysicalWalArchiveSpoolError("physical WAL spool write failed")
        offset += written


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        raise PhysicalWalArchiveSpoolError("platform lacks durable directory fsync")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_hash_and_size(path: Path, *, label: str) -> tuple[str, int]:
    descriptor = _open_regular_readonly(path, label=label)
    try:
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _read_regular_bytes(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    descriptor = _open_regular_readonly(path, label=label)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise PhysicalWalArchiveSpoolError(f"{label} exceeds its bounded size")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _capture_immutable_snapshot(
    *,
    config: PhysicalWalArchiveSpoolConfig,
    segment_name: str,
) -> tuple[Path, str, int]:
    segment_fd, source_before = _source_segment_fd(
        source_root=config.wal_source_root,
        segment_name=segment_name,
        expected_bytes=config.wal_segment_size_bytes,
    )
    temporary_directory = _secure_child_directory(config.spool_root, "tmp")
    temporary_path = temporary_directory / (
        f".{segment_name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
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
            chunk = os.read(segment_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > config.wal_segment_size_bytes:
                raise PhysicalWalArchiveSpoolError("PostgreSQL WAL source exceeds its bounded size")
            digest.update(chunk)
            _write_all(output_fd, chunk)
        os.fsync(output_fd)
        after = os.fstat(segment_fd)
        source_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
        )
        if source_after != source_before or copied != config.wal_segment_size_bytes:
            raise PhysicalWalArchiveSpoolError(
                "PostgreSQL WAL source changed during immutable snapshot capture"
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
        os.close(segment_fd)

    snapshot_sha256 = digest.hexdigest()
    snapshot_directory = _secure_child_directory(
        config.spool_root, "snapshots", snapshot_sha256[:2]
    )
    snapshot_path = snapshot_directory / f"{snapshot_sha256}.wal"
    try:
        existing_metadata = os.lstat(snapshot_path)
    except FileNotFoundError:
        try:
            # ``os.replace`` would silently overwrite a racing/tampered final
            # file.  A hard-link create is same-filesystem and no-overwrite.
            os.link(temporary_path, snapshot_path, follow_symlinks=False)
        except FileExistsError:
            existing_sha256, existing_size = _read_hash_and_size(
                snapshot_path, label="raced physical WAL snapshot"
            )
            temporary_path.unlink(missing_ok=True)
            if existing_sha256 != snapshot_sha256 or existing_size != copied:
                raise PhysicalWalArchiveSpoolError("raced physical WAL snapshot was tampered")
        else:
            temporary_path.unlink(missing_ok=True)
        _fsync_directory(snapshot_directory)
    else:
        if stat.S_ISLNK(existing_metadata.st_mode) or not stat.S_ISREG(existing_metadata.st_mode):
            temporary_path.unlink(missing_ok=True)
            raise PhysicalWalArchiveSpoolError("existing physical WAL snapshot is unsafe")
        existing_sha256, existing_size = _read_hash_and_size(
            snapshot_path, label="existing physical WAL snapshot"
        )
        temporary_path.unlink(missing_ok=True)
        if existing_sha256 != snapshot_sha256 or existing_size != copied:
            raise PhysicalWalArchiveSpoolError("existing physical WAL snapshot was tampered")
    return snapshot_path, snapshot_sha256, copied


def _write_immutable_artifact(
    *,
    directory: Path,
    filename: str,
    content: bytes,
    label: str,
) -> Path:
    if not filename or "/" in filename or "\\" in filename:
        raise PhysicalWalArchiveSpoolError(f"{label} filename is invalid")
    final_path = directory / filename
    expected_sha256 = hashlib.sha256(content).hexdigest()
    try:
        metadata = os.lstat(final_path)
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PhysicalWalArchiveSpoolError(f"existing {label} is unsafe")
        actual_sha256, actual_size = _read_hash_and_size(final_path, label=f"existing {label}")
        if actual_sha256 != expected_sha256 or actual_size != len(content):
            raise PhysicalWalArchiveSpoolError(f"existing {label} was tampered")
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
            # Never replace an existing immutable descriptor/manifest.  A
            # racing writer must already have produced byte-identical output.
            os.link(temporary_path, final_path, follow_symlinks=False)
        except FileExistsError:
            actual_sha256, actual_size = _read_hash_and_size(final_path, label=f"raced {label}")
            temporary_path.unlink(missing_ok=True)
            if actual_sha256 != expected_sha256 or actual_size != len(content):
                raise PhysicalWalArchiveSpoolError(f"raced {label} was tampered")
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


def _object_key_parts(binding: PhysicalWalArchiveManifestBinding) -> tuple[str, ...]:
    return (
        binding.object_storage_namespace,
        binding.campaign_id,
        binding.release_sha,
        binding.baseline_generation_id,
        f"{binding.source_site}-to-{binding.destination_site}",
        f"timeline-{binding.timeline_id:08X}",
    )


def derive_physical_wal_archive_object_key(
    *,
    binding: VerifiedPhysicalWalArchiveBinding,
    segment_name: str,
    snapshot_sha256: str,
    wal_segment_size_bytes: int,
    now: datetime,
) -> str:
    """Derive the only legal immutable object key for one local descriptor."""

    verified = require_verified_physical_wal_archive_binding(binding, now=now)
    _timeline, _ordinal, _start, _end, _size = parse_postgresql_wal_segment_name(
        segment_name,
        wal_segment_size_bytes=wal_segment_size_bytes,
    )
    if _timeline != verified.manifest_binding.timeline_id:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment timeline does not match binding")
    digest = _require_sha256(snapshot_sha256, label="physical WAL snapshot SHA-256")
    key = "/".join(
        (*_object_key_parts(verified.manifest_binding), segment_name, f"{digest}.age")
    )
    _validate_object_key(key, label="derived physical WAL Object key")
    return key


def _validate_object_key(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _OBJECT_KEY_RE.fullmatch(value) is None:
        raise PhysicalWalArchiveSpoolError(f"{label} is invalid")
    if (
        _URL_VALUE_RE.search(value)
        or _SENSITIVE_VALUE_RE.search(value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise PhysicalWalArchiveSpoolError(f"{label} contains an unsafe value")
    return value


def _validate_upload_receipt(
    value: object,
    *,
    descriptor_sha256: str,
    expected_object_key: str,
    expected_destination_age_recipient: str,
    maximum_plaintext_bytes: int,
) -> PhysicalWalArchiveUploadReceipt:
    if type(value) is not PhysicalWalArchiveUploadReceipt:
        raise PhysicalWalArchiveSpoolError("physical WAL uploader receipt is invalid")
    if _require_sha256(value.descriptor_sha256, label="uploader descriptor SHA-256") != descriptor_sha256:
        raise PhysicalWalArchiveSpoolError("physical WAL uploader receipt binds a different descriptor")
    if _validate_object_key(value.object_key, label="uploader Object key") != expected_object_key:
        raise PhysicalWalArchiveSpoolError("physical WAL uploader receipt redirects the Object key")
    if not isinstance(value.version_id, str) or VERSION_ID_RE.fullmatch(value.version_id) is None:
        raise PhysicalWalArchiveSpoolError("physical WAL uploader Object version is invalid")
    ciphertext_bytes = value.ciphertext_bytes
    if type(ciphertext_bytes) is not int or not 1 <= ciphertext_bytes <= maximum_plaintext_bytes + 1024 * 1024:
        raise PhysicalWalArchiveSpoolError("physical WAL uploader ciphertext byte count is invalid")
    if value.encryption != "age-v1":
        raise PhysicalWalArchiveSpoolError("physical WAL uploader encryption is invalid")
    if value.immutability != "versioned_create_only_readback_v1":
        raise PhysicalWalArchiveSpoolError("physical WAL uploader immutability is invalid")
    age_recipient = _require_age_recipient(value.age_recipient)
    if age_recipient != expected_destination_age_recipient:
        raise PhysicalWalArchiveSpoolError(
            "physical WAL uploader receipt binds a different destination age recipient"
        )
    return PhysicalWalArchiveUploadReceipt(
        descriptor_sha256=descriptor_sha256,
        object_key=expected_object_key,
        version_id=value.version_id,
        ciphertext_sha256=_require_sha256(
            value.ciphertext_sha256, label="uploader ciphertext SHA-256"
        ),
        ciphertext_bytes=ciphertext_bytes,
        encryption=PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        age_recipient=age_recipient,
        immutability=PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    )


def _require_age_recipient(value: object) -> str:
    if not isinstance(value, str) or AGE_RECIPIENT_RE.fullmatch(value) is None:
        raise PhysicalWalArchiveSpoolError("uploader age recipient is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalWalArchiveSpoolError("uploader age recipient contains an unsafe value")
    return value


def _handoff_descriptor(
    *,
    binding: VerifiedPhysicalWalArchiveBinding,
    timeline_id: int,
    ordinal: int,
    segment_name: str,
    start_lsn: str,
    end_lsn: str,
    wal_segment_size_bytes: int,
    snapshot_sha256: str,
    snapshot_bytes: int,
    object_key: str,
) -> bytes:
    manifest = binding.manifest_binding
    term = binding.witnessed_term
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA,
            "kind": "physical_wal_segment_handoff",
            "source_site": manifest.source_site,
            "destination_site": manifest.destination_site,
            "campaign_id": manifest.campaign_id,
            "release_sha": manifest.release_sha,
            "stream_generation_id": manifest.stream_generation_id,
            "baseline_generation_id": manifest.baseline_generation_id,
            "baseline_manifest_sha256": manifest.baseline_manifest_sha256,
            "baseline_wal_lsn": manifest.baseline_wal_lsn,
            "wal_chain_start_lsn": manifest.wal_chain_start_lsn,
            "archive_manifest_sha256": manifest.archive_manifest_sha256,
            "route_binding_sha256": binding.route_binding_sha256,
            "object_storage_namespace": manifest.object_storage_namespace,
            "database_system_identifier": manifest.database_system_identifier,
            "timeline_id": timeline_id,
            "wal_segment_size_bytes": wal_segment_size_bytes,
            "destination_age_recipient": manifest.destination_age_recipient,
            "writer_term": {
                "holder_site": term.holder_site,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            "wal_segment_name": segment_name,
            "segment_ordinal": ordinal,
            "start_lsn": start_lsn,
            "end_lsn": end_lsn,
            "snapshot_sha256": snapshot_sha256,
            "snapshot_bytes": snapshot_bytes,
            "object_key": object_key,
        },
        label="physical WAL archive handoff descriptor",
    )


def _upload_manifest(
    *,
    descriptor: Mapping[str, Any],
    handoff_descriptor_sha256: str,
    receipt: PhysicalWalArchiveUploadReceipt,
) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA,
            "kind": "physical_wal_segment_uploaded",
            "handoff_descriptor_sha256": handoff_descriptor_sha256,
            "source_site": descriptor["source_site"],
            "destination_site": descriptor["destination_site"],
            "campaign_id": descriptor["campaign_id"],
            "release_sha": descriptor["release_sha"],
            "stream_generation_id": descriptor["stream_generation_id"],
            "baseline_generation_id": descriptor["baseline_generation_id"],
            "baseline_manifest_sha256": descriptor["baseline_manifest_sha256"],
            "baseline_wal_lsn": descriptor["baseline_wal_lsn"],
            "wal_chain_start_lsn": descriptor["wal_chain_start_lsn"],
            "archive_manifest_sha256": descriptor["archive_manifest_sha256"],
            "route_binding_sha256": descriptor["route_binding_sha256"],
            "object_storage_namespace": descriptor["object_storage_namespace"],
            "database_system_identifier": descriptor["database_system_identifier"],
            "timeline_id": descriptor["timeline_id"],
            "wal_segment_size_bytes": descriptor["wal_segment_size_bytes"],
            "destination_age_recipient": descriptor["destination_age_recipient"],
            "writer_term": descriptor["writer_term"],
            "wal_segment_name": descriptor["wal_segment_name"],
            "segment_ordinal": descriptor["segment_ordinal"],
            "start_lsn": descriptor["start_lsn"],
            "end_lsn": descriptor["end_lsn"],
            "snapshot_sha256": descriptor["snapshot_sha256"],
            "snapshot_bytes": descriptor["snapshot_bytes"],
            "object": {
                "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
                "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
                "object_kind": "postgresql_wal_segment",
                "object_key": receipt.object_key,
                "version_id": receipt.version_id,
                "ciphertext_sha256": receipt.ciphertext_sha256,
                "ciphertext_bytes": receipt.ciphertext_bytes,
                "encryption": receipt.encryption,
                "age_recipient": receipt.age_recipient,
                "immutability": receipt.immutability,
            },
        },
        label="physical WAL archive upload manifest",
    )


_UPLOAD_MANIFEST_DESCRIPTOR_FIELDS = (
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
)


def _completed_result_if_present(
    *,
    manifest_path: Path,
    handoff_descriptor_path: Path,
    descriptor: Mapping[str, Any],
    handoff_descriptor_sha256: str,
    snapshot_path: Path,
    snapshot_sha256: str,
    snapshot_bytes: int,
    expected_destination_age_recipient: str,
) -> PhysicalWalArchiveSpoolResult | None:
    try:
        metadata = os.lstat(manifest_path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest is unsafe")
    raw = _read_regular_bytes(
        manifest_path,
        label="existing physical WAL upload manifest",
        maximum_bytes=_MAX_LOCAL_MANIFEST_BYTES,
    )
    try:
        payload = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
    except PhysicalWalArchiveSpoolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest is invalid JSON") from exc
    if not isinstance(payload, dict) or _canonical_json_bytes(
        payload, label="existing physical WAL upload manifest"
    ) != raw:
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest is not canonical")
    expected_fields = {
        "schema",
        "kind",
        "handoff_descriptor_sha256",
        "object",
        *_UPLOAD_MANIFEST_DESCRIPTOR_FIELDS,
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema") != PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA
        or payload.get("kind") != "physical_wal_segment_uploaded"
        or payload.get("handoff_descriptor_sha256") != handoff_descriptor_sha256
    ):
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest is unbound")
    if any(payload[field_name] != descriptor[field_name] for field_name in _UPLOAD_MANIFEST_DESCRIPTOR_FIELDS):
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest does not match handoff")
    object_payload = payload.get("object")
    if not isinstance(object_payload, Mapping):
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest Object is invalid")
    try:
        receipt = PhysicalWalArchiveUploadReceipt(
            descriptor_sha256=handoff_descriptor_sha256,
            object_key=object_payload["object_key"],
            version_id=object_payload["version_id"],
            ciphertext_sha256=object_payload["ciphertext_sha256"],
            ciphertext_bytes=object_payload["ciphertext_bytes"],
            encryption=object_payload["encryption"],
            age_recipient=object_payload["age_recipient"],
            immutability=object_payload["immutability"],
        )
    except KeyError as exc:
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest Object is invalid") from exc
    if set(object_payload) != {
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
    }:
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest Object fields are invalid")
    if (
        object_payload["schema"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA
        or object_payload["version"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION
        or object_payload["object_kind"] != "postgresql_wal_segment"
    ):
        raise PhysicalWalArchiveSpoolError("existing physical WAL upload manifest Object schema is invalid")
    receipt = _validate_upload_receipt(
        receipt,
        descriptor_sha256=handoff_descriptor_sha256,
        expected_object_key=descriptor["object_key"],
        expected_destination_age_recipient=expected_destination_age_recipient,
        maximum_plaintext_bytes=snapshot_bytes,
    )
    actual_snapshot_sha256, actual_snapshot_bytes = _read_hash_and_size(
        snapshot_path, label="physical WAL snapshot for completed manifest"
    )
    if actual_snapshot_sha256 != snapshot_sha256 or actual_snapshot_bytes != snapshot_bytes:
        raise PhysicalWalArchiveSpoolError("physical WAL snapshot for completed manifest was tampered")
    return PhysicalWalArchiveSpoolResult(
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        handoff_descriptor_path=handoff_descriptor_path,
        handoff_descriptor_sha256=handoff_descriptor_sha256,
        upload_manifest_path=manifest_path,
        upload_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        object_key=receipt.object_key,
        object_version_id=receipt.version_id,
        ciphertext_sha256=receipt.ciphertext_sha256,
        ciphertext_bytes=receipt.ciphertext_bytes,
    )


def _recheck_live_binding(
    *,
    binding: VerifiedPhysicalWalArchiveBinding,
    initial_now: datetime,
    term_recheck_clock: Callable[[], datetime],
) -> VerifiedPhysicalWalArchiveBinding:
    completion_now = _utc(
        term_recheck_clock(), label="physical WAL archive completion clock"
    )
    if completion_now < initial_now:
        raise PhysicalWalArchiveSpoolError("physical WAL archive completion clock moved backwards")
    return require_verified_physical_wal_archive_binding(binding, now=completion_now)


def archive_physical_wal_segment(
    *,
    segment_name: str,
    config: PhysicalWalArchiveSpoolConfig,
    verified_binding: VerifiedPhysicalWalArchiveBinding,
    uploader: PhysicalWalArchiveUploader | None,
    now: datetime,
    term_recheck_clock: Callable[[], datetime] | None,
) -> PhysicalWalArchiveSpoolResult:
    """Capture, describe, and hand off one WAL segment through an injected uploader.

    The uploader is deliberately mandatory and has no default.  A failed
    handoff leaves the immutable snapshot and handoff descriptor on disk for a
    later explicit retry; it does not create a completed upload manifest or
    falsely return success.
    """

    if uploader is None or not callable(getattr(uploader, "upload", None)):
        raise PhysicalWalArchiveSpoolError("physical WAL archive uploader is required")
    if term_recheck_clock is None or not callable(term_recheck_clock):
        raise PhysicalWalArchiveSpoolError("physical WAL archive term recheck clock is required")
    observed_now = _utc(now, label="physical WAL archive handoff clock")
    binding = require_verified_physical_wal_archive_binding(
        verified_binding, now=observed_now
    )
    normalized_config = _normalise_config(config)
    timeline_id, ordinal, start_lsn, end_lsn, _segment_size = parse_postgresql_wal_segment_name(
        segment_name,
        wal_segment_size_bytes=normalized_config.wal_segment_size_bytes,
    )
    if timeline_id != binding.manifest_binding.timeline_id:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment timeline does not match binding")
    _chain_start_lsn, chain_start_value = _require_lsn(
        binding.manifest_binding.wal_chain_start_lsn,
        label="physical WAL chain start LSN",
    )
    _segment_start_lsn, start_value = _require_lsn(start_lsn, label="PostgreSQL WAL segment start LSN")
    if start_value < chain_start_value:
        raise PhysicalWalArchiveSpoolError("PostgreSQL WAL segment precedes WAL chain start")

    snapshot_path, snapshot_sha256, snapshot_bytes = _capture_immutable_snapshot(
        config=normalized_config,
        segment_name=segment_name,
    )
    object_key = derive_physical_wal_archive_object_key(
        binding=binding,
        segment_name=segment_name,
        snapshot_sha256=snapshot_sha256,
        wal_segment_size_bytes=normalized_config.wal_segment_size_bytes,
        now=observed_now,
    )
    handoff_bytes = _handoff_descriptor(
        binding=binding,
        timeline_id=timeline_id,
        ordinal=ordinal,
        segment_name=segment_name,
        start_lsn=start_lsn,
        end_lsn=end_lsn,
        wal_segment_size_bytes=normalized_config.wal_segment_size_bytes,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        object_key=object_key,
    )
    handoff_sha256 = hashlib.sha256(handoff_bytes).hexdigest()
    descriptor_directory = _secure_child_directory(normalized_config.spool_root, "descriptors")
    descriptor_path = _write_immutable_artifact(
        directory=descriptor_directory,
        filename=f"{handoff_sha256}.json",
        content=handoff_bytes,
        label="physical WAL handoff descriptor",
    )
    descriptor_payload = json.loads(handoff_bytes.decode("utf-8"))
    manifest_directory = _secure_child_directory(normalized_config.spool_root, "manifests")
    completed_manifest_path = manifest_directory / f"{handoff_sha256}.json"
    _recheck_live_binding(
        binding=binding,
        initial_now=observed_now,
        term_recheck_clock=term_recheck_clock,
    )
    completed = _completed_result_if_present(
        manifest_path=completed_manifest_path,
        handoff_descriptor_path=descriptor_path,
        descriptor=descriptor_payload,
        handoff_descriptor_sha256=handoff_sha256,
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        expected_destination_age_recipient=binding.manifest_binding.destination_age_recipient,
    )
    if completed is not None:
        return completed
    try:
        raw_receipt = uploader.upload(
            snapshot_path=snapshot_path,
            descriptor_bytes=handoff_bytes,
            descriptor_sha256=handoff_sha256,
        )
    except Exception as exc:
        raise PhysicalWalArchiveSpoolError("physical WAL archive uploader failed") from exc
    receipt = _validate_upload_receipt(
        raw_receipt,
        descriptor_sha256=handoff_sha256,
        expected_object_key=object_key,
        expected_destination_age_recipient=binding.manifest_binding.destination_age_recipient,
        maximum_plaintext_bytes=snapshot_bytes,
    )
    current_snapshot_sha256, current_snapshot_bytes = _read_hash_and_size(
        snapshot_path, label="physical WAL snapshot after uploader handoff"
    )
    if (
        current_snapshot_sha256 != snapshot_sha256
        or current_snapshot_bytes != snapshot_bytes
    ):
        raise PhysicalWalArchiveSpoolError(
            "physical WAL snapshot changed during uploader handoff"
        )
    _recheck_live_binding(
        binding=binding,
        initial_now=observed_now,
        term_recheck_clock=term_recheck_clock,
    )
    manifest_bytes = _upload_manifest(
        descriptor=descriptor_payload,
        handoff_descriptor_sha256=handoff_sha256,
        receipt=receipt,
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = _write_immutable_artifact(
        directory=manifest_directory,
        filename=f"{handoff_sha256}.json",
        content=manifest_bytes,
        label="physical WAL upload manifest",
    )
    return PhysicalWalArchiveSpoolResult(
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256,
        snapshot_bytes=snapshot_bytes,
        handoff_descriptor_path=descriptor_path,
        handoff_descriptor_sha256=handoff_sha256,
        upload_manifest_path=manifest_path,
        upload_manifest_sha256=manifest_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.version_id,
        ciphertext_sha256=receipt.ciphertext_sha256,
        ciphertext_bytes=receipt.ciphertext_bytes,
    )
