"""Root-pinned, fail-closed local standby-bootstrap materialization boundary.

This module is deliberately narrower than a restore implementation.  It
revalidates a verified FI→IR physical bundle, an exact canonical staging
receipt, the signed current Writer-Witness term, and observed recovery evidence
before it makes one newly-created detached PGDATA candidate available to an
injected future materializer.  The materializer receives only already-opened
directory/file descriptors and an internally created plan; it receives no
caller-supplied command, SQL, path, environment, network endpoint, or secret.

No PostgreSQL, Docker, SSH, network, Object Storage, restore, start, replay,
promotion, traffic, or writer action is implemented here.  A successful
receipt proves only local bootstrap materialization intent/result, never
writer authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_verified_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_recovery_preflight import (
    DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PhysicalPostgresRecoveryPreflightBinding,
    PhysicalPostgresRecoveryReceiverReadbackEvidence,
    PhysicalPostgresRecoveryStageBinding,
    assess_physical_postgres_recovery_preflight,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)
from core.physical_wal_receiver_staging import (
    MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
    PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA,
    PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
    PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
)


__all__ = (
    "DEFAULT_PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_MAX_RECOVERY_AGE_SECONDS",
    "PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA",
    "PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA",
    "PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_ROOT_CONFIG_SCHEMA",
    "DisabledPhysicalPostgresStandbyBootstrapMaterializer",
    "PhysicalPostgresStandbyBootstrapMaterializationAck",
    "PhysicalPostgresStandbyBootstrapMaterializationError",
    "PhysicalPostgresStandbyBootstrapMaterializationPlan",
    "PhysicalPostgresStandbyBootstrapMaterializationReceipt",
    "PhysicalPostgresStandbyBootstrapMaterializationResult",
    "PhysicalPostgresStandbyBootstrapMaterializer",
    "PhysicalPostgresStandbyBootstrapRootConfig",
    "PhysicalPostgresStandbyBootstrapStageEvidence",
    "materialize_physical_postgres_standby_bootstrap",
)


PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_ROOT_CONFIG_SCHEMA = (
    "gold-trade-physical-postgres-standby-bootstrap-root-config-v1"
)
PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA = (
    "gold-trade-physical-postgres-standby-bootstrap-plan-v1"
)
PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA = (
    "gold-trade-physical-postgres-standby-bootstrap-receipt-v1"
)
PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_MAX_RECOVERY_AGE_SECONDS = (
    DEFAULT_MAX_RECOVERY_EVIDENCE_AGE_SECONDS
)

_ROOT_OWNER_UID = 0
_SOURCE_SITE = "webapp_fi"
_RECEIVER_SITE = "webapp_ir"
_RECEIVER_ROLE = "standby"
_MATERIALIZER_STATUS = "local-standby-bootstrap-materialized"
_MAX_RECOVERY_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_REQUIRED_WAL_SEGMENT_SIZE_BYTES = 16 * 1024 * 1024
_MAX_PLAN_BYTES = 64 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)
_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_SAFE_ID_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_STAGE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "route_binding_sha256",
        "candidate_path",
        "manifest_sha256es",
        "object_versions",
        "artifacts",
        "receipt_sha256",
    }
)
_STAGE_OBJECT_FIELDS = frozenset({"object_key", "version_id"})
_STAGE_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "kind",
        "object_key",
        "version_id",
        "ciphertext_relative_path",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_relative_path",
        "plaintext_sha256",
        "plaintext_bytes",
        "wal_segment_name",
        "wal_start_lsn",
        "wal_end_lsn",
    }
)
_PLAN_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "bootstrap_id",
        "source_site",
        "receiver_site",
        "receiver_role",
        "bundle_id",
        "stage_receipt_sha256",
        "route_binding_sha256",
        "manifest_sha256es",
        "object_versions",
        "terminal_wal_lsn",
        "writer_term",
        "recovery_evidence_sha256",
        "source_stage_device",
        "source_stage_inode",
        "target_pgdata_device",
        "target_pgdata_inode",
        "recovery_signal_seed_sha256",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bootstrap_id",
        "plan_sha256",
        "source_stage_device",
        "source_stage_inode",
        "target_pgdata_device",
        "target_pgdata_inode",
        "recovery_signal_seed_sha256",
        "writer_term_proof_sha256",
        "materialized_at",
        "receipt_integrity_sha256",
    }
)
_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witness_transition_id",
        "witnessed_term_proof_sha256",
    }
)


class PhysicalPostgresStandbyBootstrapMaterializationError(ValueError):
    """A bootstrap candidate cannot safely be materialized."""


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapRootConfig:
    """Root-only fixed local roots; no caller-selected target or command exists."""

    schema: str = PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_ROOT_CONFIG_SCHEMA
    enabled: bool = PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_DEFAULT_ENABLED
    owner_uid: int = _ROOT_OWNER_UID
    source_staging_candidates_root: Path | None = None
    pgdata_candidates_root: Path | None = None
    receipt_root: Path | None = None
    failed_candidates_root: Path | None = None
    recovery_signal_seed_root: Path | None = None
    maximum_recovery_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_MAX_RECOVERY_AGE_SECONDS
    )


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapStageEvidence:
    """Canonical stage receipt plus the only permitted source-candidate path."""

    source_candidate: Path
    raw_stage_receipt: bytes
    stage_receipt_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapMaterializationPlan:
    """Opaque plan passed to a future local materializer, never a writer permit."""

    canonical_plan: bytes
    plan_sha256: str
    bootstrap_id: str
    source_site: str
    receiver_site: str
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str
    terminal_wal_lsn: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    source_stage_device: int
    source_stage_inode: int
    target_pgdata_device: int
    target_pgdata_inode: int
    recovery_signal_seed_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapMaterializationAck:
    """Narrow future-materializer result, bound to the opaque local plan."""

    status: str
    plan_sha256: str
    source_stage_device: int
    source_stage_inode: int
    target_pgdata_device: int
    target_pgdata_inode: int
    recovery_signal_seed_sha256: str
    materialized_at: datetime


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapMaterializationReceipt:
    """Canonical local intent/result receipt; it never confers writer authority."""

    raw_receipt: bytes
    receipt_sha256: str
    bootstrap_id: str
    plan_sha256: str
    materialized_at: datetime


@dataclass(frozen=True)
class PhysicalPostgresStandbyBootstrapMaterializationResult:
    """A materialized local candidate or a verified idempotent replay only."""

    plan: PhysicalPostgresStandbyBootstrapMaterializationPlan
    receipt: PhysicalPostgresStandbyBootstrapMaterializationReceipt
    target_pgdata_candidate: Path
    idempotent: bool


class PhysicalPostgresStandbyBootstrapMaterializer(Protocol):
    """Future fixed-FD local materializer; it has no arbitrary execution inputs."""

    def materialize_standby_bootstrap(
        self,
        *,
        plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        """Materialize only this prevalidated detached local candidate."""


class DisabledPhysicalPostgresStandbyBootstrapMaterializer:
    """Safe default until a separately authorized local materializer is installed."""

    def materialize_standby_bootstrap(
        self,
        *,
        plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
        source_stage_fd: int,
        target_pgdata_fd: int,
        recovery_signal_seed_fd: int,
    ) -> PhysicalPostgresStandbyBootstrapMaterializationAck:
        del plan, source_stage_fd, target_pgdata_fd, recovery_signal_seed_fd
        raise PhysicalPostgresStandbyBootstrapMaterializationError("MATERIALIZER_DISABLED")


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int


@dataclass(frozen=True)
class _RootFacts:
    owner_uid: int
    source_staging_candidates_root: Path
    pgdata_candidates_root: Path
    receipt_root: Path
    failed_candidates_root: Path
    recovery_signal_seed_root: Path
    maximum_recovery_evidence_age_seconds: int


@dataclass(frozen=True)
class _TermFacts:
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str


@dataclass(frozen=True)
class _BundleFacts:
    bundle: VerifiedPhysicalWalObjectStorageBundle
    source_site: str
    destination_site: str
    bundle_id: str
    route_binding_sha256: str
    terminal_wal_lsn: str
    terminal_wal_lsn_value: int
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[tuple[str, str], ...]
    baseline_generation_id: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int


@dataclass(frozen=True)
class _StageFacts:
    source_candidate: Path
    source_identity: _Identity
    bundle_id: str
    stage_receipt_sha256: str
    route_binding_sha256: str


def _fail(code: str) -> None:
    raise PhysicalPostgresStandbyBootstrapMaterializationError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("BOOTSTRAP_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("BOOTSTRAP_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in {_SOURCE_SITE, _RECEIVER_SITE}:
        _fail(code)
    return value


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _identity(metadata: os.stat_result) -> _Identity:
    return _Identity(device=metadata.st_dev, inode=metadata.st_ino)


def _safe_directory(path: object, *, owner_uid: int, code: str) -> tuple[Path, _Identity]:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail(code)
    try:
        resolved = path.resolve(strict=True)
        metadata = os.lstat(path)
    except OSError:
        _fail(code)
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved, _identity(metadata)


def _assert_directory_identity(
    path: Path,
    *,
    owner_uid: int,
    expected: _Identity,
    code: str,
) -> None:
    _resolved, identity = _safe_directory(path, owner_uid=owner_uid, code=code)
    if identity != expected:
        _fail(code)


def _overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


def _root_config(value: object) -> _RootFacts:
    if type(value) is not PhysicalPostgresStandbyBootstrapRootConfig:
        _fail("BOOTSTRAP_ROOT_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("BOOTSTRAP_DISABLED")
    if value.schema != PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_ROOT_CONFIG_SCHEMA:
        _fail("BOOTSTRAP_ROOT_CONFIG_INVALID")
    if type(value.owner_uid) is not int or value.owner_uid != _ROOT_OWNER_UID:
        _fail("BOOTSTRAP_ROOT_CONFIG_NOT_ROOT")
    if os.geteuid() != value.owner_uid:
        _fail("BOOTSTRAP_PROCESS_NOT_ROOT")
    source_root, _source_identity = _safe_directory(
        value.source_staging_candidates_root,
        owner_uid=value.owner_uid,
        code="BOOTSTRAP_SOURCE_ROOT_UNSAFE",
    )
    pgdata_root, pgdata_identity = _safe_directory(
        value.pgdata_candidates_root,
        owner_uid=value.owner_uid,
        code="BOOTSTRAP_PGDATA_ROOT_UNSAFE",
    )
    receipt_root, _receipt_identity = _safe_directory(
        value.receipt_root,
        owner_uid=value.owner_uid,
        code="BOOTSTRAP_RECEIPT_ROOT_UNSAFE",
    )
    failed_root, failed_identity = _safe_directory(
        value.failed_candidates_root,
        owner_uid=value.owner_uid,
        code="BOOTSTRAP_FAILED_ROOT_UNSAFE",
    )
    seed_root, _seed_identity = _safe_directory(
        value.recovery_signal_seed_root,
        owner_uid=value.owner_uid,
        code="BOOTSTRAP_RECOVERY_SIGNAL_ROOT_UNSAFE",
    )
    roots = (source_root, pgdata_root, receipt_root, failed_root, seed_root)
    if any(_overlap(left, right) for index, left in enumerate(roots) for right in roots[index + 1 :]):
        _fail("BOOTSTRAP_ROOTS_OVERLAP")
    if pgdata_identity.device != failed_identity.device:
        _fail("BOOTSTRAP_FAILURE_ROOT_NOT_RENAME_COMPATIBLE")
    maximum_age = _positive_int(
        value.maximum_recovery_evidence_age_seconds,
        maximum=_MAX_RECOVERY_AGE_SECONDS,
        code="BOOTSTRAP_RECOVERY_AGE_INVALID",
    )
    return _RootFacts(
        owner_uid=value.owner_uid,
        source_staging_candidates_root=source_root,
        pgdata_candidates_root=pgdata_root,
        receipt_root=receipt_root,
        failed_candidates_root=failed_root,
        recovery_signal_seed_root=seed_root,
        maximum_recovery_evidence_age_seconds=maximum_age,
    )


def _term(value: object, *, now: datetime, code: str) -> _TermFacts:
    try:
        term = require_verified_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail(code)
    return _TermFacts(
        holder_site=_site(term.holder_site, code=code),
        writer_epoch=_positive_int(term.writer_epoch, maximum=2**63 - 1, code=code),
        writer_lease_id=_text(term.writer_lease_id, pattern=LEASE_ID_RE, code=code),
        witness_transition_id=_text(
            term.witness_transition_id,
            pattern=_TRANSITION_ID_RE,
            code=code,
        ),
        proof_sha256=_sha256(term.proof_sha256, code=code),
    )


def _object_versions(bundle: VerifiedPhysicalWalObjectStorageBundle) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = [
        (
            bundle.baseline.base_backup_object.object_key,
            bundle.baseline.base_backup_object.version_id,
        )
    ]
    for manifest in bundle.wal_manifests:
        pairs.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
    pairs.extend(
        (shard.object.object_key, shard.object.version_id)
        for shard in bundle.blob_frontier.inventory_shards
    )
    result: list[tuple[str, str]] = []
    for object_key, version_id in pairs:
        key = _text(object_key, pattern=OBJECT_KEY_RE, code="BOOTSTRAP_BUNDLE_OBJECT_INVALID")
        version = _text(version_id, pattern=VERSION_ID_RE, code="BOOTSTRAP_BUNDLE_OBJECT_INVALID")
        if not key.endswith(".age") or version.casefold() in {"null", "none", "latest", "current"}:
            _fail("BOOTSTRAP_BUNDLE_OBJECT_INVALID")
        result.append((key, version))
    normalized = tuple(result)
    if not normalized or len(set(normalized)) != len(normalized):
        _fail("BOOTSTRAP_BUNDLE_OBJECT_INVALID")
    return normalized


def _bundle_id(*, route_binding_sha256: str, manifest_sha256es: tuple[str, ...]) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
                "route_binding_sha256": route_binding_sha256,
                "manifest_sha256es": list(manifest_sha256es),
            },
            code="BOOTSTRAP_BUNDLE_ID_INVALID",
        )
    ).hexdigest()


def _bundle(value: object, *, term: _TermFacts, route_binding_sha256: str) -> _BundleFacts:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError):
        _fail("BOOTSTRAP_BUNDLE_UNVERIFIED")
    baseline = bundle.baseline
    source_site = _site(baseline.source_site, code="BOOTSTRAP_BUNDLE_ROUTE_INVALID")
    destination_site = _site(baseline.destination_site, code="BOOTSTRAP_BUNDLE_ROUTE_INVALID")
    if source_site != _SOURCE_SITE or destination_site != _RECEIVER_SITE or term.holder_site != source_site:
        _fail("BOOTSTRAP_BUNDLE_ROUTE_INVALID")
    if (
        baseline.writer_term.epoch != term.writer_epoch
        or baseline.writer_term.lease_id != term.writer_lease_id
        or baseline.writer_term.witnessed_term_proof_sha256 != term.proof_sha256
    ):
        _fail("BOOTSTRAP_BUNDLE_TERM_MISMATCH")
    timeline_id = _positive_int(
        baseline.timeline_id,
        maximum=0xFFFFFFFF,
        code="BOOTSTRAP_BUNDLE_BASELINE_INVALID",
    )
    wal_segment_size = _positive_int(
        baseline.wal_segment_size_bytes,
        maximum=2**31 - 1,
        code="BOOTSTRAP_BUNDLE_BASELINE_INVALID",
    )
    if (
        wal_segment_size != _REQUIRED_WAL_SEGMENT_SIZE_BYTES
        or wal_segment_size not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES
    ):
        _fail("BOOTSTRAP_BUNDLE_BASELINE_INVALID")
    terminal_lsn, terminal_lsn_value = _lsn(
        bundle.terminal_wal_lsn,
        code="BOOTSTRAP_BUNDLE_TERMINAL_LSN_INVALID",
    )
    manifests = tuple(
        _sha256(item, code="BOOTSTRAP_BUNDLE_MANIFEST_INVALID")
        for item in bundle.manifest_sha256es
    )
    if not manifests or len(set(manifests)) != len(manifests):
        _fail("BOOTSTRAP_BUNDLE_MANIFEST_INVALID")
    route = _sha256(route_binding_sha256, code="BOOTSTRAP_STAGE_ROUTE_BINDING_INVALID")
    return _BundleFacts(
        bundle=bundle,
        source_site=source_site,
        destination_site=destination_site,
        bundle_id=_bundle_id(route_binding_sha256=route, manifest_sha256es=manifests),
        route_binding_sha256=route,
        terminal_wal_lsn=terminal_lsn,
        terminal_wal_lsn_value=terminal_lsn_value,
        manifest_sha256es=manifests,
        object_versions=_object_versions(bundle),
        baseline_generation_id=_text(
            baseline.baseline_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            code="BOOTSTRAP_BUNDLE_BASELINE_INVALID",
        ),
        database_system_identifier=_text(
            baseline.database_system_identifier,
            pattern=_SYSTEM_IDENTIFIER_RE,
            code="BOOTSTRAP_BUNDLE_BASELINE_INVALID",
        ),
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size,
    )


def _open_directory(path: Path, *, owner_uid: int, code: str) -> tuple[int, _Identity]:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail("BOOTSTRAP_PLATFORM_SAFE_DIRECTORY_OPEN_UNAVAILABLE")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail(code)
        return descriptor, _identity(metadata)
    except Exception:
        os.close(descriptor)
        raise


def _open_empty_seed(root: Path, *, owner_uid: int) -> tuple[int, _Identity, str]:
    path = root / "recovery.signal.seed"
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BOOTSTRAP_PLATFORM_SAFE_FILE_OPEN_UNAVAILABLE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("BOOTSTRAP_RECOVERY_SIGNAL_SEED_UNSAFE")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
            or os.read(descriptor, 1) != b""
        ):
            _fail("BOOTSTRAP_RECOVERY_SIGNAL_SEED_UNSAFE")
        return descriptor, _identity(metadata), hashlib.sha256(b"").hexdigest()
    except Exception:
        os.close(descriptor)
        raise


def _read_existing_file(
    path: Path,
    *,
    owner_uid: int,
    required_mode: int,
    maximum_bytes: int,
    code: str,
) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BOOTSTRAP_PLATFORM_SAFE_FILE_OPEN_UNAVAILABLE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) != required_mode
            or not 1 <= metadata.st_size <= maximum_bytes
        ):
            _fail(code)
        chunks = bytearray()
        while len(chunks) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(chunks))
            if not chunk:
                _fail(code)
            chunks.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(chunks)
    except Exception:
        os.close(descriptor)
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _parse_canonical_mapping(raw: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES:
        _fail(code)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalPostgresStandbyBootstrapMaterializationError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict) or set(value) != fields or _canonical(value, code=code) != raw:
        _fail(code)
    return value


def _expected_artifacts(bundle: _BundleFacts) -> tuple[dict[str, Any], ...]:
    expected: list[dict[str, Any]] = []
    base = bundle.bundle.baseline.base_backup_object
    expected.append(
        {
            "artifact_id": "base-backup",
            "kind": "base-backup",
            "object_key": base.object_key,
            "version_id": base.version_id,
            "ciphertext_relative_path": "material/base-backup.age",
            "ciphertext_sha256": base.ciphertext_sha256,
            "ciphertext_bytes": base.ciphertext_bytes,
            "plaintext_relative_path": "material/base-backup.plain",
            "wal_segment_name": None,
            "wal_start_lsn": None,
            "wal_end_lsn": None,
            "expected_plaintext_sha256": None,
            "expected_plaintext_bytes": None,
        }
    )
    for manifest in bundle.bundle.wal_manifests:
        for segment in manifest.segments:
            expected.append(
                {
                    "artifact_id": "wal-" + segment.wal_segment_name,
                    "kind": "wal",
                    "object_key": segment.object.object_key,
                    "version_id": segment.object.version_id,
                    "ciphertext_relative_path": "material/wal/" + segment.wal_segment_name + ".age",
                    "ciphertext_sha256": segment.object.ciphertext_sha256,
                    "ciphertext_bytes": segment.object.ciphertext_bytes,
                    "plaintext_relative_path": "material/wal/" + segment.wal_segment_name,
                    "wal_segment_name": segment.wal_segment_name,
                    "wal_start_lsn": segment.start_lsn,
                    "wal_end_lsn": segment.end_lsn,
                    "expected_plaintext_sha256": None,
                    "expected_plaintext_bytes": bundle.wal_segment_size_bytes,
                }
            )
    for shard in bundle.bundle.blob_frontier.inventory_shards:
        ordinal = f"{shard.ordinal:08d}"
        expected.append(
            {
                "artifact_id": "blob-inventory-" + ordinal,
                "kind": "blob-inventory",
                "object_key": shard.object.object_key,
                "version_id": shard.object.version_id,
                "ciphertext_relative_path": "material/blob-inventory/" + ordinal + ".age",
                "ciphertext_sha256": shard.object.ciphertext_sha256,
                "ciphertext_bytes": shard.object.ciphertext_bytes,
                "plaintext_relative_path": "material/blob-inventory/" + ordinal + ".inventory",
                "wal_segment_name": None,
                "wal_start_lsn": None,
                "wal_end_lsn": None,
                "expected_plaintext_sha256": shard.plaintext_sha256,
                "expected_plaintext_bytes": shard.plaintext_bytes,
            }
        )
    return tuple(expected)


def _validate_stage_artifact(value: object, *, expected: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != _STAGE_ARTIFACT_FIELDS:
        _fail("BOOTSTRAP_STAGE_ARTIFACT_INVALID")
    item = dict(value)
    for key in (
        "artifact_id",
        "kind",
        "object_key",
        "version_id",
        "ciphertext_relative_path",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_relative_path",
        "wal_segment_name",
        "wal_start_lsn",
        "wal_end_lsn",
    ):
        if item[key] != expected[key]:
            _fail("BOOTSTRAP_STAGE_ARTIFACT_MISMATCH")
    _sha256(item["plaintext_sha256"], code="BOOTSTRAP_STAGE_ARTIFACT_INVALID")
    plaintext_bytes = _positive_int(
        item["plaintext_bytes"],
        maximum=2**63 - 1,
        code="BOOTSTRAP_STAGE_ARTIFACT_INVALID",
    )
    if expected["expected_plaintext_sha256"] is not None and (
        item["plaintext_sha256"] != expected["expected_plaintext_sha256"]
        or plaintext_bytes != expected["expected_plaintext_bytes"]
    ):
        _fail("BOOTSTRAP_STAGE_ARTIFACT_MISMATCH")
    if expected["kind"] == "wal" and plaintext_bytes != expected["expected_plaintext_bytes"]:
        _fail("BOOTSTRAP_STAGE_ARTIFACT_MISMATCH")


def _stage(
    value: object,
    *,
    root: _RootFacts,
    bundle: _BundleFacts,
    expected_stage_bundle_id: str,
    expected_stage_receipt_sha256: str,
    expected_route_binding_sha256: str,
) -> _StageFacts:
    if type(value) is not PhysicalPostgresStandbyBootstrapStageEvidence:
        _fail("BOOTSTRAP_STAGE_EVIDENCE_INVALID")
    expected_candidate = root.source_staging_candidates_root / bundle.bundle_id
    if value.source_candidate != expected_candidate:
        _fail("BOOTSTRAP_STAGE_SOURCE_PATH_INVALID")
    candidate, candidate_identity = _safe_directory(
        value.source_candidate,
        owner_uid=root.owner_uid,
        code="BOOTSTRAP_STAGE_SOURCE_UNSAFE",
    )
    actual_raw = _read_existing_file(
        candidate / "stage-receipt.json",
        owner_uid=root.owner_uid,
        # The physical-WAL stager freezes canonical receipts at 0400 after
        # fsync.  Requiring that exact form prevents a writable replacement
        # from becoming bootstrap evidence.
        required_mode=0o400,
        maximum_bytes=MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
        code="BOOTSTRAP_STAGE_RECEIPT_UNSAFE",
    )
    if actual_raw != value.raw_stage_receipt:
        _fail("BOOTSTRAP_STAGE_RECEIPT_READBACK_MISMATCH")
    receipt = _parse_canonical_mapping(
        actual_raw,
        fields=_STAGE_FIELDS,
        code="BOOTSTRAP_STAGE_RECEIPT_INVALID",
    )
    if (
        receipt["schema"] != PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA
        or receipt["status"] != PHYSICAL_WAL_RECEIVER_STAGING_STATUS
        or receipt["bundle_id"] != bundle.bundle_id
        or receipt["candidate_path"] != str(candidate)
        or receipt["route_binding_sha256"] != bundle.route_binding_sha256
    ):
        _fail("BOOTSTRAP_STAGE_RECEIPT_BINDING_INVALID")
    stage_hash = _sha256(receipt["receipt_sha256"], code="BOOTSTRAP_STAGE_RECEIPT_INVALID")
    unsigned = {key: item for key, item in receipt.items() if key != "receipt_sha256"}
    if hashlib.sha256(_canonical(unsigned, code="BOOTSTRAP_STAGE_RECEIPT_INVALID")).hexdigest() != stage_hash:
        _fail("BOOTSTRAP_STAGE_RECEIPT_HASH_INVALID")
    if (
        _sha256(value.stage_receipt_sha256, code="BOOTSTRAP_STAGE_EVIDENCE_INVALID") != stage_hash
        or stage_hash != _sha256(expected_stage_receipt_sha256, code="BOOTSTRAP_STAGE_BINDING_INVALID")
        or receipt["bundle_id"] != _sha256(expected_stage_bundle_id, code="BOOTSTRAP_STAGE_BINDING_INVALID")
        or receipt["route_binding_sha256"]
        != _sha256(expected_route_binding_sha256, code="BOOTSTRAP_STAGE_BINDING_INVALID")
    ):
        _fail("BOOTSTRAP_STAGE_BINDING_INVALID")
    manifests = receipt["manifest_sha256es"]
    if not isinstance(manifests, list) or tuple(manifests) != bundle.manifest_sha256es:
        _fail("BOOTSTRAP_STAGE_MANIFEST_MISMATCH")
    objects = receipt["object_versions"]
    if not isinstance(objects, list) or len(objects) != len(bundle.object_versions):
        _fail("BOOTSTRAP_STAGE_OBJECT_MISMATCH")
    for actual, expected in zip(objects, bundle.object_versions, strict=True):
        if not isinstance(actual, Mapping) or set(actual) != _STAGE_OBJECT_FIELDS:
            _fail("BOOTSTRAP_STAGE_OBJECT_MISMATCH")
        if (actual["object_key"], actual["version_id"]) != expected:
            _fail("BOOTSTRAP_STAGE_OBJECT_MISMATCH")
    artifacts = receipt["artifacts"]
    expected_artifacts = _expected_artifacts(bundle)
    if not isinstance(artifacts, list) or len(artifacts) != len(expected_artifacts):
        _fail("BOOTSTRAP_STAGE_ARTIFACT_INVALID")
    for actual, expected in zip(artifacts, expected_artifacts, strict=True):
        _validate_stage_artifact(actual, expected=expected)
    return _StageFacts(
        source_candidate=candidate,
        source_identity=candidate_identity,
        bundle_id=bundle.bundle_id,
        stage_receipt_sha256=stage_hash,
        route_binding_sha256=bundle.route_binding_sha256,
    )


def _recovery(
    value: object,
    *,
    bundle: _BundleFacts,
    binding: object,
    now: datetime,
    maximum_age: int,
) -> str:
    if type(value) is not PhysicalPostgresRecoveryReceiverReadbackEvidence:
        _fail("BOOTSTRAP_RECOVERY_EVIDENCE_INVALID")
    evidence_hash = _sha256(value.evidence_sha256, code="BOOTSTRAP_RECOVERY_EVIDENCE_INVALID")
    if not isinstance(value.raw_evidence, bytes) or hashlib.sha256(value.raw_evidence).hexdigest() != evidence_hash:
        _fail("BOOTSTRAP_RECOVERY_EVIDENCE_INVALID")
    result = assess_physical_postgres_recovery_preflight(
        bundle=bundle.bundle,
        binding=binding,
        receiver_readback_evidence=value,
        now=now,
        maximum_evidence_age_seconds=maximum_age,
    )
    if (
        result.status != PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
        or result.evidence_sha256 != evidence_hash
        or result.terminal_wal_lsn != bundle.terminal_wal_lsn
    ):
        _fail("BOOTSTRAP_RECOVERY_EVIDENCE_NOT_OBSERVED")
    return evidence_hash


def _binding(
    value: object,
    *,
    current_term: _TermFacts,
    now: datetime,
) -> tuple[str, str, str, _TermFacts]:
    if type(value) is not PhysicalPostgresRecoveryPreflightBinding:
        _fail("BOOTSTRAP_PREFLIGHT_BINDING_INVALID")
    if value.local_standby_site != _RECEIVER_SITE:
        _fail("BOOTSTRAP_PREFLIGHT_BINDING_ROUTE_INVALID")
    stage = value.stage_binding
    if type(stage) is not PhysicalPostgresRecoveryStageBinding:
        _fail("BOOTSTRAP_PREFLIGHT_BINDING_INVALID")
    expected_term = _term(
        value.expected_witnessed_term,
        now=now,
        code="BOOTSTRAP_EXPECTED_TERM_INVALID",
    )
    if expected_term != current_term:
        _fail("BOOTSTRAP_CURRENT_TERM_MISMATCH")
    return (
        _sha256(stage.bundle_id, code="BOOTSTRAP_PREFLIGHT_BINDING_INVALID"),
        _sha256(stage.stage_receipt_sha256, code="BOOTSTRAP_PREFLIGHT_BINDING_INVALID"),
        _sha256(stage.route_binding_sha256, code="BOOTSTRAP_PREFLIGHT_BINDING_INVALID"),
        expected_term,
    )


def _seed_identity(root: _RootFacts) -> tuple[_Identity, str]:
    descriptor, identity, digest = _open_empty_seed(root.recovery_signal_seed_root, owner_uid=root.owner_uid)
    try:
        return identity, digest
    finally:
        os.close(descriptor)


def _bootstrap_id(
    *,
    bundle: _BundleFacts,
    stage: _StageFacts,
    term: _TermFacts,
    recovery_evidence_sha256: str,
    source_identity: _Identity,
    seed_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA,
                "kind": "local_standby_bootstrap_intent",
                "source_site": bundle.source_site,
                "receiver_site": bundle.destination_site,
                "bundle_id": bundle.bundle_id,
                "stage_receipt_sha256": stage.stage_receipt_sha256,
                "route_binding_sha256": stage.route_binding_sha256,
                "writer_epoch": term.writer_epoch,
                "writer_lease_id": term.writer_lease_id,
                "witness_transition_id": term.witness_transition_id,
                "witnessed_term_proof_sha256": term.proof_sha256,
                "recovery_evidence_sha256": recovery_evidence_sha256,
                "source_stage_device": source_identity.device,
                "source_stage_inode": source_identity.inode,
                "recovery_signal_seed_sha256": seed_sha256,
            },
            code="BOOTSTRAP_INTENT_CANONICAL_INVALID",
        )
    ).hexdigest()


def _target_path(root: _RootFacts, bootstrap_id: str) -> Path:
    if _SAFE_ID_RE.fullmatch(bootstrap_id) is None:
        _fail("BOOTSTRAP_IDENTIFIER_INVALID")
    return root.pgdata_candidates_root / bootstrap_id


def _receipt_path(root: _RootFacts, bootstrap_id: str) -> Path:
    if _SAFE_ID_RE.fullmatch(bootstrap_id) is None:
        _fail("BOOTSTRAP_IDENTIFIER_INVALID")
    return root.receipt_root / (bootstrap_id + ".json")


def _create_empty_target(root: _RootFacts, bootstrap_id: str) -> tuple[Path, int, _Identity]:
    target = _target_path(root, bootstrap_id)
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        _fail("BOOTSTRAP_PGDATA_TARGET_REUSED")
    except OSError:
        _fail("BOOTSTRAP_PGDATA_TARGET_CREATE_FAILED")
    try:
        resolved, identity = _safe_directory(
            target,
            owner_uid=root.owner_uid,
            code="BOOTSTRAP_PGDATA_TARGET_UNSAFE",
        )
        descriptor, opened_identity = _open_directory(
            resolved,
            owner_uid=root.owner_uid,
            code="BOOTSTRAP_PGDATA_TARGET_UNSAFE",
        )
        if identity != opened_identity or os.listdir(descriptor):
            os.close(descriptor)
            _fail("BOOTSTRAP_PGDATA_TARGET_NOT_EMPTY_OR_RACED")
        return resolved, descriptor, identity
    except Exception:
        try:
            target.rmdir()
        except OSError:
            pass
        raise


def _plan(
    *,
    bootstrap_id: str,
    bundle: _BundleFacts,
    stage: _StageFacts,
    term: _TermFacts,
    recovery_evidence_sha256: str,
    source_identity: _Identity,
    target_identity: _Identity,
    seed_sha256: str,
) -> PhysicalPostgresStandbyBootstrapMaterializationPlan:
    payload = {
        "schema": PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA,
        "kind": "local_standby_bootstrap_materialization_intent",
        "bootstrap_id": bootstrap_id,
        "source_site": bundle.source_site,
        "receiver_site": bundle.destination_site,
        "receiver_role": _RECEIVER_ROLE,
        "bundle_id": bundle.bundle_id,
        "stage_receipt_sha256": stage.stage_receipt_sha256,
        "route_binding_sha256": stage.route_binding_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "object_versions": [
            {"object_key": object_key, "version_id": version_id}
            for object_key, version_id in bundle.object_versions
        ],
        "terminal_wal_lsn": bundle.terminal_wal_lsn,
        "writer_term": {
            "holder_site": term.holder_site,
            "writer_epoch": term.writer_epoch,
            "writer_lease_id": term.writer_lease_id,
            "witness_transition_id": term.witness_transition_id,
            "witnessed_term_proof_sha256": term.proof_sha256,
        },
        "recovery_evidence_sha256": recovery_evidence_sha256,
        "source_stage_device": source_identity.device,
        "source_stage_inode": source_identity.inode,
        "target_pgdata_device": target_identity.device,
        "target_pgdata_inode": target_identity.inode,
        "recovery_signal_seed_sha256": seed_sha256,
    }
    raw = _canonical(payload, code="BOOTSTRAP_PLAN_CANONICAL_INVALID")
    if not 1 <= len(raw) <= _MAX_PLAN_BYTES:
        _fail("BOOTSTRAP_PLAN_BYTES_INVALID")
    digest = hashlib.sha256(raw).hexdigest()
    return PhysicalPostgresStandbyBootstrapMaterializationPlan(
        canonical_plan=raw,
        plan_sha256=digest,
        bootstrap_id=bootstrap_id,
        source_site=bundle.source_site,
        receiver_site=bundle.destination_site,
        bundle_id=bundle.bundle_id,
        stage_receipt_sha256=stage.stage_receipt_sha256,
        route_binding_sha256=stage.route_binding_sha256,
        terminal_wal_lsn=bundle.terminal_wal_lsn,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witnessed_term_proof_sha256=term.proof_sha256,
        source_stage_device=source_identity.device,
        source_stage_inode=source_identity.inode,
        target_pgdata_device=target_identity.device,
        target_pgdata_inode=target_identity.inode,
        recovery_signal_seed_sha256=seed_sha256,
    )


def _ack(value: object, *, plan: PhysicalPostgresStandbyBootstrapMaterializationPlan, now: datetime) -> datetime:
    if type(value) is not PhysicalPostgresStandbyBootstrapMaterializationAck:
        _fail("BOOTSTRAP_MATERIALIZER_ACK_INVALID")
    materialized_at = _utc(value.materialized_at, code="BOOTSTRAP_MATERIALIZER_ACK_TIME_INVALID")
    if materialized_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("BOOTSTRAP_MATERIALIZER_ACK_TIME_INVALID")
    if (
        value.status != _MATERIALIZER_STATUS
        or _sha256(value.plan_sha256, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.plan_sha256
        or _positive_int(value.source_stage_device, maximum=2**63 - 1, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.source_stage_device
        or _positive_int(value.source_stage_inode, maximum=2**63 - 1, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.source_stage_inode
        or _positive_int(value.target_pgdata_device, maximum=2**63 - 1, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.target_pgdata_device
        or _positive_int(value.target_pgdata_inode, maximum=2**63 - 1, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.target_pgdata_inode
        or _sha256(value.recovery_signal_seed_sha256, code="BOOTSTRAP_MATERIALIZER_ACK_INVALID")
        != plan.recovery_signal_seed_sha256
    ):
        _fail("BOOTSTRAP_MATERIALIZER_ACK_MISMATCH")
    return materialized_at


def _receipt(
    *,
    plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
    materialized_at: datetime,
) -> PhysicalPostgresStandbyBootstrapMaterializationReceipt:
    unsigned = {
        "schema": PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA,
        "status": _MATERIALIZER_STATUS,
        "bootstrap_id": plan.bootstrap_id,
        "plan_sha256": plan.plan_sha256,
        "source_stage_device": plan.source_stage_device,
        "source_stage_inode": plan.source_stage_inode,
        "target_pgdata_device": plan.target_pgdata_device,
        "target_pgdata_inode": plan.target_pgdata_inode,
        "recovery_signal_seed_sha256": plan.recovery_signal_seed_sha256,
        "writer_term_proof_sha256": plan.witnessed_term_proof_sha256,
        "materialized_at": materialized_at.isoformat(),
    }
    integrity = hashlib.sha256(_canonical(unsigned, code="BOOTSTRAP_RECEIPT_CANONICAL_INVALID")).hexdigest()
    raw = _canonical(
        {**unsigned, "receipt_integrity_sha256": integrity},
        code="BOOTSTRAP_RECEIPT_CANONICAL_INVALID",
    )
    if not 1 <= len(raw) <= _MAX_RECEIPT_BYTES:
        _fail("BOOTSTRAP_RECEIPT_BYTES_INVALID")
    return PhysicalPostgresStandbyBootstrapMaterializationReceipt(
        raw_receipt=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        bootstrap_id=plan.bootstrap_id,
        plan_sha256=plan.plan_sha256,
        materialized_at=materialized_at,
    )


def _write_receipt(path: Path, *, root: _RootFacts, receipt: PhysicalPostgresStandbyBootstrapMaterializationReceipt) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("BOOTSTRAP_PLATFORM_SAFE_FILE_OPEN_UNAVAILABLE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail("BOOTSTRAP_RECEIPT_REPLAY_CONFLICT")
    except OSError:
        _fail("BOOTSTRAP_RECEIPT_WRITE_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != root.owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("BOOTSTRAP_RECEIPT_WRITE_FAILED")
        view = memoryview(receipt.raw_receipt)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("BOOTSTRAP_RECEIPT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        _fail("BOOTSTRAP_RECEIPT_WRITE_FAILED")
    finally:
        os.close(descriptor)


def _parse_receipt(raw: bytes, *, plan: PhysicalPostgresStandbyBootstrapMaterializationPlan, now: datetime) -> PhysicalPostgresStandbyBootstrapMaterializationReceipt:
    item = _parse_canonical_mapping(raw, fields=_RECEIPT_FIELDS, code="BOOTSTRAP_RECEIPT_INVALID")
    if (
        item["schema"] != PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_RECEIPT_SCHEMA
        or item["status"] != _MATERIALIZER_STATUS
        or item["bootstrap_id"] != plan.bootstrap_id
        or _sha256(item["plan_sha256"], code="BOOTSTRAP_RECEIPT_INVALID") != plan.plan_sha256
        or _positive_int(item["source_stage_device"], maximum=2**63 - 1, code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.source_stage_device
        or _positive_int(item["source_stage_inode"], maximum=2**63 - 1, code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.source_stage_inode
        or _positive_int(item["target_pgdata_device"], maximum=2**63 - 1, code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.target_pgdata_device
        or _positive_int(item["target_pgdata_inode"], maximum=2**63 - 1, code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.target_pgdata_inode
        or _sha256(item["recovery_signal_seed_sha256"], code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.recovery_signal_seed_sha256
        or _sha256(item["writer_term_proof_sha256"], code="BOOTSTRAP_RECEIPT_INVALID")
        != plan.witnessed_term_proof_sha256
    ):
        _fail("BOOTSTRAP_RECEIPT_BINDING_MISMATCH")
    materialized_at = _timestamp(item["materialized_at"], code="BOOTSTRAP_RECEIPT_INVALID")
    if materialized_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        _fail("BOOTSTRAP_RECEIPT_INVALID")
    unsigned = {key: value for key, value in item.items() if key != "receipt_integrity_sha256"}
    if (
        _sha256(item["receipt_integrity_sha256"], code="BOOTSTRAP_RECEIPT_INVALID")
        != hashlib.sha256(_canonical(unsigned, code="BOOTSTRAP_RECEIPT_INVALID")).hexdigest()
    ):
        _fail("BOOTSTRAP_RECEIPT_INVALID")
    return PhysicalPostgresStandbyBootstrapMaterializationReceipt(
        raw_receipt=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        bootstrap_id=plan.bootstrap_id,
        plan_sha256=plan.plan_sha256,
        materialized_at=materialized_at,
    )


def _existing_receipt(
    *,
    root: _RootFacts,
    plan: PhysicalPostgresStandbyBootstrapMaterializationPlan,
    now: datetime,
) -> PhysicalPostgresStandbyBootstrapMaterializationReceipt | None:
    path = _receipt_path(root, plan.bootstrap_id)
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("BOOTSTRAP_RECEIPT_UNSAFE")
    raw = _read_existing_file(
        path,
        owner_uid=root.owner_uid,
        required_mode=0o600,
        maximum_bytes=_MAX_RECEIPT_BYTES,
        code="BOOTSTRAP_RECEIPT_UNSAFE",
    )
    return _parse_receipt(raw, plan=plan, now=now)


def _assert_source_stage_stable(root: _RootFacts, stage: _StageFacts, raw_receipt: bytes) -> None:
    _assert_directory_identity(
        stage.source_candidate,
        owner_uid=root.owner_uid,
        expected=stage.source_identity,
        code="BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED",
    )
    actual = _read_existing_file(
        stage.source_candidate / "stage-receipt.json",
        owner_uid=root.owner_uid,
        required_mode=0o400,
        maximum_bytes=MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
        code="BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED",
    )
    if actual != raw_receipt:
        _fail("BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED")


def _assert_seed_stable(root: _RootFacts, expected: _Identity) -> None:
    descriptor, identity, digest = _open_empty_seed(root.recovery_signal_seed_root, owner_uid=root.owner_uid)
    try:
        if identity != expected or digest != hashlib.sha256(b"").hexdigest():
            _fail("BOOTSTRAP_RECOVERY_SIGNAL_RACE_DETECTED")
    finally:
        os.close(descriptor)


def _assert_target_stable(root: _RootFacts, path: Path, expected: _Identity) -> None:
    _assert_directory_identity(
        path,
        owner_uid=root.owner_uid,
        expected=expected,
        code="BOOTSTRAP_PGDATA_TARGET_RACE_DETECTED",
    )


def _cleanup_failed_target(root: _RootFacts, *, target: Path, expected: _Identity, bootstrap_id: str) -> None:
    try:
        _assert_target_stable(root, target, expected)
    except PhysicalPostgresStandbyBootstrapMaterializationError:
        return
    try:
        target.rmdir()
        return
    except OSError:
        pass
    quarantine = root.failed_candidates_root / (bootstrap_id + "-" + secrets.token_hex(12))
    try:
        os.rename(target, quarantine)
    except OSError:
        _fail("BOOTSTRAP_FAILURE_CLEANUP_FAILED")


def _open_materializer_inputs(
    *,
    root: _RootFacts,
    stage: _StageFacts,
    target: Path,
    target_identity: _Identity,
) -> tuple[int, int, int, _Identity, str]:
    source_fd, source_identity = _open_directory(
        stage.source_candidate,
        owner_uid=root.owner_uid,
        code="BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED",
    )
    try:
        if source_identity != stage.source_identity:
            _fail("BOOTSTRAP_SOURCE_STAGE_RACE_DETECTED")
        target_fd, opened_target_identity = _open_directory(
            target,
            owner_uid=root.owner_uid,
            code="BOOTSTRAP_PGDATA_TARGET_RACE_DETECTED",
        )
        try:
            if opened_target_identity != target_identity:
                _fail("BOOTSTRAP_PGDATA_TARGET_RACE_DETECTED")
            seed_fd, seed_identity, seed_sha256 = _open_empty_seed(
                root.recovery_signal_seed_root,
                owner_uid=root.owner_uid,
            )
            return source_fd, target_fd, seed_fd, seed_identity, seed_sha256
        except Exception:
            os.close(target_fd)
            raise
    except Exception:
        os.close(source_fd)
        raise


def _materializer_method(value: object) -> Any:
    method = getattr(value, "materialize_standby_bootstrap", None)
    if not callable(method):
        _fail("BOOTSTRAP_MATERIALIZER_REQUIRED")
    return method


def materialize_physical_postgres_standby_bootstrap(
    *,
    root_config: PhysicalPostgresStandbyBootstrapRootConfig,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: PhysicalPostgresRecoveryPreflightBinding,
    current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    recovery_readback_evidence: PhysicalPostgresRecoveryReceiverReadbackEvidence,
    stage_evidence: PhysicalPostgresStandbyBootstrapStageEvidence,
    materializer: PhysicalPostgresStandbyBootstrapMaterializer,
    now: datetime,
) -> PhysicalPostgresStandbyBootstrapMaterializationResult:
    """Create one detached local PGDATA candidate for a future installed adapter.

    This function performs only secure local candidate/receipt bookkeeping. It
    does not extract a base backup, launch PostgreSQL, create recovery state,
    replay WAL, or promote anything.
    """

    root = _root_config(root_config)
    observed_now = _utc(now, code="BOOTSTRAP_CLOCK_INVALID")
    current_term = _term(
        current_witnessed_term,
        now=observed_now,
        code="BOOTSTRAP_CURRENT_TERM_INVALID",
    )
    expected_bundle_id, expected_stage_hash, expected_route_hash, expected_term = _binding(
        binding,
        current_term=current_term,
        now=observed_now,
    )
    bundle_facts = _bundle(
        bundle,
        term=expected_term,
        route_binding_sha256=expected_route_hash,
    )
    stage = _stage(
        stage_evidence,
        root=root,
        bundle=bundle_facts,
        expected_stage_bundle_id=expected_bundle_id,
        expected_stage_receipt_sha256=expected_stage_hash,
        expected_route_binding_sha256=expected_route_hash,
    )
    recovery_hash = _recovery(
        recovery_readback_evidence,
        bundle=bundle_facts,
        binding=binding,
        now=observed_now,
        maximum_age=root.maximum_recovery_evidence_age_seconds,
    )
    seed_identity, seed_sha256 = _seed_identity(root)
    bootstrap_id = _bootstrap_id(
        bundle=bundle_facts,
        stage=stage,
        term=expected_term,
        recovery_evidence_sha256=recovery_hash,
        source_identity=stage.source_identity,
        seed_sha256=seed_sha256,
    )
    target = _target_path(root, bootstrap_id)
    receipt_path = _receipt_path(root, bootstrap_id)
    try:
        target_metadata = os.lstat(target)
    except FileNotFoundError:
        target_metadata = None
    except OSError:
        _fail("BOOTSTRAP_PGDATA_TARGET_UNSAFE")
    if target_metadata is not None:
        target, target_identity = _safe_directory(
            target,
            owner_uid=root.owner_uid,
            code="BOOTSTRAP_PGDATA_TARGET_UNSAFE",
        )
        idempotent_plan = _plan(
            bootstrap_id=bootstrap_id,
            bundle=bundle_facts,
            stage=stage,
            term=expected_term,
            recovery_evidence_sha256=recovery_hash,
            source_identity=stage.source_identity,
            target_identity=target_identity,
            seed_sha256=seed_sha256,
        )
        receipt = _existing_receipt(root=root, plan=idempotent_plan, now=observed_now)
        if receipt is None:
            _fail("BOOTSTRAP_PGDATA_TARGET_REUSED")
        _assert_source_stage_stable(root, stage, stage_evidence.raw_stage_receipt)
        _assert_seed_stable(root, seed_identity)
        if _term(
            current_witnessed_term,
            now=observed_now,
            code="BOOTSTRAP_CURRENT_TERM_CHANGED",
        ) != expected_term:
            _fail("BOOTSTRAP_CURRENT_TERM_CHANGED")
        return PhysicalPostgresStandbyBootstrapMaterializationResult(
            plan=idempotent_plan,
            receipt=receipt,
            target_pgdata_candidate=target,
            idempotent=True,
        )
    try:
        os.lstat(receipt_path)
    except FileNotFoundError:
        pass
    except OSError:
        _fail("BOOTSTRAP_RECEIPT_UNSAFE")
    else:
        _fail("BOOTSTRAP_RECEIPT_WITHOUT_TARGET")
    target, target_fd, target_identity = _create_empty_target(root, bootstrap_id)
    plan = _plan(
        bootstrap_id=bootstrap_id,
        bundle=bundle_facts,
        stage=stage,
        term=expected_term,
        recovery_evidence_sha256=recovery_hash,
        source_identity=stage.source_identity,
        target_identity=target_identity,
        seed_sha256=seed_sha256,
    )
    source_fd: int | None = None
    seed_fd: int | None = None
    try:
        source_fd, opened_target_fd, seed_fd, opened_seed_identity, opened_seed_sha256 = _open_materializer_inputs(
            root=root,
            stage=stage,
            target=target,
            target_identity=target_identity,
        )
        os.close(target_fd)
        target_fd = opened_target_fd
        if opened_seed_identity != seed_identity or opened_seed_sha256 != seed_sha256:
            _fail("BOOTSTRAP_RECOVERY_SIGNAL_RACE_DETECTED")
        # Recheck every mutable local / witnessed input immediately before the
        # injected adapter becomes reachable.  This leaves candidate creation
        # as the only state change that can occur before a complete admission.
        _assert_source_stage_stable(root, stage, stage_evidence.raw_stage_receipt)
        _assert_seed_stable(root, seed_identity)
        if _term(
            current_witnessed_term,
            now=observed_now,
            code="BOOTSTRAP_CURRENT_TERM_CHANGED",
        ) != expected_term:
            _fail("BOOTSTRAP_CURRENT_TERM_CHANGED")
        if _term(
            binding.expected_witnessed_term,
            now=observed_now,
            code="BOOTSTRAP_CURRENT_TERM_CHANGED",
        ) != expected_term:
            _fail("BOOTSTRAP_CURRENT_TERM_CHANGED")
        if _recovery(
            recovery_readback_evidence,
            bundle=bundle_facts,
            binding=binding,
            now=observed_now,
            maximum_age=root.maximum_recovery_evidence_age_seconds,
        ) != recovery_hash:
            _fail("BOOTSTRAP_RECOVERY_EVIDENCE_CHANGED")
        call = _materializer_method(materializer)
        try:
            acknowledgement = call(
                plan=plan,
                source_stage_fd=source_fd,
                target_pgdata_fd=target_fd,
                recovery_signal_seed_fd=seed_fd,
            )
        except PhysicalPostgresStandbyBootstrapMaterializationError:
            raise
        except Exception:
            _fail("BOOTSTRAP_MATERIALIZER_FAILED")
        materialized_at = _ack(acknowledgement, plan=plan, now=observed_now)
        _assert_source_stage_stable(root, stage, stage_evidence.raw_stage_receipt)
        _assert_target_stable(root, target, target_identity)
        _assert_seed_stable(root, seed_identity)
        if _term(
            current_witnessed_term,
            now=observed_now,
            code="BOOTSTRAP_CURRENT_TERM_CHANGED",
        ) != expected_term:
            _fail("BOOTSTRAP_CURRENT_TERM_CHANGED")
        if _term(
            binding.expected_witnessed_term,
            now=observed_now,
            code="BOOTSTRAP_CURRENT_TERM_CHANGED",
        ) != expected_term:
            _fail("BOOTSTRAP_CURRENT_TERM_CHANGED")
        _recovery(
            recovery_readback_evidence,
            bundle=bundle_facts,
            binding=binding,
            now=observed_now,
            maximum_age=root.maximum_recovery_evidence_age_seconds,
        )
        receipt = _receipt(plan=plan, materialized_at=materialized_at)
        _write_receipt(receipt_path, root=root, receipt=receipt)
        return PhysicalPostgresStandbyBootstrapMaterializationResult(
            plan=plan,
            receipt=receipt,
            target_pgdata_candidate=target,
            idempotent=False,
        )
    except Exception as error:
        _cleanup_failed_target(
            root,
            target=target,
            expected=target_identity,
            bootstrap_id=bootstrap_id,
        )
        raise error
    finally:
        for descriptor in (seed_fd, source_fd, target_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
