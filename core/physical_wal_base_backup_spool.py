"""Local-only, default-off capture boundary for a completed physical base backup.

This module is deliberately downstream of a future *trusted* physical
base-backup producer.  It never starts that producer and never invokes
``pg_basebackup``, PostgreSQL, Docker, SSH, S3/HTTP, encryption, or a database
command.  It accepts only one fixed-root, completed regular-file artifact whose
pre-authorized hash, byte count, and completion-attestation hash are already
bound to a live source-held Witness term on one ordered FI/IR route.

The boundary makes an immutable local snapshot, gives it to a mandatory
injected uploader, validates an exact create-only/versioned Object Storage
readback receipt, and writes a deterministic local completion record.  A
completion record is archive/recovery evidence only: it is never evidence of
native ``remote_apply``, a strict acknowledgement, a promotion right, or a
writer right.
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
    OBJECT_KEY_RE,
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
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
    PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
)


__all__ = (
    "DEFAULT_MAX_PHYSICAL_BASE_BACKUP_BYTES",
    "DEFAULT_SPOOL_RESERVE_BYTES",
    "PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA",
    "PHYSICAL_WAL_BASE_BACKUP_SPOOL_DEFAULT_ENABLED",
    "PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA",
    "PhysicalWalBaseBackupCompletedArtifact",
    "PhysicalWalBaseBackupManifestBinding",
    "PhysicalWalBaseBackupSpoolConfig",
    "PhysicalWalBaseBackupSpoolError",
    "PhysicalWalBaseBackupSpoolResult",
    "PhysicalWalBaseBackupUploadReceipt",
    "PhysicalWalBaseBackupUploader",
    "VerifiedPhysicalWalBaseBackupBinding",
    "authorize_physical_wal_base_backup_binding",
    "capture_physical_wal_base_backup",
    "derive_physical_wal_base_backup_object_key",
    "require_verified_physical_wal_base_backup_binding",
)


PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-wal-base-backup-spool-descriptor-v1"
)
PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA = (
    "gold-trade-physical-wal-base-backup-spool-completed-v1"
)
PHYSICAL_WAL_BASE_BACKUP_SPOOL_DEFAULT_ENABLED = False
DEFAULT_MAX_PHYSICAL_BASE_BACKUP_BYTES = 512 * 1024 * 1024 * 1024
MAX_PHYSICAL_BASE_BACKUP_BYTES = 2 * 1024 * 1024 * 1024 * 1024
DEFAULT_SPOOL_RESERVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_SPOOL_RESERVE_BYTES = MAX_PHYSICAL_BASE_BACKUP_BYTES
MAX_BASE_BACKUP_ENCRYPTION_OVERHEAD_BYTES = 16 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024

_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,255}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_VERIFIED_BINDING_CAPABILITY = object()


class PhysicalWalBaseBackupSpoolError(ValueError):
    """A base-backup capture, handoff, or completion record is unsafe."""


@dataclass(frozen=True)
class PhysicalWalBaseBackupManifestBinding:
    """Manifest-compatible lineage fields expected from a trusted coordinator.

    ``source_site`` and ``destination_site`` must be the two distinct WebApp
    sites in either order.  ``destination_age_recipient`` is the recipient
    pinned for that selected destination, not an uploader-selected parameter.
    The type carries no credential, URL, host, shell command, direct
    peer-control, or implementation capability.
    """

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    destination_age_recipient: str
    object_storage_namespace: str = PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


@dataclass(frozen=True)
class PhysicalWalBaseBackupCompletedArtifact:
    """One already-completed producer artifact, identified without a path escape.

    The future producer/coordinator is responsible for establishing that this
    hash and completion attestation describe a transaction-consistent physical
    base backup.  This boundary only verifies that the fixed-root file matches
    those trusted pins before it captures it.
    """

    artifact_name: str
    plaintext_sha256: str
    plaintext_bytes: int
    completion_attestation_sha256: str


@dataclass(frozen=True)
class PhysicalWalBaseBackupSpoolConfig:
    """Two fixed local roots and a hard base-backup size cap."""

    source_root: Path
    spool_root: Path
    maximum_base_backup_bytes: int = DEFAULT_MAX_PHYSICAL_BASE_BACKUP_BYTES
    spool_reserve_bytes: int = DEFAULT_SPOOL_RESERVE_BYTES


@dataclass(frozen=True)
class PhysicalWalBaseBackupUploadReceipt:
    """Non-secret exact-version readback facts from an injected uploader."""

    descriptor_sha256: str
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    encryption: str
    age_recipient: str
    immutability: str


class PhysicalWalBaseBackupUploader(Protocol):
    """Narrow injected interface; this module ships no uploader implementation."""

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalBaseBackupUploadReceipt:
        """Publish exactly this snapshot or raise; no redirect input is accepted."""


@dataclass(frozen=True)
class VerifiedPhysicalWalBaseBackupBinding:
    """Opaque live-term-bound capture inputs; direct construction is not trust."""

    manifest_binding: PhysicalWalBaseBackupManifestBinding
    completed_artifact: PhysicalWalBaseBackupCompletedArtifact
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    route_binding_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalBaseBackupSpoolResult:
    """Immutable local result, explicitly not a remote-ack/promotion proof."""

    snapshot_path: Path
    snapshot_sha256: str
    snapshot_bytes: int
    handoff_descriptor_path: Path
    handoff_descriptor_sha256: str
    completed_record_path: Path
    completed_record_sha256: str
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class _BindingFacts:
    manifest: PhysicalWalBaseBackupManifestBinding
    artifact: PhysicalWalBaseBackupCompletedArtifact
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
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalWalBaseBackupSpoolError("base-backup spool JSON has duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PhysicalWalBaseBackupSpoolError(
        f"base-backup spool JSON contains unsupported constant: {value}"
    )


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is invalid")
    return value


def _require_safe_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalWalBaseBackupSpoolError(f"{label} contains a URL or secret-shaped value")
    return value


def _require_object_storage_namespace(
    value: object,
    *,
    source_site: str,
    destination_site: str,
) -> str:
    if type(value) is not str or value not in PHYSICAL_WAL_OBJECT_STORAGE_NAMESPACES:
        raise PhysicalWalBaseBackupSpoolError("base-backup Object Storage namespace is invalid")
    expected = (
        PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE
        if (source_site, destination_site) == ("webapp_fi", "webapp_ir")
        else PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    )
    if value != expected:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup Object Storage namespace does not match the pinned route"
        )
    return value


def _require_lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _require_age_recipient(value: object) -> str:
    if not isinstance(value, str) or AGE_RECIPIENT_RE.fullmatch(value) is None:
        raise PhysicalWalBaseBackupSpoolError("base-backup destination age recipient is invalid")
    if _URL_VALUE_RE.search(value) or _SENSITIVE_VALUE_RE.search(value):
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup destination age recipient contains an unsafe value"
        )
    return value


def _validate_wal_segment_size(value: object) -> int:
    if type(value) is not int or value not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        raise PhysicalWalBaseBackupSpoolError("base-backup WAL segment size is invalid")
    return value


def _normalise_manifest_binding(value: object) -> PhysicalWalBaseBackupManifestBinding:
    if type(value) is not PhysicalWalBaseBackupManifestBinding:
        raise PhysicalWalBaseBackupSpoolError("base-backup manifest binding is invalid")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup binding must use one ordered distinct WebApp route"
        )
    if not isinstance(value.campaign_id, str) or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None:
        raise PhysicalWalBaseBackupSpoolError("base-backup campaign is invalid")
    if not isinstance(value.release_sha, str) or RELEASE_SHA_RE.fullmatch(value.release_sha) is None:
        raise PhysicalWalBaseBackupSpoolError("base-backup release is invalid")
    if (
        not isinstance(value.baseline_generation_id, str)
        or STREAM_GENERATION_ID_RE.fullmatch(value.baseline_generation_id) is None
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup generation is invalid")
    if (
        not isinstance(value.database_system_identifier, str)
        or _SYSTEM_IDENTIFIER_RE.fullmatch(value.database_system_identifier) is None
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup database system identifier is invalid")
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        raise PhysicalWalBaseBackupSpoolError("base-backup timeline ID is invalid")
    wal_segment_size = _validate_wal_segment_size(value.wal_segment_size_bytes)
    baseline_lsn, baseline_value = _require_lsn(value.baseline_wal_lsn, label="base-backup baseline WAL LSN")
    chain_start_lsn, chain_start_value = _require_lsn(
        value.wal_chain_start_lsn, label="base-backup WAL chain start LSN"
    )
    backup_end_lsn, backup_end_value = _require_lsn(
        value.base_backup_end_lsn, label="base-backup end WAL LSN"
    )
    if backup_end_value <= baseline_value:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup end WAL LSN must follow the baseline WAL LSN"
        )
    if (
        chain_start_value % wal_segment_size
        or chain_start_value > baseline_value
        or baseline_value >= chain_start_value + wal_segment_size
    ):
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup WAL chain start does not cover the baseline LSN on a segment boundary"
        )
    return PhysicalWalBaseBackupManifestBinding(
        source_site=value.source_site,
        destination_site=value.destination_site,
        campaign_id=value.campaign_id,
        release_sha=value.release_sha,
        baseline_generation_id=value.baseline_generation_id,
        database_system_identifier=value.database_system_identifier,
        timeline_id=value.timeline_id,
        wal_segment_size_bytes=wal_segment_size,
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=chain_start_lsn,
        base_backup_end_lsn=backup_end_lsn,
        destination_age_recipient=_require_age_recipient(value.destination_age_recipient),
        object_storage_namespace=_require_object_storage_namespace(
            value.object_storage_namespace,
            source_site=value.source_site,
            destination_site=value.destination_site,
        ),
    )


def _normalise_completed_artifact(
    value: object,
) -> PhysicalWalBaseBackupCompletedArtifact:
    if type(value) is not PhysicalWalBaseBackupCompletedArtifact:
        raise PhysicalWalBaseBackupSpoolError("completed base-backup artifact is invalid")
    if (
        not isinstance(value.artifact_name, str)
        or _ARTIFACT_NAME_RE.fullmatch(value.artifact_name) is None
        or _URL_VALUE_RE.search(value.artifact_name)
        or _SENSITIVE_VALUE_RE.search(value.artifact_name)
    ):
        raise PhysicalWalBaseBackupSpoolError("completed base-backup artifact name is invalid")
    if (
        type(value.plaintext_bytes) is not int
        or not 1 <= value.plaintext_bytes <= MAX_PHYSICAL_BASE_BACKUP_BYTES
    ):
        raise PhysicalWalBaseBackupSpoolError("completed base-backup artifact byte count is invalid")
    return PhysicalWalBaseBackupCompletedArtifact(
        artifact_name=value.artifact_name,
        plaintext_sha256=_require_sha256(
            value.plaintext_sha256, label="completed base-backup plaintext SHA-256"
        ),
        plaintext_bytes=value.plaintext_bytes,
        completion_attestation_sha256=_require_sha256(
            value.completion_attestation_sha256,
            label="completed base-backup completion attestation SHA-256",
        ),
    )


def _route_binding_sha256(
    manifest: PhysicalWalBaseBackupManifestBinding,
    artifact: PhysicalWalBaseBackupCompletedArtifact,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "source_site": manifest.source_site,
                "destination_site": manifest.destination_site,
                "campaign_id": manifest.campaign_id,
                "release_sha": manifest.release_sha,
                "baseline_generation_id": manifest.baseline_generation_id,
                "database_system_identifier": manifest.database_system_identifier,
                "timeline_id": manifest.timeline_id,
                "wal_segment_size_bytes": manifest.wal_segment_size_bytes,
                "baseline_wal_lsn": manifest.baseline_wal_lsn,
                "wal_chain_start_lsn": manifest.wal_chain_start_lsn,
                "base_backup_end_lsn": manifest.base_backup_end_lsn,
                "destination_age_recipient": manifest.destination_age_recipient,
                "object_storage_namespace": manifest.object_storage_namespace,
                "artifact_name": artifact.artifact_name,
                "artifact_plaintext_sha256": artifact.plaintext_sha256,
                "artifact_plaintext_bytes": artifact.plaintext_bytes,
                "completion_attestation_sha256": artifact.completion_attestation_sha256,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witness_transition_id": term.witness_transition_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            label="base-backup route binding",
        )
    ).hexdigest()


def _binding_facts(
    *,
    manifest_binding: object,
    completed_artifact: object,
    witnessed_term: object,
    now: datetime,
) -> _BindingFacts:
    manifest = _normalise_manifest_binding(manifest_binding)
    artifact = _normalise_completed_artifact(completed_artifact)
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(witnessed_term, now=now)
    except ObjectDeltaRoleMatrixRolloverError as exc:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup Witness term is not live and verified"
        ) from exc
    if term.holder_site != manifest.source_site:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup source does not hold the live Witness term"
        )
    return _BindingFacts(
        manifest=manifest,
        artifact=artifact,
        term=term,
        route_binding_sha256=_route_binding_sha256(manifest, artifact, term),
    )


def authorize_physical_wal_base_backup_binding(
    *,
    manifest_binding: PhysicalWalBaseBackupManifestBinding,
    completed_artifact: PhysicalWalBaseBackupCompletedArtifact,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> VerifiedPhysicalWalBaseBackupBinding:
    """Bind one trusted completed file identity to a live source-held term."""

    observed_now = _utc(now, label="base-backup authorization clock")
    facts = _binding_facts(
        manifest_binding=manifest_binding,
        completed_artifact=completed_artifact,
        witnessed_term=witnessed_term,
        now=observed_now,
    )
    result = VerifiedPhysicalWalBaseBackupBinding(
        manifest_binding=facts.manifest,
        completed_artifact=facts.artifact,
        witnessed_term=facts.term,
        route_binding_sha256=facts.route_binding_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_BINDING_CAPABILITY)
    return result


def require_verified_physical_wal_base_backup_binding(
    value: object,
    *,
    now: datetime,
) -> VerifiedPhysicalWalBaseBackupBinding:
    """Revalidate immutable binding facts and the live term before every stage."""

    if (
        type(value) is not VerifiedPhysicalWalBaseBackupBinding
        or value._capability is not _VERIFIED_BINDING_CAPABILITY
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup binding is not authorized")
    observed_now = _utc(now, label="base-backup binding recheck clock")
    facts = _binding_facts(
        manifest_binding=value.manifest_binding,
        completed_artifact=value.completed_artifact,
        witnessed_term=value.witnessed_term,
        now=observed_now,
    )
    if (
        facts.manifest != value.manifest_binding
        or facts.artifact != value.completed_artifact
        or facts.term != value.witnessed_term
        or facts.route_binding_sha256 != value.route_binding_sha256
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup binding was tampered")
    return value


def _secure_root(
    path: object,
    *,
    label: str,
    allowed_owner_uids: frozenset[int],
    required_mode: int | None,
) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise PhysicalWalBaseBackupSpoolError(f"{label} must be a canonical absolute Path")
    try:
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError as exc:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is unavailable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    unsafe = (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
    )
    if required_mode is not None:
        # Spool snapshots and completion records contain plaintext backup
        # material, so a merely non-writable public directory is insufficient.
        unsafe = unsafe or metadata.st_uid not in allowed_owner_uids or mode != required_mode
    else:
        # The source may belong to the currently executing trusted producer,
        # but it cannot be an arbitrary-user or group/world-visible directory.
        unsafe = unsafe or metadata.st_uid not in allowed_owner_uids or bool(mode & 0o077)
    if unsafe:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is not a trusted non-symlink directory")
    return resolved


def _normalise_config(value: object) -> PhysicalWalBaseBackupSpoolConfig:
    if type(value) is not PhysicalWalBaseBackupSpoolConfig:
        raise PhysicalWalBaseBackupSpoolError("base-backup spool configuration is invalid")
    source_root = _secure_root(
        value.source_root,
        label="base-backup source root",
        allowed_owner_uids=frozenset({0, os.geteuid()}),
        required_mode=None,
    )
    spool_root = _secure_root(
        value.spool_root,
        label="base-backup spool root",
        allowed_owner_uids=frozenset({0}),
        required_mode=0o700,
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
        raise PhysicalWalBaseBackupSpoolError("base-backup source and spool roots overlap")
    if (
        type(value.maximum_base_backup_bytes) is not int
        or not 1 <= value.maximum_base_backup_bytes <= MAX_PHYSICAL_BASE_BACKUP_BYTES
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup spool size bound is invalid")
    if (
        type(value.spool_reserve_bytes) is not int
        or not 1 <= value.spool_reserve_bytes <= MAX_SPOOL_RESERVE_BYTES
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup spool reserve is invalid")
    return PhysicalWalBaseBackupSpoolConfig(
        source_root=source_root,
        spool_root=spool_root,
        maximum_base_backup_bytes=value.maximum_base_backup_bytes,
        spool_reserve_bytes=value.spool_reserve_bytes,
    )


def _secure_child_directory(root: Path, *parts: str) -> Path:
    candidate = root
    for part in parts:
        if not isinstance(part, str) or not part or "/" in part or "\\" in part or part in {".", ".."}:
            raise PhysicalWalBaseBackupSpoolError("base-backup spool child path is invalid")
        candidate = candidate / part
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            metadata = os.lstat(candidate)
        except OSError as exc:
            raise PhysicalWalBaseBackupSpoolError("base-backup spool child path is unavailable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise PhysicalWalBaseBackupSpoolError("base-backup spool child path is unsafe")
    return candidate


def _open_regular_readonly(path: Path, *, label: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalWalBaseBackupSpoolError("platform lacks fail-closed non-symlink open")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PhysicalWalBaseBackupSpoolError(f"{label} cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PhysicalWalBaseBackupSpoolError(f"{label} is not a single-link regular file")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


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


def _source_artifact_fd(
    *,
    source_root: Path,
    artifact_name: str,
    expected_bytes: int,
) -> tuple[int, tuple[int, int, int, int, int, int, int]]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise PhysicalWalBaseBackupSpoolError("platform lacks fail-closed directory open")
    root_fd = -1
    artifact_fd = -1
    try:
        root_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        artifact_fd = os.open(artifact_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
        metadata = os.fstat(artifact_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size != expected_bytes
            or metadata.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise PhysicalWalBaseBackupSpoolError(
                "completed base-backup source is not a bounded single-link regular file"
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
        return artifact_fd, fingerprint
    except PhysicalWalBaseBackupSpoolError:
        if artifact_fd >= 0:
            os.close(artifact_fd)
        raise
    except OSError as exc:
        if artifact_fd >= 0:
            os.close(artifact_fd)
        raise PhysicalWalBaseBackupSpoolError(
            "completed base-backup source cannot be opened safely"
        ) from exc
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise PhysicalWalBaseBackupSpoolError("base-backup spool write failed")
        offset += written


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        raise PhysicalWalBaseBackupSpoolError("platform lacks durable directory fsync")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _snapshot_path(config: PhysicalWalBaseBackupSpoolConfig, *, plaintext_sha256: str) -> Path:
    directory = _secure_child_directory(config.spool_root, "snapshots", plaintext_sha256[:2])
    return directory / f"{plaintext_sha256}.basebackup"


def _require_spool_capacity(
    *,
    spool_root: Path,
    artifact_bytes: int,
    reserve_bytes: int,
) -> None:
    """Require one full temporary copy plus the configured free-space reserve."""

    try:
        filesystem = os.statvfs(spool_root)
        available_bytes = filesystem.f_bavail * filesystem.f_frsize
    except (AttributeError, OSError) as exc:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup spool capacity cannot be inspected"
        ) from exc
    if (
        type(available_bytes) is not int
        or available_bytes < 0
        or available_bytes < artifact_bytes + reserve_bytes
    ):
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup spool lacks required free capacity plus reserve"
        )


def _verify_snapshot(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    label: str,
) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PhysicalWalBaseBackupSpoolError(f"{label} is unsafe")
    actual_sha256, actual_bytes = _read_hash_and_size(path, label=label)
    if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
        raise PhysicalWalBaseBackupSpoolError(f"{label} was tampered")


def _capture_snapshot_if_needed(
    *,
    config: PhysicalWalBaseBackupSpoolConfig,
    artifact: PhysicalWalBaseBackupCompletedArtifact,
) -> Path:
    if artifact.plaintext_bytes > config.maximum_base_backup_bytes:
        raise PhysicalWalBaseBackupSpoolError("completed base-backup exceeds spool size bound")
    snapshot_path = _snapshot_path(config, plaintext_sha256=artifact.plaintext_sha256)
    try:
        os.lstat(snapshot_path)
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        raise PhysicalWalBaseBackupSpoolError("base-backup snapshot cannot be inspected") from exc
    else:
        exists = True
    if exists:
        _verify_snapshot(
            snapshot_path,
            expected_sha256=artifact.plaintext_sha256,
            expected_bytes=artifact.plaintext_bytes,
            label="existing base-backup snapshot",
        )
        return snapshot_path

    _require_spool_capacity(
        spool_root=config.spool_root,
        artifact_bytes=artifact.plaintext_bytes,
        reserve_bytes=config.spool_reserve_bytes,
    )

    artifact_fd, source_before = _source_artifact_fd(
        source_root=config.source_root,
        artifact_name=artifact.artifact_name,
        expected_bytes=artifact.plaintext_bytes,
    )
    temporary_directory = _secure_child_directory(config.spool_root, "tmp")
    temporary_path = temporary_directory / (
        f".{artifact.plaintext_sha256}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
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
            chunk = os.read(artifact_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > artifact.plaintext_bytes:
                raise PhysicalWalBaseBackupSpoolError(
                    "completed base-backup source exceeds its bounded size"
                )
            digest.update(chunk)
            _write_all(output_fd, chunk)
        os.fsync(output_fd)
        after = os.fstat(artifact_fd)
        source_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_nlink,
        )
        if source_after != source_before or copied != artifact.plaintext_bytes:
            raise PhysicalWalBaseBackupSpoolError(
                "completed base-backup source changed during immutable snapshot capture"
            )
        if digest.hexdigest() != artifact.plaintext_sha256:
            raise PhysicalWalBaseBackupSpoolError(
                "completed base-backup source does not match its trusted plaintext SHA-256"
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
        os.close(artifact_fd)

    try:
        os.link(temporary_path, snapshot_path, follow_symlinks=False)
    except FileExistsError:
        temporary_path.unlink(missing_ok=True)
        _verify_snapshot(
            snapshot_path,
            expected_sha256=artifact.plaintext_sha256,
            expected_bytes=artifact.plaintext_bytes,
            label="raced base-backup snapshot",
        )
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PhysicalWalBaseBackupSpoolError("base-backup snapshot cannot be finalized") from exc
    else:
        temporary_path.unlink(missing_ok=True)
    _fsync_directory(snapshot_path.parent)
    _verify_snapshot(
        snapshot_path,
        expected_sha256=artifact.plaintext_sha256,
        expected_bytes=artifact.plaintext_bytes,
        label="captured base-backup snapshot",
    )
    return snapshot_path


def _write_immutable_artifact(
    *,
    directory: Path,
    filename: str,
    content: bytes,
    label: str,
) -> Path:
    if not filename or "/" in filename or "\\" in filename:
        raise PhysicalWalBaseBackupSpoolError(f"{label} filename is invalid")
    final_path = directory / filename
    expected_sha256 = hashlib.sha256(content).hexdigest()
    try:
        metadata = os.lstat(final_path)
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise PhysicalWalBaseBackupSpoolError(f"existing {label} is unsafe")
        actual_sha256, actual_size = _read_hash_and_size(final_path, label=f"existing {label}")
        if actual_sha256 != expected_sha256 or actual_size != len(content):
            raise PhysicalWalBaseBackupSpoolError(f"existing {label} was tampered")
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
            actual_sha256, actual_size = _read_hash_and_size(final_path, label=f"raced {label}")
            temporary_path.unlink(missing_ok=True)
            if actual_sha256 != expected_sha256 or actual_size != len(content):
                raise PhysicalWalBaseBackupSpoolError(f"raced {label} was tampered")
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


def _validate_object_key(value: object, *, label: str) -> str:
    if not isinstance(value, str) or OBJECT_KEY_RE.fullmatch(value) is None:
        raise PhysicalWalBaseBackupSpoolError(f"{label} is invalid")
    if (
        _URL_VALUE_RE.search(value)
        or _SENSITIVE_VALUE_RE.search(value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise PhysicalWalBaseBackupSpoolError(f"{label} contains an unsafe value")
    return value


def derive_physical_wal_base_backup_object_key(
    *,
    binding: VerifiedPhysicalWalBaseBackupBinding,
    now: datetime,
) -> str:
    """Derive the sole immutable Object Storage key for the pinned artifact."""

    verified = require_verified_physical_wal_base_backup_binding(binding, now=now)
    manifest = verified.manifest_binding
    artifact = verified.completed_artifact
    key = "/".join(
        (
            manifest.object_storage_namespace,
            manifest.campaign_id,
            manifest.release_sha,
            manifest.baseline_generation_id,
            f"{manifest.source_site}-to-{manifest.destination_site}",
            f"timeline-{manifest.timeline_id:08X}",
            "base-backup",
            f"{artifact.plaintext_sha256}.age",
        )
    )
    return _validate_object_key(key, label="derived base-backup Object key")


def _handoff_descriptor(
    *,
    binding: VerifiedPhysicalWalBaseBackupBinding,
    snapshot_path: Path,
    object_key: str,
) -> bytes:
    manifest = binding.manifest_binding
    artifact = binding.completed_artifact
    term = binding.witnessed_term
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
            "kind": "physical_postgresql_base_backup_handoff",
            "source_site": manifest.source_site,
            "destination_site": manifest.destination_site,
            "campaign_id": manifest.campaign_id,
            "release_sha": manifest.release_sha,
            "baseline_generation_id": manifest.baseline_generation_id,
            "route_binding_sha256": binding.route_binding_sha256,
            "object_storage_namespace": manifest.object_storage_namespace,
            "database_system_identifier": manifest.database_system_identifier,
            "timeline_id": manifest.timeline_id,
            "wal_segment_size_bytes": manifest.wal_segment_size_bytes,
            "baseline_wal_lsn": manifest.baseline_wal_lsn,
            "wal_chain_start_lsn": manifest.wal_chain_start_lsn,
            "base_backup_end_lsn": manifest.base_backup_end_lsn,
            "destination_age_recipient": manifest.destination_age_recipient,
            "writer_term": {
                "holder_site": term.holder_site,
                "epoch": term.writer_epoch,
                "lease_id": term.writer_lease_id,
                "witness_transition_id": term.witness_transition_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
            },
            "completed_source_artifact": {
                "artifact_name": artifact.artifact_name,
                "plaintext_sha256": artifact.plaintext_sha256,
                "plaintext_bytes": artifact.plaintext_bytes,
                "completion_attestation_sha256": artifact.completion_attestation_sha256,
            },
            "snapshot_path_name": snapshot_path.name,
            "snapshot_sha256": artifact.plaintext_sha256,
            "snapshot_bytes": artifact.plaintext_bytes,
            "object_key": object_key,
            "not_a_remote_apply_proof": True,
            "not_a_strict_acknowledgement_proof": True,
        },
        label="base-backup handoff descriptor",
    )


def _validate_upload_receipt(
    value: object,
    *,
    descriptor_sha256: str,
    expected_object_key: str,
    expected_destination_age_recipient: str,
    maximum_plaintext_bytes: int,
) -> PhysicalWalBaseBackupUploadReceipt:
    if type(value) is not PhysicalWalBaseBackupUploadReceipt:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader receipt is invalid")
    if _require_sha256(value.descriptor_sha256, label="uploader descriptor SHA-256") != descriptor_sha256:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader receipt binds a different descriptor")
    if _validate_object_key(value.object_key, label="uploader Object key") != expected_object_key:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader receipt redirects the Object key")
    if not isinstance(value.version_id, str) or VERSION_ID_RE.fullmatch(value.version_id) is None:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader Object version is invalid")
    if (
        type(value.ciphertext_bytes) is not int
        or not 1 <= value.ciphertext_bytes <= maximum_plaintext_bytes + MAX_BASE_BACKUP_ENCRYPTION_OVERHEAD_BYTES
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader ciphertext byte count is invalid")
    if value.encryption != PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader encryption is invalid")
    if value.immutability != PHYSICAL_WAL_OBJECT_IMMUTABILITY:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader immutability is invalid")
    recipient = _require_age_recipient(value.age_recipient)
    if recipient != expected_destination_age_recipient:
        raise PhysicalWalBaseBackupSpoolError(
            "base-backup uploader receipt binds a different destination age recipient"
        )
    return PhysicalWalBaseBackupUploadReceipt(
        descriptor_sha256=descriptor_sha256,
        object_key=expected_object_key,
        version_id=value.version_id,
        ciphertext_sha256=_require_sha256(
            value.ciphertext_sha256, label="uploader ciphertext SHA-256"
        ),
        ciphertext_bytes=value.ciphertext_bytes,
        encryption=PHYSICAL_WAL_OBJECT_MANIFEST_ENCRYPTION,
        age_recipient=recipient,
        immutability=PHYSICAL_WAL_OBJECT_IMMUTABILITY,
    )


def _object_descriptor(receipt: PhysicalWalBaseBackupUploadReceipt) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA,
        "version": PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION,
        "object_kind": "physical_postgresql_base_backup",
        "object_key": receipt.object_key,
        "version_id": receipt.version_id,
        "ciphertext_sha256": receipt.ciphertext_sha256,
        "ciphertext_bytes": receipt.ciphertext_bytes,
        "encryption": receipt.encryption,
        "age_recipient": receipt.age_recipient,
        "immutability": receipt.immutability,
    }


def _completed_record(
    *,
    descriptor: Mapping[str, Any],
    descriptor_sha256: str,
    receipt: PhysicalWalBaseBackupUploadReceipt,
) -> bytes:
    return _canonical_json_bytes(
        {
            "schema": PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA,
            "kind": "physical_postgresql_base_backup_uploaded_archive_recovery_only",
            "handoff_descriptor_sha256": descriptor_sha256,
            "source_site": descriptor["source_site"],
            "destination_site": descriptor["destination_site"],
            "campaign_id": descriptor["campaign_id"],
            "release_sha": descriptor["release_sha"],
            "baseline_generation_id": descriptor["baseline_generation_id"],
            "route_binding_sha256": descriptor["route_binding_sha256"],
            "object_storage_namespace": descriptor["object_storage_namespace"],
            "database_system_identifier": descriptor["database_system_identifier"],
            "timeline_id": descriptor["timeline_id"],
            "wal_segment_size_bytes": descriptor["wal_segment_size_bytes"],
            "baseline_wal_lsn": descriptor["baseline_wal_lsn"],
            "wal_chain_start_lsn": descriptor["wal_chain_start_lsn"],
            "base_backup_end_lsn": descriptor["base_backup_end_lsn"],
            "destination_age_recipient": descriptor["destination_age_recipient"],
            "writer_term": descriptor["writer_term"],
            "completed_source_artifact": descriptor["completed_source_artifact"],
            "snapshot_sha256": descriptor["snapshot_sha256"],
            "snapshot_bytes": descriptor["snapshot_bytes"],
            "object": _object_descriptor(receipt),
            "not_a_remote_apply_proof": True,
            "not_a_strict_acknowledgement_proof": True,
        },
        label="base-backup completed record",
    )


def _parse_completed_record(
    *,
    raw: bytes,
    expected_descriptor: Mapping[str, Any],
    expected_descriptor_sha256: str,
) -> PhysicalWalBaseBackupUploadReceipt:
    if not raw:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record is empty")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record is invalid JSON") from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value, label="base-backup completed record") != raw:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record is not canonical")
    expected_fields = {
        "schema", "kind", "handoff_descriptor_sha256", "source_site", "destination_site",
        "campaign_id", "release_sha", "baseline_generation_id", "route_binding_sha256",
        "object_storage_namespace",
        "database_system_identifier", "timeline_id", "wal_segment_size_bytes", "baseline_wal_lsn",
        "wal_chain_start_lsn", "base_backup_end_lsn", "destination_age_recipient", "writer_term",
        "completed_source_artifact", "snapshot_sha256", "snapshot_bytes", "object",
        "not_a_remote_apply_proof", "not_a_strict_acknowledgement_proof",
    }
    if set(value) != expected_fields:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record fields are invalid")
    if (
        value["schema"] != PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA
        or value["kind"] != "physical_postgresql_base_backup_uploaded_archive_recovery_only"
        or value["handoff_descriptor_sha256"] != expected_descriptor_sha256
        or value["not_a_remote_apply_proof"] is not True
        or value["not_a_strict_acknowledgement_proof"] is not True
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record binding is invalid")
    for field_name in (
        "source_site", "destination_site", "campaign_id", "release_sha", "baseline_generation_id",
        "route_binding_sha256", "object_storage_namespace", "database_system_identifier",
        "timeline_id", "wal_segment_size_bytes",
        "baseline_wal_lsn", "wal_chain_start_lsn", "base_backup_end_lsn", "destination_age_recipient",
        "writer_term", "completed_source_artifact", "snapshot_sha256", "snapshot_bytes",
    ):
        if value[field_name] != expected_descriptor[field_name]:
            raise PhysicalWalBaseBackupSpoolError("base-backup completed record differs from descriptor")
    object_value = value["object"]
    if not isinstance(object_value, Mapping):
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record object is invalid")
    expected_object_fields = {
        "schema", "version", "object_kind", "object_key", "version_id", "ciphertext_sha256",
        "ciphertext_bytes", "encryption", "age_recipient", "immutability",
    }
    if set(object_value) != expected_object_fields:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record object fields are invalid")
    if (
        object_value["schema"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_SCHEMA
        or object_value["version"] != PHYSICAL_WAL_OBJECT_DESCRIPTOR_VERSION
        or object_value["object_kind"] != "physical_postgresql_base_backup"
    ):
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record object type is invalid")
    return _validate_upload_receipt(
        PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=expected_descriptor_sha256,
            object_key=object_value["object_key"],
            version_id=object_value["version_id"],
            ciphertext_sha256=object_value["ciphertext_sha256"],
            ciphertext_bytes=object_value["ciphertext_bytes"],
            encryption=object_value["encryption"],
            age_recipient=object_value["age_recipient"],
            immutability=object_value["immutability"],
        ),
        descriptor_sha256=expected_descriptor_sha256,
        expected_object_key=expected_descriptor["object_key"],
        expected_destination_age_recipient=expected_descriptor["destination_age_recipient"],
        maximum_plaintext_bytes=expected_descriptor["snapshot_bytes"],
    )


def _existing_completed_record(
    *,
    config: PhysicalWalBaseBackupSpoolConfig,
    descriptor: Mapping[str, Any],
    descriptor_sha256: str,
) -> tuple[Path, str, PhysicalWalBaseBackupUploadReceipt] | None:
    directory = _secure_child_directory(config.spool_root, "completed")
    path = directory / f"{descriptor_sha256}.json"
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record cannot be inspected") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PhysicalWalBaseBackupSpoolError("existing base-backup completed record is unsafe")
    record_sha256, _record_bytes = _read_hash_and_size(path, label="existing base-backup completed record")
    descriptor_fd = _open_regular_readonly(path, label="existing base-backup completed record")
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor_fd, _COPY_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor_fd)
    if hashlib.sha256(raw).hexdigest() != record_sha256:
        raise PhysicalWalBaseBackupSpoolError("base-backup completed record changed while being read")
    receipt = _parse_completed_record(
        raw=raw,
        expected_descriptor=descriptor,
        expected_descriptor_sha256=descriptor_sha256,
    )
    return path, record_sha256, receipt


def capture_physical_wal_base_backup(
    *,
    config: PhysicalWalBaseBackupSpoolConfig,
    verified_binding: VerifiedPhysicalWalBaseBackupBinding,
    uploader: PhysicalWalBaseBackupUploader | None,
    now: datetime,
    term_recheck_clock: Callable[[], datetime] | None,
) -> PhysicalWalBaseBackupSpoolResult:
    """Capture and hand off one already-completed physical base-backup file.

    There is deliberately no default uploader.  A failure before a local
    completion record leaves only immutable local snapshot/descriptor data for
    retry.  A successful retry reads that deterministic record and never calls
    the injected uploader again.
    """

    if uploader is None or not callable(getattr(uploader, "upload", None)):
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader is required")
    if term_recheck_clock is None or not callable(term_recheck_clock):
        raise PhysicalWalBaseBackupSpoolError("base-backup term recheck clock is required")
    observed_now = _utc(now, label="base-backup capture clock")
    binding = require_verified_physical_wal_base_backup_binding(
        verified_binding, now=observed_now
    )
    normalized_config = _normalise_config(config)
    snapshot_path = _capture_snapshot_if_needed(
        config=normalized_config,
        artifact=binding.completed_artifact,
    )
    _verify_snapshot(
        snapshot_path,
        expected_sha256=binding.completed_artifact.plaintext_sha256,
        expected_bytes=binding.completed_artifact.plaintext_bytes,
        label="base-backup snapshot before handoff",
    )
    object_key = derive_physical_wal_base_backup_object_key(
        binding=binding, now=observed_now
    )
    handoff_bytes = _handoff_descriptor(
        binding=binding,
        snapshot_path=snapshot_path,
        object_key=object_key,
    )
    handoff_sha256 = hashlib.sha256(handoff_bytes).hexdigest()
    descriptor_directory = _secure_child_directory(normalized_config.spool_root, "descriptors")
    descriptor_path = _write_immutable_artifact(
        directory=descriptor_directory,
        filename=f"{handoff_sha256}.json",
        content=handoff_bytes,
        label="base-backup handoff descriptor",
    )
    descriptor = json.loads(handoff_bytes.decode("ascii"))
    existing = _existing_completed_record(
        config=normalized_config,
        descriptor=descriptor,
        descriptor_sha256=handoff_sha256,
    )
    if existing is not None:
        completed_path, completed_sha256, receipt = existing
        _verify_snapshot(
            snapshot_path,
            expected_sha256=binding.completed_artifact.plaintext_sha256,
            expected_bytes=binding.completed_artifact.plaintext_bytes,
            label="base-backup snapshot before completed retry",
        )
        completion_now = _utc(term_recheck_clock(), label="base-backup completion clock")
        if completion_now < observed_now:
            raise PhysicalWalBaseBackupSpoolError("base-backup completion clock moved backwards")
        require_verified_physical_wal_base_backup_binding(binding, now=completion_now)
        return PhysicalWalBaseBackupSpoolResult(
            snapshot_path=snapshot_path,
            snapshot_sha256=binding.completed_artifact.plaintext_sha256,
            snapshot_bytes=binding.completed_artifact.plaintext_bytes,
            handoff_descriptor_path=descriptor_path,
            handoff_descriptor_sha256=handoff_sha256,
            completed_record_path=completed_path,
            completed_record_sha256=completed_sha256,
            object_key=receipt.object_key,
            object_version_id=receipt.version_id,
            ciphertext_sha256=receipt.ciphertext_sha256,
            ciphertext_bytes=receipt.ciphertext_bytes,
        )
    try:
        raw_receipt = uploader.upload(
            snapshot_path=snapshot_path,
            descriptor_bytes=handoff_bytes,
            descriptor_sha256=handoff_sha256,
        )
    except Exception as exc:
        raise PhysicalWalBaseBackupSpoolError("base-backup uploader failed") from exc
    receipt = _validate_upload_receipt(
        raw_receipt,
        descriptor_sha256=handoff_sha256,
        expected_object_key=object_key,
        expected_destination_age_recipient=binding.manifest_binding.destination_age_recipient,
        maximum_plaintext_bytes=binding.completed_artifact.plaintext_bytes,
    )
    _verify_snapshot(
        snapshot_path,
        expected_sha256=binding.completed_artifact.plaintext_sha256,
        expected_bytes=binding.completed_artifact.plaintext_bytes,
        label="base-backup snapshot after uploader handoff",
    )
    completion_now = _utc(term_recheck_clock(), label="base-backup completion clock")
    if completion_now < observed_now:
        raise PhysicalWalBaseBackupSpoolError("base-backup completion clock moved backwards")
    require_verified_physical_wal_base_backup_binding(binding, now=completion_now)
    completed_bytes = _completed_record(
        descriptor=descriptor,
        descriptor_sha256=handoff_sha256,
        receipt=receipt,
    )
    completed_sha256 = hashlib.sha256(completed_bytes).hexdigest()
    completed_directory = _secure_child_directory(normalized_config.spool_root, "completed")
    completed_path = _write_immutable_artifact(
        directory=completed_directory,
        filename=f"{handoff_sha256}.json",
        content=completed_bytes,
        label="base-backup completed record",
    )
    return PhysicalWalBaseBackupSpoolResult(
        snapshot_path=snapshot_path,
        snapshot_sha256=binding.completed_artifact.plaintext_sha256,
        snapshot_bytes=binding.completed_artifact.plaintext_bytes,
        handoff_descriptor_path=descriptor_path,
        handoff_descriptor_sha256=handoff_sha256,
        completed_record_path=completed_path,
        completed_record_sha256=completed_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.version_id,
        ciphertext_sha256=receipt.ciphertext_sha256,
        ciphertext_bytes=receipt.ciphertext_bytes,
    )
