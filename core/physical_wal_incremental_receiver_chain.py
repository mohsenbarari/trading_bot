"""Append-only, metadata-only receiver cursor for physical WAL continuity.

The existing ``verify_physical_wal_object_storage_bundle`` API verifies a
finite bootstrap bundle from genesis.  It intentionally cannot be reused as
an unbounded runtime receiver: doing so would either require the whole
history on every poll or silently lose the predecessor that makes an update
safe.  This module is the separate local boundary for that next step.

An operator first gives it a *verified bootstrap bundle*.  The module stores
that signed metadata in a root-owned, append-only local record chain.  Each
later call accepts exactly one signed WAL manifest and one signed blob-frontier
manifest, verifies them against the durable cursor, and appends metadata only.
It never reads Object Storage, lists an object prefix, downloads a WAL/blob
payload, decrypts anything, starts PostgreSQL, proves replay, issues a remote
acknowledgement, contacts a Witness, or promotes a writer.

The local record chain is deliberately conservative:

* a cursor is rooted in one fully verified bootstrap bundle;
* every incremental record binds the exact prior WAL hash/end-LSN/ordinal and
  blob hash/frontier, as well as the root-pinned route, term and base;
* records are O_EXCL, fsync'ed and frozen root-only files; and
* retry is idempotent only for the exact current signed WAL/blob pair.  A
  replay, gap, competing branch, changed base, route or term fails closed.

This is not a durable PostgreSQL replay receipt.  Its only success status is
``metadata-staged-not-replay-verified``.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_object_manifest import (
    MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES,
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalBaseBackupManifest,
    VerifiedPhysicalWalObjectStorageBundle,
    VerifiedPhysicalWalBlobFrontierManifest,
    VerifiedPhysicalWalSegmentManifest,
    require_verified_physical_wal_object_storage_bundle,
    verify_physical_wal_blob_frontier_manifest,
    verify_physical_wal_object_storage_bundle,
    verify_physical_wal_segment_manifest,
)


__all__ = (
    "MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_BYTES",
    "PHYSICAL_WAL_INCREMENTAL_RECEIVER_BOOTSTRAP_RECORD_SCHEMA",
    "PHYSICAL_WAL_INCREMENTAL_RECEIVER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_SCHEMA",
    "PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS",
    "PhysicalWalIncrementalReceiverConfig",
    "PhysicalWalIncrementalReceiverCursor",
    "PhysicalWalIncrementalReceiverError",
    "PhysicalWalIncrementalReceiverPin",
    "PhysicalWalIncrementalReceiverStageResult",
    "bootstrap_physical_wal_incremental_receiver_chain",
    "build_physical_wal_incremental_receiver_pin",
    "load_physical_wal_incremental_receiver_cursor",
    "stage_physical_wal_incremental_receiver_append",
)


PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_SCHEMA = (
    "gold-trade-physical-wal-incremental-receiver-record-v1"
)
PHYSICAL_WAL_INCREMENTAL_RECEIVER_BOOTSTRAP_RECORD_SCHEMA = (
    "gold-trade-physical-wal-incremental-receiver-bootstrap-record-v1"
)
PHYSICAL_WAL_INCREMENTAL_RECEIVER_PIN_SCHEMA = (
    "gold-trade-physical-wal-incremental-receiver-pin-v1"
)
PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS = "metadata-staged-not-replay-verified"
PHYSICAL_WAL_INCREMENTAL_RECEIVER_DEFAULT_ENABLED = False
PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_GENESIS_SHA256 = "0" * 64

# Bootstrap metadata is finite and must be bounded independently of the
# individual manifest limit.  The existing encrypted manifest package is
# capped at 16 MiB; 32 MiB leaves room for canonical base64 encoding without
# turning the receiver state directory into an unbounded history cache.
MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_BYTES = 32 * 1024 * 1024
MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORDS = 1_000_000

_STATE_DIRECTORY_NAME = "physical-wal-incremental-receiver-v1"
_RECORDS_DIRECTORY_NAME = "records"
_LOCK_FILE_NAME = "cursor.lock"
_RECORD_FILE_RE = re.compile(r"^[0-9]{20}\.json$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$", re.ASCII)

_BOOTSTRAP_RECORD_FIELDS = frozenset(
    {
        "schema",
        "record_kind",
        "sequence",
        "previous_record_sha256",
        "route_binding_sha256",
        "baseline_manifest_sha256",
        "base_backup_manifest_base64",
        "wal_manifest_sha256es",
        "wal_segment_manifests_base64",
        "wal_manifest_sha256",
        "wal_end_lsn",
        "wal_last_segment_ordinal",
        "blob_frontier_manifest_sha256",
        "blob_frontier_manifest_base64",
        "blob_frontier_wal_lsn",
        "record_sha256",
    }
)
_APPEND_RECORD_FIELDS = frozenset(
    {
        "schema",
        "record_kind",
        "sequence",
        "previous_record_sha256",
        "route_binding_sha256",
        "baseline_manifest_sha256",
        "wal_previous_manifest_sha256",
        "wal_previous_end_lsn",
        "wal_previous_segment_ordinal",
        "wal_manifest_sha256",
        "wal_segment_manifest_base64",
        "wal_end_lsn",
        "wal_last_segment_ordinal",
        "blob_previous_manifest_sha256",
        "blob_previous_frontier_wal_lsn",
        "blob_frontier_manifest_sha256",
        "blob_frontier_manifest_base64",
        "blob_frontier_wal_lsn",
        "record_sha256",
    }
)


class PhysicalWalIncrementalReceiverError(ValueError):
    """A physical-WAL receiver cursor input or its durable state is unsafe."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalIncrementalReceiverPin:
    """Root-pinned lineage expected by one local receiver cursor.

    ``route_binding_sha256`` is a digest over every other field, including the
    source signing key and exact Writer-Witness term projection.  It makes a
    changed route, source, baseline or term a different durable cursor, never
    an in-place update.
    """

    source_site: str
    destination_site: str
    source_public_key: bytes
    destination_age_recipient: str
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
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalWalIncrementalReceiverConfig:
    """Root-owned local state configuration; it defaults to disabled."""

    state_root: Path
    enabled: bool = PHYSICAL_WAL_INCREMENTAL_RECEIVER_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalIncrementalReceiverCursor:
    """Verified metadata frontier only; never a recovery or writer permit."""

    sequence: int
    record_sha256: str
    route_binding_sha256: str
    baseline_manifest_sha256: str
    wal_manifest_sha256: str
    wal_end_lsn: str
    wal_last_segment_ordinal: int
    blob_frontier_manifest_sha256: str
    blob_frontier_wal_lsn: str


@dataclass(frozen=True)
class PhysicalWalIncrementalReceiverStageResult:
    """A successful local metadata stage, explicitly not a replay proof."""

    status: str
    cursor: PhysicalWalIncrementalReceiverCursor
    record_path: Path
    idempotent: bool


@dataclass(frozen=True)
class _NormalisedConfig:
    state_root: Path


@dataclass(frozen=True)
class _StatePaths:
    state_directory: Path
    records_directory: Path
    lock_path: Path


@dataclass(frozen=True)
class _History:
    baseline: VerifiedPhysicalWalBaseBackupManifest
    cursor: PhysicalWalIncrementalReceiverCursor
    records: tuple[dict[str, Any], ...]
    record_paths: tuple[Path, ...]


def _fail(code: str) -> None:
    raise PhysicalWalIncrementalReceiverError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except Exception:
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("CURSOR_RECORD_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    _fail("CURSOR_RECORD_JSON_CONSTANT_INVALID")


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    return _text(value, pattern=SHA256_RE, code=code)


def _site(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _positive_int(value: object, *, code: str, maximum: int = (2**63 - 1)) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _nonnegative_int(value: object, *, code: str, maximum: int = (2**63 - 1)) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> str:
    return _text(value, pattern=_LSN_RE, code=code)


def _lsn_value(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    return value


def _normalise_pin(
    value: object,
    *,
    verify_route_hash: bool,
) -> PhysicalWalIncrementalReceiverPin:
    if type(value) is not PhysicalWalIncrementalReceiverPin:
        _fail("RECEIVER_CURSOR_PIN_INVALID")
    source_site = _site(value.source_site, code="RECEIVER_CURSOR_SOURCE_SITE_INVALID")
    destination_site = _site(value.destination_site, code="RECEIVER_CURSOR_DESTINATION_SITE_INVALID")
    if source_site == destination_site:
        _fail("RECEIVER_CURSOR_ROUTE_INVALID")
    source_public_key = _public_key(value.source_public_key, code="RECEIVER_CURSOR_SOURCE_KEY_INVALID")
    destination_age_recipient = _text(
        value.destination_age_recipient,
        pattern=AGE_RECIPIENT_RE,
        code="RECEIVER_CURSOR_DESTINATION_RECIPIENT_INVALID",
    )
    campaign_id = _text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code="RECEIVER_CURSOR_CAMPAIGN_INVALID")
    release_sha = _text(value.release_sha, pattern=RELEASE_SHA_RE, code="RECEIVER_CURSOR_RELEASE_INVALID")
    writer_epoch = _positive_int(value.writer_epoch, code="RECEIVER_CURSOR_WRITER_EPOCH_INVALID")
    writer_lease_id = _text(value.writer_lease_id, pattern=LEASE_ID_RE, code="RECEIVER_CURSOR_WRITER_LEASE_INVALID")
    witnessed_term_proof_sha256 = _sha256(
        value.witnessed_term_proof_sha256,
        code="RECEIVER_CURSOR_TERM_PROOF_INVALID",
    )
    baseline_generation_id = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="RECEIVER_CURSOR_BASELINE_GENERATION_INVALID",
    )
    baseline_manifest_sha256 = _sha256(
        value.baseline_manifest_sha256,
        code="RECEIVER_CURSOR_BASELINE_HASH_INVALID",
    )
    database_system_identifier = _text(
        value.database_system_identifier,
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="RECEIVER_CURSOR_DATABASE_IDENTIFIER_INVALID",
    )
    timeline_id = _positive_int(value.timeline_id, code="RECEIVER_CURSOR_TIMELINE_INVALID", maximum=0xFFFFFFFF)
    wal_segment_size_bytes = _positive_int(value.wal_segment_size_bytes, code="RECEIVER_CURSOR_WAL_SIZE_INVALID")
    if wal_segment_size_bytes not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail("RECEIVER_CURSOR_WAL_SIZE_INVALID")
    baseline_wal_lsn = _lsn(value.baseline_wal_lsn, code="RECEIVER_CURSOR_BASELINE_LSN_INVALID")
    wal_chain_start_lsn = _lsn(value.wal_chain_start_lsn, code="RECEIVER_CURSOR_CHAIN_START_LSN_INVALID")
    base_backup_end_lsn = _lsn(value.base_backup_end_lsn, code="RECEIVER_CURSOR_BASE_BACKUP_END_LSN_INVALID")
    if _lsn_value(wal_chain_start_lsn) > _lsn_value(base_backup_end_lsn):
        _fail("RECEIVER_CURSOR_BASELINE_GEOMETRY_INVALID")
    provisional = PhysicalWalIncrementalReceiverPin(
        source_site=source_site,
        destination_site=destination_site,
        source_public_key=source_public_key,
        destination_age_recipient=destination_age_recipient,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        database_system_identifier=database_system_identifier,
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size_bytes,
        baseline_wal_lsn=baseline_wal_lsn,
        wal_chain_start_lsn=wal_chain_start_lsn,
        base_backup_end_lsn=base_backup_end_lsn,
        route_binding_sha256="0" * 64,
    )
    expected_route_hash = hashlib.sha256(_canonical(_pin_payload(provisional), code="RECEIVER_CURSOR_PIN_CANONICAL_INVALID")).hexdigest()
    if verify_route_hash:
        if _sha256(value.route_binding_sha256, code="RECEIVER_CURSOR_ROUTE_HASH_INVALID") != expected_route_hash:
            _fail("RECEIVER_CURSOR_ROUTE_HASH_INVALID")
    return PhysicalWalIncrementalReceiverPin(
        **{**provisional.__dict__, "route_binding_sha256": expected_route_hash}
    )


def _pin_payload(pin: PhysicalWalIncrementalReceiverPin) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_INCREMENTAL_RECEIVER_PIN_SCHEMA,
        "source_site": pin.source_site,
        "destination_site": pin.destination_site,
        "source_public_key_base64": base64.b64encode(pin.source_public_key).decode("ascii"),
        "destination_age_recipient": pin.destination_age_recipient,
        "campaign_id": pin.campaign_id,
        "release_sha": pin.release_sha,
        "writer_term": {
            "epoch": pin.writer_epoch,
            "lease_id": pin.writer_lease_id,
            "witnessed_term_proof_sha256": pin.witnessed_term_proof_sha256,
        },
        "baseline": {
            "baseline_generation_id": pin.baseline_generation_id,
            "baseline_manifest_sha256": pin.baseline_manifest_sha256,
            "database_system_identifier": pin.database_system_identifier,
            "timeline_id": pin.timeline_id,
            "wal_segment_size_bytes": pin.wal_segment_size_bytes,
            "baseline_wal_lsn": pin.baseline_wal_lsn,
            "wal_chain_start_lsn": pin.wal_chain_start_lsn,
            "base_backup_end_lsn": pin.base_backup_end_lsn,
        },
    }


def build_physical_wal_incremental_receiver_pin(
    *,
    source_site: str,
    destination_site: str,
    source_public_key: bytes,
    destination_age_recipient: str,
    campaign_id: str,
    release_sha: str,
    writer_epoch: int,
    writer_lease_id: str,
    witnessed_term_proof_sha256: str,
    baseline_generation_id: str,
    baseline_manifest_sha256: str,
    database_system_identifier: str,
    timeline_id: int,
    wal_segment_size_bytes: int,
    baseline_wal_lsn: str,
    wal_chain_start_lsn: str,
    base_backup_end_lsn: str,
) -> PhysicalWalIncrementalReceiverPin:
    """Build the only accepted full-route pin for this receiver boundary."""

    provisional = PhysicalWalIncrementalReceiverPin(
        source_site=source_site,
        destination_site=destination_site,
        source_public_key=source_public_key,
        destination_age_recipient=destination_age_recipient,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        database_system_identifier=database_system_identifier,
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size_bytes,
        baseline_wal_lsn=baseline_wal_lsn,
        wal_chain_start_lsn=wal_chain_start_lsn,
        base_backup_end_lsn=base_backup_end_lsn,
        route_binding_sha256="0" * 64,
    )
    return _normalise_pin(provisional, verify_route_hash=False)


def _secure_root(value: object) -> Path:
    if os.geteuid() != 0:
        _fail("RECEIVER_CURSOR_ROOT_EXECUTION_REQUIRED")
    if not isinstance(value, Path) or not value.is_absolute() or any(part in {".", ".."} for part in value.parts):
        _fail("RECEIVER_CURSOR_STATE_ROOT_UNSAFE")
    try:
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
    except OSError:
        _fail("RECEIVER_CURSOR_STATE_ROOT_UNSAFE")
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("RECEIVER_CURSOR_STATE_ROOT_UNSAFE")
    return resolved


def _normalise_config(value: object) -> _NormalisedConfig:
    if type(value) is not PhysicalWalIncrementalReceiverConfig:
        _fail("RECEIVER_CURSOR_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("RECEIVER_CURSOR_DISABLED")
    return _NormalisedConfig(state_root=_secure_root(value.state_root))


def _secure_child(parent: Path, name: str) -> Path:
    path = parent / name
    try:
        path.mkdir(mode=0o700)
        _fsync_directory(parent)
    except FileExistsError:
        pass
    except OSError:
        _fail("RECEIVER_CURSOR_DIRECTORY_CREATE_FAILED")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("RECEIVER_CURSOR_DIRECTORY_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("RECEIVER_CURSOR_DIRECTORY_UNSAFE")
    return path


def _state_paths(config: _NormalisedConfig) -> _StatePaths:
    state_directory = _secure_child(config.state_root, _STATE_DIRECTORY_NAME)
    records_directory = _secure_child(state_directory, _RECORDS_DIRECTORY_NAME)
    return _StatePaths(
        state_directory=state_directory,
        records_directory=records_directory,
        lock_path=state_directory / _LOCK_FILE_NAME,
    )


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("RECEIVER_CURSOR_DIRECTORY_FSYNC_UNAVAILABLE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("RECEIVER_CURSOR_DIRECTORY_FSYNC_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("RECEIVER_CURSOR_DIRECTORY_FSYNC_FAILED")
    finally:
        os.close(descriptor)


def _open_lock(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("RECEIVER_CURSOR_NOFOLLOW_UNAVAILABLE")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(path, flags, 0o600)
            created = False
        except OSError:
            _fail("RECEIVER_CURSOR_LOCK_UNSAFE")
    except OSError:
        _fail("RECEIVER_CURSOR_LOCK_UNSAFE")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("RECEIVER_CURSOR_LOCK_UNSAFE")
        if created:
            _fsync_directory(path.parent)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def _locked_state(paths: _StatePaths) -> Iterator[None]:
    descriptor = _open_lock(paths.lock_path)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError:
            _fail("RECEIVER_CURSOR_LOCK_FAILED")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _open_new_record(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("RECEIVER_CURSOR_NOFOLLOW_UNAVAILABLE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail("RECEIVER_CURSOR_CONCURRENT_UPDATE")
    except OSError:
        _fail("RECEIVER_CURSOR_RECORD_CREATE_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("RECEIVER_CURSOR_RECORD_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_existing_record(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("RECEIVER_CURSOR_NOFOLLOW_UNAVAILABLE")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("RECEIVER_CURSOR_RECORD_UNSAFE")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            _fail("RECEIVER_CURSOR_RECORD_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("RECEIVER_CURSOR_RECORD_WRITE_FAILED")
            view = view[written:]
    except OSError:
        _fail("RECEIVER_CURSOR_RECORD_WRITE_FAILED")


def _write_record(path: Path, value: Mapping[str, Any]) -> None:
    payload = _canonical(value, code="RECEIVER_CURSOR_RECORD_CANONICAL_INVALID")
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_BYTES:
        _fail("RECEIVER_CURSOR_RECORD_SIZE_INVALID")
    descriptor = _open_new_record(path)
    try:
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            _fail("RECEIVER_CURSOR_RECORD_UNSAFE")
    except OSError:
        _fail("RECEIVER_CURSOR_RECORD_WRITE_FAILED")
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _read_record_json(path: Path) -> tuple[dict[str, Any], bytes]:
    descriptor = _open_existing_record(path)
    try:
        try:
            size = os.fstat(descriptor).st_size
            if not 1 <= size <= MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_BYTES:
                _fail("RECEIVER_CURSOR_RECORD_SIZE_INVALID")
            payload = bytearray()
            while len(payload) < size:
                chunk = os.read(descriptor, min(1024 * 1024, size - len(payload)))
                if not chunk:
                    _fail("RECEIVER_CURSOR_RECORD_READ_FAILED")
                payload.extend(chunk)
            if os.read(descriptor, 1):
                _fail("RECEIVER_CURSOR_RECORD_READ_FAILED")
        except OSError:
            _fail("RECEIVER_CURSOR_RECORD_READ_FAILED")
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalIncrementalReceiverError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("RECEIVER_CURSOR_RECORD_JSON_INVALID")
    if not isinstance(value, dict) or _canonical(value, code="RECEIVER_CURSOR_RECORD_CANONICAL_INVALID") != raw:
        _fail("RECEIVER_CURSOR_RECORD_CANONICAL_INVALID")
    return value, raw


def _record_hash(value: Mapping[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical(unsigned, code="RECEIVER_CURSOR_RECORD_CANONICAL_INVALID")).hexdigest()


def _decode_manifest(value: object, *, code: str) -> bytes:
    if not isinstance(value, str) or not value:
        _fail(code)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not 1 <= len(raw) <= MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
        _fail(code)
    return raw


def _encode_manifest(raw: bytes) -> str:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PHYSICAL_WAL_OBJECT_MANIFEST_BYTES:
        _fail("RECEIVER_CURSOR_MANIFEST_SIZE_INVALID")
    return base64.b64encode(raw).decode("ascii")


def _record_files(records_directory: Path) -> tuple[Path, ...]:
    try:
        entries = tuple(records_directory.iterdir())
    except OSError:
        _fail("RECEIVER_CURSOR_RECORD_DIRECTORY_UNSAFE")
    if len(entries) > MAX_PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORDS:
        _fail("RECEIVER_CURSOR_RECORD_COUNT_INVALID")
    paths: list[Path] = []
    for path in entries:
        if _RECORD_FILE_RE.fullmatch(path.name) is None:
            _fail("RECEIVER_CURSOR_RECORD_DIRECTORY_UNSAFE")
        paths.append(path)
    return tuple(sorted(paths, key=lambda item: item.name))


def _cursor_from_verified(
    *,
    sequence: int,
    record_sha256: str,
    route_binding_sha256: str,
    baseline: VerifiedPhysicalWalBaseBackupManifest,
    wal: VerifiedPhysicalWalSegmentManifest,
    blob: VerifiedPhysicalWalBlobFrontierManifest,
) -> PhysicalWalIncrementalReceiverCursor:
    return PhysicalWalIncrementalReceiverCursor(
        sequence=sequence,
        record_sha256=record_sha256,
        route_binding_sha256=route_binding_sha256,
        baseline_manifest_sha256=baseline.manifest_sha256,
        wal_manifest_sha256=wal.manifest_sha256,
        wal_end_lsn=wal.end_lsn,
        wal_last_segment_ordinal=wal.last_segment_ordinal,
        blob_frontier_manifest_sha256=blob.manifest_sha256,
        blob_frontier_wal_lsn=blob.blob_object_frontier_wal_lsn,
    )


def _validate_record_envelope(
    value: Mapping[str, Any], *, expected_sequence: int, previous_hash: str, pin: PhysicalWalIncrementalReceiverPin
) -> tuple[str, str]:
    kind = value.get("record_kind")
    if kind == "bootstrap":
        expected_fields = _BOOTSTRAP_RECORD_FIELDS
        expected_schema = PHYSICAL_WAL_INCREMENTAL_RECEIVER_BOOTSTRAP_RECORD_SCHEMA
    elif kind == "append":
        expected_fields = _APPEND_RECORD_FIELDS
        expected_schema = PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_SCHEMA
    else:
        _fail("RECEIVER_CURSOR_RECORD_KIND_INVALID")
    if set(value) != expected_fields or value.get("schema") != expected_schema:
        _fail("RECEIVER_CURSOR_RECORD_FIELDS_INVALID")
    if _positive_int(value["sequence"], code="RECEIVER_CURSOR_RECORD_SEQUENCE_INVALID") != expected_sequence:
        _fail("RECEIVER_CURSOR_RECORD_SEQUENCE_INVALID")
    if _sha256(value["previous_record_sha256"], code="RECEIVER_CURSOR_RECORD_PREDECESSOR_INVALID") != previous_hash:
        _fail("RECEIVER_CURSOR_RECORD_PREDECESSOR_INVALID")
    if _sha256(value["route_binding_sha256"], code="RECEIVER_CURSOR_ROUTE_BINDING_INVALID") != pin.route_binding_sha256:
        _fail("RECEIVER_CURSOR_ROUTE_OR_TERM_DRIFT")
    if _sha256(value["baseline_manifest_sha256"], code="RECEIVER_CURSOR_BASELINE_HASH_INVALID") != pin.baseline_manifest_sha256:
        _fail("RECEIVER_CURSOR_BASELINE_PIN_DRIFT")
    record_hash = _sha256(value["record_sha256"], code="RECEIVER_CURSOR_RECORD_HASH_INVALID")
    if _record_hash(value) != record_hash:
        _fail("RECEIVER_CURSOR_RECORD_HASH_INVALID")
    return str(kind), record_hash


def _verify_bootstrap_record(
    value: Mapping[str, Any],
    *,
    pin: PhysicalWalIncrementalReceiverPin,
    record_hash: str,
) -> tuple[VerifiedPhysicalWalBaseBackupManifest, PhysicalWalIncrementalReceiverCursor]:
    base_raw = _decode_manifest(value["base_backup_manifest_base64"], code="RECEIVER_CURSOR_BOOTSTRAP_BASE_INVALID")
    wal_raw_values = value["wal_segment_manifests_base64"]
    if isinstance(wal_raw_values, (str, bytes)) or not isinstance(wal_raw_values, Sequence) or not wal_raw_values:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_WAL_INVALID")
    wal_raw = tuple(_decode_manifest(item, code="RECEIVER_CURSOR_BOOTSTRAP_WAL_INVALID") for item in wal_raw_values)
    hashes = value["wal_manifest_sha256es"]
    if isinstance(hashes, (str, bytes)) or not isinstance(hashes, Sequence):
        _fail("RECEIVER_CURSOR_BOOTSTRAP_HASHES_INVALID")
    hash_values = tuple(_sha256(item, code="RECEIVER_CURSOR_BOOTSTRAP_HASHES_INVALID") for item in hashes)
    blob_raw = _decode_manifest(value["blob_frontier_manifest_base64"], code="RECEIVER_CURSOR_BOOTSTRAP_BLOB_INVALID")
    try:
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base_raw,
            wal_segment_manifests=wal_raw,
            blob_frontier_manifest=blob_raw,
            expected_source_public_key=pin.source_public_key,
            expected_source_site=pin.source_site,
            expected_destination_site=pin.destination_site,
            expected_campaign_id=pin.campaign_id,
            expected_release_sha=pin.release_sha,
            expected_writer_epoch=pin.writer_epoch,
            expected_writer_lease_id=pin.writer_lease_id,
            expected_witnessed_term_proof_sha256=pin.witnessed_term_proof_sha256,
            expected_baseline_generation_id=pin.baseline_generation_id,
            expected_wal_segment_size_bytes=pin.wal_segment_size_bytes,
            expected_destination_age_recipient=pin.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_LINEAGE_REJECTED")
    if not bundle.blob_frontier.objects_complete:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_BLOB_FRONTIER_INCOMPLETE")
    if (
        bundle.baseline.manifest_sha256 != pin.baseline_manifest_sha256
        or bundle.baseline.database_system_identifier != pin.database_system_identifier
        or bundle.baseline.timeline_id != pin.timeline_id
        or bundle.baseline.baseline_wal_lsn != pin.baseline_wal_lsn
        or bundle.baseline.wal_chain_start_lsn != pin.wal_chain_start_lsn
        or bundle.baseline.base_backup_end_lsn != pin.base_backup_end_lsn
        or bundle.manifest_sha256es != hash_values
        or value["wal_manifest_sha256"] != bundle.wal_manifests[-1].manifest_sha256
        or value["wal_end_lsn"] != bundle.terminal_wal_lsn
        or value["wal_last_segment_ordinal"] != bundle.wal_manifests[-1].last_segment_ordinal
        or value["blob_frontier_manifest_sha256"] != bundle.blob_frontier.manifest_sha256
        or value["blob_frontier_wal_lsn"] != bundle.blob_frontier.blob_object_frontier_wal_lsn
    ):
        _fail("RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    # Validate fields whose comparison above would otherwise accept an
    # accidental bool/subclass or unbounded value without enforcing the local
    # state grammar.
    _sha256(value["wal_manifest_sha256"], code="RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    _lsn(value["wal_end_lsn"], code="RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    _nonnegative_int(value["wal_last_segment_ordinal"], code="RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    _sha256(value["blob_frontier_manifest_sha256"], code="RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    _lsn(value["blob_frontier_wal_lsn"], code="RECEIVER_CURSOR_BOOTSTRAP_FACTS_INVALID")
    return bundle.baseline, _cursor_from_verified(
        sequence=1,
        record_sha256=record_hash,
        route_binding_sha256=pin.route_binding_sha256,
        baseline=bundle.baseline,
        wal=bundle.wal_manifests[-1],
        blob=bundle.blob_frontier,
    )


def _verify_append_record(
    value: Mapping[str, Any],
    *,
    pin: PhysicalWalIncrementalReceiverPin,
    baseline: VerifiedPhysicalWalBaseBackupManifest,
    prior: PhysicalWalIncrementalReceiverCursor,
    record_hash: str,
) -> PhysicalWalIncrementalReceiverCursor:
    wal_raw = _decode_manifest(value["wal_segment_manifest_base64"], code="RECEIVER_CURSOR_APPEND_WAL_INVALID")
    blob_raw = _decode_manifest(value["blob_frontier_manifest_base64"], code="RECEIVER_CURSOR_APPEND_BLOB_INVALID")
    if (
        _sha256(value["wal_previous_manifest_sha256"], code="RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID") != prior.wal_manifest_sha256
        or _lsn(value["wal_previous_end_lsn"], code="RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID") != prior.wal_end_lsn
        or _nonnegative_int(value["wal_previous_segment_ordinal"], code="RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID") != prior.wal_last_segment_ordinal
        or _sha256(value["blob_previous_manifest_sha256"], code="RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID") != prior.blob_frontier_manifest_sha256
        or _lsn(value["blob_previous_frontier_wal_lsn"], code="RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID") != prior.blob_frontier_wal_lsn
    ):
        _fail("RECEIVER_CURSOR_APPEND_PREDECESSOR_INVALID")
    try:
        wal = verify_physical_wal_segment_manifest(
            wal_raw,
            expected_source_public_key=pin.source_public_key,
            expected_baseline=baseline,
            expected_previous_manifest_sha256=prior.wal_manifest_sha256,
            expected_previous_end_lsn=prior.wal_end_lsn,
            expected_previous_segment_ordinal=prior.wal_last_segment_ordinal,
            expected_destination_age_recipient=pin.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED")
    try:
        blob = verify_physical_wal_blob_frontier_manifest(
            blob_raw,
            expected_source_public_key=pin.source_public_key,
            expected_baseline=baseline,
            expected_previous_manifest_sha256=prior.blob_frontier_manifest_sha256,
            expected_previous_frontier_wal_lsn=prior.blob_frontier_wal_lsn,
            expected_wal_frontier_lsn=wal.end_lsn,
            expected_destination_age_recipient=pin.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_BLOB_PREDECESSOR_OR_ROUTE_TERM_REJECTED")
    if not blob.objects_complete:
        _fail("RECEIVER_CURSOR_BLOB_FRONTIER_INCOMPLETE")
    if (
        _sha256(value["wal_manifest_sha256"], code="RECEIVER_CURSOR_APPEND_FACTS_INVALID") != wal.manifest_sha256
        or _lsn(value["wal_end_lsn"], code="RECEIVER_CURSOR_APPEND_FACTS_INVALID") != wal.end_lsn
        or _nonnegative_int(value["wal_last_segment_ordinal"], code="RECEIVER_CURSOR_APPEND_FACTS_INVALID") != wal.last_segment_ordinal
        or _sha256(value["blob_frontier_manifest_sha256"], code="RECEIVER_CURSOR_APPEND_FACTS_INVALID") != blob.manifest_sha256
        or _lsn(value["blob_frontier_wal_lsn"], code="RECEIVER_CURSOR_APPEND_FACTS_INVALID") != blob.blob_object_frontier_wal_lsn
    ):
        _fail("RECEIVER_CURSOR_APPEND_FACTS_INVALID")
    return _cursor_from_verified(
        sequence=prior.sequence + 1,
        record_sha256=record_hash,
        route_binding_sha256=pin.route_binding_sha256,
        baseline=baseline,
        wal=wal,
        blob=blob,
    )


def _load_history(
    *,
    paths: _StatePaths,
    pin: PhysicalWalIncrementalReceiverPin,
) -> _History | None:
    record_paths = _record_files(paths.records_directory)
    if not record_paths:
        return None
    records: list[dict[str, Any]] = []
    previous_hash = PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_GENESIS_SHA256
    baseline: VerifiedPhysicalWalBaseBackupManifest | None = None
    cursor: PhysicalWalIncrementalReceiverCursor | None = None
    for index, path in enumerate(record_paths, start=1):
        if path.name != f"{index:020d}.json":
            _fail("RECEIVER_CURSOR_RECORD_SEQUENCE_INVALID")
        value, _raw = _read_record_json(path)
        kind, record_hash = _validate_record_envelope(
            value,
            expected_sequence=index,
            previous_hash=previous_hash,
            pin=pin,
        )
        if index == 1:
            if kind != "bootstrap":
                _fail("RECEIVER_CURSOR_BOOTSTRAP_REQUIRED")
            baseline, cursor = _verify_bootstrap_record(value, pin=pin, record_hash=record_hash)
        else:
            if kind != "append" or baseline is None or cursor is None:
                _fail("RECEIVER_CURSOR_RECORD_KIND_INVALID")
            cursor = _verify_append_record(
                value,
                pin=pin,
                baseline=baseline,
                prior=cursor,
                record_hash=record_hash,
            )
        records.append(value)
        previous_hash = record_hash
    if baseline is None or cursor is None:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_REQUIRED")
    return _History(
        baseline=baseline,
        cursor=cursor,
        records=tuple(records),
        record_paths=record_paths,
    )


def _bootstrap_record(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalIncrementalReceiverPin,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_WAL_INCREMENTAL_RECEIVER_BOOTSTRAP_RECORD_SCHEMA,
        "record_kind": "bootstrap",
        "sequence": 1,
        "previous_record_sha256": PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_GENESIS_SHA256,
        "route_binding_sha256": pin.route_binding_sha256,
        "baseline_manifest_sha256": bundle.baseline.manifest_sha256,
        "base_backup_manifest_base64": _encode_manifest(bundle.baseline.canonical_manifest),
        "wal_manifest_sha256es": list(bundle.manifest_sha256es),
        "wal_segment_manifests_base64": [
            _encode_manifest(item.canonical_manifest) for item in bundle.wal_manifests
        ],
        "wal_manifest_sha256": bundle.wal_manifests[-1].manifest_sha256,
        "wal_end_lsn": bundle.terminal_wal_lsn,
        "wal_last_segment_ordinal": bundle.wal_manifests[-1].last_segment_ordinal,
        "blob_frontier_manifest_sha256": bundle.blob_frontier.manifest_sha256,
        "blob_frontier_manifest_base64": _encode_manifest(bundle.blob_frontier.canonical_manifest),
        "blob_frontier_wal_lsn": bundle.blob_frontier.blob_object_frontier_wal_lsn,
    }
    return {**unsigned, "record_sha256": _record_hash(unsigned)}


def _append_record(
    *,
    prior: PhysicalWalIncrementalReceiverCursor,
    pin: PhysicalWalIncrementalReceiverPin,
    wal: VerifiedPhysicalWalSegmentManifest,
    blob: VerifiedPhysicalWalBlobFrontierManifest,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": PHYSICAL_WAL_INCREMENTAL_RECEIVER_RECORD_SCHEMA,
        "record_kind": "append",
        "sequence": prior.sequence + 1,
        "previous_record_sha256": prior.record_sha256,
        "route_binding_sha256": pin.route_binding_sha256,
        "baseline_manifest_sha256": prior.baseline_manifest_sha256,
        "wal_previous_manifest_sha256": prior.wal_manifest_sha256,
        "wal_previous_end_lsn": prior.wal_end_lsn,
        "wal_previous_segment_ordinal": prior.wal_last_segment_ordinal,
        "wal_manifest_sha256": wal.manifest_sha256,
        "wal_segment_manifest_base64": _encode_manifest(wal.canonical_manifest),
        "wal_end_lsn": wal.end_lsn,
        "wal_last_segment_ordinal": wal.last_segment_ordinal,
        "blob_previous_manifest_sha256": prior.blob_frontier_manifest_sha256,
        "blob_previous_frontier_wal_lsn": prior.blob_frontier_wal_lsn,
        "blob_frontier_manifest_sha256": blob.manifest_sha256,
        "blob_frontier_manifest_base64": _encode_manifest(blob.canonical_manifest),
        "blob_frontier_wal_lsn": blob.blob_object_frontier_wal_lsn,
    }
    return {**unsigned, "record_sha256": _record_hash(unsigned)}


def _verify_bootstrap_bundle_for_pin(
    value: object,
    *,
    pin: PhysicalWalIncrementalReceiverPin,
) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        supplied = require_verified_physical_wal_object_storage_bundle(value)
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=supplied.baseline.canonical_manifest,
            wal_segment_manifests=tuple(item.canonical_manifest for item in supplied.wal_manifests),
            blob_frontier_manifest=supplied.blob_frontier.canonical_manifest,
            expected_source_public_key=pin.source_public_key,
            expected_source_site=pin.source_site,
            expected_destination_site=pin.destination_site,
            expected_campaign_id=pin.campaign_id,
            expected_release_sha=pin.release_sha,
            expected_writer_epoch=pin.writer_epoch,
            expected_writer_lease_id=pin.writer_lease_id,
            expected_witnessed_term_proof_sha256=pin.witnessed_term_proof_sha256,
            expected_baseline_generation_id=pin.baseline_generation_id,
            expected_wal_segment_size_bytes=pin.wal_segment_size_bytes,
            expected_destination_age_recipient=pin.destination_age_recipient,
        )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_LINEAGE_REJECTED")
    if (
        bundle.baseline.manifest_sha256 != pin.baseline_manifest_sha256
        or bundle.baseline.database_system_identifier != pin.database_system_identifier
        or bundle.baseline.timeline_id != pin.timeline_id
        or bundle.baseline.baseline_wal_lsn != pin.baseline_wal_lsn
        or bundle.baseline.wal_chain_start_lsn != pin.wal_chain_start_lsn
        or bundle.baseline.base_backup_end_lsn != pin.base_backup_end_lsn
    ):
        _fail("RECEIVER_CURSOR_BASELINE_PIN_DRIFT")
    if not bundle.blob_frontier.objects_complete:
        _fail("RECEIVER_CURSOR_BOOTSTRAP_BLOB_FRONTIER_INCOMPLETE")
    return bundle


def _verify_append_for_cursor(
    *,
    wal_segment_manifest: Mapping[str, Any] | bytes | str,
    blob_frontier_manifest: Mapping[str, Any] | bytes | str,
    pin: PhysicalWalIncrementalReceiverPin,
    baseline: VerifiedPhysicalWalBaseBackupManifest,
    prior: PhysicalWalIncrementalReceiverCursor,
    standalone: bool,
) -> tuple[VerifiedPhysicalWalSegmentManifest, VerifiedPhysicalWalBlobFrontierManifest]:
    try:
        if standalone:
            wal = verify_physical_wal_segment_manifest(
                wal_segment_manifest,
                expected_source_public_key=pin.source_public_key,
                expected_baseline=baseline,
                expected_destination_age_recipient=pin.destination_age_recipient,
            )
        else:
            wal = verify_physical_wal_segment_manifest(
                wal_segment_manifest,
                expected_source_public_key=pin.source_public_key,
                expected_baseline=baseline,
                expected_previous_manifest_sha256=prior.wal_manifest_sha256,
                expected_previous_end_lsn=prior.wal_end_lsn,
                expected_previous_segment_ordinal=prior.wal_last_segment_ordinal,
                expected_destination_age_recipient=pin.destination_age_recipient,
            )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_WAL_PREDECESSOR_OR_ROUTE_TERM_REJECTED")
    try:
        if standalone:
            blob = verify_physical_wal_blob_frontier_manifest(
                blob_frontier_manifest,
                expected_source_public_key=pin.source_public_key,
                expected_baseline=baseline,
                expected_destination_age_recipient=pin.destination_age_recipient,
            )
        else:
            blob = verify_physical_wal_blob_frontier_manifest(
                blob_frontier_manifest,
                expected_source_public_key=pin.source_public_key,
                expected_baseline=baseline,
                expected_previous_manifest_sha256=prior.blob_frontier_manifest_sha256,
                expected_previous_frontier_wal_lsn=prior.blob_frontier_wal_lsn,
                expected_wal_frontier_lsn=wal.end_lsn,
                expected_destination_age_recipient=pin.destination_age_recipient,
            )
    except PhysicalWalObjectManifestError:
        _fail("RECEIVER_CURSOR_BLOB_PREDECESSOR_OR_ROUTE_TERM_REJECTED")
    if not blob.objects_complete:
        _fail("RECEIVER_CURSOR_BLOB_FRONTIER_INCOMPLETE")
    return wal, blob


def _record_matches_pair(
    record: Mapping[str, Any],
    *,
    wal: VerifiedPhysicalWalSegmentManifest,
    blob: VerifiedPhysicalWalBlobFrontierManifest,
) -> bool:
    if record.get("record_kind") != "append":
        return False
    try:
        return (
            record["wal_manifest_sha256"] == wal.manifest_sha256
            and record["blob_frontier_manifest_sha256"] == blob.manifest_sha256
            and _decode_manifest(record["wal_segment_manifest_base64"], code="RECEIVER_CURSOR_RECORD_UNSAFE")
            == wal.canonical_manifest
            and _decode_manifest(record["blob_frontier_manifest_base64"], code="RECEIVER_CURSOR_RECORD_UNSAFE")
            == blob.canonical_manifest
        )
    except (KeyError, PhysicalWalIncrementalReceiverError):
        return False


def bootstrap_physical_wal_incremental_receiver_chain(
    *,
    bootstrap_bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalIncrementalReceiverPin,
    config: PhysicalWalIncrementalReceiverConfig,
) -> PhysicalWalIncrementalReceiverStageResult:
    """Durably establish a receiver cursor from one verified finite bundle.

    A second call is successful only when the local cursor still consists of
    exactly that bootstrap record.  Replacing a baseline or reusing bootstrap
    after progress is deliberately a fork/replay error.
    """

    normalised_config = _normalise_config(config)
    normalised_pin = _normalise_pin(pin, verify_route_hash=True)
    bundle = _verify_bootstrap_bundle_for_pin(bootstrap_bundle, pin=normalised_pin)
    expected_record = _bootstrap_record(bundle=bundle, pin=normalised_pin)
    paths = _state_paths(normalised_config)
    with _locked_state(paths):
        history = _load_history(paths=paths, pin=normalised_pin)
        if history is not None:
            if len(history.records) == 1 and history.records[0] == expected_record:
                return PhysicalWalIncrementalReceiverStageResult(
                    status=PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS,
                    cursor=history.cursor,
                    record_path=history.record_paths[0],
                    idempotent=True,
                )
            _fail("RECEIVER_CURSOR_BOOTSTRAP_REPLAY_OR_FORK")
        path = paths.records_directory / "00000000000000000001.json"
        _write_record(path, expected_record)
        # Re-reading before returning catches unexpected local replacement and
        # makes the returned cursor a projection of durable bytes, not inputs.
        durable = _load_history(paths=paths, pin=normalised_pin)
        if durable is None or durable.records[0] != expected_record:
            _fail("RECEIVER_CURSOR_BOOTSTRAP_DURABILITY_INVALID")
        return PhysicalWalIncrementalReceiverStageResult(
            status=PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS,
            cursor=durable.cursor,
            record_path=path,
            idempotent=False,
        )


def stage_physical_wal_incremental_receiver_append(
    *,
    wal_segment_manifest: Mapping[str, Any] | bytes | str,
    blob_frontier_manifest: Mapping[str, Any] | bytes | str,
    pin: PhysicalWalIncrementalReceiverPin,
    config: PhysicalWalIncrementalReceiverConfig,
) -> PhysicalWalIncrementalReceiverStageResult:
    """Verify and append one exact WAL/blob metadata continuity point.

    This writes signed manifest metadata only.  The caller must use a distinct
    data-plane adapter to obtain exact encrypted object versions and a distinct
    PostgreSQL recovery boundary to establish any replay proof.
    """

    normalised_config = _normalise_config(config)
    normalised_pin = _normalise_pin(pin, verify_route_hash=True)
    paths = _state_paths(normalised_config)
    with _locked_state(paths):
        history = _load_history(paths=paths, pin=normalised_pin)
        if history is None:
            _fail("RECEIVER_CURSOR_BOOTSTRAP_REQUIRED")
        # Verify source signatures even for an idempotent retry.  The durable
        # record then has to contain the exact same canonical signed bytes.
        supplied_wal, supplied_blob = _verify_append_for_cursor(
            wal_segment_manifest=wal_segment_manifest,
            blob_frontier_manifest=blob_frontier_manifest,
            pin=normalised_pin,
            baseline=history.baseline,
            prior=history.cursor,
            standalone=True,
        )
        if _record_matches_pair(history.records[-1], wal=supplied_wal, blob=supplied_blob):
            return PhysicalWalIncrementalReceiverStageResult(
                status=PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS,
                cursor=history.cursor,
                record_path=history.record_paths[-1],
                idempotent=True,
            )
        # A source may not create a second blob frontier for an already
        # consumed WAL, nor a second WAL for the current blob frontier.  Both
        # are fork/replay attempts before we perform predecessor verification.
        if (
            supplied_wal.manifest_sha256 == history.cursor.wal_manifest_sha256
            or supplied_blob.manifest_sha256 == history.cursor.blob_frontier_manifest_sha256
        ):
            _fail("RECEIVER_CURSOR_APPEND_REPLAY_OR_FORK")
        wal, blob = _verify_append_for_cursor(
            wal_segment_manifest=wal_segment_manifest,
            blob_frontier_manifest=blob_frontier_manifest,
            pin=normalised_pin,
            baseline=history.baseline,
            prior=history.cursor,
            standalone=False,
        )
        record = _append_record(prior=history.cursor, pin=normalised_pin, wal=wal, blob=blob)
        path = paths.records_directory / f"{history.cursor.sequence + 1:020d}.json"
        _write_record(path, record)
        durable = _load_history(paths=paths, pin=normalised_pin)
        if durable is None or durable.records[-1] != record:
            _fail("RECEIVER_CURSOR_APPEND_DURABILITY_INVALID")
        return PhysicalWalIncrementalReceiverStageResult(
            status=PHYSICAL_WAL_INCREMENTAL_RECEIVER_STAGE_STATUS,
            cursor=durable.cursor,
            record_path=path,
            idempotent=False,
        )


def load_physical_wal_incremental_receiver_cursor(
    *,
    pin: PhysicalWalIncrementalReceiverPin,
    config: PhysicalWalIncrementalReceiverConfig,
) -> PhysicalWalIncrementalReceiverCursor:
    """Load and re-verify the complete local metadata cursor history."""

    normalised_config = _normalise_config(config)
    normalised_pin = _normalise_pin(pin, verify_route_hash=True)
    paths = _state_paths(normalised_config)
    with _locked_state(paths):
        history = _load_history(paths=paths, pin=normalised_pin)
        if history is None:
            _fail("RECEIVER_CURSOR_BOOTSTRAP_REQUIRED")
        return history.cursor
