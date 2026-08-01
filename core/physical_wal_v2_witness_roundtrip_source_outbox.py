"""Root-owned FI source outbox for the portable V2 Witness roundtrip.

This is deliberately a *local* durable boundary.  It accepts a freshly
verified Witness context certificate, constructs the ordinary V2 source
request locally, then reserves its canonical request hash before it signs the
portable FI-to-Witness envelope.  It neither selects nor performs transport:
an independently reviewed four-hop delivery runtime may carry only the
returned opaque envelope bytes.

The reservation is intentionally fail-closed.  A crash or signing failure
after the reservation is durable leaves an indeterminate record rather than
allowing a retry to reseal a second envelope.  Exact completed retries return
the original persisted bytes only after fresh certificate/envelope validation.
No WA-IR local capability, recovery object, receiver ledger object, endpoint,
credential, provider client, socket, or process surface exists here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckError,
    verify_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_witness_roundtrip_contract import (
    PhysicalWalV2WitnessRoundtripConfig,
    PhysicalWalV2WitnessRoundtripError,
    VerifiedPhysicalWalV2WitnessContextCertificate,
    VerifiedPhysicalWalV2WitnessSourceEnvelope,
    build_physical_wal_v2_witness_source_envelope,
    build_physical_wal_v2_witness_source_request,
    verify_physical_wal_v2_witness_context_certificate,
    verify_physical_wal_v2_witness_source_envelope,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_MAXIMUM_ENTRIES",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DEFAULT_ENABLED",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SCHEMA",
    "PhysicalWalV2WitnessRoundtripSourceOutboxConfig",
    "PhysicalWalV2WitnessRoundtripSourceOutboxError",
    "PhysicalWalV2WitnessRoundtripSourceOutboxResult",
    "enqueue_physical_wal_v2_witness_source_envelope",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-source-outbox-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_MAXIMUM_ENTRIES = 256

MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRIES = 4_096
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_BYTES = 8 * 1024 * 1024
MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_WIRE_BYTES = 256 * 1024

_DIRECTORY = "physical-wal-v2-witness-roundtrip-source-outbox-v1"
_STATE_FILENAME = "outbox.json"
_LOCK_FILENAME = "outbox.lock"
_STATE_SCHEMA = "gold-trade-physical-wal-v2-witness-roundtrip-source-outbox-state-v1"
_STATE_VERSION = 1
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_STATUSES = frozenset({"reserved", "completed"})

_STATE_FIELDS = frozenset(
    {"schema", "version", "configuration_sha256", "clock_floor", "entries"}
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "status",
        "source_request_base64",
        "source_request_sha256",
        "context_certificate_sha256",
        "certificate_expires_at",
        "context_sha256",
        "request_id",
        "request_nonce",
        "request_expires_at",
        "outbox_id",
        "outbox_nonce",
        "outbox_expires_at",
        "reserved_at",
        "clock_floor",
        "source_envelope_base64",
        "source_envelope_sha256",
        "completed_at",
    }
)


class PhysicalWalV2WitnessRoundtripSourceOutboxError(RuntimeError):
    """The FI-only durable source envelope boundary is unsafe or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripSourceOutboxConfig:
    """Default-off root-owned state for one exact public V2 policy binding."""

    state_root: Path | None = None
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DEFAULT_ENABLED
    maximum_entries: int = DEFAULT_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_MAXIMUM_ENTRIES


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripSourceOutboxResult:
    """One redacted, already durable FI-to-Witness portable envelope.

    ``canonical_source_envelope`` is intentionally the only carrier payload.
    It is signed canonical wire data, never an IR-local Python capability and
    never a transport instruction.
    """

    schema: str
    source_request_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    request_id: str
    request_nonce: str
    outbox_id: str
    outbox_nonce: str
    source_envelope_sha256: str
    expires_at: datetime
    committed_at: datetime
    canonical_source_envelope: bytes = field(repr=False)
    idempotent: bool = False


@dataclass(frozen=True)
class _Config:
    root: Path
    roundtrip_config: PhysicalWalV2WitnessRoundtripConfig
    configuration_sha256: str
    maximum_entries: int


@dataclass(frozen=True)
class _Intent:
    certificate: VerifiedPhysicalWalV2WitnessContextCertificate
    request_id: str
    request_nonce: str
    outbox_id: str
    outbox_nonce: str
    expires_at: datetime


@dataclass(frozen=True)
class _Prepared:
    intent: _Intent
    source_request: bytes
    source_request_sha256: str


@dataclass(frozen=True)
class _Entry:
    sequence: int
    status: str
    source_request: bytes
    source_request_sha256: str
    context_certificate_sha256: str
    certificate_expires_at: datetime
    context_sha256: str
    request_id: str
    request_nonce: str
    request_expires_at: datetime
    outbox_id: str
    outbox_nonce: str
    outbox_expires_at: datetime
    reserved_at: datetime
    clock_floor: datetime
    source_envelope: bytes | None
    source_envelope_sha256: str | None
    completed_at: datetime | None


@dataclass(frozen=True)
class _State:
    entries: tuple[_Entry, ...]
    clock_floor: datetime | None


@dataclass(frozen=True)
class _Storage:
    directory_fd: int


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_JSON_CONSTANT_FORBIDDEN")


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc


def _host_now() -> datetime:
    try:
        return datetime.now(timezone.utc).replace(microsecond=0)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CLOCK_INVALID"
        ) from exc


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_PLATFORM_UNSAFE")


def _safe_root_path(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or any(
        item in {"", ".", ".."} for item in value.parts[1:]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_ROOT_UNSAFE")
    return value


def _safe_directory_metadata(value: os.stat_result, *, final: bool) -> None:
    mode = stat.S_IMODE(value.st_mode)
    sticky_root_parent = value.st_uid == 0 and bool(value.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != 0
        or (final and mode != 0o700)
        or (not final and mode & 0o022 and not sticky_root_parent)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_ROOT_UNSAFE")


def _open_secure_root(value: Path) -> int:
    _require_fd_platform()
    path = _safe_root_path(value)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        parts = path.parts[1:]
        for index, component in enumerate(parts):
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            _safe_directory_metadata(
                os.fstat(descriptor),
                final=index == len(parts) - 1,
            )
        if not parts:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_ROOT_UNSAFE"
        ) from exc


def _fsync_fd(descriptor: int, *, code: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc


def _safe_child_metadata(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    code: str,
) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc
    expected_mode = 0o700 if directory else 0o600
    if (
        stat.S_ISLNK(metadata.st_mode)
        or (directory and not stat.S_ISDIR(metadata.st_mode))
        or (not directory and not stat.S_ISREG(metadata.st_mode))
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or (not directory and metadata.st_nlink != 1)
    ):
        _fail(code)
    return metadata


def _ensure_child_directory(parent_fd: int, name: str) -> int:
    _require_fd_platform()
    try:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            _fsync_fd(parent_fd, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_FSYNC_FAILED")
        except FileExistsError:
            pass
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_UNSAFE"
        ) from exc
    try:
        _safe_child_metadata(
            parent_fd,
            name,
            directory=True,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_UNSAFE",
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_UNSAFE")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_lock(directory_fd: int) -> int:
    _require_fd_platform()
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        try:
            descriptor = os.open(_LOCK_FILENAME, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    _LOCK_FILENAME,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                _fsync_fd(directory_fd, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_FSYNC_FAILED")
            except FileExistsError:
                descriptor = os.open(_LOCK_FILENAME, flags, dir_fd=directory_fd)
        _safe_child_metadata(
            directory_fd,
            _LOCK_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_LOCK_UNSAFE",
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_LOCK_OPEN_FAILED"
        ) from exc


@contextmanager
def _locked(config: _Config) -> Iterator[_Storage]:
    root_fd = -1
    directory_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root(config.root)
        directory_fd = _ensure_child_directory(root_fd, _DIRECTORY)
        lock_fd = _open_lock(directory_fd)
        yield _Storage(directory_fd=directory_fd)
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _read_file_at(parent_fd: int, name: str, *, code: str) -> bytes | None:
    _require_fd_platform()
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_BYTES
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
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_canonical(raw: bytes, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc
    if type(value) is not dict or _canonical(value, code=code) != raw:
        _fail(code)
    return value


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _b64(value: object, *, code: str, permit_none: bool = False) -> bytes | None:
    if value is None and permit_none:
        return None
    if type(value) is not str or not value or len(value) > MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_WIRE_BYTES * 2:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc
    if not 1 <= len(result) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_WIRE_BYTES:
        _fail(code)
    return result


def _b64_text(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _entry_from_mapping(value: object, *, config: _Config) -> _Entry:
    item = _exact_mapping(value, fields=_ENTRY_FIELDS, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_FIELDS_INVALID")
    if type(item["sequence"]) is not int or item["sequence"] < 1:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_SEQUENCE_INVALID")
    status = item["status"]
    if type(status) is not str or status not in _STATUSES:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_STATUS_INVALID")
    source_request = _b64(
        item["source_request_base64"],
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_REQUEST_INVALID",
    )
    assert source_request is not None
    source_request_sha = _sha256(
        item["source_request_sha256"],
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_REQUEST_INVALID",
    )
    if _sha256_bytes(source_request) != source_request_sha:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_REQUEST_INVALID")
    envelope = _b64(
        item["source_envelope_base64"],
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_ENVELOPE_INVALID",
        permit_none=True,
    )
    envelope_sha = item["source_envelope_sha256"]
    completed_at = item["completed_at"]
    if status == "reserved":
        if envelope is not None or envelope_sha is not None or completed_at is not None:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_STATUS_INVALID")
        normalized_envelope_sha: str | None = None
        normalized_completed_at: datetime | None = None
    else:
        if envelope is None:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_STATUS_INVALID")
        normalized_envelope_sha = _sha256(
            envelope_sha,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_ENVELOPE_INVALID",
        )
        if _sha256_bytes(envelope) != normalized_envelope_sha:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_ENVELOPE_INVALID")
        normalized_completed_at = _parse_timestamp(
            completed_at,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        )
    result = _Entry(
        sequence=item["sequence"],
        status=status,
        source_request=source_request,
        source_request_sha256=source_request_sha,
        context_certificate_sha256=_sha256(
            item["context_certificate_sha256"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_CONTEXT_INVALID",
        ),
        certificate_expires_at=_parse_timestamp(
            item["certificate_expires_at"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        context_sha256=_sha256(
            item["context_sha256"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_CONTEXT_INVALID",
        ),
        request_id=_identifier(
            item["request_id"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_IDENTITY_INVALID",
        ),
        request_nonce=_nonce(
            item["request_nonce"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_IDENTITY_INVALID",
        ),
        request_expires_at=_parse_timestamp(
            item["request_expires_at"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        outbox_id=_identifier(
            item["outbox_id"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_IDENTITY_INVALID",
        ),
        outbox_nonce=_nonce(
            item["outbox_nonce"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_IDENTITY_INVALID",
        ),
        outbox_expires_at=_parse_timestamp(
            item["outbox_expires_at"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        reserved_at=_parse_timestamp(
            item["reserved_at"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        clock_floor=_parse_timestamp(
            item["clock_floor"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        source_envelope=envelope,
        source_envelope_sha256=normalized_envelope_sha,
        completed_at=normalized_completed_at,
    )
    if (
        result.request_expires_at != result.outbox_expires_at
        or result.certificate_expires_at < result.request_expires_at
        or result.reserved_at > result.clock_floor
        or (result.completed_at is not None and result.completed_at != result.clock_floor)
        or result.clock_floor > result.outbox_expires_at
        or len({result.request_id, result.request_nonce, result.outbox_id, result.outbox_nonce}) != 4
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_OR_IDENTITY_INVALID")
    return result


def _entry_mapping(value: _Entry) -> dict[str, object]:
    return {
        "sequence": value.sequence,
        "status": value.status,
        "source_request_base64": _b64_text(value.source_request),
        "source_request_sha256": value.source_request_sha256,
        "context_certificate_sha256": value.context_certificate_sha256,
        "certificate_expires_at": _timestamp(
            value.certificate_expires_at,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        "context_sha256": value.context_sha256,
        "request_id": value.request_id,
        "request_nonce": value.request_nonce,
        "request_expires_at": _timestamp(
            value.request_expires_at,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        "outbox_id": value.outbox_id,
        "outbox_nonce": value.outbox_nonce,
        "outbox_expires_at": _timestamp(
            value.outbox_expires_at,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        "reserved_at": _timestamp(
            value.reserved_at,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        "clock_floor": _timestamp(
            value.clock_floor,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
        ),
        "source_envelope_base64": (
            None if value.source_envelope is None else _b64_text(value.source_envelope)
        ),
        "source_envelope_sha256": value.source_envelope_sha256,
        "completed_at": (
            None
            if value.completed_at is None
            else _timestamp(
                value.completed_at,
                code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_TIME_INVALID",
            )
        ),
    }


def _state_mapping(*, config: _Config, state: _State) -> dict[str, object]:
    if state.clock_floor is None:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_CLOCK_INVALID")
    return {
        "schema": _STATE_SCHEMA,
        "version": _STATE_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "clock_floor": _timestamp(
            state.clock_floor,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_CLOCK_INVALID",
        ),
        "entries": [_entry_mapping(entry) for entry in state.entries],
    }


def _validate_entry_indexes(entries: tuple[_Entry, ...]) -> None:
    if tuple(entry.sequence for entry in entries) != tuple(range(1, len(entries) + 1)):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_ORDER_INVALID")
    for values, code in (
        ([entry.source_request_sha256 for entry in entries], "REQUEST_HASH"),
        ([entry.request_id for entry in entries], "REQUEST_ID"),
        ([entry.request_nonce for entry in entries], "REQUEST_NONCE"),
        ([entry.outbox_id for entry in entries], "OUTBOX_ID"),
        ([entry.outbox_nonce for entry in entries], "OUTBOX_NONCE"),
    ):
        if len(set(values)) != len(values):
            _fail(f"V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_{code}_REUSE_CONFLICT")


def _load_state(storage: _Storage, *, config: _Config, trusted_now: datetime) -> _State:
    raw = _read_file_at(
        storage.directory_fd,
        _STATE_FILENAME,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_UNSAFE",
    )
    if raw is None:
        return _State(entries=(), clock_floor=None)
    item = _exact_mapping(
        _parse_canonical(raw, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_INVALID"),
        fields=_STATE_FIELDS,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_FIELDS_INVALID",
    )
    if (
        item["schema"] != _STATE_SCHEMA
        or item["version"] != _STATE_VERSION
        or _sha256(
            item["configuration_sha256"],
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIGURATION_CONFLICT",
        )
        != config.configuration_sha256
        or type(item["entries"]) is not list
        or len(item["entries"]) > config.maximum_entries
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIGURATION_CONFLICT")
    clock_floor = _parse_timestamp(
        item["clock_floor"],
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_CLOCK_INVALID",
    )
    entries = tuple(_entry_from_mapping(value, config=config) for value in item["entries"])
    _validate_entry_indexes(entries)
    if any(entry.clock_floor > clock_floor for entry in entries) or trusted_now < clock_floor:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CLOCK_ROLLBACK_DETECTED")
    return _State(entries=entries, clock_floor=clock_floor)


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(code)
            view = view[written:]
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(code) from exc


def _write_state(storage: _Storage, *, config: _Config, state: _State) -> None:
    _require_fd_platform()
    payload = _canonical(
        _state_mapping(config=config, state=state),
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_INVALID",
    )
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_BYTES:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_SIZE_INVALID")
    temporary = ".outbox." + secrets.token_hex(16) + ".tmp"
    descriptor = -1
    replaced = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=storage.directory_fd,
        )
        _safe_child_metadata(
            storage.directory_fd,
            temporary,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_TEMPORARY_UNSAFE",
        )
        _write_all(
            descriptor,
            payload,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_TEMPORARY_WRITE_FAILED",
        )
        _fsync_fd(descriptor, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_TEMPORARY_FSYNC_FAILED")
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            _STATE_FILENAME,
            src_dir_fd=storage.directory_fd,
            dst_dir_fd=storage.directory_fd,
        )
        replaced = True
        _fsync_fd(storage.directory_fd, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DIRECTORY_FSYNC_FAILED")
        _safe_child_metadata(
            storage.directory_fd,
            _STATE_FILENAME,
            directory=False,
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_UNSAFE",
        )
    except PhysicalWalV2WitnessRoundtripSourceOutboxError:
        raise
    except OSError as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_STATE_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary, dir_fd=storage.directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _configuration_sha256(value: PhysicalWalV2WitnessRoundtripConfig, *, maximum_entries: int) -> str:
    remote = value.remote_ack_config
    if type(remote) is not PhysicalWalV2RemoteAckConfig:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    fields = (
        value.ir_recovery_exporter_public_key,
        value.fi_outbox_public_key,
        value.ir_durable_assertion_public_key,
        value.witness_public_key,
        remote.expected_source_public_key,
        remote.expected_destination_public_key,
    )
    if any(type(item) is not bytes or len(item) != 32 for item in fields):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    _sha256(remote.expected_context_sha256, code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    if (
        type(remote.expected_source_site) is not str
        or type(remote.expected_destination_site) is not str
        or not remote.expected_source_site
        or not remote.expected_destination_site
        or remote.enabled is not True
        or value.enabled is not True
        or type(value.maximum_evidence_age_seconds) is not int
        or value.maximum_evidence_age_seconds < 1
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    return _sha256_bytes(
        _canonical(
            {
                "schema": "gold-trade-physical-wal-v2-witness-roundtrip-source-outbox-binding-v1",
                "remote_ack": {
                    "expected_context_sha256": remote.expected_context_sha256,
                    "expected_source_site": remote.expected_source_site,
                    "expected_destination_site": remote.expected_destination_site,
                    "expected_source_public_key_base64": _b64_text(remote.expected_source_public_key),
                    "expected_destination_public_key_base64": _b64_text(remote.expected_destination_public_key),
                    "maximum_evidence_age_seconds": remote.maximum_evidence_age_seconds,
                },
                "ir_recovery_exporter_public_key_base64": _b64_text(value.ir_recovery_exporter_public_key),
                "fi_outbox_public_key_base64": _b64_text(value.fi_outbox_public_key),
                "ir_durable_assertion_public_key_base64": _b64_text(value.ir_durable_assertion_public_key),
                "witness_public_key_base64": _b64_text(value.witness_public_key),
                "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
                "maximum_entries": maximum_entries,
            },
            code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID",
        )
    )


def _normalise_config(value: object) -> _Config:
    if type(value) is not PhysicalWalV2WitnessRoundtripSourceOutboxConfig:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_DISABLED")
    _require_root()
    if (
        type(value.roundtrip_config) is not PhysicalWalV2WitnessRoundtripConfig
        or type(value.maximum_entries) is not int
        or not 1 <= value.maximum_entries <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRIES
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONFIG_INVALID")
    root = _safe_root_path(value.state_root)
    return _Config(
        root=root,
        roundtrip_config=value.roundtrip_config,
        configuration_sha256=_configuration_sha256(
            value.roundtrip_config,
            maximum_entries=value.maximum_entries,
        ),
        maximum_entries=value.maximum_entries,
    )


def _fresh_certificate(
    value: object,
    *,
    config: _Config,
    now: datetime,
) -> VerifiedPhysicalWalV2WitnessContextCertificate:
    if type(value) is not VerifiedPhysicalWalV2WitnessContextCertificate:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONTEXT_CERTIFICATE_CAPABILITY_REQUIRED")
    try:
        raw = value.canonical_certificate
        result = verify_physical_wal_v2_witness_context_certificate(
            raw,
            config=config.roundtrip_config,
            now=now,
        )
    except (AttributeError, PhysicalWalV2WitnessRoundtripError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONTEXT_CERTIFICATE_INVALID_OR_STALE"
        ) from exc
    if type(result) is not VerifiedPhysicalWalV2WitnessContextCertificate:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONTEXT_CERTIFICATE_INVALID_OR_STALE")
    return result


def _intent(
    *,
    certificate: VerifiedPhysicalWalV2WitnessContextCertificate,
    request_id: object,
    request_nonce: object,
    outbox_id: object,
    outbox_nonce: object,
    expires_at: object,
    config: _Config,
    now: datetime,
) -> _Intent:
    identity = _identifier(
        request_id,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_REQUEST_ID_INVALID",
    )
    nonce = _nonce(
        request_nonce,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_REQUEST_NONCE_INVALID",
    )
    outbox_identity = _identifier(
        outbox_id,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_OUTBOX_ID_INVALID",
    )
    outbox_value = _nonce(
        outbox_nonce,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_OUTBOX_NONCE_INVALID",
    )
    expiry = _utc(
        expires_at,
        code="V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_EXPIRY_INVALID",
    )
    if (
        len({identity, nonce, outbox_identity, outbox_value}) != 4
        or expiry <= now
        or expiry > certificate.expires_at
        or expiry > now + timedelta(seconds=config.roundtrip_config.maximum_evidence_age_seconds)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_EXPIRY_OR_IDENTITY_INVALID")
    return _Intent(
        certificate=certificate,
        request_id=identity,
        request_nonce=nonce,
        outbox_id=outbox_identity,
        outbox_nonce=outbox_value,
        expires_at=expiry,
    )


def _validate_intent_against_entry(intent: _Intent, entry: _Entry) -> bool:
    return (
        entry.context_certificate_sha256 == intent.certificate.certificate_sha256
        and entry.certificate_expires_at == intent.certificate.expires_at
        and entry.context_sha256 == intent.certificate.context_sha256
        and entry.request_id == intent.request_id
        and entry.request_nonce == intent.request_nonce
        and entry.outbox_id == intent.outbox_id
        and entry.outbox_nonce == intent.outbox_nonce
        and entry.request_expires_at == intent.expires_at
        and entry.outbox_expires_at == intent.expires_at
    )


def _existing_intent_entry(entries: tuple[_Entry, ...], *, intent: _Intent) -> _Entry | None:
    matching = tuple(
        entry
        for entry in entries
        if intent.request_id == entry.request_id
        or intent.request_nonce == entry.request_nonce
        or intent.outbox_id == entry.outbox_id
        or intent.outbox_nonce == entry.outbox_nonce
    )
    if not matching:
        return None
    if len(matching) != 1 or not _validate_intent_against_entry(intent, matching[0]):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_INTENT_REUSE_CONFLICT")
    return matching[0]


def _prepare_source_request(
    *,
    intent: _Intent,
    config: _Config,
    source_signer: object,
    now: datetime,
) -> _Prepared:
    certificate = _fresh_certificate(intent.certificate, config=config, now=now)
    if (
        certificate.canonical_certificate != intent.certificate.canonical_certificate
        or certificate.certificate_sha256 != intent.certificate.certificate_sha256
        or certificate.context_sha256 != intent.certificate.context_sha256
        or certificate.expires_at != intent.certificate.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONTEXT_CERTIFICATE_CHANGED")
    try:
        wire = build_physical_wal_v2_witness_source_request(
            config=config.roundtrip_config,
            context_certificate=certificate,
            request_id=intent.request_id,
            request_nonce=intent.request_nonce,
            expires_at=intent.expires_at,
            source_signer=source_signer,
            now=now,
        )
        request = verify_physical_wal_v2_remote_ack_request(
            source_request=wire,
            config=config.roundtrip_config.remote_ack_config,
            now=now,
        )
    except (PhysicalWalV2WitnessRoundtripError, PhysicalWalV2RemoteAckError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SOURCE_REQUEST_INVALID"
        ) from exc
    raw = getattr(request, "canonical_request", None)
    if (
        type(raw) is not bytes
        or not 1 <= len(raw) <= MAX_PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_WIRE_BYTES
        or getattr(request, "context_sha256", None) != certificate.context_sha256
        or getattr(request, "request_id", None) != intent.request_id
        or getattr(request, "request_nonce", None) != intent.request_nonce
        or getattr(request, "expires_at", None) != intent.expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SOURCE_REQUEST_INVALID")
    return _Prepared(
        intent=replace(intent, certificate=certificate),
        source_request=raw,
        source_request_sha256=_sha256_bytes(raw),
    )


def _existing_hash_conflict(entries: tuple[_Entry, ...], *, prepared: _Prepared) -> None:
    for entry in entries:
        if entry.source_request_sha256 == prepared.source_request_sha256:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_REQUEST_HASH_REUSE_CONFLICT")


def _reserved_entry(*, sequence: int, prepared: _Prepared, now: datetime) -> _Entry:
    intent = prepared.intent
    return _Entry(
        sequence=sequence,
        status="reserved",
        source_request=prepared.source_request,
        source_request_sha256=prepared.source_request_sha256,
        context_certificate_sha256=intent.certificate.certificate_sha256,
        certificate_expires_at=intent.certificate.expires_at,
        context_sha256=intent.certificate.context_sha256,
        request_id=intent.request_id,
        request_nonce=intent.request_nonce,
        request_expires_at=intent.expires_at,
        outbox_id=intent.outbox_id,
        outbox_nonce=intent.outbox_nonce,
        outbox_expires_at=intent.expires_at,
        reserved_at=now,
        clock_floor=now,
        source_envelope=None,
        source_envelope_sha256=None,
        completed_at=None,
    )


def _assert_entry_matches_prepared(entry: _Entry, prepared: _Prepared) -> None:
    intent = prepared.intent
    if (
        entry.status != "reserved"
        or entry.source_request != prepared.source_request
        or entry.source_request_sha256 != prepared.source_request_sha256
        or not _validate_intent_against_entry(intent, entry)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_RESERVATION_CORRUPTED")


def _verified_completed_result(
    *,
    entry: _Entry,
    intent: _Intent,
    config: _Config,
    now: datetime,
    idempotent: bool,
) -> PhysicalWalV2WitnessRoundtripSourceOutboxResult:
    if entry.status != "completed" or entry.source_envelope is None or entry.source_envelope_sha256 is None:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_RESERVATION_INDETERMINATE")
    certificate = _fresh_certificate(intent.certificate, config=config, now=now)
    if not _validate_intent_against_entry(replace(intent, certificate=certificate), entry):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_INTENT_REUSE_CONFLICT")
    try:
        envelope = verify_physical_wal_v2_witness_source_envelope(
            entry.source_envelope,
            config=config.roundtrip_config,
            now=now,
        )
    except (PhysicalWalV2WitnessRoundtripError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_PERSISTED_ENVELOPE_INVALID_OR_STALE"
        ) from exc
    if type(envelope) is not VerifiedPhysicalWalV2WitnessSourceEnvelope:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_PERSISTED_ENVELOPE_INVALID_OR_STALE")
    if (
        envelope.canonical_envelope != entry.source_envelope
        or envelope.envelope_sha256 != entry.source_envelope_sha256
        or envelope.canonical_source_request != entry.source_request
        or envelope.source_request_sha256 != entry.source_request_sha256
        or envelope.context_certificate_sha256 != entry.context_certificate_sha256
        or envelope.context_sha256 != entry.context_sha256
        or envelope.request_id != entry.request_id
        or envelope.request_nonce != entry.request_nonce
        or envelope.request_expires_at != entry.request_expires_at
        or envelope.outbox_id != entry.outbox_id
        or envelope.outbox_nonce != entry.outbox_nonce
        or envelope.expires_at != entry.outbox_expires_at
        or entry.completed_at is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_PERSISTED_ENVELOPE_MISMATCH")
    return PhysicalWalV2WitnessRoundtripSourceOutboxResult(
        schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SCHEMA,
        source_request_sha256=entry.source_request_sha256,
        context_certificate_sha256=entry.context_certificate_sha256,
        context_sha256=entry.context_sha256,
        request_id=entry.request_id,
        request_nonce=entry.request_nonce,
        outbox_id=entry.outbox_id,
        outbox_nonce=entry.outbox_nonce,
        source_envelope_sha256=entry.source_envelope_sha256,
        expires_at=entry.outbox_expires_at,
        committed_at=entry.completed_at,
        canonical_source_envelope=entry.source_envelope,
        idempotent=idempotent,
    )


def _build_completed_entry(
    *,
    entry: _Entry,
    intent: _Intent,
    config: _Config,
    fi_outbox_signer: object,
    now: datetime,
) -> _Entry:
    """Sign only after the exact source-request reservation is durable."""

    certificate = _fresh_certificate(intent.certificate, config=config, now=now)
    if not _validate_intent_against_entry(replace(intent, certificate=certificate), entry):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CONTEXT_CERTIFICATE_CHANGED")
    try:
        request = verify_physical_wal_v2_remote_ack_request(
            source_request=entry.source_request,
            config=config.roundtrip_config.remote_ack_config,
            now=now,
        )
    except (PhysicalWalV2RemoteAckError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_RESERVED_REQUEST_INVALID_OR_STALE"
        ) from exc
    if (
        getattr(request, "canonical_request", None) != entry.source_request
        or getattr(request, "context_sha256", None) != entry.context_sha256
        or getattr(request, "request_id", None) != entry.request_id
        or getattr(request, "request_nonce", None) != entry.request_nonce
        or getattr(request, "expires_at", None) != entry.request_expires_at
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_RESERVED_REQUEST_MISMATCH")
    try:
        wire = build_physical_wal_v2_witness_source_envelope(
            config=config.roundtrip_config,
            context_certificate=certificate,
            source_request=entry.source_request,
            outbox_id=entry.outbox_id,
            outbox_nonce=entry.outbox_nonce,
            expires_at=entry.outbox_expires_at,
            fi_outbox_signer=fi_outbox_signer,
            now=now,
        )
        envelope = verify_physical_wal_v2_witness_source_envelope(
            wire,
            config=config.roundtrip_config,
            now=now,
        )
    except (PhysicalWalV2WitnessRoundtripError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripSourceOutboxError(
            "V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SOURCE_ENVELOPE_INVALID"
        ) from exc
    if type(envelope) is not VerifiedPhysicalWalV2WitnessSourceEnvelope:
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SOURCE_ENVELOPE_INVALID")
    if (
        envelope.canonical_source_request != entry.source_request
        or envelope.source_request_sha256 != entry.source_request_sha256
        or envelope.context_certificate_sha256 != entry.context_certificate_sha256
        or envelope.context_sha256 != entry.context_sha256
        or envelope.request_id != entry.request_id
        or envelope.request_nonce != entry.request_nonce
        or envelope.request_expires_at != entry.request_expires_at
        or envelope.outbox_id != entry.outbox_id
        or envelope.outbox_nonce != entry.outbox_nonce
        or envelope.expires_at != entry.outbox_expires_at
        or _sha256_bytes(envelope.canonical_envelope) != envelope.envelope_sha256
    ):
        _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_SOURCE_ENVELOPE_MISMATCH")
    return replace(
        entry,
        status="completed",
        source_envelope=envelope.canonical_envelope,
        source_envelope_sha256=envelope.envelope_sha256,
        completed_at=now,
        clock_floor=now,
    )


def enqueue_physical_wal_v2_witness_source_envelope(
    *,
    config: PhysicalWalV2WitnessRoundtripSourceOutboxConfig,
    context_certificate: VerifiedPhysicalWalV2WitnessContextCertificate,
    request_id: str,
    request_nonce: str,
    outbox_id: str,
    outbox_nonce: str,
    expires_at: datetime,
    source_signer: object,
    fi_outbox_signer: object,
) -> PhysicalWalV2WitnessRoundtripSourceOutboxResult:
    """Durably mint one FI envelope, or return its exact completed retry.

    The caller never supplies a V2 raw context, recovery projection, target
    proof, or receiver-ledger object.  The contract re-verifies the typed
    Witness certificate at every effectful edge and is the sole owner of
    request/envelope signing grammar.
    """

    normalized = _normalise_config(config)
    initial_now = _host_now()
    certificate = _fresh_certificate(
        context_certificate,
        config=normalized,
        now=initial_now,
    )
    initial_intent = _intent(
        certificate=certificate,
        request_id=request_id,
        request_nonce=request_nonce,
        outbox_id=outbox_id,
        outbox_nonce=outbox_nonce,
        expires_at=expires_at,
        config=normalized,
        now=initial_now,
    )
    with _locked(normalized) as storage:
        locked_now = _host_now()
        state = _load_state(storage, config=normalized, trusted_now=locked_now)
        certificate = _fresh_certificate(
            initial_intent.certificate,
            config=normalized,
            now=locked_now,
        )
        intent = _intent(
            certificate=certificate,
            request_id=initial_intent.request_id,
            request_nonce=initial_intent.request_nonce,
            outbox_id=initial_intent.outbox_id,
            outbox_nonce=initial_intent.outbox_nonce,
            expires_at=initial_intent.expires_at,
            config=normalized,
            now=locked_now,
        )
        existing = _existing_intent_entry(state.entries, intent=intent)
        if existing is not None:
            if existing.status != "completed":
                _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_RESERVATION_INDETERMINATE")
            return _verified_completed_result(
                entry=existing,
                intent=intent,
                config=normalized,
                now=locked_now,
                idempotent=True,
            )
        if len(state.entries) >= normalized.maximum_entries:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_ENTRY_LIMIT_EXCEEDED")
        prepared = _prepare_source_request(
            intent=intent,
            config=normalized,
            source_signer=source_signer,
            now=locked_now,
        )
        _existing_hash_conflict(state.entries, prepared=prepared)
        reservation = _reserved_entry(
            sequence=len(state.entries) + 1,
            prepared=prepared,
            now=locked_now,
        )
        reserved_state = _State(
            entries=state.entries + (reservation,),
            clock_floor=locked_now,
        )
        _write_state(storage, config=normalized, state=reserved_state)
        reloaded = _load_state(storage, config=normalized, trusted_now=locked_now)
        reserved = reloaded.entries[-1]
        _assert_entry_matches_prepared(reserved, prepared)
        signing_now = _host_now()
        if signing_now < reserved.clock_floor:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CLOCK_ROLLBACK_DETECTED")
        completed = _build_completed_entry(
            entry=reserved,
            intent=prepared.intent,
            config=normalized,
            fi_outbox_signer=fi_outbox_signer,
            now=signing_now,
        )
        after_signing = _host_now()
        if after_signing < signing_now:
            _fail("V2_WITNESS_ROUNDTRIP_SOURCE_OUTBOX_CLOCK_ROLLBACK_DETECTED")
        # A later clock is persisted, and the envelope is re-verified against
        # it before its completed bytes become retryable.
        completed = replace(completed, clock_floor=after_signing, completed_at=after_signing)
        completed_state = _State(
            entries=reloaded.entries[:-1] + (completed,),
            clock_floor=after_signing,
        )
        _write_state(storage, config=normalized, state=completed_state)
        durable = _load_state(storage, config=normalized, trusted_now=after_signing).entries[-1]
        return _verified_completed_result(
            entry=durable,
            intent=prepared.intent,
            config=normalized,
            now=after_signing,
            idempotent=False,
        )
